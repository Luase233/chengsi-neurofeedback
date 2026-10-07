"""Protocol and evidence tests use explicit synthetic EEG, never an implicit live fallback."""
import csv
import io
import json
from pathlib import Path
import sys
import time
from uuid import uuid4
import zipfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.api import create_app
from backend.providers import SyntheticProvider
from backend.sessions import SessionService
from backend.storage import SessionStore, export_session


class AcceleratedSynthetic(SyntheticProvider):
    """Advance source sample time for protocol tests while emitting genuine waveforms."""
    def read(self):
        self._start -= .15
        return super().read()


@pytest.fixture
def client(tmp_path):
    service = SessionService(tmp_path, provider_factory=lambda request: AcceleratedSynthetic(seed=42))
    with TestClient(create_app(tmp_path, service=service), base_url="http://127.0.0.1:8768") as instance:
        yield instance, service, tmp_path


def create(client):
    response = client.post("/api/sessions", json={"mode": "synthetic", "participant_id": "test-volunteer", "calibration_seconds": 5})
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["phase"] == "connected", state
    assert state["source"] == "synthetic"
    return state["session_id"]


def command(client, session_id, action, command_id=None, **extra):
    response = client.post(f"/api/sessions/{session_id}/commands", json={"command": action, "command_id": command_id or str(uuid4()), **extra})
    assert response.status_code == 200, response.text
    return response.json()


