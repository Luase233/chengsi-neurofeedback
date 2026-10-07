"""Participant audio acknowledgements gate calibration; no physical EEG device."""
import asyncio
import copy
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.baselines import compatibility
from backend.models import CommandRequest, SessionRequest
from backend.sessions import SessionConflict, SessionService
from backend.signal import LiveProcessor, fit_calibration
from backend.storage import SessionStore


def feature(start=10, end=12, attention=.3, meditation=1.5):
    return {"window_start": start, "window_end": end, "valid": True, "reasons": [],
            "attention_raw": attention, "meditation_raw": meditation, "bands": {}}


def make_service(directory, dual=True):
    service = SessionService(directory)
    service.state.update(session_id=str(uuid4()), mode="synthetic", source="synthetic", phase="connected",
                         participant_id="dual-screen-test", algorithm_config=LiveProcessor().config)
    service.state["connection"].update(status="connected", sample_rate=256,
                                        channels=["TP9", "AF7", "AF8", "TP10"], units="uV")
    service.state["calibration"]["target_seconds"] = 5
    service.state["presentation"]["dual_screen"] = dual
    service._store = SessionStore(directory, service.state["session_id"])
    return service


def fresh_signal(service):
    service.state["quality"].update(valid=True, reasons=[], channel_quality={channel: {"valid": True} for channel in ["TP9", "AF7", "AF8", "TP10"]})
    service._last_data_mono = service._last_feedback_mono = time.monotonic()


async def command(service, action, **extra):
    return await service.command(service.state["session_id"], CommandRequest(command=action, command_id=str(uuid4()), **extra))


async def prepare(service, stage="closed"):
    await service.presentation_heartbeat("participant-A", True)
    fresh_signal(service)
    result = await command(service, "calibrate_" + stage)
    return result["presentation"]["cue_request"]


async def acknowledge(service, request, client="participant-A"):
    return await command(service, "cue_finished", cue_request_id=request["id"], client_id=client)


def test_dual_screen_models_are_opt_in_and_ack_requires_request_and_owner():
    assert SessionRequest(mode="synthetic").dual_screen is False
    assert SessionRequest(mode="synthetic", dual_screen=True).dual_screen is True
    for payload in ({"command": "cue_finished"}, {"command": "set_presentation"},
                    {"command": "set_presentation", "volume": 1.1},
                    {"command": "set_presentation", "cue_mode": "unknown"}):
        with pytest.raises(ValidationError):
            CommandRequest(command_id="invalid", **payload)
    assert CommandRequest(command="set_presentation", command_id="zero", volume=0).volume == 0


def test_participant_lease_survives_create_is_exclusive_and_ignores_visibility(tmp_path):
    class QuietProvider:
        def connect(self): return {"source": "synthetic", "sample_rate": 256, "channels": ["TP9", "AF7", "AF8", "TP10"]}
        def read(self): return None
        def disconnect(self): pass

    async def scenario():
        service = SessionService(tmp_path, provider_factory=lambda request: QuietProvider())
        try:
            idle = await service.presentation_heartbeat("participant-A", True, visible=False)
            assert idle["presentation"]["participant_ready"] is True
            assert idle["presentation"]["visible"] is False
            created = await service.create(SessionRequest(mode="synthetic", dual_screen=True, calibration_seconds=5))
            assert created["presentation"]["participant_client_id"] == created["presentation"]["client_id"] == "participant-A"
            assert created["presentation"]["participant_ready"] is True
            with pytest.raises(SessionConflict):
                await service.presentation_heartbeat("participant-B", True)
            service._presentation_lease["seen_mono"] -= 5.01
            replaced = await service.presentation_heartbeat("participant-B", True)
            assert replaced["presentation"]["client_id"] == "participant-B"
        finally:
            await service.shutdown()
    asyncio.run(scenario())


