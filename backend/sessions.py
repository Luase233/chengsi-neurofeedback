"""Server-owned acquisition, protocol state, feedback gating and session evidence."""
import asyncio
import copy
import inspect
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .models import CommandRequest, SessionRequest
from .audio_profiles import MUSIC_FEEDBACK_RECORDING_SCOPE, music_feedback_profile
from .baselines import BaselineStore, compatibility
from .metrics import peak_metrics
from .providers import MuseProvider, ReplayProvider, SyntheticProvider
from .signal import CalibrationError, LiveProcessor, fit_calibration, score_feature
from .storage import SessionStore, clean, session_path, utc_now
from .training_plan import plan_snapshot, single_plan, refresh_totals, visual_scene

ACTIVE_PHASES = {"calibrating_closed", "calibrating_open", "training"}
PREPARING_PHASES = {"preparing_closed", "preparing_open", "preparing_training"}
TERMINAL_PHASES = {"idle", "completed", "error"}
DATA_TIMEOUT = 2.5
PRESENTATION_LEASE_SECONDS = 5.0


def empty_wear_confirmation():
    return {"headband": False, "headphones": False, "updated_at": None, "client_id": None}


class SessionConflict(Exception):
    pass


def idle_state():
    return {
        "session_id": None, "mode": None, "source": None, "phase": "idle", "participant_id": None,
        "connection": {"status": "disconnected", "message": "尚未连接", "device_name": None, "sample_rate": None, "channels": []},
        "quality": {"valid": False, "reasons": ["not_connected"], "channel_quality": {}},
        "calibration": {"closed_seconds": 0, "open_seconds": 0, "target_seconds": 60, "valid": False, "parameters": None,
                        "baseline_id": None, "reused": False, "trajectory": []},
        "training": {"elapsed_seconds": 0, "valid_seconds": 0, "target_seconds": 120, "wall_elapsed_seconds": 0,
                     "started_at": None, "ended_at": None, "target_basis": "accepted_source_time_coverage",
                     "elapsed_basis": "active_phase_wall_time_excluding_pause_and_disconnection"},
        "feedback": {"score": None, "meditation": None, "valid": False, "window_end": None, "bands": {}, "generated_at": None, "age_ms": None},
        "program_id": "clear-current-v04", "music_feedback_profile": None,
        "music_feedback_recording_scope": MUSIC_FEEDBACK_RECORDING_SCOPE,
        "summary": None, "error": None,
        "created_at": None, "updated_at": None, "provenance": {}, "algorithm_version": None,
        "attempts": [], "calibration_history": [], "resume_phase": None,
        "protocol": single_plan(),
        "presentation": {"dual_screen": False, "cue_mode": "voice", "volume": .35, "sound_enabled": True,
                         "training_scene": "lake-trees", "scene_metadata": visual_scene("lake-trees"),
                         "cue_request": None, "participant_ready": False, "participant_client_id": None,
                         "client_id": None, "visible": None, "message": "", "reason": None,
                         "wear_confirmation": empty_wear_confirmation()},
    }


