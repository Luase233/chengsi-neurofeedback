"""EDF evidence tests: no resampling, no padding, no false calibration rounds."""
import csv
import io
import json
from pathlib import Path
from uuid import uuid4
import zipfile

import numpy as np
import pyedflib
import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.baselines import compatibility
from backend.storage import EDFExportError, SessionStore, export_session


CHANNELS = ["TP9", "AF7", "AF8", "TP10"]
EDF_CHANNELS = ["AF7", "AF8", "TP9", "TP10"]


def record(root, phases, rate=256, participant="subject-test"):
    sid = str(uuid4())
    store = SessionStore(root, sid)
    store.configure_raw(CHANNELS)
    all_values, offset = [], 0
    for phase, count in phases:
        index = np.arange(offset, offset + count)
        # Different channel values expose accidental positional channel ordering.
        values = np.column_stack([np.sin(index * .13 + channel) * 80 + channel * 20 for channel in range(4)])
        if count:
            values[-1, 1] = 1234.25  # Preserve a large raw artifact, regardless of QC.
        store.raw(1700000000 + index / rate, values, phase)
        all_values.append(values)
        offset += count
    state = {"session_id": sid, "phase": "completed", "participant_id": participant,
             "mode": "live", "source": "muse", "algorithm_config": {"quality": {}},
             "device_metadata": {"provider": "brainflow", "board_id": 38, "device_address": "aa:bb:cc:dd:ee:ff"},
             "connection": {"sample_rate": rate, "channels": CHANNELS, "units": "uV"}}
    store.save(state)
    store.close()
    return root / sid, state, np.vstack(all_values) if all_values else np.empty((0, 4))


def archive_for(directory, root=None):
    archive = zipfile.ZipFile(io.BytesIO(export_session(directory, include_edf=True, sessions_root=root)))
    return archive, json.loads(archive.read("edf-segments.json"))


@pytest.mark.parametrize("rate,count,expected,tail", [(256, 263, 256, 7), (256, 260, 256, 4), (256, 264, 264, 0), (250, 263, 263, 0)])
def test_round_trip_exact_rate_raw_channels_units_and_quantization(tmp_path, rate, count, expected, tail):
    directory, _, original = record(tmp_path, [("training", count)], rate)
    archive, manifest = archive_for(directory)
    assert manifest["channel_order"] == EDF_CHANNELS
    assert manifest["quality_filter_applied"] is False
    assert manifest["signal_filter_applied"] is False
    assert manifest["samples_filled"] == 0
    assert manifest["raw_sample_count"] == count
    assert manifest["exported_sample_count"] == expected
    assert sum(item["sample_count"] for item in manifest["omitted_tails"]) == tail
    part = manifest["segments"][0]
    path = tmp_path / part["file"]
    path.write_bytes(archive.read(part["file"]))
    with pyedflib.EdfReader(str(path)) as reader:
        assert reader.getSignalLabels() == EDF_CHANNELS
        assert reader.getSampleFrequencies().tolist() == [rate] * 4
        assert reader.getNSamples().tolist() == [expected] * 4
        for channel, label in enumerate(EDF_CHANNELS):
            assert reader.getPhysicalDimension(channel) == "uV"
            error = np.max(np.abs(reader.readSignal(channel) - original[:expected, CHANNELS.index(label)]))
            assert error <= part["physical_quantization_step_uv"][label] + 1e-5
    assert archive.read("raw.csv") == (directory / "raw.csv").read_bytes()


def test_tasks_are_separate_and_all_artifacts_preserved(tmp_path):
    directory, state, _ = record(tmp_path, [("calibrating_closed", 64), ("calibrating_open", 64), ("training", 64)])
    # Features are explicitly all invalid: raw EEG must still be exportable.
    (directory / "features.csv").write_text("phase,valid\ntraining,False\n", encoding="utf-8")
    archive, manifest = archive_for(directory)
    assert [part["phase"] for part in manifest["segments"]] == ["calibrating_closed", "calibrating_open", "training"]
    assert [part["sample_count"] for part in manifest["segments"]] == [64] * 3
    assert manifest["omitted_tails"] == []
    for part in manifest["segments"]:
        assert part["file"].startswith(f"subject-test_{state['session_id'][:8]}_")
        assert part["task"] in part["file"]
        assert not any(marker in part["file"] for marker in ("week", "best"))


