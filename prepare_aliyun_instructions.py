"""Build local guidance using Alibaba DashScope TTS and the original earcons.

Only fixed, anonymous guidance text is sent. Credentials stay in the process or
an explicitly supplied private config file; metadata never contains credentials
or signed download URLs. Runtime playback remains fully local.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import wave

import numpy as np
from scipy.signal import butter, sosfiltfilt

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "assets/audio/instructions"
RATE = 24000
VOICE_STYLE = "清晰自然的普通话，亲切轻柔的女声，像工作人员面对面说明。语速自然，吐字清楚，语调平稳，不过分抑扬，不拖长尾音。只朗读文本，不添加口头语、笑声、背景音乐或环境音。"
TEXTS = {
    "closed-before-start": "接下来进行闭眼前测。下面先示范声音，请保持睁眼。当听到",
    "closed-between": "后，请轻轻闭眼。当听到",
    "closed-after-complete": "后，请慢慢睁眼。声音示范结束，现在请准备好，我们正式开始。",
    "open-before-start": "接下来进行睁眼前测。下面先示范声音。当听到",
    "open-between": "后，请自然注视屏幕中央的圆环，正常眨眼。当听到",
    "open-after-complete": "后，请保持放松，等待工作人员安排。声音示范结束，现在准备开始。",
    "closed-complete": "本段前测已完成，请慢慢睁开眼睛。",
    "open-complete": "睁眼测试已经完成。请放松一下。",
    "training-wait": "接下来将进行专注训练。请保持舒适的坐姿，自然看向画面，等待工作人员开始。",
    "training-start": "接下来开始本轮专注训练。请自然看向画面，正常眨眼，保持舒适的坐姿。准备好了，我们开始。",
    "training-round-complete": "这一轮训练已完成。请放松休息，下一轮开始前会再次提示。",
    "training-day-complete": "今天的训练已完成。请放松休息。",
    "recovery": "本段前测已暂停，请慢慢睁开眼睛，等待工作人员。",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pcm_wav(data):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(np.rint(np.clip(data, -.999, .999) * 32768).astype("<i2").tobytes())
    return stream.getvalue()


def decode_wav(contents):
    with wave.open(io.BytesIO(contents), "rb") as handle:
        if handle.getsampwidth() != 2 or handle.getframerate() != RATE:
            raise ValueError("TTS must return 24 kHz, 16-bit PCM WAV")
        data = np.frombuffer(handle.readframes(handle.getnframes()), "<i2").astype(float) / 32768
        channels = handle.getnchannels()
    return data.reshape(-1, channels).mean(axis=1)


def clean_speech(contents):
    data = decode_wav(contents)
    if not len(data) or np.max(np.abs(data)) < .001:
        raise ValueError("TTS returned no usable speech")
    data -= np.mean(data)
    # Remove DC/very low rumble. No synthetic noise or accompaniment is added.
    data = sosfiltfilt(butter(2, 65, btype="highpass", fs=RATE, output="sos"), data)
    # Trim only leading/trailing silence, retaining breaths and internal pauses.
    frame = round(RATE * .01)
    blocks = len(data) // frame
    rms = np.sqrt(np.mean(data[:blocks*frame].reshape(-1, frame)**2, axis=1))
    activity = np.flatnonzero(rms > max(.0003, float(np.max(rms)) * .006))
    if not len(activity):
        raise ValueError("TTS returned no speech above the silence floor")
    start = max(0, int(activity[0])*frame-round(RATE*.08))
    end = min(len(data), (int(activity[-1])+1)*frame+round(RATE*.12))
    data = data[start:end]
    fade = min(round(RATE*.008), len(data)//2)
    data[:fade] *= np.linspace(0, 1, fade)
    data[-fade:] *= np.linspace(1, 0, fade)
    peak = float(np.max(np.abs(data)))
    data *= .28 / peak
    return data


def synthesize(text, key, endpoint, model, voice):
    body = {"model": model, "input": {"text": text, "voice": voice, "language_type": "Chinese"}}
    if "instruct" in model:
        body["input"].update(instructions=VOICE_STYLE, optimize_instructions=True)
    request = urllib.request.Request(endpoint, json.dumps(body, ensure_ascii=False).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=75) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        # Do not include response bodies, request headers or credentials in logs.
        try:
            code = json.loads(error.read()).get("code", "unknown")
        except (ValueError, UnicodeError):
            code = "unknown"
        raise RuntimeError(f"Alibaba TTS HTTP {error.code}, code={code}") from None
    audio = result.get("output", {}).get("audio", {})
    if audio.get("url"):
        url = audio["url"]
        hostname = urllib.parse.urlsplit(url).hostname or ""
        if not (hostname.endswith(".aliyuncs.com") or hostname.endswith(".aliyun.com")):
            raise RuntimeError("TTS returned an unexpected audio host")
        # API responses may use an HTTP OSS URL; request its HTTPS equivalent.
        url = urllib.parse.urlunsplit(urllib.parse.urlsplit(url)._replace(scheme="https"))
        with urllib.request.urlopen(url, timeout=45) as response:
            return response.read()
    if audio.get("data"):
        data = base64.b64decode(audio["data"], validate=True)
        if data.startswith(b"RIFF"):
            return data
        return pcm_wav(np.frombuffer(data, "<i2").astype(float) / 32768)
    raise RuntimeError("Alibaba TTS did not return a complete audio file")


def earcon(kind):
    node = os.environ.get("NODE_BIN") or shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required to reuse the exact project earcon")
    code = "const c=require(process.argv[1]);process.stdout.write(Buffer.from(c.samples(process.argv[2],24000).buffer));"
    result = subprocess.run([node, "-e", code, str(ROOT / "calibration-cues.js"), kind],
                            check=True, capture_output=True)
    return np.frombuffer(result.stdout, "<f4").astype(float)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Private JSON: api_key, endpoint, model, voice")
    parser.add_argument("--refresh", action="store_true", help="Regenerate cached TTS parts")
    instruction_ids = ("closed-start", "open-start", "closed-complete", "open-complete", "training-wait",
                       "training-start", "training-round-complete", "training-day-complete", "recovery")
    parser.add_argument("--only", action="append", choices=instruction_ids,
                        help="Build only named instructions, preserving all other installed voices")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8-sig")) if args.config else {}
    key = os.environ.get("DASHSCOPE_API_KEY") or config.get("api_key")
    if not key:
        parser.error("Missing DASHSCOPE_API_KEY or private --config api_key")
    endpoint = config.get("endpoint", "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation")
    if endpoint not in {
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
        "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
    }:
        parser.error("Use the official Beijing or Singapore DashScope endpoint")
    model, voice = config.get("model", "qwen3-tts-instruct-flash"), config.get("voice", "Cherry")
    # Staging/cache stays private; a failed request cannot replace live assets.
    cache = ROOT / "runtime/tts-build"
    cache.mkdir(parents=True, exist_ok=True)
    requested = set(args.only or instruction_ids)
    selected_sources = set()
    for name in requested:
        if name in ("closed-start", "open-start"):
            stage = name.split("-")[0]
            selected_sources.update(stage + suffix for suffix in ("-before-start", "-between", "-after-complete"))
        else:
            selected_sources.add(name)
    parts, source_metadata = {}, []
    for name, text in TEXTS.items():
        if name not in selected_sources:
            continue
        cache_key = sha(json.dumps([endpoint, model, voice, VOICE_STYLE, text], ensure_ascii=False).encode())
        source = cache / (cache_key + ".wav")
        if args.refresh or not source.is_file():
            source.write_bytes(synthesize(text, key, endpoint, model, voice))
        contents = source.read_bytes()
        parts[name] = clean_speech(contents)
        source_metadata.append({"id": name, "text": text, "sourceSha256": sha(contents), "cacheFile": source.name})
        print("Prepared", name, round(len(parts[name])/RATE, 3), "seconds", flush=True)
    motifs = {kind: earcon(kind) for kind in ("start", "complete")}
    composed = {}
    for stage in ("closed", "open"):
        if stage + "-start" not in requested:
            continue
        composed[stage + "-start"] = np.concatenate([
            parts[stage+"-before-start"], motifs["start"], parts[stage+"-between"],
            motifs["complete"], parts[stage+"-after-complete"],
        ])
    for name in instruction_ids:
        if name in requested and name not in ("closed-start", "open-start"):
            composed[name] = parts[name]
    old_metadata = json.loads((DEST / "metadata.json").read_text(encoding="utf-8")) if args.only and (DEST / "metadata.json").is_file() else {}
    source_metadata = [item for item in old_metadata.get("sourceParts", []) if item["id"] not in selected_sources] + source_metadata
    metadata = {**old_metadata, "version": 4, "engine": "Alibaba DashScope TTS", "model": model, "voice": voice,
        "language": "Chinese", "sampleRate": RATE, "leadSeconds": .45, "tailSeconds": .25,
        "peakTarget": .28, "style": VOICE_STYLE,
        "sourceParts": source_metadata, "demonstration": "spoken prefix -> original start -> action -> original complete -> action; formal start follows in player",
        "instructions": [item for item in old_metadata.get("instructions", []) if item["id"] not in requested]}
    outputs = {}
    for name, data in composed.items():
        padded = np.concatenate([np.zeros(round(RATE*.45)), data, np.zeros(round(RATE*.25))])
        contents = outputs[name] = pcm_wav(padded)
        metadata["instructions"].append({"id": name, "file": name+".wav", "duration": len(padded)/RATE,
            "sampleRate": RATE, "channels": 1, "sha256": sha(contents),
            "text": " ".join(TEXTS[key] for key in (name.replace("-start", "-before-start"), name.replace("-start", "-between"), name.replace("-start", "-after-complete"))) if name in ("closed-start", "open-start") else TEXTS[name],
            "containsDemonstration": name in ("closed-start", "open-start")})
    if "closed-start" in outputs:
        preview = np.concatenate([decode_wav(outputs["closed-start"]), np.zeros(round(RATE*.075)), motifs["start"]])
        outputs["closed-preview"] = pcm_wav(preview)
        metadata["previewFile"] = "closed-preview.wav"
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / "cues").mkdir(exist_ok=True)
    for kind, data in motifs.items():
        (DEST / "cues" / (kind+".wav")).write_bytes(pcm_wav(data))
    for name, contents in outputs.items():
        staged = DEST / (name+".wav.tmp")
        staged.write_bytes(contents)
        staged.replace(DEST / (name+".wav"))
    (DEST / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(f"Installed {len(composed)} local Alibaba guidance WAVs; unrequested assets were preserved; API credentials are not in assets.")


if __name__ == "__main__":
    main()
