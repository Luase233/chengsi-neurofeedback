"""Acquisition adapters. Every adapter emits real waveform samples in microvolts."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import re
import time
from typing import Any

import numpy as np

CHANNELS = ("TP9", "AF7", "AF8", "TP10")


@dataclass
class EEGChunk:
    samples_uv: np.ndarray
    timestamps: np.ndarray
    sample_rate: float = 256.0
    channels: tuple[str, ...] = CHANNELS
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.samples_uv = np.asarray(self.samples_uv, dtype=np.float64)
        self.timestamps = np.asarray(self.timestamps, dtype=np.float64)
        self.channels = tuple(self.channels)
        if self.samples_uv.ndim != 2 or self.samples_uv.shape[1] != len(self.channels):
            raise ValueError("EEG samples must have shape (samples, named channels)")
        if self.timestamps.ndim != 1 or len(self.timestamps) != len(self.samples_uv):
            raise ValueError("One timestamp is required for every EEG sample")
        if not np.isfinite(self.sample_rate) or self.sample_rate <= 0:
            raise ValueError("Invalid EEG sample rate")


class _PacketContinuity:
    """Muse EEG packets contain 12 samples with one repeated uint16 packet id."""
    def __init__(self):
        self.last = None
        self.count = 0
        self.initial = True

    def inspect(self, numbers):
        gaps = []
        for index, raw in enumerate(numbers):
            if not np.isfinite(raw) or raw != int(raw) or not 0 <= raw <= 65535:
                gaps.append(index)
                self.last, self.count, self.initial = None, 0, True
                continue
            current = int(raw)
            if self.last is None:
                self.count = 1
            elif current == self.last:
                self.count += 1
                if self.count > 12:
                    gaps.append(index)
                    self.count = 1
            else:
                if (current - self.last) % 65536 != 1 or (not self.initial and self.count != 12):
                    gaps.append(index)
                self.count = 1
                self.initial = False
            self.last = current
        return gaps


class MuseProvider:
    """BrainFlow native BLE Muse 2 adapter; never silently falls back to synthetic."""

    def __init__(self, address: str | None = None, timeout: int = 10, name: str | None = None):
        self.address, self.timeout, self.name = address, timeout, name
        self._board = None
        self.metadata: dict[str, Any] = {}
        self.eof = False
        self.stage = "idle"

    def connect(self) -> dict:
        if self._board is not None:
            return dict(self.metadata)
        from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds

        board_id = BoardIds.MUSE_2_BOARD.value
        params = BrainFlowInputParams()
        params.timeout = max(1, int(self.timeout))
        if self.address:
            address = self.address.strip()
            # Bleak on Windows reports uppercase MACs, while BrainFlow's
            # SimpleBLE discovery matches its lowercase address case-sensitively.
            # Keep platform-specific identifiers (e.g. CoreBluetooth UUIDs) intact.
            params.mac_address = address.lower() if re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", address) else address
        if self.name:
            params.serial_number = self.name
        descriptor = BoardShim.get_board_descr(board_id)
        eeg_rows = BoardShim.get_eeg_channels(board_id)
        names = descriptor.get("eeg_names", "")
        names = [name.strip().upper() for name in (names.split(",") if isinstance(names, str) else names)]
        if len(names) != len(eeg_rows) or any(name not in names for name in CHANNELS):
            raise RuntimeError("Muse channel names are unavailable or incomplete; refusing positional guessing")
        self._rows = [eeg_rows[names.index(name)] for name in CHANNELS]
        self._timestamp_row = BoardShim.get_timestamp_channel(board_id)
        self._packet_row = descriptor["package_num_channel"]
        self._packets = _PacketContinuity()
        self._sample_rate = float(BoardShim.get_sampling_rate(board_id))
        board = BoardShim(board_id, params)
        try:
            board.prepare_session()
            board.start_stream(int(self._sample_rate * 120))
        except Exception:
            try:
                board.release_session()
            except Exception:
                pass
            raise
        self._board = board
        self.metadata = {"source": "muse", "original_source": "muse", "provider": "brainflow-native-ble",
                         "device_name": self.name or "Muse 2", "device_address": self.address,
                         "board_id": board_id, "sample_rate": self._sample_rate,
                         "channels": list(CHANNELS), "units": "uV", "synthetic": False}
        return dict(self.metadata)

    def read(self) -> EEGChunk | None:
        if self._board is None:
            raise RuntimeError("Muse is not connected")
        data = np.asarray(self._board.get_board_data())  # Drains only samples supplied by BrainFlow.
        if data.ndim != 2 or data.shape[1] == 0:
            return None
        numbers = data[self._packet_row, :]
        return EEGChunk(data[self._rows, :].T, data[self._timestamp_row, :], self._sample_rate,
                        CHANNELS, {"source": "muse", "original_source": "muse", "units": "uV",
                                   "clock_mode": "brainflow-muse-arrival-interpolated",
                                   "package_numbers": numbers.tolist(), "packet_gap_indices": self._packets.inspect(numbers)})

    def disconnect(self):
        board, self._board = self._board, None
        if board is not None:
            try:
                board.stop_stream()
            finally:
                board.release_session()

    def set_stage(self, stage: str):
        self.stage = stage


class SyntheticProvider:
    """Explicit test source with theta/alpha/beta waveforms, never fabricated scores."""

    def __init__(self, seed: int = 42, sample_rate: float = 256.0):
        self.sample_rate = float(sample_rate)
        self.seed = seed
        self.stage = "idle"
        self.eof = False
        self._connected = False
        self._dropout_until = 0.0

    def connect(self) -> dict:
        self._epoch, self._start = time.time(), time.monotonic()
        self._emitted = 0
        self._rng = np.random.default_rng(self.seed)
        self._dropout_until = 0.0
        self._connected = True
        self.metadata = {"source": "synthetic", "original_source": "synthetic", "synthetic": True,
                         "device_name": "合成脑电测试源", "sample_rate": self.sample_rate,
                         "channels": list(CHANNELS), "units": "uV", "seed": self.seed}
        return dict(self.metadata)

    def read(self) -> EEGChunk | None:
        if not self._connected:
            raise RuntimeError("Synthetic source is not connected")
        now = time.monotonic()
        if self._dropout_until:
            skipped_until = min(now, self._dropout_until)
            self._emitted = max(self._emitted, int((skipped_until - self._start) * self.sample_rate))
            if now < self._dropout_until:
                return None
            self._dropout_until = 0.0
        due = max(self._emitted, int((now - self._start) * self.sample_rate))
        count = min(due - self._emitted, int(self.sample_rate * 2))
        if count <= 0:
            return None
        indices = np.arange(self._emitted, self._emitted + count)
        t = indices / self.sample_rate
        if self.stage in ("eyes_closed", "calibrating_closed"):
            theta, alpha, beta = 4.0, 14.0, 3.0
        elif self.stage in ("eyes_open", "calibrating_open"):
            theta, alpha, beta = 4.0, 7.0, 7.0
        elif self.stage == "training":
            theta, alpha, beta = 4.0, 8.0, 5.0 + 3.0 * (0.5 + 0.5 * np.sin(t * 0.12))
        else:
            theta, alpha, beta = 4.0, 10.0, 5.0
        left_ref = 2.0 * np.sin(2 * np.pi * 7 * t + .2) + .8 * np.sin(2 * np.pi * 17 * t)
        right_ref = 2.1 * np.sin(2 * np.pi * 7 * t + .7) + .8 * np.sin(2 * np.pi * 17 * t + .5)
        left = theta * np.sin(2 * np.pi * 6 * t) + alpha * np.sin(2 * np.pi * 10 * t) + beta * np.sin(2 * np.pi * 20 * t)
        right = theta * np.sin(2 * np.pi * 6 * t + .3) + alpha * np.sin(2 * np.pi * 10 * t + .2) + beta * np.sin(2 * np.pi * 20 * t + .4)
        samples = np.column_stack((left_ref, left + left_ref, right + right_ref, right_ref))
        samples += self._rng.normal(0, .35, samples.shape)
        self._emitted += count
        return EEGChunk(samples, self._epoch + t, self.sample_rate, CHANNELS,
                        {"source": "synthetic", "original_source": "synthetic", "stage": self.stage, "units": "uV"})

    def disconnect(self):
        self._connected = False

    def set_stage(self, stage: str):
        self.stage = stage

    def inject_dropout(self, duration_seconds: float = 3.0):
        self._dropout_until = time.monotonic() + max(0.0, float(duration_seconds))


class ReplayProvider:
    """Paced, non-looping playback of a backend-authorized local raw.csv session."""

    def __init__(self, path: str | Path, speed: float = 1.0):
        self.path = Path(path).resolve()
        self.speed = float(speed)
        if not np.isfinite(self.speed) or self.speed <= 0:
            raise ValueError("Replay speed must be positive")
        self.eof = False
        self._connected = False
        self.stage = "idle"

    def connect(self) -> dict:
        csv_path = self.path / "raw.csv" if self.path.is_dir() else self.path
        if csv_path.name != "raw.csv" or not csv_path.is_file():
            raise ValueError("Replay requires an existing local session raw.csv")
        manifest_path = csv_path.parent / "session.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        self.manifest = manifest
        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or any(name not in reader.fieldnames for name in ("timestamp", *CHANNELS)):
                raise ValueError("Replay CSV is missing named EEG channels or timestamp")
            rows = list(reader)
        algorithm = manifest.get("algorithm_config")
        calibration = manifest.get("calibration", {})
        events_path = csv_path.parent / "events.jsonl"
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()] if events_path.exists() else []
        starts = [event for event in events if event.get("reason") == "training_started" or event.get("type") == "training_started"]
        selected_start = starts[-1] if starts else None
        if selected_start:
            expected_version = selected_start.get("calibration_version")
            if expected_version is not None and calibration.get("version") != expected_version:
                calibration = next((item for item in manifest.get("calibration_history", []) if item.get("version") == expected_version), {})
            if selected_start.get("sample_index") is not None:
                if not rows or "sample_index" not in rows[0]:
                    raise ValueError("Replay cannot locate the recorded training attempt sample index")
                rows = [row for row in rows if int(row["sample_index"]) >= int(selected_start["sample_index"])]
            elif selected_start.get("at"):
                cutoff = datetime.fromisoformat(selected_start["at"].replace("Z", "+00:00")).timestamp()
                rows = [row for row in rows if float(row["timestamp"]) >= cutoff]
            else:
                raise ValueError("Replay cannot locate the latest training attempt")
        elif manifest.get("calibration_history"):
            raise ValueError("Replay cannot match multiple calibration versions without training events")
        rows = [row for row in rows if row.get("phase") == "training"]
        if not isinstance(algorithm, dict) or not algorithm.get("sample_rate"):
            raise ValueError("Replay session has no recorded algorithm configuration")
        params = calibration.get("parameters") if calibration.get("valid") else None
        if not isinstance(params, dict) or any(not isinstance(params.get(key), dict) for key in ("attention", "meditation")):
            raise ValueError("Replay session has no valid recorded calibration")
        # Imported here because signal processing also imports the provider's
        # channel layout. Validate the recorded method, never today's default.
        from .signal import validate_calibration
        try:
            validate_calibration(params)
        except ValueError as error:
            raise ValueError(f"Replay session calibration is invalid: {error}") from error
        if len(rows) < 2:
            raise ValueError("Replay has no complete recorded training samples")
        self._timestamps = np.array([float(row["timestamp"]) for row in rows])
        self._samples = np.array([[float(row[name]) for name in CHANNELS] for row in rows])
        self._phases = [row.get("phase", "") for row in rows]
        self._package_numbers = np.array([float(row["packet_num"]) for row in rows]) if all(row.get("packet_num", "") != "" for row in rows) else None
        self._packets = _PacketContinuity()
        if not np.all(np.isfinite(self._timestamps)):
            raise ValueError("Replay contains invalid timestamps")
        intervals = np.diff(self._timestamps)
        positive = intervals[intervals > 0]
        if not len(positive):
            raise ValueError("Replay contains no forward sample times")
        inferred_rate = 1.0 / float(np.median(positive))
        declared = manifest.get("provider_metadata", manifest.get("device", manifest.get("metadata", {})))
        declared = declared if isinstance(declared, dict) else {}
        self._sample_rate = float(declared.get("sample_rate", round(inferred_rate)))
        original_source = manifest.get("original_source", declared.get("original_source", manifest.get("source", manifest.get("mode", declared.get("source", "unknown")))))
        self.metadata = {"source": "replay", "original_source": original_source, "device_name": "本地会话回放",
                         "sample_rate": self._sample_rate, "channels": list(CHANNELS), "units": "uV",
                         "replay_session_id": manifest.get("session_id", csv_path.parent.name),
                         "replay_speed": self.speed, "recorded_samples": len(rows),
                         "recorded_calibration": calibration, "recorded_algorithm_config": algorithm,
                         "selected_attempt": "latest_training_attempt"}
        self._index = 0
        self._played_seconds = 0.0
        self._running_since = None
        self.stage = "ready"
        self.eof = False
        self._connected = True
        return dict(self.metadata)

    def read(self) -> EEGChunk | None:
        if not self._connected:
            raise RuntimeError("Replay source is not connected")
        if self.stage != "training":
            return None
        if self._index >= len(self._timestamps):
            self.eof = True
            return None
        played = self._played_seconds + time.monotonic() - self._running_since
        limit = self._timestamps[0] + played * self.speed
        end = self._index
        while end < len(self._timestamps) and self._timestamps[end] <= limit and end - self._index < 512:
            end += 1
        if end == self._index:
            return None
        start, self._index = self._index, end
        self.eof = end == len(self._timestamps)
        metadata = {"source": "replay", "original_source": self.metadata["original_source"],
                    "recorded_phases": self._phases[start:end], "units": "uV"}
        if self._package_numbers is not None:
            numbers = self._package_numbers[start:end]
            metadata.update(clock_mode="brainflow-muse-arrival-interpolated", package_numbers=numbers.tolist(),
                            packet_gap_indices=self._packets.inspect(numbers))
        return EEGChunk(self._samples[start:end].copy(), self._timestamps[start:end].copy(), self._sample_rate,
                        CHANNELS, metadata)

    def disconnect(self):
        self._connected = False

    def set_stage(self, stage: str):
        if stage == self.stage:
            return
        if self._running_since is not None:
            self._played_seconds += time.monotonic() - self._running_since
        self._running_since = time.monotonic() if stage == "training" else None
        self.stage = stage  # Samples and original timestamps remain unchanged across pauses.


async def discover_muse(timeout: float = 5) -> list[dict]:
    """Read Bluetooth advertisements only; scanning never opens a device connection."""
    from bleak import BleakScanner

    devices = await BleakScanner.discover(timeout=max(.1, min(30.0, float(timeout))), return_adv=True)
    found = []
    for device, advertisement in devices.values():
        name = advertisement.local_name or device.name or ""
        if "muse" in name.lower():
            found.append({"name": name, "address": device.address, "rssi": advertisement.rssi})
    return sorted(found, key=lambda item: item["name"])