def baseline_pair(tmp_path):
    phases = [("calibrating_closed", 32), ("calibrating_open", 32), ("training", 16),
              ("calibrating_closed", 24), ("calibrating_open", 24), ("training", 16)]
    source, source_state, _ = record(tmp_path, phases)
    destination, state, _ = record(tmp_path, [("training", 64)])
    parameters = {"version": "fixture-v2", "attention": {"center": .3}}
    calibration = {"valid": True, "version": 2, "parameters": parameters}
    source_state["calibration"] = calibration
    baseline = {"baseline_id": str(uuid4()), "source_session_id": source_state["session_id"],
                "participant_id": state["participant_id"], "mode": "live", "calibration": calibration,
                "compatibility": compatibility(source_state)}
    state["calibration"] = {**calibration, "reused": True, "baseline_id": baseline["baseline_id"],
                            "source_session_id": source_state["session_id"]}
    (source / "session.json").write_text(json.dumps(source_state), encoding="utf-8")
    (destination / "session.json").write_text(json.dumps(state), encoding="utf-8")
    (destination / "baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
    events = []
    def phase(name, reason, index):
        events.append({"type": "phase", "phase": name, "reason": reason, "sample_index": index})
    phase("calibrating_closed", "closed_calibration_started", 0)
    phase("closed_complete", "closed_calibration_complete", 32)
    phase("calibrating_open", "open_calibration_started", 32)
    phase("ready", "calibration_passed", 64)
    events.append({"type": "calibration", "version": 1, "parameters": {"old": 1}})
    phase("training", "training_started", 64)
    phase("calibrating_closed", "closed_calibration_started", 80)
    phase("closed_complete", "closed_calibration_complete", 104)
    phase("calibrating_open", "open_calibration_started", 104)
    phase("ready", "calibration_passed", 128)
    events.append({"type": "calibration", "version": 2, "parameters": parameters})
    phase("training", "training_started", 128)
    (source / "events.jsonl").write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    return source, destination, source_state, state, baseline


def test_reused_baseline_exports_only_exact_successful_source_round(tmp_path):
    source, destination, _, _, _ = baseline_pair(tmp_path)
    original_files = {file.name: file.read_bytes() for file in source.iterdir()}
    archive, manifest = archive_for(destination, tmp_path)
    reference = manifest["baseline_reference"]
    assert reference["included"]
    assert reference["source_sample_ranges"] == [[80, 104], [104, 128]]
    assert all(part["phase"] == "training" for part in manifest["segments"])
    ref_manifest = json.loads(archive.read("baseline-reference/edf-segments.json"))
    assert ref_manifest["source_kind"] == "baseline_reference"
    assert [part["first_sample_index"] for part in ref_manifest["segments"]] == [80, 104]
    assert [part["sample_count"] for part in ref_manifest["segments"]] == [24, 24]
    assert [part["sample_mapping"]["raw_csv_first_data_row_zero_based"] for part in ref_manifest["segments"]] == [0, 24]
    rows = list(csv.DictReader(io.StringIO(archive.read("baseline-reference/raw.csv").decode())))
    assert [int(row["sample_index"]) for row in rows] == list(range(80, 128))
    assert {row["phase"] for row in rows} == {"calibrating_closed", "calibrating_open"}
    assert {file.name: file.read_bytes() for file in source.iterdir()} == original_files


@pytest.mark.parametrize("problem", ["participant", "parameters", "device", "events", "path", "missing_root", "truncated_raw"])
def test_unverifiable_baseline_never_mixes_other_data_or_blocks_current_edf(tmp_path, problem):
    source, destination, source_state, state, baseline = baseline_pair(tmp_path)
    if problem == "participant":
        source_state["participant_id"] = "someone-else"
    elif problem == "parameters":
        baseline["calibration"]["parameters"] = {"wrong": 1}
    elif problem == "device":
        source_state["device_metadata"]["device_address"] = "ff:ff:ff:ff:ff:ff"
    elif problem == "events":
        (source / "events.jsonl").write_text("", encoding="utf-8")
    elif problem == "path":
        baseline["source_session_id"] = "../../outside"
    elif problem == "truncated_raw":
        lines = (source / "raw.csv").read_text(encoding="utf-8").splitlines()
        (source / "raw.csv").write_text("\n".join(lines[:113]) + "\n", encoding="utf-8")
    (source / "session.json").write_text(json.dumps(source_state), encoding="utf-8")
    (destination / "baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
    archive, manifest = archive_for(destination, None if problem == "missing_root" else tmp_path)
    assert not manifest["baseline_reference"]["included"]
    assert manifest["baseline_reference"]["reason"]
    assert not any(name.startswith("baseline-reference/") for name in archive.namelist())
    assert manifest["segments"][0]["phase"] == "training"


def test_edf_error_is_http_error_and_plain_csv_export_remains_available(tmp_path):
    directory, state, _ = record(tmp_path, [("training", 4)])
    with pytest.raises(EDFExportError, match="原始 CSV 未改变"):
        export_session(directory, include_edf=True)
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8768") as client:
        url = f"/api/sessions/{state['session_id']}/export"
        response = client.get(url + "?edf=true")
        assert response.status_code == 422
        assert "EDF" in response.json()["detail"] and "CSV" in response.json()["detail"]
        plain = client.get(url)
        assert plain.status_code == 200
        assert "raw.csv" in zipfile.ZipFile(io.BytesIO(plain.content)).namelist()


def test_existing_reference_edf_has_same_channel_and_unit_convention():
    folder = Path(__file__).resolve().parents[2] / "02-数据" / "eeg_raw" / "subject01"
    paths = list(folder.glob("*.edf"))
    if not paths:
        pytest.skip("Reference dataset not present in this checkout")
    with pyedflib.EdfReader(str(paths[0])) as reader:
        assert reader.getSignalLabels() == EDF_CHANNELS
        assert reader.getSampleFrequencies().tolist() == [250] * 4
        assert [reader.getPhysicalDimension(index) for index in range(4)] == ["uV"] * 4
