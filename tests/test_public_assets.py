"""Publication checks: the clean checkout must serve its catalog and media."""
import json
from pathlib import Path
from fastapi.testclient import TestClient
from backend.api import create_app

ROOT = Path(__file__).resolve().parents[1]

def test_published_pages_and_catalog_are_complete(tmp_path):
    catalog = json.loads((ROOT / "assets/audio/catalog.json").read_text(encoding="utf-8"))
    assert catalog["defaultProgram"] == "clear-current-v04"
    assert len(catalog["programs"]) == 5
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8768") as client:
        for page in ["/", "/operator.html", "/participant.html"]:
            response = client.get(page)
            assert response.status_code == 200
            assert response.text.index('src="audio-programs.js"') < response.text.index('src="audio-engine.js"')
        assert client.get("/audio-programs.js").status_code == 200
        for program in catalog["programs"]:
            url = "/assets/audio/" + program["metadata"]
            metadata = client.get(url).json()
            assert len(metadata["tracks"]) == 3
            for track in metadata["tracks"]:
                assert client.get(url.rsplit("/", 1)[0] + "/" + track["file"]).status_code == 200
        for image in ["training-lake-trees.jpg", "training-lake-house.jpg"]:
            assert client.get("/assets/visuals/" + image).status_code == 200
        for cue in ["closed-start", "closed-complete", "open-start", "open-complete", "training-start", "training-wait", "training-round-complete", "training-day-complete"]:
            assert client.get("/assets/audio/instructions/" + cue + ".wav").status_code == 200
        for path in ["/runtime/sessions", "/.env", "/server.py"]:
            assert client.get(path).status_code == 404
