"""Prepare the purchased Easy Going layers without trimming or resampling them.

Source ZIP and byte-identical source WAVs are retained. Only a 3 ms window at
each end receives a raised-cosine endpoint correction. This is a sample-click
repair, not a musical crossfade: every stem keeps its original 60 second cycle.
Measurements are PCM engineering checks, not a subjective listening verdict.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import itertools
import json
import wave
import zipfile
from pathlib import Path

import numpy as np
from scipy import signal


ROOT = Path(__file__).resolve().parent
DEST = ROOT / "assets" / "audio" / "easy-going"
SOURCE_NAMES = tuple(f"Track_1_Layer_{i}.wav" for i in range(1, 4))
REPAIR_SECONDS = 0.003


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decode(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getcomptype()) != (2, 2, "NONE"):
            raise ValueError("Expected uncompressed 16-bit stereo WAV")
        rate = wav.getframerate()
        pcm = np.frombuffer(wav.readframes(wav.getnframes()), "<i2")
    return pcm.reshape(-1, 2).astype(np.float64) / 32768.0, rate


def encode(samples: np.ndarray, rate: int) -> bytes:
    pcm = np.rint(samples * 32768.0)
    if np.any(pcm < -32768) or np.any(pcm > 32767):
        raise ValueError("Endpoint repair would clip PCM")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm.astype("<i2").tobytes())
    return output.getvalue()


def repair_endpoint(samples: np.ndarray, rate: int) -> np.ndarray:
    """Bring adjacent loop samples to their midpoint over two short windows.

    Corrections and their first derivatives vanish at each window's interior
    edge. The original sample positions, channel relationship and gain are
    retained throughout the rest of the loop.
    """
    count = round(rate * REPAIR_SECONDS)
    if len(samples) < 2 * count:
        raise ValueError("Audio is too short for endpoint correction")
    result = samples.copy()
    envelope = 0.5 * (1 + np.cos(np.linspace(0, np.pi, count)))
    midpoint = (samples[0] + samples[-1]) / 2
    result[:count] += (midpoint - samples[0]) * envelope[:, None]
    result[-count:] += (midpoint - samples[-1]) * envelope[::-1, None]
    return np.rint(result * 32768.0) / 32768.0


def pcm_metrics(samples: np.ndarray, rate: int) -> dict:
    endpoint_count = round(rate * REPAIR_SECONDS)
    seam = np.concatenate((samples[-endpoint_count:], samples[:endpoint_count]))
    return {
        "rms": float(np.sqrt(np.mean(samples * samples))),
        "peak": float(np.max(np.abs(samples))),
        "sampleClippingCount": int(np.count_nonzero(np.abs(samples) >= 1.0)),
        "loopBoundaryStep": float(np.max(np.abs(samples[0] - samples[-1]))),
        "adjacentStep99_9Percentile": float(np.percentile(np.abs(np.diff(samples, axis=0)), 99.9)),
        "seamWindowMaximumAdjacentStep": float(np.max(np.abs(np.diff(seam, axis=0)))),
    }


def grid_evidence(samples: np.ndarray, rate: int) -> dict:
    """Test the timing grid using positive spectral flux, without naming beats.

    STFT bin-center timestamps can be a few milliseconds before the perceived
    attack. Concentration checks synchronization and rejects the earlier
    64/128 BPM ambiguity; it does not establish the composer's meter/downbeat.
    """
    hop = 220
    _, times, spectrum = signal.stft(
        samples.mean(axis=1), rate, nperseg=2048,
        noverlap=2048 - hop, boundary=None,
    )
    flux = np.maximum(np.diff(np.abs(spectrum), axis=1), 0).sum(axis=0)
    times = times[1:]
    peaks, _ = signal.find_peaks(
        flux, prominence=np.percentile(flux, 80) * 0.35,
        distance=0.11 / (hop / rate),
    )
    weights, onsets = flux[peaks], times[peaks]
    candidates = []
    for bpm in (64, 96, 128):
        step = 60 / bpm / 4
        phasor = np.sum(weights * np.exp(2j * np.pi * onsets / step))
        error = np.abs((onsets + step / 2) % step - step / 2)
        candidates.append({
            "bpm": bpm,
            "subdivisionsPerBeat": 4,
            "circularConcentration": float(abs(phasor) / weights.sum()),
            "medianOnsetDistanceSeconds": float(np.median(error)),
            "onsetDistance90PercentileSeconds": float(np.percentile(error, 90)),
        })
    if candidates[1]["circularConcentration"] < 0.8:
        raise ValueError("Source no longer supports the expected 96 BPM timing grid")
    return {"detectedOnsetCount": len(peaks), "candidates": candidates}


def prepare(source_zip: Path, destination: Path = DEST) -> dict:
    archive_bytes = source_zip.read_bytes()
    originals = []
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for name in SOURCE_NAMES:
            originals.append(archive.read(name))
    decoded = [decode(data) for data in originals]
    formats = {(rate, samples.shape) for samples, rate in decoded}
    if formats != {(44100, (2646000, 2))}:
        raise ValueError(f"Unexpected source format or unsynchronized lengths: {formats}")
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "source").mkdir(exist_ok=True)
    tracks, prepared = [], []
    for number, (name, original, (samples, rate)) in enumerate(zip(SOURCE_NAMES, originals, decoded), 1):
        source_target = destination / "source" / name
        source_target.write_bytes(original)
        output_bytes = encode(repair_endpoint(samples, rate), rate)
        output, _ = decode(output_bytes)
        target_name = f"layer-{number}.wav"
        (destination / target_name).write_bytes(output_bytes)
        original_metrics = pcm_metrics(samples, rate)
        output_metrics = pcm_metrics(output, rate)
        track = {
            "id": f"layer-{number}", "file": target_name,
            "channels": 2, "sourceSampleRate": rate, "sampleRate": rate,
            "sampleWidthBytes": 2, "frames": len(output), "duration": len(output) / rate,
            "rms": output_metrics["rms"], "peak": output_metrics["peak"],
            "sha256": sha256(output_bytes), "bytes": len(output_bytes),
            "sourceFile": f"source/{name}", "sourceArchiveEntry": name,
            "sourceSha256": sha256(original), "sourceMetrics": original_metrics,
            "preparedMetrics": output_metrics, "beatGridEvidence": grid_evidence(samples, rate),
            "processing": {
                "method": "Raised-cosine endpoint midpoint correction; no trim, resample, phase shift or normalization",
                "windowSecondsPerEnd": REPAIR_SECONDS,
                "windowFramesPerEnd": round(rate * REPAIR_SECONDS),
                "changedFrames": int(np.count_nonzero(np.any(samples != output, axis=1))),
                "correctionRms": float(np.sqrt(np.mean((samples - output) ** 2))),
            },
        }
        tracks.append(track)
        prepared.append(output)
    combinations = []
    for size in range(1, 4):
        for indices in itertools.combinations(range(3), size):
            mixed = sum(prepared[i] for i in indices)
            combinations.append({
                "layers": [f"layer-{i + 1}" for i in indices],
                "gains": [1.0] * len(indices), **pcm_metrics(mixed, 44100),
            })
    if any(item["sampleClippingCount"] for item in combinations):
        raise ValueError("A unity-gain layer combination clips")
    metadata = {
        "version": 2, "id": "easy-going", "title": "Easy Going · 分层反馈",
        "source": "Purchased Blips Easy Going WAV layers",
        "sourceUrl": "https://blips.fm/easy-going",
        "sourceArchiveName": source_zip.name, "sourceArchiveSha256": sha256(archive_bytes),
        "duration": 60.0, "sourceDuration": 60.0, "cycleDuration": 60.0,
        "outputSampleRate": 44100, "loopMode": "native", "loopStartSeconds": 0.0,
        "loopEndSeconds": 60.0, "crossfadeSeconds": 0.0, "headStartSeconds": 0.0,
        "demoStartSeconds": 0.0, "feedbackProfile": "easy-going-continuous-v2",
        "beatGrid": {
            "bpm": 96, "offsetSeconds": 0.0, "beatsPerBar": 4,
            "source": "PCM onset-grid analysis; author BPM and meter unavailable",
            "verifiedMethod": "2048-frame positive spectral flux, 220-frame hop; all stems strongly align to 0.15625 s subdivisions and reject 64/128 BPM candidates",
            "quarterNoteSeconds": 0.625, "quantizationGroupSeconds": 2.5,
            "loopBeats": 96, "loopQuantizationGroups": 24,
            "meterVerified": False,
            "note": "Four-beat groups are an engineering trigger grid, not a claim that the author confirmed the meter or downbeat",
        },
        "tracks": tracks,
        "combinationChecks": combinations,
        "measurementNote": "PCM sample peaks and RMS only; not LUFS, true-peak certification or subjective listening approval",
    }
    (destination / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if sha256(source_zip.read_bytes()) != metadata["sourceArchiveSha256"]:
        raise RuntimeError("Source archive changed during preparation")
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_zip", type=Path, nargs="?", default=Path.home() / "Downloads" / "wav_Files.zip")
    parser.add_argument("--destination", type=Path, default=DEST)
    args = parser.parse_args()
    result = prepare(args.source_zip, args.destination)
    print(json.dumps({
        "destination": str(args.destination), "tracks": len(result["tracks"]),
        "duration": result["duration"], "beatGrid": result["beatGrid"],
        "combinationChecks": result["combinationChecks"],
    }, ensure_ascii=False, indent=2))
