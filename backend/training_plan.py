"""Four-round daily protocol; progress is reconstructed from immutable session evidence.

The study manual specifies 4 weeks x 4 training days, 4 x 60 valid seconds per
day, with 60-second rests. Calendar dates are Asia/Shanghai. Historical single
sessions never become protocol days retroactively; replay never counts as work.
"""
import copy
import json
from datetime import datetime, timedelta, timezone

SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")
VERSION = "daily-four-rounds-v1"
WEEK_PROGRAMS = ("deep-learning", "efficiency", "re-life", "beta-focus")
SCENES = {
    "lake-trees": {"version": "quiet-landscape-v1", "motion": "slow_local_cloud_and_water", "score_driven": False},
    "lake-house": {"version": "quiet-landscape-v1", "motion": "slow_local_cloud_and_water", "score_driven": False},
    "rings-still": {"version": "fixed-full-rings-v1", "motion": "none", "score_driven": False},
    "rings-feedback": {"version": "legacy-feedback-rings-v1", "motion": "audio_and_score_driven", "score_driven": True},
}


def stamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError, TypeError):
        return None


def date_now(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(SHANGHAI).date().isoformat()


def visual_scene(scene):
    return {"scene": scene, **copy.deepcopy(SCENES[scene])}


def plan_snapshot(root, participant_id, mode, training_seconds=60, now=None):
    """Deduplicate resumed sessions by (date, round), never by score or file count."""
    today = date_now(now)
    per_date = {}
    day_numbers = {}
    for path in root.glob("*/session.json"):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            p = state.get("protocol") or {}
            if (state.get("participant_id") != participant_id or state.get("mode") != mode
                    or mode == "replay" or p.get("plan") != "daily"
                    or p.get("version") != VERSION or p.get("round_target_seconds") != training_seconds):
                continue
            day = p.get("training_date")
            if not day or day > today:
                continue
            day_numbers.setdefault(day, p.get("training_day_number"))
            records = per_date.setdefault(day, {})
            for item in p.get("completed_rounds", []):
                number, summary = item.get("round_number"), item.get("summary") or {}
                if (not isinstance(number, int) or not 1 <= number <= 4
                        or summary.get("reason") != "training_valid_target_reached"
                        or float(summary.get("valid_seconds") or 0) < training_seconds - 1e-6):
                    continue
                # The original successful record is authoritative. Resumed files
                # carry the same record; an earlier completion wins any conflict.
                previous = records.get(number)
                if previous is None or (summary.get("ended_at") or "~") < (previous["summary"].get("ended_at") or "~"):
                    records[number] = copy.deepcopy(item)
        except (OSError, ValueError, TypeError, KeyError):
            continue
    complete_dates = sorted(day for day, rows in per_date.items() if set(rows) == {1, 2, 3, 4})
    rounds = [per_date.get(today, {})[i] for i in range(1, 5) if i in per_date.get(today, {})]
    # Never skip a missing round because a malformed/partial file has a later one.
    contiguous = []
    for item in rounds:
        if item["round_number"] != len(contiguous) + 1:
            break
        contiguous.append(item)
    completed_before_today = sum(day < today for day in complete_dates)
    day_number = day_numbers.get(today) or completed_before_today + 1
    day_number = max(1, min(16, int(day_number)))
    week = (day_number - 1) // 4 + 1
    last_end = (contiguous[-1]["summary"].get("ended_at") if contiguous else None)
    rest_until = None
    if last_end and len(contiguous) < 4:
        epoch = stamp(last_end)
        if epoch is not None:
            rest_until = datetime.fromtimestamp(epoch + 60, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    result = {
        "plan": "daily", "version": VERSION, "training_date": today, "timezone": "Asia/Shanghai",
        "week_number": week, "day_in_week": (day_number - 1) % 4 + 1,
        "training_day_number": day_number, "total_weeks": 4, "days_per_week": 4, "total_days": 16,
        "rounds_target": 4, "round_target_seconds": training_seconds, "rest_seconds": 60,
        "round_number": min(4, len(contiguous) + 1), "completed_round_count": len(contiguous),
        "completed_rounds": contiguous, "completed_days": len(complete_dates),
        "completed_dates": complete_dates, "day_complete": len(contiguous) == 4,
        "cycle_complete": len(complete_dates) >= 16,
        "progress_basis": "completed_training_days_not_elapsed_calendar_weeks",
        "recommended_program_id": WEEK_PROGRAMS[week - 1],
        "rest_until": rest_until, "rest_remaining_seconds": 0,
        "original_round_seconds": 60, "same_day_resumed": bool(contiguous),
        "baseline_policy": "reuse_compatible_personal_baseline",
        "weekly_reference_rule": "fourth_training_day_fourth_completed_round",
        "transfer_test": {"tasks": ["Press Reaction", "Flick Aiming"], "due_within_days_after_cycle": 7,
                          "status": "external_task_not_automatically_measured"},
    }
    refresh_totals(result, now=now)
    return result


def single_plan(training_seconds=120):
    return {"plan": "single", "version": VERSION, "rounds_target": 1, "round_number": 1,
            "round_target_seconds": training_seconds, "rest_seconds": 0, "rest_until": None,
            "rest_remaining_seconds": 0, "completed_round_count": 0, "completed_rounds": [], "day_complete": False}


def refresh_totals(protocol, current=None, now=None, active=False):
    now = now or datetime.now(timezone.utc)
    rows = [row["summary"] for row in protocol.get("completed_rounds", [])]
    if current and current.get("started_at") and not current.get("ended_at"):
        rows.append(current)
    protocol["completed_round_count"] = len(protocol.get("completed_rounds", []))
    total = sum(float(row.get("valid_seconds") or 0) for row in rows)
    protocol["daily_valid_seconds"] = total
    for field, target in (("mean_score", "daily_mean_score"), ("mean_meditation", "daily_mean_meditation")):
        weighted = [(float(row.get("valid_seconds") or 0), row.get(field)) for row in rows if row.get(field) is not None]
        weight = sum(w for w, _ in weighted)
        protocol[target] = sum(w * v for w, v in weighted) / weight if weight else None
    protocol["daily_round_wall_seconds"] = sum(float(row.get("wall_elapsed_seconds") or 0) for row in rows)
    protocol["baseline_ids"] = sorted({row["baseline_id"] for row in rows if row.get("baseline_id")})
    protocol["mixed_baselines"] = len(protocol["baseline_ids"]) > 1
    protocol["scene_ids"] = sorted({segment["scene"] for row in rows for segment in row.get("visual_segments", []) if segment.get("scene")})
    protocol["mixed_scenes"] = len(protocol["scene_ids"]) > 1
    protocol["program_ids"] = sorted({segment["program_id"] for row in rows for segment in row.get("program_segments", []) if segment.get("program_id")})
    starts = [stamp(row.get("started_at")) for row in rows]
    ends = [stamp(row.get("ended_at")) for row in rows]
    starts, ends = [s for s in starts if s is not None], [e for e in ends if e is not None]
    finish = now.timestamp() if active and starts else max(ends, default=0)
    protocol["daily_wall_seconds"] = max(0, finish - min(starts)) if starts else 0
    protocol["daily_wall_basis"] = "first_round_start_to_last_round_end_including_breaks_and_interruptions"
    until = stamp(protocol.get("rest_until"))
    protocol["rest_remaining_seconds"] = max(0, until - now.timestamp()) if until is not None else 0
    if protocol.get("plan") == "daily":
        protocol["day_complete"] = protocol["completed_round_count"] >= 4
        dates = set(protocol.get("completed_dates", []))
        if protocol["day_complete"]:
            dates.add(protocol["training_date"])
        protocol["completed_dates"] = sorted(dates)
        protocol["completed_days"] = len(dates)
        protocol["cycle_complete"] = len(dates) >= 16
    return protocol
