"""Versioned personal score mapping, independently of waveform quality checks."""
import copy
import json
import math

import numpy as np
import pytest

from backend.signal import (ALGORITHM_VERSION, CALIBRATION_VERSION, LEGACY_CALIBRATION_VERSION,
                            LOG_SCALE_FLOOR, QUALITY_VERSION, CalibrationError, LiveProcessor,
                            fit_calibration, score_feature, validate_calibration)


def feature(attention, meditation=2., *, valid=True):
    return {"valid": valid, "attention_raw": attention, "meditation_raw": meditation,
            "algorithm_version": ALGORITHM_VERSION, "quality_version": QUALITY_VERSION,
            "algorithm_profile": "responsive-v1", "smooth_alpha": .65}


def baseline(values=None):
    if values is None:
        values = .3 * np.exp(np.linspace(-.5, .5, 41))
    return [feature(float(value), float(1 / value)) for value in values]


def calibration():
    return fit_calibration(baseline(np.full(41, .28358)), baseline())


def test_eyes_open_log_median_maps_to_50_for_both_indices():
    fitted = calibration()
    observed = score_feature(feature(math.exp(fitted["attention"]["center_log"]),
                                     math.exp(fitted["meditation"]["center_log"])), fitted)
    assert observed["score"] == pytest.approx(50)
    assert observed["meditation"] == pytest.approx(50)
    assert observed["calibration_method"] == CALIBRATION_VERSION
    assert fitted["baseline"] == "eyes_open_rest"
    assert LiveProcessor().config["calibration_method"] == CALIBRATION_VERSION
    # Persisted parameters round-trip, including distribution counts and the
    # engineering rationale; a new process need not guess any scale constants.
    saved = json.loads(json.dumps(fitted, allow_nan=False))
    validate_calibration(saved)
    assert saved["attention"]["open_distribution"]["log"]["count"] == 41
    assert saved["closed_valid_windows"] == saved["open_valid_windows"] == 41
    assert saved["parameter_status"] == "engineering_draft_requires_empirical_validation"


def test_similar_closed_and_open_medians_do_not_amplify_tiny_changes():
    opened = baseline(.31443 * np.exp(np.linspace(-.5, .5, 41)))
    fitted = fit_calibration(baseline(np.full(41, .28358)), opened)
    values = [score_feature(feature(raw), fitted)["score"] for raw in (.30443, .31443, .32443)]
    assert values[1] == pytest.approx(50)
    assert 45 < values[0] < 50 < values[2] < 55
    # Even identical resting states are acceptable: separation between eyes
    # closed and open is no longer the denominator of the scoring function.
    identical = fit_calibration(opened, opened)
    assert identical["attention"]["scale_log"] == fitted["attention"]["scale_log"]


def test_closed_state_is_recorded_but_cannot_shift_training_score_scale():
    opened = baseline()
    a = fit_calibration(baseline(np.full(41, .00001)), opened)
    b = fit_calibration(baseline(np.full(41, 10000.)), opened)
    assert a["attention"]["closed_median"] != b["attention"]["closed_median"]
    assert a["attention"]["center_log"] == b["attention"]["center_log"]
    assert a["attention"]["scale_log"] == b["attention"]["scale_log"]
    assert score_feature(feature(.38), a)["score"] == score_feature(feature(.38), b)["score"]


def test_a_single_large_outlier_does_not_stretch_the_baseline_scale():
    opened = baseline()
    original = fit_calibration(opened, opened)
    contaminated = copy.deepcopy(opened)
    contaminated[-1] = feature(1e100, 1e-100)
    fitted = fit_calibration(opened, contaminated)
    for label in ("attention", "meditation"):
        assert fitted[label]["center_log"] == pytest.approx(original[label]["center_log"])
        assert fitted[label]["scale_log"] == pytest.approx(original[label]["scale_log"])
    assert abs(score_feature(feature(.4), fitted)["score"] - score_feature(feature(.4), original)["score"]) < .01


def test_constant_baseline_uses_a_documented_scale_floor():
    values = baseline(np.full(25, .3))
    fitted = fit_calibration(values, values)
    params = fitted["attention"]
    assert params["scale_log"] == pytest.approx(math.log(1.2))
    assert params["scale_floor_applied"] and params["robust_scale_log"] == 0
    assert params["sigmoid_temperature"] == 1.5
    center = score_feature(feature(.3), fitted)["score"]
    slightly_higher = score_feature(feature(.300001), fitted)["score"]
    assert 0 < slightly_higher - center < .001