class SessionService:
    def __init__(self, data_dir: Path, provider_factory=None, processor_factory=None, reconnect_policy="preserve"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._baselines = BaselineStore(self.data_dir)
        self.state = idle_state()
        self.lock = asyncio.Lock()
        self._seq = 0
        self._provider_factory = provider_factory or self._make_provider
        self._processor_factory = processor_factory or LiveProcessor
        self.reconnect_policy = reconnect_policy
        self._request = None
        self._store = None
        self._provider = None
        self._processor = None
        self._task = None
        self._stop_requested = False
        self._generation = 0
        self._phase_before_pause = None
        self._phase_before_disconnect = None
        self._command_results = {}
        self._last_data_mono = None
        self._last_feedback_mono = None
        self._clock_mono = time.monotonic()
        self._last_save_mono = 0
        self._closed_features = []
        self._open_features = []
        self._coverage_end = None
        self._calibration_version = 0
        self._score_weight = self._score_sum = self._meditation_sum = 0.0
        self._trajectory = []
        self._drop_next_chunk = False
        self._provider_connected = False
        self._calibration_features = []
        self._training_started_mono = None
        self._training_origin = None
        self._trajectory_segment = 0
        self._last_training_end = None
        self._baseline_record = None
        self._program_segments = []
        self._visual_segments = []
        self._round_sample_start = None
        self._calibration_requested = False
        # The participant window may unlock its audio before staff creates a session.
        # Keep this lease across sessions, without putting it in baseline compatibility.
        self._presentation_lease = None
        self._preparation_return_phase = None

    def _presentation_status(self, now=None):
        now = time.monotonic() if now is None else now
        lease = self._presentation_lease
        fresh = bool(lease and now - lease["seen_mono"] < PRESENTATION_LEASE_SECONDS)
        client = lease["client_id"] if fresh else None
        return {"participant_ready": bool(fresh and lease["ready"]),
                "participant_client_id": client, "client_id": client,
                "visible": lease["visible"] if fresh else None,
                "heartbeat_age_ms": round(max(0, now - lease["seen_mono"]) * 1000, 1) if lease else None}

    async def presentation_heartbeat(self, client_id: str, ready: bool, visible: bool = True):
        """Claim/refresh the one participant audio window; returns a full snapshot.

        A second live client gets SessionConflict. Visibility is diagnostic only:
        a background window on the same computer can still play and acknowledge.
        This reports frontend readiness, not proof of audible hardware playback.
        """
        if not isinstance(client_id, str) or not 1 <= len(client_id.strip()) <= 100 or client_id != client_id.strip():
            raise ValueError("Invalid participant client_id")
        if not isinstance(ready, bool) or not isinstance(visible, bool):
            raise ValueError("Participant ready and visible must be booleans")
        async with self.lock:
            self._advance_clock()  # Expired leases must pause before a new claim.
            previous = self._presentation_status()
            self._require(previous["client_id"] in {None, client_id}, "已有其他被试屏接管声音，请关闭该窗口或等待其连接超时")
            wear = self.state["presentation"]["wear_confirmation"]
            if wear["client_id"] not in {None, client_id}:
                self.state["presentation"]["wear_confirmation"] = empty_wear_confirmation()
            self._presentation_lease = {"client_id": client_id, "ready": ready, "visible": visible,
                                        "seen_mono": time.monotonic()}
            changed = self._guard_presentation()
            if self._store and (changed or previous["client_id"] != client_id or previous["participant_ready"] != ready):
                self._store.event("participant_presentation_status", client_id=client_id, ready=ready, visible=visible,
                                  evidence="client_report")
                self._save()
            return self.snapshot()

    async def presentation_wear_confirmation(self, client_id, step, confirmed, session_id=None):
        """Record the participant's button press, never a sensor-quality verdict."""
        if step not in {"headband", "headphones"} or not isinstance(confirmed, bool):
            raise ValueError("Invalid wear confirmation")
        async with self.lock:
            self._require_wear_editable(session_id)
            self._require(bool(client_id) and client_id == self._presentation_status()["client_id"],
                          "只有当前被试屏可以确认佩戴，请先重新连接被试屏")
            wear = self.state["presentation"]["wear_confirmation"]
            if confirmed and step == "headphones":
                self._require(wear["headband"], "请先确认头环佩戴，再确认耳机佩戴")
            wear[step] = confirmed
            if step == "headband" and not confirmed:
                wear["headphones"] = False
            wear.update(updated_at=utc_now(), client_id=client_id)
            self._save_wear_event("participant_wear_confirmed", step=step, confirmed=confirmed,
                                  client_id=client_id, evidence="participant_self_report")
            return self.snapshot()

    async def presentation_wear_reset(self, step, session_id=None):
        """Staff replaying a wearing step invalidates it and dependent steps."""
        if step not in {"headband", "headphones"}:
            raise ValueError("Invalid wear step")
        async with self.lock:
            self._require_wear_editable(session_id)
            wear = self.state["presentation"]["wear_confirmation"]
            wear[step] = False
            if step == "headband":
                wear["headphones"] = False
            wear.update(updated_at=utc_now())
            self._save_wear_event("participant_wear_reset", step=step, evidence="operator_request")
            return self.snapshot()

    def _require_wear_editable(self, session_id):
        self._require(session_id == self.state["session_id"], "会话已变化，请刷新佩戴状态后重试")
        self._require(self.state["phase"] not in ACTIVE_PHASES | PREPARING_PHASES,
                      "请先暂停前测或训练，再重新检查佩戴")

    def _save_wear_event(self, kind, **details):
        if self._store:
            self._store.event(kind, **details,
                              wear_confirmation=copy.deepcopy(self.state["presentation"]["wear_confirmation"]))
            self._save()

    def _guard_presentation(self):
        presentation = self.state["presentation"]
        status = self._presentation_status()
        presentation.update(status)
        if not presentation["dual_screen"] or status["participant_ready"]:
            return False
        reason = "participant_not_ready" if status["client_id"] else "participant_unavailable"
        message = "被试屏声音尚未就绪，请在被试窗口启用声音后手动继续" if status["client_id"] else "被试屏连接已失效，请恢复被试窗口后手动继续"
        if self.state["phase"] in PREPARING_PHASES:
            self._cancel_preparation(reason, message)
            return True
        if self.state["phase"] in ACTIVE_PHASES:
            self._phase_before_pause = self.state["phase"]
            presentation.update(reason=reason, message=message)
            self._phase("paused", reason)
            return True
        return False

    def _cancel_preparation(self, reason, message):
        request = copy.deepcopy(self.state["presentation"].get("cue_request"))
        return_phase = self._preparation_return_phase or "connected"
        self._phase(return_phase, reason, reset_signal=False)
        self.state["presentation"].update(reason=reason, message=message)
        if self._store:
            prefix = "training" if request and request.get("stage") == "training" else "calibration"
            self._store.event(prefix + "_preparation_cancelled", reason=reason, cue_request=request)

    def _prepare_calibration(self, stage):
        self._require(self._presentation_status()["participant_ready"], "请先在被试屏启用声音，待被试屏就绪后开始")
        presentation = self.state["presentation"]
        self._preparation_return_phase = self.state["phase"]
        request = {"id": str(uuid4()), "session_id": self.state["session_id"], "stage": stage,
                   "kind": "start", "mode": presentation["cue_mode"], "created_at": utc_now(),
                   "client_id": self._presentation_status()["client_id"]}
        presentation.update(cue_request=request, reason=None, message="正在播放开始引导；完整提示结束后才开始本轮计时" if stage == "training" else "正在等待被试屏播放开始提示；提示结束后才采集")
        # Continue the existing quality monitor during speech. Resetting the EEG
        # filter here would make a short tone fail the final fresh-window check.
        event = ("training" if stage == "training" else "calibration") + "_cue_requested"
        self._phase("preparing_" + stage, event, reset_signal=False)
        self._store.event(event, cue_request=copy.deepcopy(request))

    def _refresh_protocol(self, active=None):
        current = {**self.state["training"],
                   "mean_score": self._score_sum / self._score_weight if self._score_weight else None,
                   "mean_meditation": self._meditation_sum / self._score_weight if self._score_weight else None}
        refresh_totals(self.state["protocol"], current=current,
                       active=self.state["phase"] not in TERMINAL_PHASES if active is None else active)

    def _begin_training(self):
        self.state["presentation"].update(reason=None, message="")
        self._reset_training()
        self._training_started_mono = time.monotonic()
        started = utc_now()
        self.state["training"]["started_at"] = started
        protocol = self.state["protocol"]
        protocol["rest_until"] = None
        protocol["round_number"] = len(protocol.get("completed_rounds", [])) + 1
        self.state["training"]["round_number"] = protocol["round_number"]
        self._program_segments = [{"program_id": self.state["program_id"], "at": started,
                                   "wall_elapsed_seconds": 0, "phase": "training",
                                   "music_feedback_profile": copy.deepcopy(self.state["music_feedback_profile"]),
                                   "music_feedback_recording_scope": MUSIC_FEEDBACK_RECORDING_SCOPE}]
        self._visual_segments = [{**visual_scene(self.state["presentation"]["training_scene"]),
                                  "at": started, "wall_elapsed_seconds": 0, "phase": "training"}]
        self._round_sample_start = self._store.sample_index
        self._phase("training", "training_started")
        self._store.event("training_round_started", round_number=protocol["round_number"],
                          training_date=protocol.get("training_date"),
                          sample_index=self._round_sample_start,
                          visual_scene=copy.deepcopy(self._visual_segments[0]))

    def _begin_calibration(self, stage):
        if stage == "closed":
            if self.state["calibration"]["parameters"]:
                self.state["calibration_history"].append(copy.deepcopy(self.state["calibration"]))
            self._closed_features, self._open_features = [], []
            self._calibration_features = []
            self._baseline_record = None
            self._calibration_requested = True
            self.state["calibration"].update(closed_seconds=0, open_seconds=0, valid=False, parameters=None,
                                            baseline_id=None, reused=False, trajectory=[], source_session_id=None, created_at=None)
        else:
            self._open_features = []
            self._calibration_features = [feature for feature in self._calibration_features if feature.get("stage") == "calibrating_closed"]
            self.state["calibration"].update(open_seconds=0, valid=False, parameters=None)
        self.state["presentation"].update(reason=None, message="")
        self._phase("calibrating_" + stage, stage + "_calibration_started")

    def _make_provider(self, request):
        if request.mode == "live":
            return MuseProvider(address=request.device_address, timeout=10)
        if request.mode == "synthetic":
            return SyntheticProvider(seed=42)
        return ReplayProvider(session_path(self.data_dir, request.replay_session_id), speed=1.0)

    def snapshot(self):
        self._guard_presentation()
        self._seq += 1
        now = time.monotonic()
        if self._training_started_mono is not None and not self.state["training"].get("ended_at"):
            self.state["training"]["wall_elapsed_seconds"] = max(0, now - self._training_started_mono)
        self._refresh_protocol()
        state = copy.deepcopy(self.state)
        state["presentation"].update(self._presentation_status(now))
        age = (now - self._last_feedback_mono) * 1000 if self._last_feedback_mono is not None else None
        state["feedback"]["age_ms"] = round(age, 1) if age is not None else None
        stale = age is None or age > DATA_TIMEOUT * 1000
        if state["phase"] != "training" or stale or not state["quality"]["valid"]:
            state["feedback"].update(score=None, meditation=None, valid=False)
        if state["phase"] == "training" and stale and state["quality"]["valid"]:
            state["quality"].update(valid=False, reasons=["feedback_stale"])
        state["seq"] = self._seq
        state["sent_at"] = utc_now()
        return clean(state)

    async def create(self, request: SessionRequest):
        async with self.lock:
            if self.state["phase"] not in TERMINAL_PHASES:
                raise SessionConflict("已有活动会话，请先结束后再创建")
            protocol = (plan_snapshot(self.data_dir, request.participant_id, request.mode, request.training_seconds)
                        if request.protocol_plan == "daily" else single_plan(request.training_seconds))
            if request.protocol_plan == "daily":
                self._require(not protocol["day_complete"], "该被试今天已完成4轮训练，请使用单轮试听或下个训练日继续")
                self._require(not protocol["cycle_complete"], "已完成16个训练日，请进行训练后迁移测试；额外试听请使用单轮模式")
            await self._stop_acquisition()
            if self._store:
                self._store.close()
            # Initial preparation may happen before staff creates the first session.
            # Later sessions need fresh human confirmation, even with a reused baseline.
            initial_wear = (copy.deepcopy(self.state["presentation"]["wear_confirmation"])
                            if self.state["session_id"] is None else empty_wear_confirmation())
            if initial_wear["client_id"] != self._presentation_status()["client_id"]:
                initial_wear = empty_wear_confirmation()
            self._request = request
            self.state = idle_state()
            self.state["presentation"]["wear_confirmation"] = initial_wear
            now = utc_now()
            self.state.update(session_id=str(uuid4()), mode=request.mode, source=request.mode,
                              participant_id=request.participant_id, program_id=request.program_id,
                              music_feedback_profile=music_feedback_profile(request.program_id),
                              created_at=now, updated_at=now,
                              provenance={"mode": request.mode, "is_synthetic": request.mode == "synthetic", "replay_session_id": request.replay_session_id})
            self.state["calibration"]["target_seconds"] = request.calibration_seconds
            self.state["training"]["target_seconds"] = request.training_seconds
            self.state["protocol"] = protocol
            self.state["presentation"]["dual_screen"] = request.dual_screen
            self.state["presentation"].update(training_scene=request.training_scene, scene_metadata=visual_scene(request.training_scene))
            self._preparation_return_phase = None
            self._store = SessionStore(self.data_dir, self.state["session_id"])
            self._command_results = {}
            self._closed_features, self._open_features = [], []
            self._calibration_features = []
            self._baseline_record = None
            self._calibration_requested = False
            self._calibration_version = 0
            self._reset_training()
            self._store.event("session_created", request=request.model_dump(),
                              music_feedback_profile=copy.deepcopy(self.state["music_feedback_profile"]),
                              music_feedback_recording_scope=MUSIC_FEEDBACK_RECORDING_SCOPE)
            await self._connect()
            if protocol.get("completed_round_count") and self.state["phase"] == "ready":
                self._phase("resting", "same_day_training_resumed", reset_signal=False)
            self._save()
            return self.snapshot()

    async def _connect(self):
        self._phase("connecting", "connect_requested")
        self.state["error"] = None
        self.state["connection"].update(status="connecting", message="正在连接数据源")
        self._invalidate("connecting")
        try:
            self._provider = self._provider_factory(self._request)
            metadata = await asyncio.to_thread(self._provider.connect)
            if inspect.isawaitable(metadata):
                metadata = await metadata
            metadata = dict(metadata or {})
            rate = metadata.get("sample_rate", metadata.get("rate", 256))
            channels = list(metadata.get("channels", ("TP9", "AF7", "AF8", "TP10")))
            self._store.configure_raw(channels)
            processor_options = {"sample_rate": rate}
            if self._request.mode == "replay":
                recorded_calibration = metadata.get("recorded_calibration")
                recorded_config = metadata.get("recorded_algorithm_config")
                if not isinstance(recorded_calibration, dict) or not recorded_calibration.get("valid") or not recorded_calibration.get("parameters"):
                    raise ValueError("该记录没有有效个体校准，不能作为训练回放")
                if not isinstance(recorded_config, dict) or "smooth_alpha" not in recorded_config:
                    raise ValueError("该记录缺少算法配置，无法按原流程重算")
                processor_options.update(smooth_alpha=recorded_config["smooth_alpha"], quality=recorded_config.get("quality"))
                profile = recorded_config.get("profile", recorded_config.get("algorithm_profile"))
                if profile and "profile" in inspect.signature(self._processor_factory).parameters:
                    processor_options["profile"] = profile
                self.state["calibration"] = copy.deepcopy(recorded_calibration)
                self._calibration_version = recorded_calibration.get("version", 1)
                self.state["training"]["target_seconds"] = None
                self.state["training"]["elapsed_basis"] = "replay_wall_clock_including_recorded_gaps_excluding_user_pause"
                self.state["provenance"]["selected_attempt"] = metadata.get("selected_attempt", "latest_training_attempt")
            self._processor = self._processor_factory(**processor_options)
            self.state["algorithm_config"] = clean(getattr(self._processor, "config", {}))
            if self._request.mode == "replay":
                self.state["algorithm_config"]["calibration_method"] = recorded_calibration["parameters"].get("version") or "sorted-rest-minmax-v1.0"
            self._provider_connected = True
            self.state["connection"] = {"status": "connected", "message": "已连接，正在检查有效数据", "device_name": metadata.get("device_name", metadata.get("name", self._request.mode)), "sample_rate": rate, "channels": channels, "units": metadata.get("units", "uV")}
            self.state["device_metadata"] = clean(metadata)
            self.state["provider_metadata"] = clean(metadata)
            self.state["source"] = metadata.get("source", self._request.mode)
            self.state["provenance"].update(source=self.state["source"], original_source=metadata.get("original_source"), device_metadata=clean(metadata))
            retained = self.state["calibration"].get("valid") and self._baseline_record
            if retained and self._baseline_record["compatibility"] != compatibility(self.state):
                self.state["calibration"] = idle_state()["calibration"]
                self.state["calibration"]["target_seconds"] = self._request.calibration_seconds
                self._baseline_record = None
                self._store.event("baseline_incompatible", reason="device_or_algorithm_configuration_changed")
            self._phase("connected", "source_connected")
            if self._request.mode == "replay":
                self._phase("ready", "recording_loaded")
            elif not self.state["calibration"]["valid"] and not self._calibration_requested:
                baseline = self._baselines.find(self.state)
                if baseline:
                    self._baseline_record = baseline
                    self.state["calibration"] = self._baselines.apply(baseline, reused=True)
                    self._calibration_version = self.state["calibration"].get("version", 1)
                    self._store.save_baseline(baseline)
                    self._store.event("baseline_reused", baseline_id=baseline["baseline_id"], source_session_id=baseline["source_session_id"])
                    self._phase("ready", "personal_baseline_loaded")
            self._last_data_mono = time.monotonic()
            self._last_feedback_mono = None
            self._clock_mono = time.monotonic()
            self._stop_requested = False
            self._task = asyncio.create_task(self._acquisition_loop(), name="eeg-session-" + self.state["session_id"])
        except Exception as error:
            self.state["error"] = f"连接失败：{type(error).__name__}: {error}"
            self.state["connection"].update(status="error", message=self.state["error"])
            self._phase("error", "connect_failed")
            self._invalidate("connect_failed")
            self._store.event("connection_error", error=self.state["error"])
            if self._provider:
                try:
                    await asyncio.to_thread(self._provider.disconnect)
                except Exception:
                    pass
            self._provider_connected = False

    async def command(self, session_id, command: CommandRequest):
        async with self.lock:
            if session_id != self.state["session_id"]:
                raise SessionConflict("该会话不是当前活动会话")
            if command.command_id in self._command_results:
                if self._command_results[command.command_id] != command.model_dump():
                    raise SessionConflict("command_id 已用于其他命令")
                return self.snapshot()
            action = command.command
            self._advance_clock()
            phase = self.state["phase"]
            if action == "set_program":
                self._require(phase not in TERMINAL_PHASES, "已结束的会话不能修改训练歌曲；请选择新训练")
                self.state["program_id"] = command.program_id
                self.state["music_feedback_profile"] = music_feedback_profile(command.program_id)
                if self._training_started_mono is not None:
                    self._program_segments.append({"program_id": command.program_id, "at": utc_now(),
                                                   "wall_elapsed_seconds": self.state["training"]["wall_elapsed_seconds"],
                                                   "phase": phase,
                                                   "music_feedback_profile": copy.deepcopy(self.state["music_feedback_profile"]),
                                                   "music_feedback_recording_scope": MUSIC_FEEDBACK_RECORDING_SCOPE})
            elif action == "calibrate_closed":
                self._require(self.state["mode"] != "replay", "回放使用录制时的校准参数，无需重新校准")
                self._require(phase in {"connected", "closed_complete", "ready", "paused", "resting"} and self.state["connection"]["status"] == "connected", "当前阶段不能开始闭眼校准")
                self._require(self._fresh_signal(), "请先调整四路触点，等待完整有效脑电窗口，再开始闭眼前测")
                if self.state["presentation"]["dual_screen"]:
                    self._prepare_calibration("closed")
                else:
                    self._begin_calibration("closed")
            elif action == "calibrate_open":
                self._require(phase == "closed_complete", "请先完成闭眼校准")
                self._require(self._fresh_signal(), "请先调整四路触点，等待完整有效脑电窗口，再开始睁眼前测")
                if self.state["presentation"]["dual_screen"]:
                    self._prepare_calibration("open")
                else:
                    self._begin_calibration("open")
            elif action == "cue_finished":
                self._require(self.state["presentation"]["dual_screen"] and phase in PREPARING_PHASES, "当前没有等待确认的开始提示，请由工作人员重新开始")
                cue = self.state["presentation"].get("cue_request")
                self._require(bool(cue) and cue["id"] == command.cue_request_id, "开始提示已失效，不能使用旧提示启动采集")
                self._require(cue["client_id"] == command.client_id == self._presentation_status()["client_id"], "只有当前被试屏可以确认开始提示")
                if not self._presentation_status()["participant_ready"] or not self._fresh_signal():
                    self._cancel_preparation("preparation_signal_unavailable", "开始提示已结束，但四路信号尚未就绪；请调整佩戴后重新开始")
                    self._save()
                    raise SessionConflict(self.state["presentation"]["message"])
                self._require(phase == "preparing_" + cue["stage"], "开始提示与当前阶段不匹配")
                prefix = "training" if cue["stage"] == "training" else "calibration"
                self._store.event(prefix + "_cue_acknowledged", cue_request_id=cue["id"], client_id=command.client_id,
                                  stage=cue["stage"], evidence="participant_client_report")
                if cue["stage"] == "training":
                    self._begin_training()
                else:
                    self._begin_calibration(cue["stage"])
            elif action == "cancel_preparation":
                self._require(phase in PREPARING_PHASES, "当前没有待取消的开始提示")
                self._cancel_preparation("preparation_cancelled", "已取消开始提示，请重新检查佩戴后开始")
            elif action == "set_presentation":
                self._require(phase not in TERMINAL_PHASES, "已结束的会话不能修改呈现配置")
                presentation = self.state["presentation"]
                if command.training_scene is not None:
                    self._require(phase not in ACTIVE_PHASES | PREPARING_PHASES,
                                  "请在开始前、轮间休息或暂停时更换训练画面，避免训练中突然切换")
                    if command.training_scene != presentation["training_scene"]:
                        presentation.update(training_scene=command.training_scene, scene_metadata=visual_scene(command.training_scene))
                        segment = {**visual_scene(command.training_scene), "at": utc_now(),
                                   "wall_elapsed_seconds": self.state["training"]["wall_elapsed_seconds"], "phase": phase}
                        if self._training_started_mono is not None and not self.state["training"].get("ended_at"):
                            self._visual_segments.append(segment)
                        self._store.event("training_scene_changed", **segment)
                if phase in PREPARING_PHASES and command.cue_mode is not None and command.cue_mode != presentation["cue_mode"]:
                    self._cancel_preparation("cue_mode_changed", "提示方式已改变，请重新开始")
                for key in ("cue_mode", "volume", "sound_enabled"):
                    value = getattr(command, key)
                    if value is not None:
                        presentation[key] = value
            elif action == "start_training":
                self._require(phase in {"ready", "resting"} and self.state["calibration"]["valid"], "个体校准尚未通过或当前轮次尚未结束")
                self._refresh_protocol()
                self._require(self.state["protocol"]["rest_remaining_seconds"] <= 0, "请完成60秒轮间休息，再手动开始下一轮")
                self._require(not self.state["protocol"].get("day_complete"), "今天的4轮训练已完成")
                self._require(self.state["mode"] == "replay" or self._fresh_signal(), "请等待完整有效脑电窗口，再开始训练")
                self._require(not self.state["presentation"]["dual_screen"] or self._presentation_status()["participant_ready"], "请先在被试屏启用声音，再开始训练")
                if self.state["presentation"]["dual_screen"] and self.state["mode"] != "replay":
                    self._prepare_calibration("training")
                else:
                    self._begin_training()
            elif action == "pause":
                self._require(phase in ACTIVE_PHASES, "当前阶段不能暂停")
                self._phase_before_pause = phase
                self._phase("paused", "user_paused")
            elif action == "resume":
                self._require(phase == "paused" and self._phase_before_pause in ACTIVE_PHASES, "当前没有可恢复的阶段")
                self._require(self.state["mode"] == "replay" or self.state["quality"]["valid"] and self._last_data_mono is not None and time.monotonic() - self._last_data_mono < DATA_TIMEOUT, "请等待重连或暂停后的完整有效窗口，再手动继续")
                self._require(not self.state["presentation"]["dual_screen"] or self._presentation_status()["participant_ready"], "请恢复被试屏声音后，再手动继续")
                self.state["presentation"].update(reason=None, message="")
                self._phase(self._phase_before_pause, "user_resumed")
            elif action == "finish":
                self._finish("user_finished")
            elif action in {"disconnect", "inject_dropout"}:
                if action == "inject_dropout":
                    self._require(self.state["mode"] == "synthetic", "模拟断连只允许合成数据模式")
                self._require(phase not in TERMINAL_PHASES, "当前没有可断开的会话")
                self._disconnect("synthetic_dropout" if action == "inject_dropout" else "user_disconnected")
            elif action == "reconnect":
                self._require(phase in {"disconnected", "error"}, "当前状态无需重连")
                await self._stop_acquisition()
                if self.reconnect_policy == "recalibrate":
                    if self.state["training"]["elapsed_seconds"] > 0:
                        self.state["attempts"].append(self._summary("interrupted_before_recalibration"))
                    self._closed_features, self._open_features = [], []
                    self.state["calibration"].update(closed_seconds=0, open_seconds=0, valid=False, parameters=None)
                    self._reset_training()
                await self._connect()
                if self.reconnect_policy == "preserve" and self.state["phase"] in {"connected", "ready"} and self.state["calibration"]["valid"]:
                    return_to_training = self._phase_before_disconnect == "training" or self._phase_before_disconnect == "paused" and self._phase_before_pause == "training"
                    self._phase_before_pause = "training" if return_to_training else "calibrating_open"
                    if return_to_training:
                        self._phase("paused", "reconnected_manual_resume_required")
                    else:
                        self._phase("resting" if self.state["protocol"].get("completed_round_count") else "ready", "reconnected_calibration_retained")
            self._command_results[command.command_id] = command.model_dump()
            music_metadata = ({"music_feedback_profile": copy.deepcopy(self.state["music_feedback_profile"]),
                               "music_feedback_recording_scope": MUSIC_FEEDBACK_RECORDING_SCOPE}
                              if action == "set_program" else {})
            self._store.event("command", **command.model_dump(), resulting_phase=self.state["phase"], **music_metadata)
            self._save()
            if self._stop_requested:
                await self._stop_acquisition()
            return self.snapshot()

    @staticmethod
    def _require(condition, message):
        if not condition:
            raise SessionConflict(message)

    def _fresh_signal(self):
        return (self.state["connection"]["status"] == "connected" and self.state["quality"]["valid"]
                and self._last_feedback_mono is not None and time.monotonic() - self._last_feedback_mono < DATA_TIMEOUT)

    def _phase(self, phase, reason, reset_signal=True):
        previous = self.state["phase"]
        if previous == "training" and phase != "training":
            self._trajectory_segment += 1
            if self._trajectory:
                self._trajectory.append({"time": self._trajectory[-1]["time"], "score": None, "meditation": None,
                                         "valid": False, "segment": self._trajectory_segment, "reason": reason,
                                         "valid_duration": 0})
            self._last_training_end = None
        self.state["phase"] = phase
        if phase not in PREPARING_PHASES:
            self.state["presentation"]["cue_request"] = None
            self._preparation_return_phase = None
        self.state["resume_phase"] = self._phase_before_pause if phase == "paused" else None
        self.state["updated_at"] = utc_now()
        self._generation += 1
        self._coverage_end = None
        self._clock_mono = time.monotonic()
        if reset_signal:
            self._drop_next_chunk = True
        if self._processor and reset_signal:
            self._processor.reset(reason=reason)
        if self._provider and hasattr(self._provider, "set_stage"):
            self._provider.set_stage(phase)
        if reset_signal:
            self._invalidate(reason)
        if self._store:
            self._store.event("phase", previous=previous, phase=phase, reason=reason,
                              sample_index=self._store.sample_index, calibration_version=self._calibration_version)

    def _invalidate(self, reason):
        self.state["quality"].update(valid=False, reasons=[reason])
        self.state["feedback"].update(score=None, meditation=None, valid=False)
        self._last_feedback_mono = None

    def _reset_training(self):
        self.state["training"].update(elapsed_seconds=0, valid_seconds=0, wall_elapsed_seconds=0,
                                     started_at=None, ended_at=None)
        self._training_started_mono = None
        self._training_origin = None
        self._trajectory_segment = 0
        self._last_training_end = None
        self._program_segments = []
        self._visual_segments = []
        self._round_sample_start = None
        self._score_weight = self._score_sum = self._meditation_sum = 0.0
        self._trajectory = []
        self.state["summary"] = None

    def _advance_clock(self):
        now = time.monotonic()
        delta = max(0, now - self._clock_mono)
        self._clock_mono = now
        if self.state["phase"] == "training":
            self.state["training"]["elapsed_seconds"] += delta
        if self._training_started_mono is not None and not self.state["training"].get("ended_at"):
            self.state["training"]["wall_elapsed_seconds"] = max(0, now - self._training_started_mono)
        self._guard_presentation()

    async def _acquisition_loop(self):
        provider = self._provider
        try:
            while not self._stop_requested:
                generation, phase = self._generation, self.state["phase"]
                try:
                    chunk = await asyncio.to_thread(provider.read)
                except Exception as error:
                    self._disconnect("acquisition_error", str(error))
                    break
                self._advance_clock()
                if chunk is not None and len(chunk.timestamps):
                    self._last_data_mono = time.monotonic()
                    self._store.raw(chunk.timestamps, chunk.samples_uv, phase, package_numbers=chunk.metadata.get("package_numbers"))
                    if generation == self._generation and not self._stop_requested:
                        if self._drop_next_chunk:
                            self._drop_next_chunk = False
                        else:
                            for feature in self._processor.push(chunk):
                                if generation != self._generation or self._stop_requested:
                                    break
                                self._feature(feature, phase)
                elif getattr(provider, "eof", False):
                    self._finish("replay_ended")
                    break
                elif self.state["mode"] != "replay" and self._last_data_mono is not None and time.monotonic() - self._last_data_mono >= DATA_TIMEOUT:
                    self._disconnect("data_timeout", "超过 2.5 秒未收到新脑电，需手动重连")
                    break
                if time.monotonic() - self._last_save_mono >= 1:
                    self._save()
                await asyncio.sleep(0.025)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.state["error"] = f"数据处理失败：{type(error).__name__}: {error}"
            self._disconnect("processing_error", self.state["error"])
        finally:
            if self._provider_connected:
                try:
                    await asyncio.to_thread(provider.disconnect)
                except Exception as error:
                    self._store.event("disconnect_error", error=str(error))
                self._provider_connected = False
            if self.state["phase"] == "completed":
                self.state["connection"].update(status="disconnected", message="会话已结束")
            self._save()

    def _feature(self, feature, phase):
        feature = clean(dict(feature))
        valid = bool(feature.get("valid"))
        self.state["algorithm_version"] = feature.get("algorithm_version")
        self.state["quality"] = {"valid": valid, "reasons": feature.get("reasons", []), "channel_quality": feature.get("channel_quality", {})}
        feature["score"] = feature["meditation"] = None
        feature["calibration_method"] = self.state.get("algorithm_config", {}).get("calibration_method")
        if phase == "training" and valid and self.state["calibration"]["valid"]:
            feature = clean(score_feature(feature, self.state["calibration"]["parameters"]))
        start = feature.get("window_start")
        end = feature.get("window_end")
        if not all(isinstance(value, (float, int)) and math.isfinite(value) for value in (start, end)) or end < start:
            valid = False
            start = end = None
        else:
            start, end = float(start), float(end)
        feature["valid"] = valid
        if not valid:
            feature["score"] = feature["meditation"] = None
        seconds = 0.0
        contribution_start = start
        if valid:
            contribution_start = max(start, self._coverage_end if self._coverage_end is not None else start)
            seconds = max(0, end - contribution_start)
        # Every processed window advances the watermark: a recovered window must
        # not retrospectively count the part already classified as invalid.
        if end is not None:
            self._coverage_end = max(end, self._coverage_end if self._coverage_end is not None else end)
        if phase == "training":
            target = self.state["training"]["target_seconds"]
            if target is not None:
                seconds = min(seconds, max(0, target - self.state["training"]["valid_seconds"]))
            feature.update(valid_duration=seconds, baseline_id=self.state["calibration"].get("baseline_id"),
                           contribution_start=contribution_start,
                           contribution_end=contribution_start + seconds if contribution_start is not None else None)
        self._store.feature(feature, phase, self._calibration_version)
        self.state["feedback"] = {"score": feature.get("score") if phase == "training" and valid else None,
                                  "meditation": feature.get("meditation") if phase == "training" and valid else None,
                                  "valid": phase == "training" and valid and feature.get("score") is not None,
                                  "window_end": end, "bands": feature.get("bands", {}), "generated_at": utc_now(), "age_ms": 0}
        self._last_feedback_mono = time.monotonic()
        target = self.state["calibration"]["target_seconds"]
        if phase in {"calibrating_closed", "calibrating_open"}:
            self._calibration_features.append({**feature, "stage": phase})
        if phase == "calibrating_closed":
            if valid:
                self._closed_features.append(feature)
                self.state["calibration"]["closed_seconds"] = min(target, self.state["calibration"]["closed_seconds"] + seconds)
            if self.state["calibration"]["closed_seconds"] >= target - 1e-6:
                self._phase("closed_complete", "closed_calibration_complete")
                self._save()
        elif phase == "calibrating_open":
            if valid:
                self._open_features.append(feature)
                self.state["calibration"]["open_seconds"] = min(target, self.state["calibration"]["open_seconds"] + seconds)
            if self.state["calibration"]["open_seconds"] >= target - 1e-6:
                try:
                    minimum = 20 if self.state["mode"] == "live" or target >= 20 else 3
                    parameters = fit_calibration(self._closed_features, self._open_features, min_valid_windows=minimum)
                    self._calibration_version += 1
                    self.state["calibration"].update(valid=True, parameters=clean(parameters), version=self._calibration_version)
                    baseline = self._baselines.save(self.state, self._calibration_features)
                    self._baseline_record = baseline
                    self.state["calibration"] = self._baselines.apply(baseline, reused=False)
                    self._store.save_baseline(baseline)
                    self._phase("ready", "calibration_passed")
                    self._store.event("calibration", parameters=clean(parameters), version=self._calibration_version)
                except CalibrationError as error:
                    self.state["calibration"].update(valid=False, parameters=None)
                    self.state["error"] = f"个体校准未通过：{error}"
                    self._open_features = []
                    self.state["calibration"]["open_seconds"] = 0
                    self._phase("closed_complete", "calibration_rejected")
                    self._store.event("calibration_rejected", reason=str(error))
                self._save()
        elif phase == "training":
            scored = valid and feature.get("score") is not None
            if self._training_origin is None and start is not None:
                self._training_origin = start
            if (not scored or self._last_training_end is not None and end is not None
                    and end - self._last_training_end > 1.5):
                self._trajectory_segment += 1
            point = {"time": max(0, end - self._training_origin) if end is not None and self._training_origin is not None
                     else (self._trajectory[-1]["time"] if self._trajectory else 0),
                     "score": feature.get("score") if scored else None,
                     "meditation": feature.get("meditation") if scored else None,
                     "valid": bool(scored), "valid_duration": seconds if scored else 0,
                     "segment": self._trajectory_segment, "window_start": start, "window_end": end,
                     "reasons": feature.get("reasons", [])}
            if scored:
                score, meditation = float(feature["score"]), float(feature["meditation"])
                self.state["training"]["valid_seconds"] += seconds
                self._score_weight += seconds
                self._score_sum += score * seconds
                self._meditation_sum += meditation * seconds
                point.update(contribution_start=contribution_start - self._training_origin,
                             contribution_end=contribution_start + seconds - self._training_origin)
                # A final partial contribution is displayed at its accepted end.
                if seconds > 0:
                    point["time"] = point["contribution_end"]
            self._trajectory.append(point)
            self._last_training_end = end if scored else None
            training_target = self.state["training"]["target_seconds"]
            if training_target is not None and self.state["training"]["valid_seconds"] >= training_target - 1e-9:
                self.state["training"]["valid_seconds"] = training_target
                self._finish("training_valid_target_reached")
        self.state["updated_at"] = utc_now()

    def _disconnect(self, reason, message=None):
        self._phase_before_disconnect = self.state["phase"]
        self._stop_requested = True
        self.state["connection"].update(status="disconnected", message=message or "数据源已断开，需手动重连")
        self._phase("disconnected", reason)
        self._store.event("disconnected", reason=reason, message=message)

    def _finish(self, reason):
        if self._training_started_mono is not None and not self.state["training"].get("ended_at"):
            self.state["training"]["wall_elapsed_seconds"] = max(0, time.monotonic() - self._training_started_mono)
            self.state["training"]["ended_at"] = utc_now()
        summary = self._summary(reason)
        protocol = self.state["protocol"]
        if reason == "training_valid_target_reached" and protocol["plan"] == "daily":
            number = protocol["round_number"]
            summary.update(round_number=number, training_date=protocol["training_date"],
                           training_week=protocol["week_number"], training_day=protocol["day_in_week"],
                           source_session_id=self.state["session_id"],
                           raw_sample_start=self._round_sample_start, raw_sample_end_exclusive=self._store.sample_index,
                           weekly_reference=protocol["day_in_week"] == 4 and number == 4)
            protocol["completed_rounds"].append({"round_number": number, "source_session_id": self.state["session_id"],
                                                  "summary": copy.deepcopy(summary)})
            protocol["last_round_summary"] = copy.deepcopy(summary)
            self._refresh_protocol(active=False)
            self._store.event("training_round_completed", round_number=number, summary=summary)
            if number < 4:
                # Keep the provider and baseline alive. Rest time is real time,
                # not EEG source-time, and finishing it never auto-starts audio.
                protocol["rest_until"] = (datetime.now(timezone.utc) + timedelta(seconds=protocol["rest_seconds"])).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                protocol["round_number"] = number + 1
                self.state["summary"] = None
                self._phase("resting", "training_round_rest_started", reset_signal=False)
                self._save()
                return
        self._stop_requested = True
        self._refresh_protocol(active=False)
        summary["summary_scope"] = "last_round" if protocol["plan"] == "daily" else "single_round"
        summary["round_summaries"] = copy.deepcopy(protocol.get("completed_rounds", []))
        summary["protocol"] = copy.deepcopy(protocol)
        self.state["summary"] = summary
        self._phase("completed", reason)
        self._store.event("session_finished", summary=self.state["summary"])
        self._save()

    def _summary(self, reason):
        elapsed = self.state["training"]["elapsed_seconds"]
        valid = self.state["training"]["valid_seconds"]
        calibration = self.state["calibration"]
        return {"label": "本次训练表现，不代表干预疗效", "reason": reason,
                "mean_score": self._score_sum / self._score_weight if self._score_weight else None,
                "mean_meditation": self._meditation_sum / self._score_weight if self._score_weight else None,
                "elapsed_seconds": elapsed, "valid_seconds": valid, "valid_ratio": min(1, valid / elapsed) if elapsed else 0,
                "wall_elapsed_seconds": self.state["training"]["wall_elapsed_seconds"],
                "started_at": self.state["training"]["started_at"], "ended_at": self.state["training"]["ended_at"],
                "score_trajectory": copy.deepcopy(self._trajectory), "calibration_version": self._calibration_version,
                "baseline_id": calibration.get("baseline_id"),
                "calibration_method": (calibration.get("parameters") or {}).get("version"),
                "calibration_trajectory": copy.deepcopy(calibration.get("trajectory", [])),
                "peak": peak_metrics(self._trajectory), "program_segments": copy.deepcopy(self._program_segments),
                "visual_segments": copy.deepcopy(self._visual_segments),
                "training_scene": self._visual_segments[0]["scene"] if self._visual_segments else self.state["presentation"]["training_scene"],
                "round_number": self.state["training"].get("round_number", 1),
                "music_feedback_profile": copy.deepcopy(self.state.get("music_feedback_profile")),
                "music_feedback_recording_scope": MUSIC_FEEDBACK_RECORDING_SCOPE,
                "metric_version": "accepted-coverage-peaks-v1.0"}

    def _save(self):
        if self._store:
            self.state["updated_at"] = utc_now()
            self.state["training"]["score_trajectory"] = copy.deepcopy(self._trajectory)
            self.state["training"]["program_segments"] = copy.deepcopy(self._program_segments)
            self.state["training"]["visual_segments"] = copy.deepcopy(self._visual_segments)
            self._store.save(self.snapshot())
            self._last_save_mono = time.monotonic()

    async def _stop_acquisition(self):
        self._stop_requested = True
        task = self._task
        if task and task is not asyncio.current_task() and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=12)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._task = None

    async def shutdown(self):
        if self.state["session_id"] and self.state["phase"] not in TERMINAL_PHASES:
            self._advance_clock()
            self._finish("server_shutdown")
        await self._stop_acquisition()
        if self._store:
            self._store.close()