def test_prepare_requires_quality_and_ready_and_collects_no_calibration_until_ack(tmp_path):
    async def scenario():
        service = make_service(tmp_path)
        try:
            await service.presentation_heartbeat("participant-A", True)
            with pytest.raises(SessionConflict):
                await command(service, "calibrate_closed")
            fresh_signal(service)
            await service.presentation_heartbeat("participant-A", False)
            with pytest.raises(SessionConflict):
                await command(service, "calibrate_closed")
            cue = await prepare(service)
            assert service.state["phase"] == "preparing_closed"
            assert cue["stage"] == "closed" and cue["kind"] == "start" and cue["mode"] == "voice"
            assert cue["created_at"] and cue["session_id"] == service.state["session_id"]
            assert service.state["quality"]["valid"] is True, "Preparation must retain the continuous quality monitor"
            service._feature(feature(), "preparing_closed")
            assert service.state["calibration"]["closed_seconds"] == 0
            assert service._closed_features == service._calibration_features == []
            assert service.state["feedback"]["score"] is None
            with pytest.raises(SessionConflict):
                await acknowledge(service, cue, client="participant-B")
            assert service.state["phase"] == "preparing_closed"
            started = await acknowledge(service, cue)
            assert started["phase"] == "calibrating_closed" and started["presentation"]["cue_request"] is None
            service._feature(feature(20, 22), "calibrating_closed")
            assert service.state["calibration"]["closed_seconds"] == 2
            with pytest.raises(SessionConflict):
                await acknowledge(service, cue)
            assert service.state["calibration"]["closed_seconds"] == 2
            events = [json.loads(line) for line in (service._store.directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            ack = next(event for event in events if event["type"] == "calibration_cue_acknowledged")
            assert ack["evidence"] == "participant_client_report"
        finally:
            await service.shutdown()
    asyncio.run(scenario())


def test_open_preparation_has_its_own_request_and_keeps_closed_observations(tmp_path):
    async def scenario():
        service = make_service(tmp_path)
        try:
            closed = await prepare(service)
            await acknowledge(service, closed)
            for start in (10, 12, 14):
                service._feature(feature(start, start + 2, attention=.15), "calibrating_closed")
            assert service.state["phase"] == "closed_complete"
            saved_closed = copy.deepcopy(service._closed_features)
            opened = await prepare(service, "open")
            assert opened["id"] != closed["id"] and opened["stage"] == "open"
            with pytest.raises(SessionConflict):
                await acknowledge(service, closed)
            service._feature(feature(30, 32), "preparing_open")
            assert service.state["calibration"]["open_seconds"] == 0
            assert service._closed_features == saved_closed and service._open_features == []
            await acknowledge(service, opened)
            assert service.state["phase"] == "calibrating_open" and service._closed_features == saved_closed
        finally:
            await service.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["quality", "not-ready", "expired"])
def test_failed_preparation_cancels_request_and_recovery_never_accepts_old_ack(tmp_path, failure):
    async def scenario():
        service = make_service(tmp_path)
        try:
            cue = await prepare(service)
            if failure == "quality":
                service.state["quality"]["valid"] = False
            elif failure == "not-ready":
                await service.presentation_heartbeat("participant-A", False)
            else:
                service._presentation_lease["seen_mono"] -= 5.01
            with pytest.raises(SessionConflict):
                await acknowledge(service, cue)
            assert service.state["phase"] == "connected"
            assert service.state["presentation"]["cue_request"] is None
            assert service.state["presentation"]["message"]
            await service.presentation_heartbeat("participant-A", True)
            fresh_signal(service)
            with pytest.raises(SessionConflict):
                await acknowledge(service, cue)
            assert service.state["phase"] == "connected"
            replacement = await prepare(service)
            assert replacement["id"] != cue["id"]
            assert (await acknowledge(service, replacement))["phase"] == "calibrating_closed"
        finally:
            await service.shutdown()
    asyncio.run(scenario())


def test_cancel_or_change_cue_mode_preserves_existing_baseline_and_algorithm(tmp_path):
    async def scenario():
        service = make_service(tmp_path)
        try:
            parameters = fit_calibration([feature(attention=.15)], [feature()], min_valid_windows=1)
            service.state["phase"] = "ready"
            service.state["calibration"].update(valid=True, parameters=parameters, baseline_id="existing-baseline", reused=True)
            calibration = copy.deepcopy(service.state["calibration"])
            before = compatibility(service.state)
            cue = await prepare(service)
            await command(service, "cancel_preparation")
            assert service.state["phase"] == "ready" and service.state["calibration"] == calibration
            with pytest.raises(SessionConflict):
                await acknowledge(service, cue)
            await prepare(service)
            changed = await command(service, "set_presentation", cue_mode="tone", sound_enabled=False, volume=0)
            assert changed["phase"] == "ready" and changed["presentation"]["cue_request"] is None
            assert changed["presentation"]["volume"] == 0 and changed["presentation"]["sound_enabled"] is False
            assert service.state["calibration"] == calibration and service.state["calibration_history"] == []
            assert compatibility(service.state) == before
            new_cue = await prepare(service)
            assert new_cue["mode"] == "tone", "Training music mute must not suppress the calibration prompt"
        finally:
            await service.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["calibrating_closed", "calibrating_open", "training"])
def test_lost_participant_pauses_active_phase_preserves_progress_and_needs_manual_resume(tmp_path, phase):
    async def scenario():
        service = make_service(tmp_path)
        try:
            await service.presentation_heartbeat("participant-A", True)
            service.state["phase"] = phase
            service.state["calibration"].update(closed_seconds=3, open_seconds=2)
            service.state["training"]["valid_seconds"] = 4
            snapshot = await service.presentation_heartbeat("participant-A", False)
            assert snapshot["phase"] == "paused" and snapshot["resume_phase"] == phase
            assert snapshot["presentation"]["reason"] == "participant_not_ready"
            assert snapshot["calibration"]["closed_seconds"] == 3
            assert snapshot["calibration"]["open_seconds"] == 2 and snapshot["training"]["valid_seconds"] == 4
            with pytest.raises(SessionConflict):
                await command(service, "resume")
            await service.presentation_heartbeat("participant-A", True)
            assert service.state["phase"] == "paused"
            fresh_signal(service)
            assert (await command(service, "resume"))["phase"] == phase
            service._presentation_lease["seen_mono"] -= 5.01
            assert service.snapshot()["phase"] == "paused"
            assert service.state["presentation"]["reason"] == "participant_unavailable"
        finally:
            await service.shutdown()
    asyncio.run(scenario())