def wait_state(client, predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get("/api/state").json()
        if predicate(state):
            return state
        assert state["phase"] not in {"error", "disconnected"}, state
        time.sleep(.04)
    raise AssertionError(f"State did not settle: {state}")


def calibrate(client, session_id):
    wait_state(client, lambda state: state["quality"]["valid"] and state["feedback"]["age_ms"] is not None and state["feedback"]["age_ms"] < 2500)
    command(client, session_id, "calibrate_closed")
    closed = wait_state(client, lambda state: state["phase"] == "closed_complete")
    assert closed["calibration"]["closed_seconds"] == 5
    assert closed["feedback"]["score"] is None
    wait_state(client, lambda state: state["quality"]["valid"] and state["feedback"]["age_ms"] is not None and state["feedback"]["age_ms"] < 2500)
    command(client, session_id, "calibrate_open")
    ready = wait_state(client, lambda state: state["phase"] == "ready")
    assert ready["calibration"]["valid"]
    assert ready["feedback"]["score"] is None
    return wait_state(client, lambda state: state["phase"] == "ready" and state["quality"]["valid"])


def test_health_origin_and_private_files(client):
    http, _, _ = client
    assert http.get("/api/health").json()["service"] == "chengsi-backend"
    assert http.get("/api/state", headers={"Origin": "https://example.com"}).status_code == 403
    assert http.get("/api/state", headers={"Host": "attacker.example"}).status_code == 403
    for path in ["/server.py", "/backend/sessions.py", "/runtime/sessions", "/.venv/pyvenv.cfg", "/assets/%2e%2e/backend/sessions.py"]:
        assert http.get(path).status_code == 404
    assert http.get("/audio-engine.js").status_code == 200
    assert http.get("/feedback-policy.js").status_code == 200


def test_model_rejects_short_live_and_missing_replay(client):
    http, _, _ = client
    assert http.post("/api/sessions", json={"mode": "live", "calibration_seconds": 5}).status_code == 422
    assert http.post("/api/sessions", json={"mode": "replay"}).status_code == 422


def test_flow_idempotency_pause_reconnect_and_export(client):
    http, service, directory = client
    sid = create(http)
    assert http.post("/api/sessions", json={"mode": "synthetic"}).status_code == 409
    assert http.post(f"/api/sessions/{sid}/commands", json={"command": "start_training", "command_id": "too-early"}).status_code == 409
    ready = calibrate(http, sid)
    calibration = ready["calibration"]["parameters"]
    assert calibration["version"] == "eyes-open-robust-log-v2.0"
    assert ready["algorithm_config"]["calibration_method"] == calibration["version"]
    command(http, sid, "start_training", "train-once")
    training = wait_state(http, lambda state: state["feedback"]["valid"])
    assert 0 <= training["feedback"]["score"] <= 100
    assert training["feedback"]["age_ms"] <= 2500
    # Repeating a command must not restart the phase or its elapsed time.
    duplicate = command(http, sid, "start_training", "train-once")
    assert duplicate["training"]["elapsed_seconds"] >= training["training"]["elapsed_seconds"]
    paused = command(http, sid, "pause")
    assert paused["phase"] == "paused" and paused["feedback"]["score"] is None
    elapsed = paused["training"]["elapsed_seconds"]
    time.sleep(.25)
    assert http.get("/api/state").json()["training"]["elapsed_seconds"] == elapsed
    wait_state(http, lambda state: state["quality"]["valid"])
    command(http, sid, "resume")
    wait_state(http, lambda state: state["feedback"]["valid"])
    disconnected = command(http, sid, "inject_dropout")
    assert disconnected["phase"] == "disconnected" and disconnected["feedback"]["score"] is None
    reconnected = command(http, sid, "reconnect")
    assert reconnected["phase"] == "paused"
    assert reconnected["calibration"]["parameters"] == calibration
    assert reconnected["feedback"]["score"] is None
    immediate_resume = http.post(f"/api/sessions/{sid}/commands", json={"command": "resume", "command_id": "too-fresh"})
    assert immediate_resume.status_code == 409
    wait_state(http, lambda state: state["quality"]["valid"])
    command(http, sid, "resume")
    wait_state(http, lambda state: state["feedback"]["valid"])
    ended = command(http, sid, "finish")
    assert ended["phase"] == "completed"
    assert ended["feedback"]["score"] is None
    assert ended["summary"]["mean_score"] is not None
    assert "不代表干预疗效" in ended["summary"]["label"]
    response = http.get(f"/api/sessions/{sid}/export?edf=true")
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert {"raw.csv", "features.csv", "events.jsonl", "session.json"} <= set(archive.namelist())
    raw = list(csv.DictReader(io.StringIO(archive.read("raw.csv").decode())))
    assert {"calibrating_closed", "calibrating_open", "training", "paused"} <= {row["phase"] for row in raw}
    features = list(csv.DictReader(io.StringIO(archive.read("features.csv").decode())))
    assert features and all(not row["score"] for row in features if row["phase"] != "training")
    assert {row["calibration_method"] for row in features} == {calibration["version"]}
    metadata = json.loads(archive.read("session.json"))
    assert metadata["mode"] == "synthetic" and metadata["provenance"]["is_synthetic"]
    assert metadata["algorithm_version"]
    assert (directory / sid / "events.jsonl").stat().st_size > 0


def test_websocket_resync_does_not_create_session(client):
    http, _, _ = client
    sid = create(http)
    with http.websocket_connect("ws://127.0.0.1:8768/ws/live", headers={"Origin": "http://127.0.0.1:8768"}) as ws:
        first = ws.receive_json()
        second = ws.receive_json()
        assert first["session_id"] == sid and second["seq"] > first["seq"]
    with http.websocket_connect("ws://127.0.0.1:8768/ws/live") as ws:
        assert ws.receive_json()["session_id"] == sid
    assert len(http.get("/api/sessions").json()["sessions"]) == 1


def test_failed_live_connection_does_not_fallback(tmp_path):
    class FailingDevice:
        def set_stage(self, _stage): pass
        def connect(self): raise RuntimeError("No Muse hardware present")
        def disconnect(self): pass
    service = SessionService(tmp_path, provider_factory=lambda request: FailingDevice())
    with TestClient(create_app(tmp_path, service), base_url="http://127.0.0.1:8768") as http:
        state = http.post("/api/sessions", json={"mode": "live"}).json()
        assert state["mode"] == "live" and state["phase"] == "error"
        assert state["source"] != "synthetic" and state["feedback"]["score"] is None
        assert "No Muse hardware" in state["error"]


def test_timeout_invalidates_and_requires_explicit_reconnect(tmp_path):
    class SilentDevice:
        eof = False
        def set_stage(self, _stage): pass
        def connect(self): return {"source": "muse", "sample_rate": 256, "channels": ["TP9", "AF7", "AF8", "TP10"]}
        def read(self): return None
        def disconnect(self): pass
    service = SessionService(tmp_path, provider_factory=lambda request: SilentDevice())
    with TestClient(create_app(tmp_path, service), base_url="http://127.0.0.1:8768") as http:
        state = http.post("/api/sessions", json={"mode": "live"}).json()
        assert state["phase"] == "connected"
        started = time.monotonic()
        while time.monotonic() - started < 3:
            state = http.get("/api/state").json()
            if state["phase"] == "disconnected": break
            time.sleep(.04)
        assert state["phase"] == "disconnected"
        assert time.monotonic() - started < 2.8
        assert state["feedback"]["score"] is None
        response = http.post(f"/api/sessions/{state['session_id']}/commands", json={"command": "inject_dropout", "command_id": "illegal-live-dropout"})
        assert response.status_code == 409


def test_valid_progress_uses_nonoverlapping_windows(tmp_path):
    service = SessionService(tmp_path)
    service._store = SessionStore(tmp_path, str(uuid4()))
    service.state["phase"] = "calibrating_closed"
    def feature(start, end, valid=True):
        return {"window_start": start, "window_end": end, "valid": valid, "reasons": [] if valid else ["artifact"], "attention_raw": .2, "meditation_raw": 2, "bands": {"theta": 1, "alpha": 2, "beta": 1}}
    service._feature(feature(10, 12), "calibrating_closed")
    service._feature(feature(11, 13), "calibrating_closed")
    service._feature(feature(11, 13), "calibrating_closed")
    service._feature(feature(12, 14, False), "calibrating_closed")
    service._feature(feature(15, 17), "calibrating_closed")
    assert service.state["calibration"]["closed_seconds"] == 5
    assert service.state["feedback"]["score"] is None
    service._feature(feature(None, None, False), "calibrating_closed")
    assert service.state["calibration"]["closed_seconds"] == 5
    service._store.close()


def test_active_export_rejected_and_interrupted_history_identified(client):
    http, _, directory = client
    sid = create(http)
    assert http.get(f"/api/sessions/{sid}/export").status_code == 409
    historical = str(uuid4())
    folder = directory / historical
    folder.mkdir()
    (folder / "session.json").write_text(json.dumps({"session_id": historical, "phase": "training", "mode": "live", "feedback": {"score": 80}, "quality": {"valid": True}}), encoding="utf-8")
    state = http.get(f"/api/sessions/{historical}").json()
    assert state["interrupted"] and state["phase_before_interrupt"] == "training"
    assert state["feedback"]["score"] is None
    archive = zipfile.ZipFile(io.BytesIO(http.get(f"/api/sessions/{historical}/export").content))
    assert json.loads(archive.read("session.json"))["interrupted"]


def test_edf_splits_gaps_and_never_pads_partial_seconds(tmp_path):
    import numpy as np
    import pyedflib
    sid = str(uuid4())
    store = SessionStore(tmp_path, sid)
    channels = ["TP9", "AF7", "AF8", "TP10"]
    store.configure_raw(channels)
    first = 1700000000 + np.arange(384) / 256
    second = 1700000005 + np.arange(320) / 256
    wave = lambda count: np.tile(np.sin(np.arange(count)[:, None] * .1) * 10, (1, 4))
    store.raw(first, wave(len(first)), "training")
    store.raw(second, wave(len(second)), "paused")
    store.save({"session_id": sid, "phase": "completed", "connection": {"sample_rate": 256, "channels": channels}})
    store.close()
    archive = zipfile.ZipFile(io.BytesIO(export_session(tmp_path / sid, include_edf=True)))
    manifest = json.loads(archive.read("edf-segments.json"))
    assert len(manifest["segments"]) == 2
    assert [item["sample_count"] for item in manifest["segments"]] == [384, 320]
    assert manifest["omitted_tails"] == []
    for item in manifest["segments"]:
        path = tmp_path / item["file"]
        path.write_bytes(archive.read(item["file"]))
        with pyedflib.EdfReader(str(path)) as reader:
            assert reader.getNSamples().tolist() == [item["sample_count"]] * 4
            assert reader.getSampleFrequencies().tolist() == [256] * 4
            assert reader.readAnnotations()[2][0] in {"training", "paused"}


def test_recorded_training_replay_uses_saved_calibration_and_freezes_when_paused(tmp_path):
    # Use the real paced synthetic provider and real storage, then the actual replay provider.
    service = SessionService(tmp_path)
    with TestClient(create_app(tmp_path, service), base_url="http://127.0.0.1:8768") as http:
        sid = create(http)
        original = calibrate(http, sid)
        command(http, sid, "start_training")
        wait_state(http, lambda state: state["feedback"]["valid"], timeout=8)
        time.sleep(3.3)
        source = command(http, sid, "finish")
        assert source["summary"]["valid_seconds"] > 0
        replay = http.post("/api/sessions", json={"mode": "replay", "replay_session_id": sid}).json()
        assert replay["phase"] == "ready", replay
        assert replay["calibration"]["parameters"] == original["calibration"]["parameters"]
        assert replay["algorithm_config"]["calibration_method"] == original["calibration"]["parameters"]["version"]
        assert replay["algorithm_config"]["smooth_alpha"] == source["algorithm_config"]["smooth_alpha"]
        assert replay["source"] == "replay" and replay["provenance"]["original_source"] == "synthetic"
        replay_id = replay["session_id"]
        time.sleep(.3)
        assert service._provider._index == 0, "a ready recording must not be consumed before start"
        command(http, replay_id, "start_training")
        scored = wait_state(http, lambda state: state["feedback"]["valid"], timeout=8)
        assert scored["feedback"]["age_ms"] < 2500
        command(http, replay_id, "pause")
        cursor = service._provider._index
        time.sleep(.3)
        assert service._provider._index == cursor, "pause must freeze the playback cursor"
        command(http, replay_id, "resume")
        ended = wait_state(http, lambda state: state["phase"] == "completed", timeout=10)
        assert ended["summary"]["reason"] == "replay_ended"
        assert ended["feedback"]["score"] is None


def muse_packet_export(tmp_path, missing_packet=False, long_outage=False, reverse_clock=False):
    """BrainFlow-style 12-sample arrival interpolation with visible normal BLE jitter."""
    import numpy as np
    sid = str(uuid4())
    store = SessionStore(tmp_path, sid)
    channels = ["TP9", "AF7", "AF8", "TP10"]
    store.configure_raw(channels)
    stamps, numbers = [], []
    arrival = 1700000000.0
    for packet in range(120):
        interval = 12 / 256 + [.030, -.024, .018, -.024][packet % 4]
        if missing_packet and packet == 60:
            arrival += 12 / 256
        if long_outage and packet == 90:
            arrival += .5
        for sample in range(12):
            stamps.append(arrival + (sample + 1) * interval / 12)
            numbers.append((65520 + packet + (1 if missing_packet and packet >= 60 else 0)) % 65536)
        arrival += interval
    stamps = np.asarray(stamps)
    if reverse_clock:
        stamps[720:] -= .10
    samples = np.tile(np.sin(np.arange(len(stamps))[:, None] * .1) * 10, (1, 4))
    store.raw(stamps, samples, "training", package_numbers=numbers)
    store.save({"session_id": sid, "phase": "completed", "mode": "live", "source": "muse",
                "connection": {"sample_rate": 256, "channels": channels},
                "algorithm_config": {"quality": {"maximum_sdk_timestamp_step_seconds": .25}}})
    store.close()
    archive = zipfile.ZipFile(io.BytesIO(export_session(tmp_path / sid, include_edf=True)))
    assert "edf-unavailable.txt" not in archive.namelist()
    return archive, json.loads(archive.read("edf-segments.json")), stamps


def test_muse_edf_accepts_packet_contiguous_arrival_jitter_and_wrap(tmp_path):
    archive, manifest, original = muse_packet_export(tmp_path)
    assert manifest["timestamp_basis"] == "nominal_sample_clock_from_muse_packet_continuity"
    assert manifest["segment_boundaries"] == []
    assert len(manifest["segments"]) == 1
    segment = manifest["segments"][0]
    assert segment["sample_count"] == 1440
    assert segment["max_abs_arrival_clock_deviation_ms"] > 10
    assert segment["sample_mapping"]["signal_samples_resampled"] is False
    assert segment["sample_mapping"]["raw_csv_first_data_row_zero_based"] == 0
    assert segment["sample_mapping"]["raw_csv_last_data_row_zero_based"] == 1439
    assert len(list(csv.DictReader(io.StringIO(archive.read("raw.csv").decode())))) == len(original)
    assert "EDF does not reproduce each arrival timestamp" in archive.read("edf-notes.txt").decode()


def test_muse_edf_splits_real_packet_loss_long_outage_and_clock_reversal(tmp_path):
    _, manifest, _ = muse_packet_export(tmp_path, missing_packet=True, long_outage=True)
    assert len(manifest["segments"]) == 3
    boundaries = {item["raw_data_row_zero_based"]: item["reasons"] for item in manifest["segment_boundaries"]}
    assert "muse_packet_sequence_or_12_sample_count" in boundaries[720]
    assert "long_arrival_clock_outage" in boundaries[1080]
    _, reversed_manifest, _ = muse_packet_export(tmp_path, reverse_clock=True)
    assert len(reversed_manifest["segments"]) == 2
    assert reversed_manifest["segment_boundaries"][0]["reasons"] == ["non_monotonic_arrival_timestamp"]
