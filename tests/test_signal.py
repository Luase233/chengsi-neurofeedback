"""Signal-level invariants: physical waveforms, discontinuities and calibration."""
import numpy as np
import pytest

from backend.providers import CHANNELS, EEGChunk
from backend.signal import (CalibrationError, LEGACY_CALIBRATION_VERSION, LEGACY_QUALITY_VERSION, QUALITY_VERSION, LiveProcessor,
                            fit_calibration, integrate_band, score_feature)


def waveform(seconds=8, start=1000.0, amplitudes=(2., 10., 4.)):
    t = np.arange(round(seconds * 256)) / 256
    theta, alpha, beta = amplitudes
    left_ref = 2 * np.sin(2 * np.pi * 7.3 * t)
    right_ref = 2 * np.sin(2 * np.pi * 6.8 * t + .4)
    left = theta * np.sin(2 * np.pi * 6 * t) + alpha * np.sin(2 * np.pi * 10 * t) + beta * np.sin(2 * np.pi * 20 * t)
    right = theta * np.sin(2 * np.pi * 6 * t + .2) + alpha * np.sin(2 * np.pi * 10 * t + .3) + beta * np.sin(2 * np.pi * 20 * t + .4)
    return EEGChunk(np.column_stack((left_ref, left + left_ref, right + right_ref, right_ref)), start + t)


def subset(chunk, sl):
    return EEGChunk(chunk.samples_uv[sl], chunk.timestamps[sl], chunk.sample_rate, chunk.channels)


def test_welch_recovers_physical_band_power_and_units():
    features = LiveProcessor(smooth_alpha=1).push(waveform())
    assert not features[0]["valid"] and "filter_warmup" in features[0]["reasons"]
    last = features[-1]
    assert last["valid"]
    # A sine with peak amplitude A has power A**2/2 in uV**2.
    assert last["bands"]["theta"] == pytest.approx(2, rel=.12)
    assert last["bands"]["alpha"] == pytest.approx(50, rel=.08)
    assert last["bands"]["beta"] == pytest.approx(8, rel=.08)
    assert last["attention_raw"] == pytest.approx(8 / 52, rel=.12)
    assert last["meditation_raw"] == pytest.approx(50 / 8, rel=.12)
    assert last["window_end"] - last["window_start"] == pytest.approx(2)


def test_adjacent_bands_do_not_duplicate_a_frequency_interval():
    frequencies = np.arange(0, 129.)
    density = np.ones(len(frequencies)) * 2
    bands = sum(integrate_band(frequencies, density, low, high) for low, high in ((4, 8), (8, 13), (13, 30)))
    assert bands == pytest.approx(integrate_band(frequencies, density, 4, 30))
    assert bands == pytest.approx(52)


def test_channel_names_control_reordering_not_array_position():
    chunk = waveform()
    order = [1, 3, 0, 2]
    shuffled = EEGChunk(chunk.samples_uv[:, order], chunk.timestamps, channels=tuple(CHANNELS[i] for i in order))
    baseline = LiveProcessor().push(chunk)
    actual = LiveProcessor().push(shuffled)
    assert actual[-1]["bands"] == pytest.approx(baseline[-1]["bands"])
    with pytest.raises(ValueError, match="four uniquely named"):
        LiveProcessor().push(EEGChunk(chunk.samples_uv, chunk.timestamps, channels=("AF7", "AF8", "TP9", "AUX")))


def test_chunk_boundaries_do_not_restart_causal_filters():
    chunk = waveform()
    expected = LiveProcessor().push(chunk)
    processor = LiveProcessor()
    actual = []
    for start in range(0, len(chunk.timestamps), 27):
        actual.extend(processor.push(subset(chunk, slice(start, start + 27))))
    assert len(actual) == len(expected)
    for a, b in zip(actual, expected):
        assert a["valid"] == b["valid"]
        if a["valid"]:
            assert a["bands"] == pytest.approx(b["bands"], rel=1e-10)


