"""Immutable, participant-scoped calibration evidence; historical sessions are read only."""
import copy
import csv
import hashlib
import json
import math
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .signal import CALIBRATION_VERSION, CalibrationError, score_feature, validate_calibration
from .storage import clean, json_text, read_session, utc_now


def compatibility(state):
    metadata = state.get("device_metadata") or state.get("provider_metadata") or {}
    connection = state.get("connection") or {}
    config = state.get("algorithm_config") or {}
    return clean({"mode": state.get("mode"), "source": state.get("source"),
                  "sample_rate": connection.get("sample_rate"), "channels": connection.get("channels"),
                  "units": connection.get("units", "uV"),
                  "device": {"board_id": metadata.get("board_id"), "provider": metadata.get("provider"),
                             "address": str(metadata.get("device_address") or "").lower()},
                  "algorithm_config": config})


def calibration_trajectory(features, parameters):
    """Re-score two pretest curves using the final fixed mapping, keeping gaps."""
    trajectory, origin, previous_stage, segment, previous_end = [], None, None, 0, None
    for feature in features:
        stage = feature.get("stage") or feature.get("phase")
        if stage not in {"calibrating_closed", "calibrating_open"}:
            continue
        start, end = feature.get("window_start"), feature.get("window_end")
        if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in (start, end)):
            if trajectory:
                segment += 1
                trajectory.append({"time": trajectory[-1]["time"], "score": None, "meditation": None,
                                   "valid": False, "stage": stage.removeprefix("calibrating_"), "segment": segment})
            continue
        if origin is None:
            origin = start
        if stage != previous_stage or previous_end is not None and end - previous_end > 1.5:
            segment += 1
        scored = score_feature(feature, parameters)
        valid = bool(feature.get("valid")) and scored.get("score") is not None
        if not valid:
            segment += 1
        trajectory.append({"time": round(end - origin, 6), "score": scored.get("score") if valid else None,
                           "meditation": scored.get("meditation") if valid else None, "valid": valid,
                           "stage": stage.removeprefix("calibrating_"), "phase": stage,
                           "segment": segment, "window_start": start, "window_end": end})
        previous_stage, previous_end = stage, end
    return trajectory


def read_calibration_features(directory, calibration):
    """Choose the final successful calibration round in an old append-only file."""
    before_version = max(0, int(calibration.get("version", 1)) - 1)
    closed_started = open_started = None
    try:
        current_closed = current_open = None
        for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("type") == "phase":
                if event.get("reason") == "closed_calibration_started":
                    current_closed, current_open = event.get("at"), None
                elif event.get("reason") == "open_calibration_started":
                    current_open = event.get("at")
            if event.get("type") == "calibration" and event.get("version") == before_version + 1:
                closed_started, open_started = current_closed, current_open
        closed_started = datetime.fromisoformat(closed_started.replace("Z", "+00:00")).timestamp() if closed_started else None
        open_started = datetime.fromisoformat(open_started.replace("Z", "+00:00")).timestamp() if open_started else None
    except (OSError, ValueError, TypeError):
        closed_started = open_started = None
    features = []
    with (directory / "features.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("phase") not in {"calibrating_closed", "calibrating_open"}:
                continue
            if row.get("calibration_version") and int(row["calibration_version"]) != before_version:
                continue
            feature = {**row, "valid": row.get("valid", "").lower() in {"true", "1"}, "stage": row["phase"]}
            for key in ("window_start", "window_end", "attention_raw", "meditation_raw", "smooth_alpha"):
                feature[key] = float(row[key]) if row.get(key) else None
            cutoff = closed_started if row["phase"] == "calibrating_closed" else open_started
            if cutoff is not None and feature["window_end"] is not None and feature["window_end"] < cutoff:
                continue
            features.append(feature)
    return features


class BaselineStore:
    def __init__(self, sessions_root):
        self.sessions_root = Path(sessions_root)
        self.directory = self.sessions_root / "_baselines"

    def _participant_directory(self, participant_id, mode):
        digest = hashlib.sha256(participant_id.encode("utf-8")).hexdigest()
        return self.directory / mode / digest

    def save(self, state, features, imported=False):
        calibration = copy.deepcopy(state["calibration"])
        # The frontend's fixed guide is a pretest condition, not measured EEG
        # feedback. Historical acquisitions must never be retroactively relabeled.
        calibration["visual_protocol"] = (calibration.get("visual_protocol", "legacy-unspecified") if imported
                                          else "fixed-three-layer-guide-v1")
        validate_calibration(calibration["parameters"])
        if calibration["parameters"].get("version") != CALIBRATION_VERSION or state.get("mode") == "replay":
            raise CalibrationError("此校准不能作为新的个人基线")
        trajectory = calibration_trajectory(features, calibration["parameters"])
        if not all(any(p["valid"] and p["stage"] == stage for p in trajectory)
                   for stage in ("closed", "open")):
            raise CalibrationError("个人基线缺少完整的双阶段前测曲线")
        baseline_id = str(uuid4())
        created = utc_now()
        record = {"schema_version": 1, "baseline_id": baseline_id, "participant_id": state["participant_id"],
                  "mode": state["mode"], "created_at": created, "source_session_id": state["session_id"],
                  "source_created_at": state.get("created_at"), "imported": imported,
                  "calibration_method": calibration["parameters"]["version"], "compatibility": compatibility(state),
                  "calibration": calibration, "trajectory": trajectory,
                  "visual_protocol": calibration["visual_protocol"]}
        folder = self._participant_directory(state["participant_id"], state["mode"])
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder / (baseline_id + ".tmp")
        target = folder / (baseline_id + ".json")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json_text(record) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        return record

    def find(self, state):
        if state.get("mode") == "replay":
            return None
        folder = self._participant_directory(state["participant_id"], state["mode"])
        compatible = []
        for path in folder.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                validate_calibration(record["calibration"]["parameters"])
                if (record["participant_id"] == state["participant_id"] and record["mode"] == state["mode"]
                        and record["compatibility"] == compatibility(state)
                        and record["calibration_method"] == CALIBRATION_VERSION):
                    compatible.append(record)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        if compatible:
            return max(compatible, key=lambda record: record["created_at"])
        # One-time migration deliberately uses the earliest compatible real v2
        # session, never a replay/synthetic or min/max mapping as a live baseline.
        if state["mode"] != "live":
            return None
        candidates = []
        for directory in self.sessions_root.iterdir():
            try:
                saved = read_session(directory)
                calibration = saved.get("calibration") or {}
                if (saved.get("session_id") == state["session_id"] or saved.get("mode") != "live"
                        or saved.get("participant_id") != state["participant_id"]
                        or saved.get("source") != "muse" or saved.get("provenance", {}).get("is_synthetic")
                        or not calibration.get("valid") or compatibility(saved) != compatibility(state)
                        or calibration.get("parameters", {}).get("version") != CALIBRATION_VERSION):
                    continue
                validate_calibration(calibration["parameters"])
                candidates.append((saved, directory))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        for saved, directory in sorted(candidates, key=lambda item: item[0].get("created_at") or ""):
            try:
                features = read_calibration_features(directory, saved["calibration"])
                return self.save(saved, features, imported=True)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return None

    @staticmethod
    def apply(record, reused):
        calibration = copy.deepcopy(record["calibration"])
        calibration.update(baseline_id=record["baseline_id"], reused=reused,
                           source_session_id=record["source_session_id"], created_at=record["created_at"],
                           source_created_at=record.get("source_created_at"),
                           trajectory=copy.deepcopy(record["trajectory"]), imported=record.get("imported", False))
        return calibration
