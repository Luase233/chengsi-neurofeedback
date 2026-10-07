"""Versioned HTTP command models; session state remains server-owned."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .audio_profiles import CONTINUOUS_LAYER_PROGRAM_IDS

PROGRAM_IDS = {"deep-learning", "efficiency", "re-life", "beta-focus"} | CONTINUOUS_LAYER_PROGRAM_IDS
TrainingScene = Literal["lake-trees", "lake-house", "rings-still", "rings-feedback"]


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["synthetic", "live", "replay"]
    participant_id: str = Field(default="demo", min_length=1, max_length=80)
    program_id: str = "clear-current-v04"
    calibration_seconds: float = Field(default=60, ge=5, le=180)
    training_seconds: float | None = Field(default=120, ge=5, le=3600)
    replay_session_id: str | None = None
    device_address: str | None = Field(default=None, max_length=100)
    dual_screen: bool = False
    protocol_plan: Literal["single", "daily"] = "single"
    training_scene: TrainingScene = "lake-trees"

    @model_validator(mode="after")
    def protocol_constraints(self):
        if self.protocol_plan == "daily":
            if "training_seconds" not in self.model_fields_set:
                self.training_seconds = 60
            if self.mode == "replay":
                raise ValueError("Replay cannot count toward the daily training plan")
            if self.training_seconds not in {60, 120}:
                raise ValueError("Daily rounds require 60 or 120 valid seconds")
        if self.program_id not in PROGRAM_IDS:
            raise ValueError("Unknown audio program")
        if self.mode == "live" and self.calibration_seconds != 60:
            raise ValueError("Live calibration requires 60 valid seconds per stage")
        if self.mode == "replay" and not self.replay_session_id:
            raise ValueError("A replay_session_id is required for replay mode")
        return self


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: Literal["calibrate_closed", "calibrate_open", "start_training", "pause", "resume", "finish", "reconnect", "disconnect", "set_program", "inject_dropout", "set_presentation", "cue_finished", "cancel_preparation"]
    command_id: str = Field(min_length=1, max_length=100)
    program_id: str | None = None
    cue_mode: Literal["voice", "tone"] | None = None
    volume: float | None = Field(default=None, ge=0, le=1)
    sound_enabled: bool | None = None
    training_scene: TrainingScene | None = None
    cue_request_id: str | None = Field(default=None, min_length=1, max_length=100)
    client_id: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def command_constraints(self):
        if self.command == "set_program" and self.program_id not in PROGRAM_IDS:
            raise ValueError("set_program requires a valid program_id")
        if self.command == "cue_finished" and (not self.cue_request_id or not self.client_id):
            raise ValueError("cue_finished requires cue_request_id and client_id")
        if self.command == "set_presentation" and all(value is None for value in (self.cue_mode, self.volume, self.sound_enabled, self.training_scene)):
            raise ValueError("set_presentation requires at least one presentation setting")
        return self