def test_legacy_single_screen_still_starts_directly_but_requires_fresh_wearing_check(tmp_path):
    async def scenario():
        service = make_service(tmp_path, dual=False)
        try:
            fresh_signal(service)
            service._last_feedback_mono -= 3
            with pytest.raises(SessionConflict):
                await command(service, "calibrate_closed")
            fresh_signal(service)
            state = await command(service, "calibrate_closed")
            assert state["phase"] == "calibrating_closed"
            assert state["presentation"]["participant_ready"] is False and state["presentation"]["cue_request"] is None
            service._feature(feature(), "calibrating_closed")
            assert service.snapshot()["phase"] == "calibrating_closed"
            assert service.state["calibration"]["closed_seconds"] == 2
        finally:
            await service.shutdown()
    asyncio.run(scenario())


def test_wear_confirmation_uses_current_window_and_never_replaces_signal_quality(tmp_path):
    async def scenario():
        service = make_service(tmp_path)
        session_id = service.state["session_id"]
        try:
            await service.presentation_heartbeat("participant-A", True)
            for owner, request_session in (("participant-B", session_id), ("participant-A", "old-session")):
                with pytest.raises(SessionConflict):
                    await service.presentation_wear_confirmation(owner, "headband", True, request_session)
            with pytest.raises(SessionConflict):
                await service.presentation_wear_confirmation("participant-A", "headphones", True, session_id)
            await service.presentation_wear_confirmation("participant-A", "headband", True, session_id)
            confirmed = await service.presentation_wear_confirmation("participant-A", "headphones", True, session_id)
            assert confirmed["presentation"]["wear_confirmation"]["headphones"] is True
            assert confirmed["phase"] == "connected" and confirmed["quality"]["valid"] is False
            with pytest.raises(SessionConflict):
                await command(service, "calibrate_closed")
            service._presentation_lease["seen_mono"] -= 5.01
            with pytest.raises(SessionConflict):
                await service.presentation_wear_confirmation("participant-A", "headband", True, session_id)
            await service.presentation_heartbeat("participant-B", True)
            assert service.snapshot()["presentation"]["wear_confirmation"]["headband"] is False
        finally:
            await service.shutdown()
    asyncio.run(scenario())


def test_replaying_wear_steps_clears_dependents_but_not_calibration(tmp_path):
    async def scenario():
        service = make_service(tmp_path)
        session_id = service.state["session_id"]
        try:
            await service.presentation_heartbeat("participant-A", True)
            for step in ("headband", "headphones"):
                await service.presentation_wear_confirmation("participant-A", step, True, session_id)
            calibration = copy.deepcopy(service.state["calibration"])
            state = await service.presentation_wear_reset("headphones", session_id)
            assert state["presentation"]["wear_confirmation"]["headband"] is True
            assert state["presentation"]["wear_confirmation"]["headphones"] is False
            await service.presentation_wear_confirmation("participant-A", "headphones", True, session_id)
            state = await service.presentation_wear_reset("headband", session_id)
            assert not state["presentation"]["wear_confirmation"]["headband"]
            assert not state["presentation"]["wear_confirmation"]["headphones"]
            assert service.state["calibration"] == calibration
            for phase in ("preparing_closed", "calibrating_open", "training"):
                service.state["phase"] = phase
                with pytest.raises(SessionConflict):
                    await service.presentation_wear_reset("headband", session_id)
                with pytest.raises(SessionConflict):
                    await service.presentation_wear_confirmation("participant-A", "headband", True, session_id)
        finally:
            await service.shutdown()
    asyncio.run(scenario())


def test_initial_wear_confirmation_survives_first_creation_only(tmp_path):
    class QuietProvider:
        def connect(self): return {"source": "synthetic", "sample_rate": 256, "channels": ["TP9", "AF7", "AF8", "TP10"]}
        def read(self): return None
        def disconnect(self): pass

    async def scenario():
        service = SessionService(tmp_path, provider_factory=lambda request: QuietProvider())
        try:
            await service.presentation_heartbeat("participant-A", True)
            for step in ("headband", "headphones"):
                await service.presentation_wear_confirmation("participant-A", step, True)
            first = await service.create(SessionRequest(mode="synthetic", dual_screen=True, calibration_seconds=5))
            assert first["presentation"]["wear_confirmation"]["headband"] is True
            assert first["presentation"]["wear_confirmation"]["headphones"] is True
            await command(service, "finish")
            second = await service.create(SessionRequest(mode="synthetic", dual_screen=True, calibration_seconds=5))
            assert not second["presentation"]["wear_confirmation"]["headband"]
            assert not second["presentation"]["wear_confirmation"]["headphones"]
            assert second["presentation"]["participant_ready"] is True
        finally:
            await service.shutdown()
    asyncio.run(scenario())