def test_mapping_is_monotone_bounded_stable_and_fixed_during_training():
    fitted = calibration()
    snapshot = copy.deepcopy(fitted)
    scores = [score_feature(feature(float(raw)), fitted) for raw in np.geomspace(1e-300, 1e300, 101)]
    values = [item["score"] for item in scores]
    assert all(np.isfinite(values)) and all(0 <= value <= 100 for value in values)
    assert values == sorted(values)
    assert not any(item["attention_clipped"] for item in scores)
    assert fitted == snapshot
    # Practical baseline-range changes remain distinct, rather than collapsing
    # into zero/full-scale because of the difference between two resting states.
    central = [score_feature(feature(raw), fitted)["score"] for raw in (.1, .2, .3, .4, .6)]
    assert 0 < central[0] < central[1] < central[2] < central[3] < central[4] < 100


@pytest.mark.parametrize("bad", [0., -1., float("nan"), float("inf"), True, "0.3", None])
def test_nonpositive_or_nonfinite_baseline_samples_are_not_counted(bad):
    good = baseline(np.full(20, .3))
    fitted = fit_calibration(good + [feature(bad)], good + [feature(bad)])
    assert fitted["closed_valid_windows"] == fitted["open_valid_windows"] == 20
    with pytest.raises(CalibrationError, match="不足"):
        fit_calibration(good[:19] + [feature(bad)], good)
    with pytest.raises(CalibrationError, match="特征无效"):
        score_feature(feature(bad), fitted)


def test_invalid_quality_windows_never_produce_a_score():
    fitted = calibration()
    result = score_feature(feature(None, None, valid=False), fitted)
    assert result["score"] is result["attention"] is result["meditation"] is None


@pytest.mark.parametrize("field,bad", [("center_log", float("nan")), ("center_log", float("inf")),
                                      ("scale_log", 0.), ("scale_log", -1.),
                                      ("scale_log", LOG_SCALE_FLOOR / 2), ("scale_log", float("inf")),
                                      ("scale_floor_log", 0.), ("sigmoid_temperature", 0.),
                                      ("sigmoid_temperature", float("nan"))])
def test_invalid_new_mapping_parameters_are_rejected(field, bad):
    fitted = calibration()
    fitted["attention"][field] = bad
    with pytest.raises(CalibrationError, match="参数"):
        validate_calibration(fitted)
    with pytest.raises(CalibrationError, match="参数"):
        score_feature(feature(.3), fitted)


@pytest.mark.parametrize("version", ["future-v9", "", 2, {}])
def test_unknown_explicit_calibration_versions_are_rejected(version):
    fitted = calibration()
    fitted["version"] = version
    with pytest.raises(CalibrationError, match="未知校准"):
        validate_calibration(fitted)
    with pytest.raises(CalibrationError, match="未知校准"):
        fit_calibration(baseline(), baseline(), version=version)


def test_recorded_legacy_mapping_remains_exact_and_missing_version_is_legacy_only():
    recorded = {"version": LEGACY_CALIBRATION_VERSION,
                "attention": {"x_low": .28358, "x_high": .31443},
                "meditation": {"x_low": 2., "x_high": 4.}}
    for raw in (.2, .28358, .3, .31443, .4):
        old_feature = {"valid": True, "attention_raw": raw, "meditation_raw": 3.}
        unbounded = 100 * (raw - .28358) / (.31443 - .28358)
        expected = float(np.clip(unbounded, 0, 100))
        result = score_feature(old_feature, recorded)
        assert result["score"] == expected
        assert result["meditation"] == 50
        assert result["attention_clipped"] == (unbounded < 0 or unbounded > 100)
        missing = {key: value for key, value in recorded.items() if key != "version"}
        assert score_feature(old_feature, missing)["score"] == expected
        assert score_feature(old_feature, missing)["calibration_method"] == LEGACY_CALIBRATION_VERSION
    with pytest.raises(CalibrationError):
        validate_calibration({**recorded, "attention": {"x_low": 1, "x_high": 1}})


def test_mixed_waveform_configuration_and_mismatched_score_version_are_rejected():
    values = baseline()
    changed = copy.deepcopy(values)
    changed[-1]["algorithm_version"] = "future-waveform-v9"
    with pytest.raises(CalibrationError, match="不同算法"):
        fit_calibration(values, changed)
    fitted = calibration()
    with pytest.raises(CalibrationError, match="不同算法版本"):
        score_feature({**feature(.3), "algorithm_version": "future-waveform-v9"}, fitted)
    with pytest.raises(CalibrationError, match="不同算法配置"):
        score_feature({**feature(.3), "smooth_alpha": .2}, fitted)


@pytest.mark.parametrize("minimum", [0, -1, True, 2.5, float("nan")])
def test_invalid_required_window_count_is_rejected(minimum):
    with pytest.raises(ValueError):
        fit_calibration(baseline(), baseline(), min_valid_windows=minimum)