def test_missing_samples_are_reported_and_never_bridged_by_valid_windows():
    chunk = waveform(seconds=12)
    processor = LiveProcessor()
    first = processor.push(subset(chunk, slice(0, 4 * 256)))
    following = processor.push(subset(chunk, slice(5 * 256, None)))
    assert any(item["valid"] for item in first)
    assert "timestamp_gap" in following[0]["reasons"]
    valid = [feature for feature in following if feature["valid"]]
    assert valid and all(feature["window_start"] >= 1006 for feature in valid)


def test_non_finite_samples_reset_without_contaminating_later_scores():
    chunk = waveform(seconds=10)
    chunk.samples_uv[3 * 256, 1] = np.nan
    result = LiveProcessor().push(chunk)
    assert any("non_finite_sample" in feature["reasons"] for feature in result)
    assert result[-1]["valid"]
    assert all(np.isfinite(value) for value in result[-1]["bands"].values())


@pytest.mark.parametrize("kind", ["flatline", "saturation", "amplitude_artifact"])
def test_quality_guards_mark_artifacts_not_low_attention(kind):
    chunk = waveform()
    if kind == "flatline":
        chunk.samples_uv[:, 1] = 0
    elif kind == "saturation":
        chunk.samples_uv[:, 1] += 2000
    else:
        t = np.arange(len(chunk.timestamps)) / 256
        chunk.samples_uv[:, 1] += 400 * np.sin(2 * np.pi * 2 * t)
    features = LiveProcessor().push(chunk)
    assert features and all(not feature["valid"] for feature in features)
    assert kind in features[-1]["channel_quality"]["AF7"]["reasons"]
    assert features[-1]["attention_raw"] is None


def raw_feature(a, m, valid=True):
    return {"valid": valid, "reasons": [] if valid else ["flatline"], "attention_raw": a, "meditation_raw": m}


def test_calibration_sorts_actual_medians_without_assuming_eye_direction():
    closed = [raw_feature(.8, 2.)] * 20
    opened = [raw_feature(.2, 5.)] * 20
    calibration = fit_calibration(closed, opened, version=LEGACY_CALIBRATION_VERSION)
    assert calibration["attention"]["x_low"] == .2
    assert calibration["attention"]["x_high"] == .8
    assert calibration["meditation"]["x_low"] == 2.
    score = score_feature(raw_feature(.5, 3.5), calibration)
    assert score["score"] == pytest.approx(50)
    assert score["meditation"] == pytest.approx(50)


def test_insufficient_or_degenerate_calibration_is_rejected():
    with pytest.raises(CalibrationError, match="不足"):
        fit_calibration([raw_feature(.2, 5)] * 19, [raw_feature(.8, 2)] * 20)
    with pytest.raises(CalibrationError, match="接近"):
        fit_calibration([raw_feature(.2, 5)] * 20, [raw_feature(.201, 2)] * 20, version=LEGACY_CALIBRATION_VERSION)
    with pytest.raises(CalibrationError, match="不足"):
        fit_calibration([raw_feature(.2, 5, False)] * 20, [raw_feature(.8, 2)] * 20)
    assert fit_calibration([raw_feature(.2, 5)] * 3, [raw_feature(.8, 2)] * 3, min_valid_windows=3)


def test_clipping_is_a_score_boundary_not_a_human_data_failure():
    calibration = fit_calibration([raw_feature(.2, 5)] * 20, [raw_feature(.8, 2)] * 20, version=LEGACY_CALIBRATION_VERSION)
    result = score_feature(raw_feature(1.2, 1.), calibration)
    assert result["score"] == 100 and result["meditation"] == 0
    assert result["valid"] and result["reasons"] == []
    assert result["attention_clipped"]
    invalid = score_feature(raw_feature(None, None, False), calibration)
    assert invalid["score"] is None and not invalid["valid"]


def test_rate_change_requires_explicit_new_pipeline():
    chunk = waveform()
    chunk.sample_rate = 250
    with pytest.raises(ValueError, match="Sample rate changed"):
        LiveProcessor().push(chunk)


