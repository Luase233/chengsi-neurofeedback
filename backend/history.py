"""Read-only participant history; old records retain their original scoring scale."""
import copy
import csv
import json
from datetime import datetime

from .metrics import peak_metrics
from .storage import read_session


def _epoch(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError, TypeError):
        return None


def program_segments(directory, fallback):
    """Reconstruct only changes up to and during training, never after completion."""
    program, started, segments = None, None, []
    try:
        for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("type") == "session_created":
                program = event.get("request", {}).get("program_id", fallback)
            if event.get("type") == "phase" and event.get("reason") == "training_started":
                started = _epoch(event.get("at"))
                segments = [{"program_id": program or fallback, "at": event.get("at"), "wall_elapsed_seconds": 0,
                             "phase": "training"}]
            if event.get("type") == "command" and event.get("command") == "set_program":
                phase = event.get("resulting_phase")
                if phase in {"idle", "completed", "error"}:
                    continue
                program = event.get("program_id", program)
                if started is not None:
                    stamp = _epoch(event.get("at"))
                    segments.append({"program_id": program, "at": event.get("at"),
                                     "wall_elapsed_seconds": max(0, stamp - started) if stamp is not None else None,
                                     "phase": phase})
        return segments
    except (OSError, ValueError):
        return []


def _legacy_trajectory(directory, state):
    """Use every original feature (including bad windows), never sparse UI points."""
    rows, segment, origin, watermark, previous_end = [], 0, None, None, None
    # The phase event sample index is the authoritative attempt boundary. Feature
    # UTC endpoints select that last attempt; phase breaks are retained below.
    training_started = None
    phase_breaks = []
    try:
        for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("type") == "phase":
                stamp = _epoch(event.get("at"))
                if event.get("reason") == "training_started":
                    training_started, phase_breaks = stamp, []
                elif event.get("previous") == "training" and stamp is not None:
                    phase_breaks.append(stamp)
        with (directory / "features.csv").open(encoding="utf-8", newline="") as handle:
            for feature in csv.DictReader(handle):
                if feature.get("phase") != "training":
                    previous_end = watermark = None
                    segment += 1
                    continue
                start = float(feature["window_start"]) if feature.get("window_start") else None
                end = float(feature["window_end"]) if feature.get("window_end") else None
                if training_started and end is not None and end < training_started:
                    continue
                if origin is None and start is not None:
                    origin = start
                valid = feature.get("valid", "").lower() in {"true", "1"} and bool(feature.get("score"))
                valid = valid and start is not None and end is not None
                if (not valid or previous_end is not None and end - previous_end > 1.5
                        or previous_end is not None and any(previous_end < stamp <= end for stamp in phase_breaks)):
                    segment += 1
                contribution = max(start, watermark if watermark is not None else start) if valid else None
                duration = max(0, end - contribution) if valid else 0
                if end is not None:
                    watermark = max(end, watermark if watermark is not None else end)
                rows.append({"time": end - origin if end is not None and origin is not None else (rows[-1]["time"] if rows else 0),
                             "score": float(feature["score"]) if valid else None,
                             "meditation": float(feature["meditation"]) if valid and feature.get("meditation") else None,
                             "valid": valid, "valid_duration": duration, "segment": segment,
                             "contribution_start": contribution - origin if valid else None,
                             "contribution_end": end - origin if valid else None})
                previous_end = end if valid else None
        return rows
    except (OSError, ValueError, TypeError):
        return []


def participant_history(root, participant_id, mode, active_session_id=None):
    from .baselines import BaselineStore
    baseline_store = BaselineStore(root)
    source_ids = {}
    for path in baseline_store._participant_directory(participant_id, mode).glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            source_ids[record["source_session_id"]] = record["baseline_id"]
        except (OSError, ValueError, KeyError):
            continue
    entries = []
    daily_entries = {}
    for directory in root.iterdir():
        try:
            state = read_session(directory, active_session_id)
            if (state.get("participant_id") != participant_id or state.get("mode") != mode
                    or not state.get("summary")):
                continue
            summary = copy.deepcopy(state["summary"])
            calibration = state.get("calibration") or {}
            method = (calibration.get("parameters") or {}).get("version") or summary.get("calibration_method")
            baseline_id = (calibration.get("baseline_id") or summary.get("baseline_id")
                           or source_ids.get(state["session_id"]) or "historical-session:" + state["session_id"])
            if "peak" not in summary:
                trajectory = _legacy_trajectory(directory, state)
                summary["peak"] = peak_metrics(trajectory)
                summary["peak"]["historical_reconstruction"] = "original_full_features" if trajectory else "unavailable"
            segments = summary.get("program_segments") or program_segments(directory, state.get("program_id"))
            summary["program_segments"] = segments
            summary.update(baseline_id=baseline_id, calibration_method=method)
            if "wall_elapsed_seconds" not in summary:
                summary["wall_elapsed_seconds"] = None  # Old active timer excluded interruptions.
            entries.append({"session_id": state["session_id"], "created_at": state.get("created_at"),
                            "program_id": segments[0]["program_id"] if segments else state.get("program_id"),
                            "program_segments": segments, "baseline_id": baseline_id, "calibration_method": method,
                            "comparison_group": baseline_id + "|" + str(method), "summary": summary,
                            "phase": state.get("phase"), "interrupted": state.get("interrupted", False),
                            "protocol": copy.deepcopy(state.get("protocol")),
                            "round_summaries": copy.deepcopy((state.get("protocol") or {}).get("completed_rounds", [])),
                            "visual_segments": copy.deepcopy(summary.get("visual_segments", []))})
            protocol = state.get("protocol") or {}
            if protocol.get("plan") == "daily" and protocol.get("training_date"):
                # Daily means use every completed round, weighted by accepted
                # seconds. A resumed session must not create a second day point.
                key = (protocol["training_date"], protocol.get("round_target_seconds"))
                candidate = {"training_date": protocol["training_date"], "session_id": state["session_id"],
                             "protocol": copy.deepcopy(protocol), "baseline_id": baseline_id,
                             "comparison_group": baseline_id + "|" + str(method),
                             "mean_score": protocol.get("daily_mean_score"),
                             "mean_meditation": protocol.get("daily_mean_meditation"),
                             "valid_seconds": protocol.get("daily_valid_seconds"),
                             "complete": protocol.get("day_complete", False)}
                old = daily_entries.get(key)
                if old is None or protocol.get("completed_round_count", 0) >= old["protocol"].get("completed_round_count", 0):
                    daily_entries[key] = candidate
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return {"participant_id": participant_id, "mode": mode,
            "entries": sorted(entries, key=lambda row: row.get("created_at") or ""),
            "daily_entries": sorted(daily_entries.values(), key=lambda row: row["training_date"])}
