"""Focused tests of daily round evidence and cue gates; no hardware or UI use."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.models import SessionRequest
from backend.sessions import SessionConflict
from backend.signal import fit_calibration
from backend.training_plan import plan_snapshot
from test_dual_screen_protocol import make_service, feature, fresh_signal, command, acknowledge


def ready_daily(tmp_path, dual=True):
    service = make_service(tmp_path, dual=dual)
    service.state["phase"] = "ready"
    service.state["protocol"] = plan_snapshot(tmp_path, "dual-screen-test", "synthetic", 60)
    service.state["training"]["target_seconds"] = 60
    params = fit_calibration([feature(attention=.15)], [feature()], min_valid_windows=1)
    service.state["calibration"].update(valid=True, parameters=params, baseline_id="fixed-baseline")
    return service


def skip_rest(service):
    service.state["protocol"]["rest_until"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()


def test_daily_defaults_to_document_minute_and_preserves_single_api_default():
    assert SessionRequest(mode="synthetic", protocol_plan="daily").training_seconds == 60
    assert SessionRequest(mode="synthetic").training_seconds == 120
    for args in ({"mode": "replay", "protocol_plan": "daily", "replay_session_id": "old"},
                 {"mode": "synthetic", "protocol_plan": "daily", "training_seconds": 30}):
        with pytest.raises(ValidationError):
            SessionRequest(**args)


def test_training_waits_for_owned_complete_cue_then_four_rounds_keep_baseline(tmp_path):
    async def scenario():
        service = ready_daily(tmp_path)
        baseline = copy.deepcopy(service.state["calibration"])
        try:
            await service.presentation_heartbeat("participant-A", True)
            for number in range(1, 5):
                fresh_signal(service)
                state = await command(service, "start_training")
                cue = state["presentation"]["cue_request"]
                assert state["phase"] == "preparing_training" and cue["stage"] == "training"
                assert service._training_started_mono is None if number == 1 else service.state["training"]["ended_at"]
                service._feature(feature(number * 100, number * 100 + 2), "preparing_training")
                assert service.state["training"]["valid_seconds"] == (0 if number == 1 else 60)
                with pytest.raises(SessionConflict):
                    await acknowledge(service, cue, "wrong-owner")
                await acknowledge(service, cue)
                assert service.state["phase"] == "training" and service.state["training"]["valid_seconds"] == 0
                with pytest.raises(SessionConflict):
                    await acknowledge(service, cue)
                # Accepted source-time completes the round, independent of test wall time.
                service._feature(feature(number * 100 + 3, number * 100 + 63), "training")
                state = service.snapshot()
                assert state["protocol"]["completed_round_count"] == number
                assert state["protocol"]["daily_valid_seconds"] == 60 * number
                assert service.state["calibration"] == baseline
                if number < 4:
                    assert state["phase"] == "resting" and not service._stop_requested
                    assert state["summary"] is None
                    fresh_signal(service)
                    with pytest.raises(SessionConflict):
                        await command(service, "start_training")
                    skip_rest(service)
                    assert service.snapshot()["phase"] == "resting", "Rest expiry must never start the next round"
                else:
                    assert state["phase"] == "completed" and service._stop_requested
                    assert state["protocol"]["day_complete"] and state["protocol"]["completed_days"] == 1
                    assert state["summary"]["summary_scope"] == "last_round"
                    assert len(state["summary"]["round_summaries"]) == 4
                    assert state["summary"]["reason"] == "training_valid_target_reached"
            stored = plan_snapshot(tmp_path, "dual-screen-test", "synthetic", 60)
            assert stored["completed_days"] == 1 and stored["completed_round_count"] == 4
            events = [json.loads(x) for x in (service._store.directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            assert len([e for e in events if e["type"] == "training_round_completed"]) == 4
        finally:
            await service.shutdown()
    asyncio.run(scenario())


def test_interrupted_day_resumes_completed_rounds_once_and_preserves_rest(tmp_path):
    async def scenario():
        first = ready_daily(tmp_path, dual=False)
        try:
            fresh_signal(first)
            await command(first, "start_training")
            first._feature(feature(100, 160), "training")
            await command(first, "finish")
            assert first.state["phase"] == "completed"
        finally:
            await first.shutdown()
        second = ready_daily(tmp_path, dual=False)
        try:
            assert second.state["protocol"]["completed_round_count"] == 1
            assert second.state["protocol"]["round_number"] == 2
            assert second.state["protocol"]["rest_remaining_seconds"] > 0
            fresh_signal(second)
            with pytest.raises(SessionConflict):
                await command(second, "start_training")
            skip_rest(second)
            await command(second, "start_training")
            second._feature(feature(200, 260), "training")
            await command(second, "finish")
            resumed = plan_snapshot(tmp_path, "dual-screen-test", "synthetic", 60)
            assert resumed["completed_round_count"] == 2
            assert resumed["completed_days"] == 0
            assert resumed["daily_valid_seconds"] == 120
            assert plan_snapshot(tmp_path, "dual-screen-test", "live", 60)["completed_round_count"] == 0
            assert plan_snapshot(tmp_path, "dual-screen-test", "synthetic", 120)["completed_round_count"] == 0
        finally:
            await second.shutdown()
    asyncio.run(scenario())


def test_visual_changes_are_logged_and_training_change_requires_pause(tmp_path):
    async def scenario():
        service = ready_daily(tmp_path, dual=False)
        try:
            await command(service, "set_presentation", training_scene="rings-still")
            fresh_signal(service)
            await command(service, "start_training")
            with pytest.raises(SessionConflict):
                await command(service, "set_presentation", training_scene="lake-house")
            await command(service, "pause")
            await command(service, "set_presentation", training_scene="lake-house")
            assert [s["scene"] for s in service._visual_segments] == ["rings-still", "lake-house"]
            assert all(not s["score_driven"] for s in service._visual_segments)
            await command(service, "finish")
            assert service.state["summary"]["visual_segments"][-1]["scene"] == "lake-house"
            assert service.state["protocol"]["completed_round_count"] == 0
        finally:
            await service.shutdown()
    asyncio.run(scenario())


def test_week_progress_uses_completed_days_and_shanghai_calendar(tmp_path):
    now = datetime(2026, 10, 5, 17, tzinfo=timezone.utc)  # Shanghai Oct 6.
    for index, day in enumerate(("2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01")):
        folder = tmp_path / str(index)
        folder.mkdir()
        p = plan_snapshot(tmp_path, "p1", "live", 60, now=now)
        p.update(training_date=day, training_day_number=index + 1,
                 completed_rounds=[{"round_number": n, "source_session_id": str(index),
                     "summary": {"reason": "training_valid_target_reached", "valid_seconds": 60,
                                 "mean_score": 50 + n, "mean_meditation": 40,
                                 "ended_at": day + "T08:00:00Z"}} for n in range(1, 5)])
        (folder / "session.json").write_text(json.dumps({"participant_id": "p1", "mode": "live", "protocol": p}), encoding="utf-8")
    result = plan_snapshot(tmp_path, "p1", "live", 60, now=now)
    assert result["training_date"] == "2026-10-06"
    assert result["completed_days"] == 4 and result["week_number"] == 2 and result["day_in_week"] == 1
    assert result["recommended_program_id"] == "efficiency"