def test_sdk_arrival_jitter_is_not_confused_with_missing_packet_samples():
    chunk = waveform(seconds=8)
    # BrainFlow spreads each 12-sample group over its BLE arrival interval.
    packet = np.arange(len(chunk.timestamps)) // 12
    intervals = np.where(packet % 2, .0025, .0055)
    chunk.timestamps = 1000 + np.cumsum(intervals)
    chunk.metadata = {"clock_mode": "brainflow-muse-arrival-interpolated",
                      "package_numbers": packet.tolist(), "packet_gap_indices": []}
    features = LiveProcessor().push(chunk)
    assert any(feature["valid"] for feature in features)
    assert not any("timestamp_gap" in feature["reasons"] for feature in features)
    chunk.metadata["packet_gap_indices"] = [4 * 256]
    assert any("packet_gap" in feature["reasons"] for feature in LiveProcessor().push(chunk))


def test_responsive_profile_reduces_waveform_step_lag_without_changing_bands():
    chunk = waveform(seconds=30)
    t = np.arange(len(chunk.timestamps)) / 256
    after = t >= 8
    chunk.samples_uv[after, 1] += 4 * np.sin(2 * np.pi * 20 * t[after])
    chunk.samples_uv[after, 2] += 4 * np.sin(2 * np.pi * 20 * t[after] + .4)
    reference = [feature for feature in LiveProcessor(smooth_alpha=1).push(chunk) if feature["valid"]]
    low = reference[5]["bands"]["beta"]
    high = reference[-1]["bands"]["beta"]
    threshold = low + .95 * (high - low)
    delays = {}
    for alpha in (.65, .2):
        processor = LiveProcessor(smooth_alpha=alpha)
        features = processor.push(chunk)
        reached = next(feature for feature in features if feature["valid"] and feature["window_end"] > 1008 and feature["bands"]["beta"] >= threshold)
        delays[alpha] = reached["window_end"] - 1008
        assert processor.config["smooth_alpha"] == alpha
    assert delays[.65] <= 5
    assert delays[.2] >= 12
    assert LiveProcessor().config["profile"] == "responsive-v1"
    assert LiveProcessor(smooth_alpha=.2).config["profile"] == "report-v1"


def test_calibration_cannot_mix_smoothing_profiles():
    a = {**raw_feature(.2, 5), "algorithm_profile": "responsive-v1", "smooth_alpha": .65}
    b = {**raw_feature(.8, 2), "algorithm_profile": "report-v1", "smooth_alpha": .2}
    with pytest.raises(CalibrationError, match="不同算法"):
        fit_calibration([a] * 20, [b] * 20)


def add_powerline(chunk, frequency=50., amplitude=600., only_line=False):
    t = np.arange(len(chunk.timestamps)) / chunk.sample_rate
    mains = amplitude * np.sin(2 * np.pi * frequency * t[:, None] + np.arange(4)[None, :] * .3)
    chunk.samples_uv = mains if only_line else chunk.samples_uv + mains
    # Actual Muse 12-bit conversion, rather than ideal floating-point sines.
    step = 2000 / 4096
    chunk.samples_uv = np.round(chunk.samples_uv / step) * step
    return chunk


def test_filtered_quality_retains_useful_waveform_with_explicit_mains_warning():
    result = LiveProcessor().push(add_powerline(waveform(seconds=12)))
    assert any(feature["valid"] for feature in result[3:])
    last = result[-1]
    assert last["valid"] and last["quality_version"] == QUALITY_VERSION
    for channel in last["channel_quality"].values():
        assert channel["artifact_domain"] == "filtered"
        assert channel["max_step_uv"] > 150  # raw mains is not a motion spike
        assert channel["filtered_max_step_uv"] < 150
        assert channel["warnings"] == ["powerline_50hz"]
        assert channel["line_noise_ratio"] > .9
    baseline = LiveProcessor().push(waveform(seconds=12))[-1]
    assert last["bands"] == pytest.approx(baseline["bands"], rel=.08)


@pytest.mark.parametrize("frequency", [49.8, 50., 50.2])
@pytest.mark.parametrize("amplitude", [50., 600., 900.])
def test_quantized_mains_alone_never_becomes_valid_eeg(frequency, amplitude):
    result = LiveProcessor().push(add_powerline(waveform(seconds=12), frequency, amplitude, only_line=True))
    assert not any(feature["valid"] for feature in result)
    assert "insufficient_residual_signal" in result[-1]["reasons"]
    assert all("powerline_50hz" in channel["warnings"] for channel in result[-1]["channel_quality"].values())


