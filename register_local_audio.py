"""Register already-prepared optional local audio, without publishing its files."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "assets/audio/catalog.json"
METADATA = {"easy-going": "easy-going/metadata.json", "deep-learning": "metadata.json",
            "efficiency": "efficiency/metadata.json", "re-life": "re-life/metadata.json",
            "beta-focus": "beta-focus/metadata.json"}

def register(program_id):
    relative = METADATA[program_id]
    source = ROOT / "assets/audio" / relative
    metadata = json.loads(source.read_text(encoding="utf-8-sig"))
    tracks = metadata.get("tracks", [])
    expected = {"layer-1", "layer-2", "layer-3"} if program_id == "easy-going" else {"other", "drums", "bass"}
    if len(tracks) != 3 or {track["id"] for track in tracks} != expected:
        raise ValueError("Metadata must contain the three expected tracks")
    for track in tracks:
        candidate = (source.parent / track["file"]).resolve()
        candidate.relative_to(source.parent.resolve())
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
    item = {"id": program_id, "title": metadata.get("title", program_id), "metadata": relative}
    if program_id == "easy-going":
        if metadata.get("loopMode") != "native" or metadata.get("feedbackProfile") != "easy-going-continuous-v2":
            raise ValueError("Run prepare_easy_going.py before registering Easy Going")
        item.update(feedbackProfile=metadata["feedbackProfile"], beatGrid=metadata["beatGrid"])
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    catalog["programs"] = [entry for entry in catalog["programs"] if entry["id"] != program_id] + [item]
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "audio-programs.js").write_text("// Runtime catalog. Optional licensed programs are registered locally.\nwindow.CHENGSI_AUDIO_PROGRAMS = " + json.dumps(catalog["programs"], ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")
    print("Registered local audio: " + program_id + ". Refresh the browser.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("program_id", choices=METADATA)
    register(parser.parse_args().program_id)
