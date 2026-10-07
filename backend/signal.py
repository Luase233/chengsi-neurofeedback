"""Versioned causal waveform processing for the Muse live pipeline.

Quality thresholds below are initial engineering guards in microvolts, not clinical
criteria. Calibration and training must use this same pipeline and version.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import numpy as np
from scipy.signal import butter, iirnotch, sosfilt, sosfilt_zi, tf2sos, welch

from .providers import CHANNELS, EEGChunk

ALGORITHM_VERSION = "muse-live-v1.0"
LEGACY_QUALITY_VERSION = "muse-quality-draft-v1.0"
QUALITY_VERSION = "muse-quality-draft-v1.1"
LEGACY_CALIBRATION_VERSION = "sorted-rest-minmax-v1.0"
CALIBRATION_VERSION = "eyes-open-robust-log-v2.0"
LOG_SCALE_FLOOR = math.log(1.2)
SIGMOID_TEMPERATURE = 1.5


@dataclass(frozen=True)
class QualityConfig:
    artifact_domain: str = "filtered"
    warmup_seconds: float = 1.0
    maximum_gap_samples: float = 1.5
    maximum_sdk_timestamp_step_seconds: float = .25
    flatline_std_uv: float = .1
    saturation_absolute_uv: float = 995.0
    artifact_peak_to_peak_uv: float = 350.0
    artifact_step_uv: float = 150.0
    artifact_centered_rms_uv: float = 75.0
    minimum_band_power_uv2: float = 1e-8
    # A warning, not a claim that electrode contact is good or bad.
    line_noise_ratio: float = .5
    line_noise_rms_uv: float = 10.0
    # 0.5 uV RMS is about one Muse ADC step (2000 / 4096 uV). This
    # conservative floor exceeds pure quantization-error power. It only rejects
    # almost-empty 4-30 Hz signals; it cannot authenticate physiological EEG.
    minimum_residual_band_power_uv2: float = .25

    def __post_init__(self):
        if self.artifact_domain not in ("raw", "filtered"):
            raise ValueError("artifact_domain must be raw or filtered")


class CalibrationError(ValueError):
    pass


def integrate_band(freqs: np.ndarray, density: np.ndarray, low: float, high: float) -> np.ndarray:
    """Integrate piecewise-linear PSD once over [low, high].

    Adjacent bands share only an endpoint of zero width, never a frequency interval.
    Density may be (frequency,) or (frequency, channel); output is in uV**2.
    """
    f, p = np.asarray(freqs), np.asarray(density)
    scalar = p.ndim == 1
    if scalar:
        p = p[:, None]
    inside = (f > low) & (f < high)
    x = np.r_[low, f[inside], high]
    y = np.vstack(([np.interp(low, f, p[:, i]) for i in range(p.shape[1])], p[inside],
                   [np.interp(high, f, p[:, i]) for i in range(p.shape[1])]))
    value = np.trapezoid(y, x=x, axis=0)
    return float(value[0]) if scalar else value


class LiveProcessor:
    def __init__(self, sample_rate: float = 256, smooth_alpha: float = .65, quality: QualityConfig | dict | None = None,
                 profile: str | None = None):
        self.sample_rate = float(sample_rate)
        if not np.isfinite(self.sample_rate) or self.sample_rate <= 100:
            raise ValueError("EEG sample rate must exceed 100 Hz for this 50 Hz notch pipeline")
        if not 0 < smooth_alpha <= 1:
            raise ValueError("smooth_alpha must be in (0, 1]")
        self.smooth_alpha = float(smooth_alpha)
        self.profile = profile or ("responsive-v1" if smooth_alpha == .65 else "report-v1" if smooth_alpha == .2 else "custom-v1")
        # Saved v1.0 sessions do not have artifact_domain. Replaying those must
        # retain their original raw-domain decision, including calibration.
        self.quality = QualityConfig(**{"artifact_domain": "raw", **quality}) if isinstance(quality, dict) else quality or QualityConfig()
        self.quality_version = QUALITY_VERSION if self.quality.artifact_domain == "filtered" else LEGACY_QUALITY_VERSION
        bandpass = butter(4, [.5, 40], fs=self.sample_rate, btype="bandpass", output="sos")
        notch_b, notch_a = iirnotch(50, 30, fs=self.sample_rate)
        self._notch_sos = tf2sos(notch_b, notch_a)
        self._sos = np.vstack((bandpass, self._notch_sos))
        self.window_samples = round(2 * self.sample_rate)
        self.step_samples = round(self.sample_rate)
        self.config = {"algorithm_version": ALGORITHM_VERSION, "quality_version": self.quality_version,
                       "calibration_method": CALIBRATION_VERSION,
                       "sample_rate": self.sample_rate, "profile": self.profile, "units": "uV", "band_units": "uV^2",
                       "channels": list(CHANNELS), "reference": "AF7-TP9; AF8-TP10",
                       "window_seconds": 2, "step_seconds": 1, "smooth_alpha": self.smooth_alpha,
                       "welch": {"window": "hamming", "nperseg": round(self.sample_rate),
                                 "noverlap": round(self.sample_rate) // 2, "scaling": "density",
                                 "detrend": "constant", "average": "mean", "onesided": True},
                       "band_integration": "piecewise-linear PSD integral; adjacent bands share no interval",
                       "filter": "causal SOS: Butterworth order 4, 0.5-40 Hz; 50 Hz notch Q=30",
                       "quality_domains": {"flatline_saturation": "raw",
                                           "amplitude_and_step": self.quality.artifact_domain,
                                           "wideband_spike_guard": "parallel causal 50 Hz notch Q=30, no low-pass; same artifact_step_uv threshold" if self.quality.artifact_domain == "filtered" else "disabled (legacy raw step guard)",
                                           "residual_signal": "raw 2-second Hann PSD integrated over 4-30 Hz" if self.quality.artifact_domain == "filtered" else "disabled (legacy)",
                                           "powerline_warning": "raw 2-second Hann PSD: 48-52 Hz / total power"},
                       "quality": asdict(self.quality)}
        self.reset()

    def reset(self, reason: str | None = None):
        self._raw = np.empty((0, 4))
        self._filtered = np.empty((0, 4))
        self._notched = np.empty((0, 4))
        self._timestamps = np.empty(0)
        self._zi = None
        self._notch_zi = None
        self._ema = None
        self._last_timestamp = None
        self._segment_start = None
        self.last_reset_reason = reason

    def _invalid(self, start: float | None, end: float | None, reasons: list[str], channel_quality=None):
        return {"window_start": start, "window_end": end, "valid": False, "reasons": reasons,
                "channel_quality": channel_quality or {}, "bands": None, "attention_raw": None,
                "meditation_raw": None, "algorithm_version": ALGORITHM_VERSION, "quality_version": self.quality_version,
                "algorithm_profile": self.profile, "smooth_alpha": self.smooth_alpha}

    def push(self, chunk: EEGChunk) -> list[dict]:
        if not math.isclose(chunk.sample_rate, self.sample_rate, rel_tol=1e-6):
            raise ValueError("Sample rate changed; construct a new processor and recalibrate")
        names = [name.upper() for name in chunk.channels]
        if len(set(names)) != len(names) or any(name not in names for name in CHANNELS):
            raise ValueError("All four uniquely named Muse EEG channels are required")
        samples = chunk.samples_uv[:, [names.index(name) for name in CHANNELS]]
        timestamps = chunk.timestamps
        sdk_clock = (chunk.metadata.get("clock_mode") == "brainflow-muse-arrival-interpolated"
                     and len(chunk.metadata.get("package_numbers", [])) == len(timestamps))
        packet_gaps = set(chunk.metadata.get("packet_gap_indices", [])) if sdk_clock else set()
        gap_limit = self.quality.maximum_sdk_timestamp_step_seconds if sdk_clock else self.quality.maximum_gap_samples / self.sample_rate
        output = []
        start = 0
        # Split only at actual discontinuities. Never interpolate missing samples.
        for index in range(len(timestamps)):
            stamp = timestamps[index]
            previous = timestamps[index - 1] if index > start else self._last_timestamp
            bad = not np.isfinite(stamp) or not np.all(np.isfinite(samples[index]))
            gap = (previous is not None and np.isfinite(stamp) and (stamp <= previous or stamp - previous > gap_limit)) or index in packet_gaps
            if bad or gap:
                if index > start:
                    output.extend(self._append(samples[start:index], timestamps[start:index]))
                reason = "non_finite_sample" if bad else "packet_gap" if index in packet_gaps else "non_monotonic_timestamp" if stamp <= previous else "timestamp_gap"
                output.append(self._invalid(float(previous) if previous is not None else None,
                                            float(stamp) if np.isfinite(stamp) else None, [reason]))
                self.reset(reason)
                start = index + 1 if bad else index
                # A gap's first real sample starts the new segment rather than being discarded.
                if not bad:
                    self._last_timestamp = None
        if start < len(timestamps):
            output.extend(self._append(samples[start:], timestamps[start:]))
        return output

    def _append(self, data, timestamps):
        if not len(data):
            return []
        if self._zi is None:
            self._zi = sosfilt_zi(self._sos)[:, :, None] * data[0][None, None, :]
            self._notch_zi = sosfilt_zi(self._notch_sos)[:, :, None] * data[0][None, None, :]
            self._segment_start = float(timestamps[0])
        filtered, self._zi = sosfilt(self._sos, data, axis=0, zi=self._zi)
        notched, self._notch_zi = sosfilt(self._notch_sos, data, axis=0, zi=self._notch_zi)
        self._raw = np.concatenate((self._raw, data))
        self._filtered = np.concatenate((self._filtered, filtered))
        self._notched = np.concatenate((self._notched, notched))
        self._timestamps = np.concatenate((self._timestamps, timestamps))
        self._last_timestamp = float(timestamps[-1])
        result = []
        while len(self._timestamps) >= self.window_samples:
            result.append(self._feature(self._raw[:self.window_samples], self._filtered[:self.window_samples],
                                        self._notched[:self.window_samples], self._timestamps[:self.window_samples]))
            self._raw = self._raw[self.step_samples:]
            self._filtered = self._filtered[self.step_samples:]
            self._notched = self._notched[self.step_samples:]
            self._timestamps = self._timestamps[self.step_samples:]
        return result

    def _feature(self, raw, filtered, notched, timestamps):
        start, end = float(timestamps[0]), float(timestamps[-1] + 1 / self.sample_rate)
        reasons, channels, warnings = [], {}, []
        # Use a separate full-window Hann spectrum for diagnostics. Measuring
        # the residual band on raw data avoids classifying filter startup tails
        # as EEG when the input consists only of mains plus ADC quantization.
        raw_freqs, raw_density = welch(raw, fs=self.sample_rate, window="hann", nperseg=len(raw),
                                      noverlap=0, detrend="constant", scaling="density", axis=0)
        line_powers = integrate_band(raw_freqs, raw_density, 48, 52)
        total_powers = integrate_band(raw_freqs, raw_density, 0, self.sample_rate / 2)
        residual_powers = integrate_band(raw_freqs, raw_density, 4, 30)
        if start < self._segment_start + self.quality.warmup_seconds - .5 / self.sample_rate:
            reasons.append("filter_warmup")
        for index, name in enumerate(CHANNELS):
            values = raw[:, index]
            centered = values - np.median(values)
            std, ptp = float(np.std(values)), float(np.ptp(values))
            rms, step = float(np.sqrt(np.mean(centered ** 2))), float(np.max(np.abs(np.diff(values))))
            cleaned = filtered[:, index]
            filtered_ptp = float(np.ptp(cleaned))
            filtered_rms = float(np.sqrt(np.mean((cleaned - np.median(cleaned)) ** 2)))
            filtered_step = float(np.max(np.abs(np.diff(cleaned))))
            notched_step = float(np.max(np.abs(np.diff(notched[:, index]))))
            if self.quality.artifact_domain == "filtered":
                artifact_ptp, artifact_rms, artifact_step = filtered_ptp, filtered_rms, filtered_step
            else:
                artifact_ptp, artifact_rms, artifact_step = ptp, rms, step
            flags = []
            if std < self.quality.flatline_std_uv:
                flags.append("flatline")
            if np.max(np.abs(values)) >= self.quality.saturation_absolute_uv:
                flags.append("saturation")
            if artifact_ptp > self.quality.artifact_peak_to_peak_uv or artifact_rms > self.quality.artifact_centered_rms_uv:
                flags.append("amplitude_artifact")
            # A 40 Hz low-pass can spread a genuine one-sample spike until it
            # passes the amplitude/step tests. Preserve it through this parallel
            # notch-only path while suppressing regular mains oscillation.
            if artifact_step > self.quality.artifact_step_uv or (self.quality.artifact_domain == "filtered" and notched_step > self.quality.artifact_step_uv):
                flags.append("large_step")
            if self.quality.artifact_domain == "filtered" and residual_powers[index] < self.quality.minimum_residual_band_power_uv2:
                flags.append("insufficient_residual_signal")
            line_ratio = float(line_powers[index] / max(total_powers[index], 1e-30))
            line_rms = float(np.sqrt(max(line_powers[index], 0)))
            channel_warnings = ["powerline_50hz"] if line_ratio >= self.quality.line_noise_ratio and line_rms >= self.quality.line_noise_rms_uv else []
            warnings.extend(warning for warning in channel_warnings if warning not in warnings)
            channels[name] = {"valid": not flags, "reasons": flags, "std_uv": std, "peak_to_peak_uv": ptp,
                              "centered_rms_uv": rms, "max_step_uv": step,
                              "artifact_domain": self.quality.artifact_domain,
                              "filtered_std_uv": float(np.std(cleaned)), "filtered_peak_to_peak_uv": filtered_ptp,
                              "filtered_centered_rms_uv": filtered_rms, "filtered_max_step_uv": filtered_step,
                              "notched_max_step_uv": notched_step,
                              "line_noise_ratio": line_ratio, "line_noise_rms_uv": line_rms,
                              "residual_band_power_uv2": float(residual_powers[index]), "warnings": channel_warnings}
            reasons.extend(flag for flag in flags if flag not in reasons)
        if reasons:
            self._ema = None
            return {**self._invalid(start, end, reasons, channels), "warnings": warnings}
        differences = np.column_stack((filtered[:, 1] - filtered[:, 0], filtered[:, 2] - filtered[:, 3]))
        segment = round(self.sample_rate)
        freqs, density = welch(differences, fs=self.sample_rate, window="hamming", nperseg=segment,
                               noverlap=segment // 2, nfft=segment, detrend="constant", return_onesided=True,
                               scaling="density", axis=0, average="mean")
        powers = np.array([np.mean(integrate_band(freqs, density, low, high))
                           for low, high in ((4, 8), (8, 13), (13, 30))])
        self._ema = powers if self._ema is None else self.smooth_alpha * powers + (1 - self.smooth_alpha) * self._ema
        theta, alpha, beta = self._ema
        if not np.all(np.isfinite(self._ema)) or alpha + theta <= self.quality.minimum_band_power_uv2 or beta <= self.quality.minimum_band_power_uv2:
            self._ema = None
            return {**self._invalid(start, end, ["insufficient_band_power"], channels), "warnings": warnings}
        return {"window_start": start, "window_end": end, "valid": True, "reasons": [], "channel_quality": channels,
                "bands": {"theta": float(theta), "alpha": float(alpha), "beta": float(beta)},
                "attention_raw": float(beta / (alpha + theta)), "meditation_raw": float(alpha / beta),
                "algorithm_version": ALGORITHM_VERSION, "quality_version": self.quality_version, "warnings": warnings,
                "algorithm_profile": self.profile, "smooth_alpha": self.smooth_alpha}


def _finite_number(value) -> bool:
    return (not isinstance(value, (bool, np.bool_))
            and isinstance(value, (float, int, np.floating, np.integer)) and bool(np.isfinite(value)))


def _calibration_method(calibration: dict) -> str:
    # Early recorded fixtures have no version. Only this omission means legacy;
    # an unknown explicit version must never silently choose a new mapping.
    version = calibration.get("version")
    return LEGACY_CALIBRATION_VERSION if version is None else version


def validate_calibration(calibration: dict) -> None:
    """Validate recorded mapping parameters without changing or refitting them."""
    if not isinstance(calibration, dict):
        raise CalibrationError("校准参数无效")
    method = _calibration_method(calibration)
    if method not in (LEGACY_CALIBRATION_VERSION, CALIBRATION_VERSION):
        raise CalibrationError("未知校准方法，请使用支持该记录版本的程序")
    if calibration.get("algorithm_version", ALGORITHM_VERSION) != ALGORITHM_VERSION:
        raise CalibrationError("校准使用了不支持的算法版本")
    for label in ("attention", "meditation"):
        params = calibration.get(label)
        if not isinstance(params, dict):
            raise CalibrationError("校准参数无效")
        if method == LEGACY_CALIBRATION_VERSION:
            low, high = params.get("x_low"), params.get("x_high")
            if not all(_finite_number(value) for value in (low, high)) or high <= low:
                raise CalibrationError("校准参数范围无效")
        else:
            center, scale = params.get("center_log"), params.get("scale_log")
            floor, temperature = params.get("scale_floor_log"), params.get("sigmoid_temperature")
            if (not all(_finite_number(value) for value in (center, scale, floor, temperature))
                    or floor <= 0 or scale < floor or temperature <= 0):
                raise CalibrationError("校准参数中心、尺度或映射温度无效")


def _distribution(values: np.ndarray) -> dict:
    q05, q25, median, q75, q95 = np.quantile(values, [.05, .25, .5, .75, .95])
    return {"count": len(values), "minimum": float(np.min(values)), "q05": float(q05),
            "q25": float(q25), "median": float(median), "q75": float(q75), "q95": float(q95),
            "maximum": float(np.max(values)), "mad": float(np.median(np.abs(values - median))),
            "iqr": float(q75 - q25)}


def fit_calibration(closed_features, open_features, min_valid_windows: int = 20,
                    min_relative_span: float = .05, min_absolute_span: float = 1e-6, *,
                    version: str = CALIBRATION_VERSION) -> dict:
    """Fit a fixed personal scale, with closed-eye data retained as a contrast.

    The default mapping is an engineering feedback index relative to eyes-open
    rest. It is not a validated absolute attention measurement or a population
    percentile. Explicit legacy fitting is retained only for reproducibility.
    """
    if isinstance(min_valid_windows, bool) or not isinstance(min_valid_windows, (int, np.integer)) or min_valid_windows < 1:
        raise ValueError("min_valid_windows must be a positive integer")
    if version not in (LEGACY_CALIBRATION_VERSION, CALIBRATION_VERSION):
        raise CalibrationError("未知校准方法")
    if (not all(_finite_number(value) for value in (min_relative_span, min_absolute_span))
            or min_relative_span < 0 or min_absolute_span < 0):
        raise ValueError("legacy minimum spans must be finite and nonnegative")

    def valid(features):
        return [feature for feature in features if feature.get("valid") and all(
            _finite_number(feature.get(key)) and (feature[key] > 0 if version == CALIBRATION_VERSION else feature[key] >= 0)
            for key in ("attention_raw", "meditation_raw"))]

    closed, opened = valid(closed_features), valid(open_features)
    if min(len(closed), len(opened)) < min_valid_windows:
        raise CalibrationError(f"有效校准窗不足：闭眼 {len(closed)}，睁眼 {len(opened)}，每段至少 {min_valid_windows}")
    profiles = {(feature.get("algorithm_profile"), feature.get("smooth_alpha")) for feature in closed + opened}
    versions = {feature.get("algorithm_version", ALGORITHM_VERSION) for feature in closed + opened}
    if len(profiles) != 1 or versions != {ALGORITHM_VERSION}:
        raise CalibrationError("校准窗口混用了不同算法配置，请重新校准")
    quality_versions = {feature.get("quality_version") or LEGACY_QUALITY_VERSION for feature in closed + opened}
    if len(quality_versions) != 1:
        raise CalibrationError("校准窗口混用了不同质量规则，请重新校准")
    quality_version = next(iter(quality_versions))
    profile, smoothing = next(iter(profiles))
    result = {"version": version, "algorithm_version": ALGORITHM_VERSION, "quality_version": quality_version,
              "closed_valid_windows": len(closed), "open_valid_windows": len(opened), "target_range": [0, 100],
              "min_valid_windows": min_valid_windows, "algorithm_profile": profile, "smooth_alpha": smoothing}
    if version == LEGACY_CALIBRATION_VERSION:
        result.update(min_relative_span=min_relative_span, min_absolute_span=min_absolute_span)
    else:
        result.update(baseline="eyes_open_rest", closed_role="contrast_only_not_score_endpoint",
                      adaptation="fixed_for_training_no_online_refit", transform="natural_log",
                      mapping="100 * sigmoid((log(raw) - center_log) / scale_log / sigmoid_temperature)",
                      scale_rule="max(1.4826 * MAD(log(open)), IQR(log(open)) / 1.349, log(1.2))",
                      scale_constants={"mad_multiplier": 1.4826, "iqr_divisor": 1.349,
                                       "floor_relative_factor": 1.2},
                      quantile_method="numpy.quantile linear",
                      scale_floor_source="Engineering choice: log(1.2) prevents a nearly constant baseline from amplifying tiny relative changes; not a clinical threshold",
                      interpretation="Within-person feedback index relative to eyes-open rest; not an absolute attention score, diagnostic criterion or population percentile",
                      parameter_status="engineering_draft_requires_empirical_validation")
    for label, key in (("attention", "attention_raw"), ("meditation", "meditation_raw")):
        c_values = np.asarray([feature[key] for feature in closed], dtype=float)
        o_values = np.asarray([feature[key] for feature in opened], dtype=float)
        c, o = float(np.median(c_values)), float(np.median(o_values))
        if version == LEGACY_CALIBRATION_VERSION:
            low, high = sorted((c, o))
            if high - low <= max(min_absolute_span, min_relative_span * max(abs(low), abs(high))):
                raise CalibrationError(f"{label} 的两段参考值过于接近，请调整佩戴并重新校准")
            result[label] = {"x_low": low, "x_high": high, "closed_median": c, "open_median": o}
            continue
        c_log, o_log = _distribution(np.log(c_values)), _distribution(np.log(o_values))
        robust_scale = max(1.4826 * o_log["mad"], o_log["iqr"] / 1.349)
        result[label] = {"center_log": o_log["median"], "scale_log": max(robust_scale, LOG_SCALE_FLOOR),
                         "robust_scale_log": robust_scale, "scale_floor_log": LOG_SCALE_FLOOR,
                         "scale_floor_applied": robust_scale < LOG_SCALE_FLOOR,
                         "sigmoid_temperature": SIGMOID_TEMPERATURE,
                         "closed_median": c, "open_median": o,
                         "closed_distribution": {"raw": _distribution(c_values), "log": c_log},
                         "open_distribution": {"raw": _distribution(o_values), "log": o_log}}
    validate_calibration(result)
    return result


def _sigmoid(value: float) -> float:
    # The exponent is always nonpositive, including extreme finite EEG ratios.
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    exponent = math.exp(value)
    return exponent / (1 + exponent)


def score_feature(feature: dict, calibration: dict) -> dict:
    validate_calibration(calibration)
    method = _calibration_method(calibration)
    result = {**feature, "attention": None, "score": None, "meditation": None,
              "calibration_version": method, "calibration_method": method}
    if not feature.get("valid"):
        return result
    if feature.get("algorithm_version", ALGORITHM_VERSION) != calibration.get("algorithm_version", ALGORITHM_VERSION):
        raise CalibrationError("评分与校准使用了不同算法版本")
    if (feature.get("quality_version") or LEGACY_QUALITY_VERSION) != (calibration.get("quality_version") or LEGACY_QUALITY_VERSION):
        raise CalibrationError("评分与校准使用了不同质量规则，请重新校准")
    if calibration.get("algorithm_profile") and (feature.get("algorithm_profile"), feature.get("smooth_alpha")) != (calibration["algorithm_profile"], calibration.get("smooth_alpha")):
        raise CalibrationError("评分与校准使用了不同算法配置")
    for label, raw_key in (("attention", "attention_raw"), ("meditation", "meditation_raw")):
        params = calibration[label]
        raw = feature.get(raw_key)
        if not _finite_number(raw) or (method == CALIBRATION_VERSION and raw <= 0):
            raise CalibrationError("校准参数或特征无效")
        if method == LEGACY_CALIBRATION_VERSION:
            low, high = params["x_low"], params["x_high"]
            unbounded = 100 * (raw - low) / (high - low)
            result[label] = float(np.clip(unbounded, 0, 100))
            result[label + "_clipped"] = bool(unbounded < 0 or unbounded > 100)
        else:
            z = (math.log(raw) - params["center_log"]) / params["scale_log"]
            result[label] = 100 * _sigmoid(z / params["sigmoid_temperature"])
            result[label + "_clipped"] = False  # smooth asymptote, no min/max clipping
    result["score"] = result["attention"]
    return result