@pytest.mark.parametrize("artifact", ["clipped", "detached", "slow_motion"])
def test_filtered_quality_does_not_hide_hardware_failure_or_real_motion(artifact):
    chunk = waveform(seconds=12)
    if artifact == "clipped":
        chunk.samples_uv[:, 1] = np.clip(chunk.samples_uv[:, 1] * 300, -1000, 999.51171875)
        reason = "saturation"
    elif artifact == "detached":
        chunk.samples_uv[:, 1] = 45
        reason = "flatline"
    else:
        t = np.arange(len(chunk.timestamps)) / 256
        chunk.samples_uv[:, 1] += 400 * np.sin(2 * np.pi * 2 * t)
        reason = "amplitude_artifact"
    result = LiveProcessor().push(chunk)
    assert not any(feature["valid"] for feature in result)
    assert reason in result[-1]["channel_quality"]["AF7"]["reasons"]


def test_notch_only_guard_preserves_genuine_one_sample_spikes_hidden_by_lowpass():
    chunk = waveform(seconds=12)
    chunk.samples_uv[::128, 1] += 600
    result = LiveProcessor().push(chunk)
    assert not any(feature["valid"] for feature in result)
    last = result[-1]["channel_quality"]["AF7"]
    assert last["filtered_peak_to_peak_uv"] < 350
    assert last["filtered_max_step_uv"] < 150
    assert last["notched_max_step_uv"] > 150
    assert "large_step" in last["reasons"]


def test_notch_spike_guard_state_is_continuous_across_chunks():
    chunk = add_powerline(waveform(seconds=12))
    chunk.samples_uv[7 * 256 + 27, 1] += 400
    expected = LiveProcessor().push(chunk)
    processor = LiveProcessor()
    actual = []
    for start in range(0, len(chunk.timestamps), 27):
        actual.extend(processor.push(subset(chunk, slice(start, start + 27))))
    assert [feature["valid"] for feature in actual] == [feature["valid"] for feature in expected]
    for a, b in zip(actual, expected):
        assert a["reasons"] == b["reasons"]
        for name in CHANNELS:
            assert a["channel_quality"][name]["notched_max_step_uv"] == pytest.approx(b["channel_quality"][name]["notched_max_step_uv"], rel=1e-10)


def test_legacy_quality_dictionary_keeps_original_raw_domain_gate():
    processor = LiveProcessor(quality={"saturation_absolute_uv": 995.})
    assert processor.config["quality_version"] == LEGACY_QUALITY_VERSION
    assert processor.config["quality"]["artifact_domain"] == "raw"
    result = processor.push(add_powerline(waveform()))
    assert not any(feature["valid"] for feature in result)
    assert "large_step" in result[-1]["reasons"]
    assert "amplitude_artifact" in result[-1]["reasons"]


def test_quality_versions_cannot_mix_calibration_or_score():
    old = {**raw_feature(.2, 5), "quality_version": LEGACY_QUALITY_VERSION}
    new = {**raw_feature(.8, 2), "quality_version": QUALITY_VERSION}
    with pytest.raises(CalibrationError, match="不同质量"):
        fit_calibration([old] * 20, [new] * 20)
    calibration = fit_calibration([old] * 20, [{**old, "attention_raw": .8, "meditation_raw": 2}] * 20, version=LEGACY_CALIBRATION_VERSION)
    with pytest.raises(CalibrationError, match="不同质量"):
        score_feature(new, calibration)
    # A missing quality_version denotes legacy v1.0, never today's default.
    missing = raw_feature(.5, 3.5)
    assert score_feature(missing, calibration)["score"] == pytest.approx(50)
    modern = fit_calibration([new] * 20, [{**new, "attention_raw": .2, "meditation_raw": 5}] * 20)
    assert modern["quality_version"] == QUALITY_VERSION
    with pytest.raises(CalibrationError, match="不同质量"):
        score_feature(missing, modern)
