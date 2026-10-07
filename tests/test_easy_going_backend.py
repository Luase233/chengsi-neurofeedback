"""Music-selection evidence must not alter a participant's EEG baseline."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.api import create_app
from backend.audio_profiles import CONTINUOUS_LAYER_PROGRAM_IDS, MUSIC_FEEDBACK_RECORDING_SCOPE, music_feedback_profile
from backend.baselines import BaselineStore, compatibility
from backend.models import CommandRequest, SessionRequest
from backend.sessions import SessionService, idle_state
from backend.signal import LiveProcessor, fit_calibration


METADATA = {"source": "synthetic", "sample_rate": 256.0,
            "channels": ["TP9", "AF7", "AF8", "TP10"], "units": "uV"}


class SilentProvider:
    def connect(self): return copy.deepcopy(METADATA)
    def read(self): return None
    def disconnect(self): pass


def seed_baseline(directory):
    state = idle_state()
    state.update(session_id=str(uuid4()), participant_id="audio-test", mode="synthetic", source="synthetic",
                 algorithm_config=LiveProcessor().config, device_metadata=copy.deepcopy(METADATA))
    state["connection"].update(METADATA)
    features = [
        {"window_start": 0, "window_end": 2, "stage": "calibrating_closed", "valid": True,
         "attention_raw": .15, "meditation_raw": 2},
        {"window_start": 10, "window_end": 12, "stage": "calibrating_open", "valid": True,
         "attention_raw": .3, "meditation_raw": 1.5},
    ]
    parameters = fit_calibration(features[:1], features[1:], min_valid_windows=1)
    state["calibration"].update(valid=True, parameters=parameters, version=1, closed_seconds=5, open_seconds=5)
    record = BaselineStore(directory).save(state, features)
    return state, record


@pytest.mark.parametrize("program_id", ["easy-going", "deep-learning", "efficiency", "re-life", "beta-focus",
                                      "clear-current-v01", "clear-current-v02", "clear-current-v03", "clear-current-v04", "neon-study-v01"])
def test_supported_program_requests(program_id):
    assert SessionRequest(mode="synthetic", program_id=program_id).program_id == program_id
    assert CommandRequest(command="set_program", command_id="choose", program_id=program_id).program_id == program_id


def test_unrecognised_program_still_rejected():
    with pytest.raises(ValidationError):
        SessionRequest(mode="synthetic", program_id="unknown")
    with pytest.raises(ValidationError):
        CommandRequest(command="set_program", command_id="choose", program_id="unknown")


@pytest.mark.parametrize("program_id", sorted(CONTINUOUS_LAYER_PROGRAM_IDS))
def test_layered_program_create_reuses_baseline_and_records_configuration_not_audio_output(tmp_path, program_id):
    original, baseline = seed_baseline(tmp_path)
    baseline_path = next((tmp_path / "_baselines").rglob("*.json"))
    baseline_hash = hashlib.sha256(baseline_path.read_bytes()).digest()
    service = SessionService(tmp_path, provider_factory=lambda request: SilentProvider())
    with TestClient(create_app(tmp_path, service), base_url="http://127.0.0.1:8768") as http:
        response = http.post("/api/sessions", json={"mode": "synthetic", "participant_id": "audio-test",
                                                   "program_id": program_id, "calibration_seconds": 5})
        assert response.status_code == 200, response.text
        state = response.json()
        sid = state["session_id"]
        assert state["program_id"] == program_id
        assert state["phase"] == "ready" and state["calibration"]["reused"]
        assert state["calibration"]["baseline_id"] == baseline["baseline_id"]
        assert state["algorithm_config"] == original["algorithm_config"]
        assert compatibility(state) == compatibility(original)
        assert state["music_feedback_profile"] == {
            "version": "easy-going-continuous-v2", "fullLevelScores": [40, 55, 70],
            "baseFloor": 0.55, "transitionSeconds": 2.5, "trackGainCaps": [0.47, 0.47, 0.47],
        }
        assert state["music_feedback_recording_scope"] == MUSIC_FEEDBACK_RECORDING_SCOPE
        assert not {"audible_state", "actual_layers", "playback_events"} & state.keys()
        events = [json.loads(line) for line in (tmp_path / sid / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        created = next(event for event in events if event["type"] == "session_created")
        assert created["request"]["program_id"] == program_id
        assert created["at"] and created["music_feedback_profile"] == state["music_feedback_profile"]
        assert hashlib.sha256(baseline_path.read_bytes()).digest() == baseline_hash


def test_switches_snapshot_profiles_and_keep_selection_times_and_terminal_history(tmp_path):
    original, baseline = seed_baseline(tmp_path)
    service = SessionService(tmp_path, provider_factory=lambda request: SilentProvider())
    with TestClient(create_app(tmp_path, service), base_url="http://127.0.0.1:8768") as http:
        state = http.post("/api/sessions", json={"mode": "synthetic", "participant_id": "audio-test",
                                               "program_id": "easy-going", "calibration_seconds": 5}).json()
        sid = state["session_id"]
        service.state["quality"]["valid"] = True
        service._last_feedback_mono = time.monotonic()
        started = http.post(f"/api/sessions/{sid}/commands", json={"command": "start_training", "command_id": "start"})
        assert started.status_code == 200, started.text
        choice = {"command": "set_program", "command_id": "legacy", "program_id": "re-life"}
        legacy = http.post(f"/api/sessions/{sid}/commands", json=choice)
        assert legacy.status_code == 200 and legacy.json()["music_feedback_profile"] is None
        assert http.post(f"/api/sessions/{sid}/commands", json=choice).status_code == 200
        choice = {"command": "set_program", "command_id": "easy", "program_id": "easy-going"}
        returned = http.post(f"/api/sessions/{sid}/commands", json=choice).json()
        assert returned["calibration"]["baseline_id"] == baseline["baseline_id"]
        assert compatibility(returned) == compatibility(original)
        finished = http.post(f"/api/sessions/{sid}/commands", json={"command": "finish", "command_id": "end"}).json()
        summary = finished["summary"]
        segments = summary["program_segments"]
        assert [segment["program_id"] for segment in segments] == ["easy-going", "re-life", "easy-going"]
        assert all(segment["at"] and segment["phase"] == "training" for segment in segments)
        assert segments[0]["wall_elapsed_seconds"] == 0
        assert segments[2]["wall_elapsed_seconds"] >= segments[1]["wall_elapsed_seconds"]
        assert segments[0]["music_feedback_profile"] == segments[2]["music_feedback_profile"] == summary["music_feedback_profile"]
        assert segments[1]["music_feedback_profile"] is None
        assert all(segment["music_feedback_recording_scope"] == MUSIC_FEEDBACK_RECORDING_SCOPE for segment in segments)
        saved = json.loads((tmp_path / sid / "session.json").read_text(encoding="utf-8"))
        assert saved["training"]["program_segments"] == segments
        assert saved["summary"]["music_feedback_profile"] == music_feedback_profile("easy-going")
        # Changing a returned mapping cannot retroactively alter the recorded first segment.
        service.state["music_feedback_profile"]["fullLevelScores"][0] = 99
        assert service._program_segments[0]["music_feedback_profile"]["fullLevelScores"][0] == 40
        assert music_feedback_profile("easy-going")["fullLevelScores"][0] == 40
        before = (tmp_path / sid / "session.json").read_bytes()
        rejected = http.post(f"/api/sessions/{sid}/commands", json={"command": "set_program", "command_id": "late", "program_id": "beta-focus"})
        assert rejected.status_code == 409
        assert (tmp_path / sid / "session.json").read_bytes() == before
        events = [json.loads(line) for line in (tmp_path / sid / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        switches = [event for event in events if event.get("command") == "set_program"]
        assert len(switches) == 2
        assert switches[0]["music_feedback_profile"] is None
        assert switches[1]["music_feedback_profile"] == music_feedback_profile("easy-going")


def test_clear_current_versions_are_saved_as_distinct_program_segments(tmp_path):
    _, baseline = seed_baseline(tmp_path)
    versions = [f"clear-current-v{version:02d}" for version in range(1, 5)]
    service = SessionService(tmp_path, provider_factory=lambda request: SilentProvider())
    with TestClient(create_app(tmp_path, service), base_url="http://127.0.0.1:8768") as http:
        created = http.post("/api/sessions", json={"mode": "synthetic", "participant_id": "audio-test",
                                                   "program_id": "easy-going", "calibration_seconds": 5})
        assert created.status_code == 200, created.text
        sid = created.json()["session_id"]
        service.state["quality"]["valid"] = True
        service._last_feedback_mono = time.monotonic()
        started = http.post(f"/api/sessions/{sid}/commands", json={"command": "start_training", "command_id": "start"})
        assert started.status_code == 200, started.text
        for program_id in versions:
            selected = http.post(f"/api/sessions/{sid}/commands", json={
                "command": "set_program", "command_id": program_id, "program_id": program_id,
            })
            assert selected.status_code == 200, selected.text
            assert selected.json()["program_id"] == program_id
            assert selected.json()["calibration"]["baseline_id"] == baseline["baseline_id"]
        finished = http.post(f"/api/sessions/{sid}/commands", json={"command": "finish", "command_id": "end"})
        assert finished.status_code == 200, finished.text
        segments = finished.json()["summary"]["program_segments"]
        assert [segment["program_id"] for segment in segments] == ["easy-going", *versions]
        assert all(segment["music_feedback_profile"] == music_feedback_profile("easy-going") for segment in segments)
        assert all(segment["music_feedback_recording_scope"] == MUSIC_FEEDBACK_RECORDING_SCOPE for segment in segments)
        saved = json.loads((tmp_path / sid / "session.json").read_text(encoding="utf-8"))
        assert saved["program_id"] == versions[-1]
        assert saved["training"]["program_segments"] == saved["summary"]["program_segments"] == segments
        events = [json.loads(line) for line in (tmp_path / sid / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        switches = [event for event in events if event.get("command") == "set_program"]
        assert [event["program_id"] for event in switches] == versions
