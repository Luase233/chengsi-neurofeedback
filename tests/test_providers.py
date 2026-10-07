"""Provider contracts using deterministic clocks and fake hardware boundaries."""
import asyncio
import csv
import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from backend import providers
from backend.providers import CHANNELS, EEGChunk, MuseProvider, ReplayProvider, SyntheticProvider, discover_muse, _PacketContinuity
from backend.signal import CALIBRATION_VERSION, LiveProcessor, fit_calibration


class Clock:
    now = 0.0

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(providers.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(providers.time, "time", lambda: 1700000000 + clock.now)
    return clock


def collect(source, clock, seconds):
    chunks = []
    for _ in range(seconds * 4):
        clock.advance(.25)
        chunk = source.read()
        if chunk is not None:
            chunks.append(chunk)
    return chunks


def test_synthetic_source_changes_waveform_spectrum_not_scores(clock):
    source = SyntheticProvider(seed=3)
    assert source.connect()["synthetic"]
    source.set_stage("calibrating_closed")
    closed = collect(source, clock, 8)
    source.set_stage("calibrating_open")
    opened = collect(source, clock, 8)
    processor = LiveProcessor(smooth_alpha=1)
    c = [feature for chunk in closed for feature in processor.push(chunk) if feature["valid"]]
    processor.reset()
    o = [feature for chunk in opened for feature in processor.push(chunk) if feature["valid"]]
    assert np.median([feature["attention_raw"] for feature in o]) > np.median([feature["attention_raw"] for feature in c]) * 2
    assert all(chunk.samples_uv.shape == (64, 4) for chunk in closed + opened)
    assert "score" not in closed[0].metadata
    assert np.allclose(np.diff(closed[0].timestamps), 1 / 256)
    source.disconnect()
    with pytest.raises(RuntimeError):
        source.read()


def test_synthetic_dropout_is_missing_time_not_zero_fill(clock):
    source = SyntheticProvider()
    source.connect()
    clock.advance(1)
    before = source.read()
    source.inject_dropout(3)
    clock.advance(2)
    assert source.read() is None
    clock.advance(1.25)
    after = source.read()
    assert after.timestamps[0] - before.timestamps[-1] >= 3
    assert np.any(after.samples_uv != 0)


def write_session(tmp_path, channels=("AF8", "TP10", "TP9", "AF7")):
    with (tmp_path / "raw.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "sample_index", "phase", *channels])
        for i in range(256):
            values = {"TP9": i + 1, "AF7": i + 2, "AF8": i + 3, "TP10": i + 4}
            writer.writerow([1000 + i / 256, i, "training", *[values[name] for name in channels]])
    manifest = {"source": "synthetic", "session_id": "fixture", "provider_metadata": {"sample_rate": 256},
                "algorithm_config": LiveProcessor().config,
                "calibration": {"valid": True, "version": 1, "parameters": {
                    "attention": {"x_low": .1, "x_high": 1.}, "meditation": {"x_low": 1., "x_high": 4.}}}}
    (tmp_path / "session.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_replay_keeps_original_timestamps_channel_names_and_origin(tmp_path, clock):
    write_session(tmp_path)
    source = ReplayProvider(tmp_path)
    metadata = source.connect()
    assert metadata["source"] == "replay" and metadata["original_source"] == "synthetic"
    source.set_stage("training")
    clock.advance(2)
    chunk = source.read()
    assert chunk.channels == CHANNELS
    assert chunk.samples_uv[0].tolist() == [1, 2, 3, 4]
    assert chunk.timestamps[0] == 1000
    assert chunk.metadata["recorded_phases"] == ["training"] * 256
    assert source.eof and source.read() is None


def test_replay_rejects_missing_required_columns(tmp_path, clock):
    (tmp_path / "raw.csv").write_text("timestamp,AF7\n1,5\n2,6\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing named"):
        ReplayProvider(tmp_path).connect()


@pytest.mark.parametrize("device_address, native_address", [
    ("AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff"),
    ("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:ff"),
    ("  Aa:bB:cC:Dd:eE:fF  ", "aa:bb:cc:dd:ee:ff"),
    ("ABCDEF01-2345-6789-ABCD-EF0123456789", "ABCDEF01-2345-6789-ABCD-EF0123456789"),
])
def test_muse_adapter_uses_native_board_metadata_and_drains_actual_samples(monkeypatch, device_address, native_address):
    data = np.vstack((np.zeros(3), np.full(3, 30), np.full(3, 10), np.full(3, 40), np.full(3, 20), [1, 1 + 1 / 256, 1 + 2 / 256]))
    instances = []

    class Board:
        get_board_descr = staticmethod(lambda board_id: {"eeg_names": "AF8,TP9,TP10,AF7", "package_num_channel": 0})
        get_eeg_channels = staticmethod(lambda board_id: [1, 2, 3, 4])
        get_timestamp_channel = staticmethod(lambda board_id: 5)
        get_sampling_rate = staticmethod(lambda board_id: 256)

        def __init__(self, board_id, params):
            self.board_id, self.params, self.reads = board_id, params, 0
            self.stopped = self.released = False
            instances.append(self)

        def prepare_session(self):
            # Reproduce the native discovery's case-sensitive address boundary.
            assert self.params.mac_address == native_address

        def start_stream(self, size):
            self.size = size

        def get_board_data(self):
            self.reads += 1
            return data if self.reads == 1 else np.empty((6, 0))

        def stop_stream(self):
            self.stopped = True

        def release_session(self):
            self.released = True

    fake = SimpleNamespace(BoardShim=Board, BrainFlowInputParams=SimpleNamespace, BoardIds=SimpleNamespace(MUSE_2_BOARD=SimpleNamespace(value=38)))
    monkeypatch.setitem(sys.modules, "brainflow.board_shim", fake)
    source = MuseProvider(address=device_address, name="Muse-test")
    metadata = source.connect()
    chunk = source.read()
    assert metadata["sample_rate"] == 256 and metadata["units"] == "uV"
    assert metadata["device_address"] == device_address
    assert chunk.samples_uv[0].tolist() == [10, 20, 30, 40]
    assert chunk.timestamps.tolist() == data[5].tolist()
    assert source.read() is None
    assert instances[0].board_id == 38 and instances[0].params.mac_address == native_address
    source.disconnect()
    assert instances[0].stopped and instances[0].released


def test_discovery_only_reads_advertisements(monkeypatch):
    calls = []

    class Scanner:
        @staticmethod
        async def discover(**kwargs):
            calls.append(kwargs)
            return {"one": (SimpleNamespace(name="Muse-A", address="AA"), SimpleNamespace(local_name=None, rssi=-50)),
                    "two": (SimpleNamespace(name="Headphones", address="BB"), SimpleNamespace(local_name=None, rssi=-30))}

    monkeypatch.setitem(sys.modules, "bleak", SimpleNamespace(BleakScanner=Scanner))
    assert asyncio.run(discover_muse()) == [{"name": "Muse-A", "address": "AA", "rssi": -50}]
    assert calls == [{"timeout": 5.0, "return_adv": True}]


def test_chunk_rejects_misaligned_samples_and_timestamps():
    with pytest.raises(ValueError, match="timestamp"):
        EEGChunk(np.zeros((4, 4)), np.arange(3))


def test_muse_packet_counter_accepts_repeated_samples_and_wrap_but_reports_loss():
    packets = _PacketContinuity()
    assert packets.inspect([65535] * 6) == []
    assert packets.inspect([65535] * 6 + [0] * 12 + [1] * 12) == []
    assert packets.inspect([3] * 12) == [0]
    assert packets.inspect([4] * 7) == []
    assert packets.inspect([5] * 12) == [0]


def test_replay_preserves_packet_counter_quality_evidence(tmp_path, clock):
    write_session(tmp_path)
    with (tmp_path / "raw.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "phase", "packet_num", *CHANNELS])
        for index in range(36):
            packet = (index // 12) + (1 if index >= 24 else 0)
            writer.writerow([1000 + index / 256, "training", packet, 1, 2, 3, 4])
    source = ReplayProvider(tmp_path)
    source.connect()
    source.set_stage("training")
    clock.advance(1)
    chunk = source.read()
    assert chunk.metadata["clock_mode"] == "brainflow-muse-arrival-interpolated"
    assert chunk.metadata["packet_gap_indices"] == [24]


def test_replay_waits_for_start_and_freezes_playhead_when_paused(tmp_path, clock):
    write_session(tmp_path)
    source = ReplayProvider(tmp_path)
    source.connect()
    clock.advance(100)
    assert source.read() is None and not source.eof
    source.set_stage("training")
    clock.advance(.25)
    first = source.read()
    source.set_stage("paused")
    clock.advance(20)
    assert source.read() is None
    source.set_stage("training")
    clock.advance(.25)
    second = source.read()
    assert second.timestamps[0] - first.timestamps[-1] == pytest.approx(1 / 256)
    assert second.timestamps[-1] < 1000.6


def test_replay_selects_latest_training_attempt_with_its_recorded_calibration(tmp_path, clock):
    write_session(tmp_path)
    manifest = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    old = manifest["calibration"]
    manifest["calibration_history"] = [old]
    manifest["calibration"] = {**old, "version": 2}
    (tmp_path / "session.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "events.jsonl").write_text(json.dumps({"reason": "training_started", "sample_index": 128, "calibration_version": 1}) + "\n", encoding="utf-8")
    source = ReplayProvider(tmp_path)
    metadata = source.connect()
    assert metadata["recorded_calibration"]["version"] == 1
    assert metadata["recorded_samples"] == 128
    source.set_stage("training")
    clock.advance(1)
    assert source.read().timestamps[0] == 1000.5


def test_replay_rejects_sessions_without_recorded_calibration(tmp_path, clock):
    write_session(tmp_path)
    manifest = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    manifest["calibration"] = {"valid": False, "parameters": None}
    (tmp_path / "session.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="recorded calibration"):
        ReplayProvider(tmp_path).connect()


def robust_replay_manifest(tmp_path):
    write_session(tmp_path)
    manifest = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    feature = lambda a: {"valid": True, "attention_raw": a, "meditation_raw": 1 / a}
    parameters = fit_calibration([feature(.2)] * 20, [feature(a) for a in [.22, .27, .31, .38] * 5])
    manifest["calibration"]["parameters"] = parameters
    (tmp_path / "session.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_replay_accepts_and_preserves_robust_eyes_open_calibration(tmp_path, clock):
    manifest = robust_replay_manifest(tmp_path)
    result = ReplayProvider(tmp_path).connect()
    assert result["recorded_calibration"]["parameters"] == manifest["calibration"]["parameters"]
    assert result["recorded_calibration"]["parameters"]["version"] == CALIBRATION_VERSION


@pytest.mark.parametrize("bad_parameter", ["scale", "version"])
def test_replay_rejects_broken_or_unknown_calibration_before_playback(tmp_path, clock, bad_parameter):
    manifest = robust_replay_manifest(tmp_path)
    parameters = manifest["calibration"]["parameters"]
    if bad_parameter == "scale":
        parameters["attention"]["scale_log"] = 0
    else:
        parameters["version"] = "unknown-future-method"
    (tmp_path / "session.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="calibration is invalid"):
        ReplayProvider(tmp_path).connect()
