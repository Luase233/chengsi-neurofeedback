"""Stable baselines and source-time result metrics, without any physical device."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.api import create_app
from backend.baselines import BaselineStore, calibration_trajectory, read_calibration_features
from backend.history import participant_history
from backend.metrics import peak_metrics
from backend.models import CommandRequest, SessionRequest
from backend.sessions import SessionService, idle_state
from backend.signal import LiveProcessor, fit_calibration, LEGACY_CALIBRATION_VERSION
from backend.storage import SessionStore, export_session


def feature(start, end, valid=True, attention=.3, meditation=1.5):
    config = LiveProcessor().config
    return {"window_start": start, "window_end": end, "valid": valid,
            "attention_raw": attention if valid else None, "meditation_raw": meditation if valid else None,
            "algorithm_version": config["algorithm_version"], "quality_version": config["quality_version"],
            "algorithm_profile": config["profile"], "smooth_alpha": config["smooth_alpha"],
            "reasons": [] if valid else ["artifact"], "bands": {}}


def calibrated_state(mode="live", participant="person-A"):
    state = idle_state()
    state.update(session_id=str(uuid4()), mode=mode, source="muse" if mode == "live" else mode,
                 participant_id=participant, created_at="2026-10-01T01:00:00Z", phase="completed")
    state["algorithm_config"] = LiveProcessor().config
    state["connection"].update(sample_rate=256.0, channels=["TP9", "AF7", "AF8", "TP10"], units="uV")
    state["device_metadata"] = {"board_id": 38, "provider": "brainflow-native-ble", "device_address": "00:11:22:33:44:55"}
    closed = [{**feature(10, 12, attention=.15), "stage": "calibrating_closed"}]
    opened = [{**feature(20, 22), "stage": "calibrating_open"}]
    params = fit_calibration(closed, opened, min_valid_windows=1)
    state["calibration"].update(valid=True, parameters=params, closed_seconds=60, open_seconds=60, version=1)
    return state, closed + opened


def prepare_training(tmp_path, target=120):
    service = SessionService(tmp_path)
    state, _ = calibrated_state()
    state["phase"] = "training"
    state["training"]["target_seconds"] = target
    service.state = state
    service._store = SessionStore(tmp_path, state["session_id"])
    service._training_started_mono = time.monotonic()
    service.state["training"]["started_at"] = "2026-10-02T00:00:00Z"
    return service


def test_default_training_is_two_valid_minutes_with_explicit_unlimited_compatibility():
    assert SessionRequest(mode="live").training_seconds == 120
    assert SessionRequest(mode="live", training_seconds=None).training_seconds is None


def test_two_minute_target_ends_at_exactly_120_accepted_seconds(tmp_path):
    service = prepare_training(tmp_path)
    # Non-integer first coverage makes the final contribution genuinely partial.
    service._feature(feature(100, 102.2), "training")
    for index in range(118):
        service._feature(feature(101.2 + index, 103.2 + index), "training")
    assert service.state["phase"] == "completed"
    assert service.state["summary"]["valid_seconds"] == 120
    assert service.state["summary"]["reason"] == "training_valid_target_reached"
    assert service.state["summary"]["score_trajectory"][-1]["valid_duration"] == pytest.approx(.8)
    assert service.state["summary"]["peak"]["total_peak_duration"] <= 120
    service._store.close()


def test_valid_coverage_exact_cap_and_invalid_overlap_not_backfilled(tmp_path):
    service = prepare_training(tmp_path, target=5)
    service._feature(feature(100, 102), "training")  # 2 s
    service._feature(feature(101, 103), "training")  # +1 s
    service._feature(feature(101, 103), "training")  # duplicate 0
    service._feature(feature(102, 104, False), "training")
    service._feature(feature(103, 105), "training")  # only 104..105 (+1)
    service._feature(feature(104, 106.7), "training")  # truncate 1.7 to +1
    assert service.state["phase"] == "completed"
    summary = service.state["summary"]
    assert summary["valid_seconds"] == 5
    assert sum(p.get("valid_duration", 0) for p in summary["score_trajectory"]) == 5
    assert summary["score_trajectory"][-1]["contribution_end"] == 6
    assert summary["score_trajectory"][3]["score"] is None
    assert summary["score_trajectory"][3]["valid"] is False
    assert summary["peak"]["total_peak_duration"] == 5
    assert summary["peak"]["longest_peak_duration"] == 3
    assert summary["peak"]["n_peaks"] == 3  # invalid gap plus the final >1.5 s source gap
    service._store.close()


def test_wall_time_includes_pause_and_disconnection_but_neither_adds_valid_time(tmp_path, monkeypatch):
    service = prepare_training(tmp_path)
    fake = [100.0]
    monkeypatch.setattr("backend.sessions.time.monotonic", lambda: fake[0])
    service._training_started_mono = service._clock_mono = 100
    service._feature(feature(1000, 1002), "training")
    fake[0] = 102
    service._advance_clock()
    service._phase("paused", "user_paused")
    fake[0] = 107
    service._advance_clock()
    assert service.snapshot()["training"]["wall_elapsed_seconds"] == 7
    assert service.state["training"]["elapsed_seconds"] == 2
    service._phase("disconnected", "data_timeout")
    fake[0] = 111
    service._advance_clock()
    service._phase("training", "user_resumed")
    service._feature(feature(1011, 1013), "training")
    fake[0] = 113
    service._advance_clock()
    service._finish("user_finished")
    summary = service.state["summary"]
    assert summary["wall_elapsed_seconds"] == 13
    assert summary["elapsed_seconds"] == 4
    assert summary["valid_seconds"] == 4
    assert summary["peak"]["n_peaks"] == 2
    assert summary["peak"]["longest_peak_duration"] == 2
    service._store.close()


def test_source_timestamps_not_polling_cadence_define_trajectory_and_peaks(tmp_path):
    service = prepare_training(tmp_path, target=None)
    for index in range(4):
        service._feature(feature(100 + index, 102 + index), "training")
    assert [p["time"] for p in service._trajectory] == [2, 3, 4, 5]
    assert peak_metrics(service._trajectory)["total_peak_duration"] == 5
    service._save()
    saved = json.loads((service._store.directory / "session.json").read_text(encoding="utf-8"))
    assert len(saved["training"]["score_trajectory"]) == 4
    service._store.close()


def test_peak_crossings_are_integrated_and_never_bridge_invalid_or_segment_boundaries():
    points = [
        {"time": 1, "score": 90, "valid": True, "valid_duration": 1, "contribution_start": 0, "contribution_end": 1, "segment": 0},
        {"time": 2, "score": 100, "valid": True, "valid_duration": 1, "contribution_start": 1, "contribution_end": 2, "segment": 0},
        {"time": 3, "score": None, "valid": False, "segment": 1},
        {"time": 4, "score": 100, "valid": True, "valid_duration": 1, "contribution_start": 3, "contribution_end": 4, "segment": 1},
    ]
    result = peak_metrics(points)
    assert result["peak_value"] == 100 and result["peak_time"] == 2
    assert result["threshold"] == 95
    assert result["total_peak_duration"] == 1.5 and result["longest_peak_duration"] == 1
    assert result["n_peaks"] == 2


def test_baseline_immutable_reuse_mode_participant_device_and_algorithm_isolation(tmp_path):
    store = BaselineStore(tmp_path)
    state, features = calibrated_state()
    first = store.save(state, features)
    assert first["visual_protocol"] == "fixed-three-layer-guide-v1"
    new_state = copy.deepcopy(state)
    new_state["session_id"] = str(uuid4())
    new_store = BaselineStore(tmp_path)
    assert new_store.find(new_state)["baseline_id"] == first["baseline_id"]
    for field, value in (("participant_id", "person-B"), ("mode", "synthetic"), ("mode", "replay")):
        changed = copy.deepcopy(new_state)
        changed[field] = value
        assert new_store.find(changed) is None
    changed = copy.deepcopy(new_state)
    changed["device_metadata"]["device_address"] = "11:22:33:44:55:66"
    assert new_store.find(changed) is None
    changed = copy.deepcopy(new_state)
    changed["algorithm_config"]["smooth_alpha"] = .2
    assert new_store.find(changed) is None
    second = store.save(new_state, features)
    assert second["baseline_id"] != first["baseline_id"]
    assert store.find(new_state)["baseline_id"] == second["baseline_id"]
    assert len(list(store.directory.rglob("*.json"))) == 2


def test_calibration_curve_includes_both_scores_and_invalid_gaps():
    state, features = calibrated_state()
    features.insert(1, {**feature(11, 13, False), "stage": "calibrating_closed"})
    trajectory = calibration_trajectory(features, state["calibration"]["parameters"])
    assert {p["stage"] for p in trajectory} == {"closed", "open"}
    assert trajectory[0]["score"] is not None and trajectory[0]["meditation"] is not None
    assert trajectory[1]["score"] is None and trajectory[1]["meditation"] is None
    assert trajectory[2]["segment"] != trajectory[0]["segment"]


def test_import_earliest_compatible_live_v2_preserves_source_files(tmp_path):
    state, features = calibrated_state()
    original_files = {}
    for index, mode in enumerate(("live", "live", "synthetic", "replay")):
        item = copy.deepcopy(state)
        item.update(session_id=str(uuid4()), mode=mode, created_at=f"2026-10-01T0{index}:00:00Z")
        store = SessionStore(tmp_path, item["session_id"])
        for point in features:
            store.feature(point, point["stage"], 0)
        store.save(item)
        store.close()
        original_files[item["session_id"]] = hashlib.sha256((store.directory / "session.json").read_bytes()).hexdigest()
    expected = next(iter(original_files))
    record = BaselineStore(tmp_path).find(state)
    assert record["source_session_id"] == expected and record["imported"]
    assert record["visual_protocol"] == "legacy-unspecified"
    assert record["trajectory"]
    for sid, checksum in original_files.items():
        assert hashlib.sha256((tmp_path / sid / "session.json").read_bytes()).hexdigest() == checksum


def test_import_never_uses_legacy_mapping_and_calibration_retry_uses_last_successful_round(tmp_path):
    state, features = calibrated_state()
    source = SessionStore(tmp_path, state["session_id"])
    for item in features:
        source.feature(item, item["stage"], 0)
    legacy = copy.deepcopy(state)
    legacy["calibration"]["parameters"]["version"] = LEGACY_CALIBRATION_VERSION
    source.save(legacy)
    source.close()
    request = copy.deepcopy(state)
    request["session_id"] = str(uuid4())
    assert BaselineStore(tmp_path).find(request) is None
    # Same calibration_version can contain retries. Select the stages belonging
    # to the final successful calibration event, not the failed first round.
    folder = tmp_path / str(uuid4())
    target = SessionStore(tmp_path, folder.name)
    for offset in (0, 100):
        for item in features:
            target.feature({**item, "window_start": item["window_start"] + offset,
                            "window_end": item["window_end"] + offset}, item["stage"], 0)
    target.close()
    events = [
        {"type": "phase", "reason": "closed_calibration_started", "at": "1970-01-01T00:00:09Z"},
        {"type": "phase", "reason": "open_calibration_started", "at": "1970-01-01T00:00:19Z"},
        {"type": "phase", "reason": "closed_calibration_started", "at": "1970-01-01T00:01:49Z"},
        {"type": "phase", "reason": "open_calibration_started", "at": "1970-01-01T00:01:59Z"},
        {"type": "calibration", "version": 1, "at": "1970-01-01T00:02:03Z"},
    ]
    (folder / "events.jsonl").write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    accepted = read_calibration_features(folder, state["calibration"])
    assert [row["window_start"] for row in accepted] == [110, 120]


def test_live_reuse_ready_still_requires_fresh_signal_and_snapshot_is_exported(tmp_path):
    state, features = calibrated_state()
    record = BaselineStore(tmp_path).save(state, features)
    class SilentMuse:
        def connect(self):
            return {**state["device_metadata"], "source": "muse", "sample_rate": 256.0,
                    "channels": ["TP9", "AF7", "AF8", "TP10"], "units": "uV"}
        def read(self): return None
        def disconnect(self): pass
    service = SessionService(tmp_path, provider_factory=lambda request: SilentMuse())
    with TestClient(create_app(tmp_path, service), base_url="http://127.0.0.1:8768") as http:
        connected = http.post("/api/sessions", json={"mode": "live", "participant_id": "person-A"}).json()
        assert connected["phase"] == "ready"
        assert connected["calibration"]["baseline_id"] == record["baseline_id"]
        sid = connected["session_id"]
        response = http.post(f"/api/sessions/{sid}/commands", json={"command": "start_training", "command_id": "early"})
        assert response.status_code == 409
        service._feature(feature(100, 102), "ready")
        assert http.post(f"/api/sessions/{sid}/commands", json={"command": "start_training", "command_id": "fresh"}).status_code == 200
        assert (tmp_path / sid / "baseline.json").exists()
        import io, zipfile
        http.post(f"/api/sessions/{sid}/commands", json={"command": "finish", "command_id": "finish"})
        archive = zipfile.ZipFile(io.BytesIO(http.get(f"/api/sessions/{sid}/export").content))
        assert json.loads(archive.read("baseline.json"))["baseline_id"] == record["baseline_id"]


def test_terminal_program_changes_rejected_and_history_uses_training_events(tmp_path):
    state, _ = calibrated_state()
    store = SessionStore(tmp_path, state["session_id"])
    state.update(program_id="beta-focus", summary={"mean_score": 50, "mean_meditation": 45, "valid_seconds": 20})
    store.event("session_created", request={"program_id": "efficiency"})
    store.event("phase", previous="ready", phase="training", reason="training_started")
    store.event("command", command="set_program", resulting_phase="training", program_id="re-life")
    store.event("command", command="set_program", resulting_phase="completed", program_id="beta-focus")
    store.save(state)
    store.close()
    history = participant_history(tmp_path, "person-A", "live")
    assert len(history["entries"]) == 1
    entry = history["entries"][0]
    assert entry["program_id"] == "efficiency"
    assert [p["program_id"] for p in entry["program_segments"]] == ["efficiency", "re-life"]
    assert entry["summary"]["peak"]["peak_value"] is None
    service = SessionService(tmp_path)
    service.state = state
    with TestClient(create_app(tmp_path, service), base_url="http://127.0.0.1:8768") as http:
        rejected = http.post(f"/api/sessions/{state['session_id']}/commands", json={"command": "set_program", "command_id": "change", "program_id": "deep-learning"})
        assert rejected.status_code == 409
        assert service.state["program_id"] == "beta-focus"
        assert http.get("/api/participant-history?participant_id=person-A&mode=synthetic").json()["entries"] == []
