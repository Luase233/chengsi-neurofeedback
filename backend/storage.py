"""Append-only, flush-on-write session evidence outside the static asset routes."""
import csv
import io
import json
import math
import os
import re
import tempfile
import warnings
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def clean(value):
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if hasattr(value, "tolist"):
        return clean(value.tolist())
    if hasattr(value, "item"):
        return clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Path):
        return str(value)
    return value


def json_text(value):
    return json.dumps(clean(value), ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def session_path(root: Path, session_id: str):
    try:
        canonical = str(UUID(session_id))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Invalid session id") from error
    if canonical != session_id:
        raise ValueError("Non-canonical session id")
    return root / canonical


class SessionStore:
    def __init__(self, root: Path, session_id: str):
        self.directory = session_path(root, session_id)
        self.directory.mkdir(parents=True, exist_ok=False)
        self._events = (self.directory / "events.jsonl").open("a", encoding="utf-8", newline="")
        self._raw = None
        self._raw_writer = None
        self._features = (self.directory / "features.csv").open("w", encoding="utf-8", newline="")
        self._feature_writer = csv.DictWriter(self._features, fieldnames=[
            "feature_index", "phase", "window_start", "window_end", "valid", "reasons", "channel_quality",
            "bands", "attention_raw", "meditation_raw", "score", "meditation", "calibration_version",
            "algorithm_version", "quality_version", "algorithm_profile", "smooth_alpha", "calibration_method",
            "baseline_id", "valid_duration", "contribution_start", "contribution_end",
        ])
        self._feature_writer.writeheader()
        self._features.flush()
        self.sample_index = 0
        self.feature_index = 0
        self.channels = []
        self._closed = False

    def configure_raw(self, channels):
        channels = list(channels)
        if self._raw is not None:
            if channels != self.channels:
                raise ValueError("Channel layout changed within a session")
            return
        self.channels = channels
        self._raw = (self.directory / "raw.csv").open("w", encoding="utf-8", newline="")
        self._raw_writer = csv.writer(self._raw)
        self._raw_writer.writerow(["timestamp", "sample_index", "phase", "packet_num", *channels])
        self._raw.flush()

    def raw(self, timestamps, samples, phase, package_numbers=None):
        if self._closed or self._raw_writer is None:
            return
        for index, (timestamp, row) in enumerate(zip(timestamps, samples)):
            packet = package_numbers[index] if package_numbers is not None and index < len(package_numbers) else ""
            self._raw_writer.writerow([f"{float(timestamp):.9f}", self.sample_index, phase, packet, *[f"{float(v):.9g}" for v in row]])
            self.sample_index += 1
        self._raw.flush()

    def feature(self, feature, phase, calibration_version=None):
        if self._closed:
            return
        row = {"feature_index": self.feature_index, "phase": phase, "calibration_version": calibration_version}
        for key in self._feature_writer.fieldnames:
            if key in feature:
                value = clean(feature[key])
                row[key] = json_text(value) if isinstance(value, (dict, list)) else value
        row["calibration_version"] = calibration_version
        self._feature_writer.writerow(row)
        self._features.flush()
        self.feature_index += 1

    def event(self, kind, **payload):
        if self._closed:
            return
        self._events.write(json_text({"at": utc_now(), "type": kind, **payload}) + "\n")
        self._events.flush()
        os.fsync(self._events.fileno())

    def save(self, state):
        if self._closed:
            return
        target = self.directory / "session.json"
        temp = self.directory / "session.json.tmp"
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(json_text(state) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)

    def save_baseline(self, baseline):
        """Self-contained frozen baseline makes each exported session reproducible."""
        target = self.directory / "baseline.json"
        temporary = self.directory / "baseline.json.tmp"
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json_text(baseline) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)

    def flush(self):
        for handle in (self._raw, self._features, self._events):
            if handle and not handle.closed:
                handle.flush()

    def close(self):
        if self._closed:
            return
        for handle in (self._raw, self._features, self._events):
            if handle and not handle.closed:
                handle.flush()
                os.fsync(handle.fileno())
                handle.close()
        self._closed = True


def read_session(directory: Path, active_session_id=None):
    state = json.loads((directory / "session.json").read_text(encoding="utf-8"))
    if state.get("session_id") != active_session_id and state.get("phase") not in {"completed", "error", "idle"}:
        state["interrupted"] = True
        state["phase_before_interrupt"] = state.get("phase")
        state["phase"] = "error"
        state["error"] = "上次服务退出时会话尚未结束；保留已写入数据，不自动恢复训练"
        state.setdefault("feedback", {}).update(score=None, meditation=None, valid=False)
        state.setdefault("quality", {}).update(valid=False, reasons=["process_interrupted"])
        state.setdefault("connection", {}).update(status="disconnected", message="采集服务已中断")
    return state


def list_sessions(root: Path, active_session_id=None):
    rows = []
    if not root.exists():
        return rows
    for item in root.iterdir():
        try:
            state = read_session(item, active_session_id)
            rows.append({key: state.get(key) for key in ["session_id", "mode", "participant_id", "program_id", "phase", "created_at", "updated_at", "summary", "interrupted", "phase_before_interrupt"]})
        except (OSError, ValueError):
            continue
    return sorted(rows, key=lambda row: row.get("created_at") or "", reverse=True)


class EDFExportError(ValueError):
    """The requested EDF archive could not be made without changing evidence."""


def export_session(directory: Path, include_edf=False, sessions_root=None):
    """Create an archive from a finite snapshot; never serve the runtime directory."""
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ("raw.csv", "features.csv", "events.jsonl", "session.json", "baseline.json"):
            path = directory / name
            if path.exists():
                archive.writestr(name, json_text(read_session(directory)) + "\n" if name == "session.json" else path.read_bytes())
        if include_edf:
            try:
                artifacts = _make_edf(directory)
                manifest = json.loads(artifacts["edf-segments.json"])
                reference, reference_artifacts = _baseline_reference(directory, sessions_root)
                manifest["baseline_reference"] = reference
                artifacts["edf-segments.json"] = json_text(manifest).encode("utf-8")
                artifacts.update(reference_artifacts)
                for name, content in artifacts.items():
                    archive.writestr(name, content)
                archive.writestr("edf-notes.txt", "原始脑波按实际采集阶段、任务和连续片段导出 EDF+，通道顺序 AF7、AF8、TP9、TP10，单位 uV，保留实际采样率。包含伪迹，不按质量筛选、不滤波、不补点、不重采样。闭眼/睁眼文件为实际前测，复用的历史前测仅放在 baseline-reference/ 并标明来源，不视作本次重新采集。原始 CSV 始终保留精确到达时间及全部样本；EDF 为 16 位线性量化，其量化步长写入清单。\nEDF files contain separate contiguous task segments on a nominal sampling clock. For Muse records with complete packet numbers, continuity is determined by packet sequence/count plus long outages and non-monotonic arrival timestamps; normal BLE arrival jitter is not treated as missing samples. EDF does not reproduce each arrival timestamp. No missing samples are filled. Exact original timestamps and one-to-one sample mappings remain in raw.csv and edf-segments.json. EDF record durations are exactly representable both in the header and this writer; any remaining tail samples stay in CSV and are explicitly listed. Header start time uses UTC; exact fractional timestamps remain in the manifest and CSV.\n")
            except Exception as error:
                raise EDFExportError(f"EDF 导出失败：{error}。原始 CSV 未改变，可单独导出数据记录。") from error
    return payload.getvalue()


def _safe_slug(value, fallback="participant"):
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "")).strip("-_")[:40]
    return slug or fallback


def _record_layout(sample_count, rate):
    """EDFlib writes duration to 10us, even though EDF can represent 1/256s.

    Require exact representability; 256 Hz needs multiples of 8 samples, not
    4. Never let writeSamples pad a partial record or silently drift the rate.
    """
    from fractions import Fraction
    quantum = rate // math.gcd(rate, 100000)
    count = sample_count // quantum * quantum
    if not count:
        return 0, None, None
    divisors = set()
    for divisor in range(1, math.isqrt(count) + 1):
        if count % divisor == 0:
            divisors.update((divisor, count // divisor))
    for samples_per_record in sorted(divisors, reverse=True):
        duration = Fraction(samples_per_record, rate)
        if not Fraction(1, 1000) <= duration <= 60 or (duration * 100000).denominator != 1:
            continue
        text = f"{float(duration):.5f}".rstrip("0").rstrip(".")
        if len(text) <= 8:
            return count, samples_per_record, float(duration)
    raise ValueError("Sampling rate has no exact EDF record duration")


def _make_edf(directory, *, selected_ranges=None, raw_csv_name="raw.csv", source_kind="session"):
    import numpy as np
    import pyedflib
    state = json.loads((directory / "session.json").read_text(encoding="utf-8"))
    channels = ["AF7", "AF8", "TP9", "TP10"]
    if set(state["connection"]["channels"]) != set(channels):
        raise ValueError("EDF requires the four named raw channels AF7/AF8/TP9/TP10")
    rate = float(state["connection"]["sample_rate"])
    if not math.isfinite(rate) or not rate.is_integer() or rate <= 0:
        raise ValueError("Invalid or unsupported recorded sampling rate")
    rate = int(rate)
    if state["connection"].get("units", "uV") not in {"uV", "µV", "μV"}:
        raise ValueError("Raw EEG units must be microvolts")
    with (directory / "raw.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("No raw EEG samples are available")
    timestamps = np.array([float(row["timestamp"]) for row in rows])
    if not np.all(np.isfinite(timestamps)):
        raise ValueError("Invalid timestamps cannot be represented in EDF; use raw.csv")
    values = np.array([[float(row[channel]) for row in rows] for channel in channels])
    if not np.all(np.isfinite(values)):
        raise ValueError("Raw signal contains nonfinite samples")
    steps = np.diff(timestamps)
    complete_packets = all(row.get("packet_num", "") != "" for row in rows)
    boundary_reasons = {}
    if complete_packets:
        from .providers import _PacketContinuity
        numbers = np.array([float(row["packet_num"]) for row in rows])
        if not np.all(np.isfinite(numbers)) or np.any(numbers != np.floor(numbers)) or np.any((numbers < 0) | (numbers > 65535)):
            raise ValueError("Invalid Muse packet numbers; nominal-clock EDF continuity cannot be verified")
        long_gap = float(state.get("algorithm_config", {}).get("quality", {}).get("maximum_sdk_timestamp_step_seconds", .25))
        if not math.isfinite(long_gap) or long_gap <= 0:
            raise ValueError("Invalid recorded arrival-clock gap threshold")
        for index in _PacketContinuity().inspect(numbers):
            boundary_reasons.setdefault(index, []).append("muse_packet_sequence_or_12_sample_count")
        for index in (np.flatnonzero(steps <= 0) + 1).tolist():
            boundary_reasons.setdefault(index, []).append("non_monotonic_arrival_timestamp")
        for index in (np.flatnonzero(steps > long_gap) + 1).tolist():
            boundary_reasons.setdefault(index, []).append("long_arrival_clock_outage")
        timestamp_basis = "nominal_sample_clock_from_muse_packet_continuity"
        continuity = {"packet_samples": 12, "packet_id_modulus": 65536, "long_gap_threshold_seconds": long_gap,
                      "arrival_clock": "brainflow-muse-arrival-interpolated", "normal_arrival_jitter_is_not_a_gap": True}
    else:
        for index in (np.flatnonzero(np.abs(steps - 1 / rate) > .5 / rate) + 1).tolist():
            boundary_reasons[index] = ["timestamp_step_outside_half_sample_tolerance"]
        timestamp_basis = "nominal_sample_clock_verified_by_regular_raw_timestamps"
        continuity = {"timestamp_step_tolerance_seconds": .5 / rate, "complete_packet_numbers_available": False}
    phase_changes = (np.flatnonzero([rows[index]["phase"] != rows[index - 1]["phase"] for index in range(1, len(rows))]) + 1).tolist()
    for index in phase_changes:
        boundary_reasons.setdefault(index, []).append("phase_changed")
    sample_indices = [int(row.get("sample_index", index)) for index, row in enumerate(rows)]
    if any(b <= a for a, b in zip(sample_indices, sample_indices[1:])):
        raise ValueError("Original sample indices are not strictly increasing")
    events_path = directory / "events.jsonl"
    if events_path.exists():
        index_to_row = {sample_index: index for index, sample_index in enumerate(sample_indices)}
        for line in events_path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("reason") in {"closed_calibration_started", "open_calibration_started", "training_started"}:
                index = index_to_row.get(event.get("sample_index"))
                if index is not None and index > 0:
                    boundary_reasons.setdefault(index, []).append("task_started")
    if selected_ranges is None:
        selected_ranges = [(sample_indices[0], sample_indices[-1] + 1)]
    selected = [any(begin <= sample_index < end for begin, end in selected_ranges) for sample_index in sample_indices]
    selected_indices = [index for index, included in enumerate(selected) if included]
    csv_row_mapping = {original: exported for exported, original in enumerate(selected_indices)}
    for index in range(1, len(rows)):
        if selected[index] != selected[index - 1]:
            boundary_reasons.setdefault(index, []).append("selected_calibration_round_boundary")
    boundaries = sorted({0, len(rows), *boundary_reasons})
    artifacts, segments, omitted = {}, [], []
    label_map = {"calibrating_closed": "calibration_eyes_closed", "calibrating_open": "calibration_eyes_open", "training": "train_task"}
    base_name = f"{_safe_slug(state.get('participant_id'))}_{_safe_slug(state.get('session_id'), 'session')[:8]}"
    task_counts = {}
    with tempfile.TemporaryDirectory(prefix="chengsi-edf-") as temporary:
        for begin, end in zip(boundaries, boundaries[1:]):
            if not selected[begin]:
                continue
            count, record_samples, record_duration = _record_layout(end - begin, rate)
            if begin + count < end:
                omitted.append({"first_sample_index": sample_indices[begin + count], "raw_data_row_zero_based": csv_row_mapping[begin + count], "sample_count": end - begin - count, "phase": rows[begin]["phase"], "reason": "tail_not_representable_as_exact_EDF_record; preserved_in_raw_csv"})
            if not count:
                continue
            phase = rows[begin].get("phase", "unknown")
            task = label_map.get(phase, f"recording_{_safe_slug(phase, 'unknown')}")
            task_counts[task] = task_counts.get(task, 0) + 1
            name = f"{base_name}_{task}_seg-{task_counts[task]:03d}.edf"
            path = Path(temporary) / name
            writer = pyedflib.EdfWriter(str(path), len(channels), file_type=pyedflib.FILETYPE_EDFPLUS)
            annotations = []
            try:
                headers = []
                for index, channel in enumerate(channels):
                    bound = math.ceil(max(1000, float(np.max(np.abs(values[index, begin:begin + count]))) * 1.05))
                    if len(str(-bound)) > 8:
                        raise ValueError("Raw voltage range exceeds exact EDF header capacity")
                    headers.append({"label": channel, "dimension": "uV", "sample_frequency": rate,
                                    "physical_min": -bound, "physical_max": bound,
                                    "digital_min": -32768, "digital_max": 32767})
                writer.setSignalHeaders(headers)
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", message="Forcing a specific record_duration.*")
                    writer.setDatarecordDuration(record_duration)
                writer.setStartdatetime(datetime.fromtimestamp(float(timestamps[begin]), timezone.utc).replace(tzinfo=None))
                writer.setPatientCode(_safe_slug(state.get("participant_id")))
                writer.setRecordingAdditional("raw-EEG_UTC_" + _safe_slug(state.get("source"), "unknown"))
                writer.writeSamples(values[:, begin:begin + count])
                cursor = begin
                while cursor < begin + count:
                    phase = rows[cursor].get("phase", "unknown")
                    stop = cursor + 1
                    while stop < begin + count and rows[stop].get("phase", "unknown") == phase:
                        stop += 1
                    onset, duration = (cursor - begin) / rate, (stop - cursor) / rate
                    writer.writeAnnotation(onset, duration, phase)
                    annotations.append({"onset": onset, "duration": duration, "phase": phase})
                    cursor = stop
            finally:
                writer.close()
            # Validate the actual written header, not merely the requested rate.
            with pyedflib.EdfReader(str(path)) as reader:
                if reader.getSignalLabels() != channels or reader.getNSamples().tolist() != [count] * 4 or any(reader.getSampleFrequency(channel) != rate for channel in range(4)):
                    raise ValueError("EDF round-trip validation failed: sample rate/count/channel mismatch")
            artifacts[name] = path.read_bytes()
            nominal_timestamps = timestamps[begin] + np.arange(count) / rate
            segments.append({"file": name, "task": task, "phase": phase, "source_kind": source_kind,
                             "source_session_id": state.get("session_id"), "first_sample_index": sample_indices[begin], "sample_count": count,
                             "channels": channels, "units": "uV", "data_record_seconds": record_duration, "samples_per_record": record_samples,
                             "physical_quantization_step_uv": {header["label"]: (header["physical_max"] - header["physical_min"]) / 65535 for header in headers},
                             "start_timestamp": float(timestamps[begin]), "end_timestamp_exclusive": float(timestamps[begin + count - 1] + 1 / rate),
                             "nominal_sample_rate": rate, "timestamp_basis": timestamp_basis,
                             "nominal_end_timestamp": float(timestamps[begin] + count / rate),
                             "max_abs_arrival_clock_deviation_ms": float(np.max(np.abs(timestamps[begin:begin + count] - nominal_timestamps)) * 1000),
                             "sample_mapping": {"raw_csv": raw_csv_name,
                                                "raw_csv_first_data_row_zero_based": csv_row_mapping[begin],
                                                "raw_csv_last_data_row_zero_based": csv_row_mapping[begin + count - 1],
                                                "source_raw_csv_first_data_row_zero_based": begin,
                                                "raw_sample_index_start": int(rows[begin].get("sample_index", begin)),
                                                "rule": "raw_data_row = first_raw_data_row + edf_sample_index; edf_onset_seconds = edf_sample_index / nominal_sample_rate",
                                                "exact_arrival_timestamps": raw_csv_name + ":timestamp", "signal_samples_resampled": False},
                             "phase_annotations": annotations})
    if not segments:
        raise ValueError("No raw segment can form an exact EDF record; exact samples remain in raw.csv")
    artifacts["edf-segments.json"] = json_text({"schema_version": 2, "source_session_id": state.get("session_id"), "source_kind": source_kind,
                                               "channel_order": channels, "units": "uV", "nominal_sample_rate": rate,
                                               "raw_sample_count": len(selected_indices), "exported_sample_count": sum(s["sample_count"] for s in segments),
                                               "quality_filter_applied": False, "signal_filter_applied": False, "samples_filled": 0,
                                               "timestamp_basis": timestamp_basis,
                                               "continuity": continuity,
                                               "segment_boundaries": [{"raw_data_row_zero_based": index, "reasons": reasons} for index, reasons in sorted(boundary_reasons.items())],
                                               "segments": segments, "omitted_tails": omitted}).encode("utf-8")
    if source_kind == "baseline_reference":
        csv_text = io.StringIO(newline="")
        csv_writer = csv.DictWriter(csv_text, fieldnames=list(rows[0]))
        csv_writer.writeheader()
        csv_writer.writerows(rows[index] for index in selected_indices)
        artifacts[raw_csv_name] = csv_text.getvalue().encode("utf-8")
    return artifacts


def _baseline_reference(directory, sessions_root):
    """Only export an independently verified successful source pretest round.

    Missing/mismatched historical evidence must never block the current raw EDF
    or lead us to guess a calibration round. The manifest explains the omission.
    """
    state = json.loads((directory / "session.json").read_text(encoding="utf-8"))
    calibration = state.get("calibration") or {}
    source_id = calibration.get("source_session_id")
    result = {"source_session_id": source_id, "included": False, "status": "no_reused_baseline"}
    if source_id and source_id == state.get("session_id"):
        return {**result, "status": "acquired_in_this_session"}, {}
    if not calibration.get("reused"):
        return result, {}
    result.update(status="reference_unavailable", download_source_session_id=source_id)
    try:
        baseline = json.loads((directory / "baseline.json").read_text(encoding="utf-8"))
        source_id = baseline["source_session_id"]
        result.update(source_session_id=source_id, download_source_session_id=source_id, baseline_id=baseline.get("baseline_id"))
        if sessions_root is None:
            raise ValueError("Source session root was not supplied; download the original session separately")
        root = Path(sessions_root).resolve()
        source = session_path(root, source_id).resolve()
        if source.parent != root or directory.resolve().parent != root:
            raise ValueError("Source session is outside the session root")
        recorded = json.loads((source / "session.json").read_text(encoding="utf-8"))
        if source_id == state.get("session_id") or recorded.get("session_id") != source_id:
            raise ValueError("Invalid baseline source session identity")
        for key in ("participant_id", "mode"):
            if not state.get(key) or baseline.get(key) != state[key] or recorded.get(key) != state[key]:
                raise ValueError("Baseline participant or recording mode mismatch")
        if recorded.get("source") != state.get("source"):
            raise ValueError("Baseline acquisition source mismatch")
        if (calibration.get("source_session_id") != source_id or baseline.get("baseline_id") != calibration.get("baseline_id")
                or baseline["calibration"]["parameters"] != calibration.get("parameters")):
            raise ValueError("Frozen baseline identity or calibration parameters mismatch")
        from .baselines import compatibility
        if baseline.get("compatibility") != compatibility(recorded) or baseline.get("compatibility") != compatibility(state):
            raise ValueError("Baseline device, channel layout or algorithm compatibility mismatch")
        ranges = _successful_calibration_ranges(source, baseline["calibration"])
        reference = _make_edf(source, selected_ranges=ranges, source_kind="baseline_reference")
        manifest = json.loads(reference["edf-segments.json"])
        if manifest["raw_sample_count"] != sum(end - begin for begin, end in ranges):
            raise ValueError("Source pretest raw samples are incomplete for the recorded phase boundaries")
        if {item["phase"] for item in manifest["segments"]} != {"calibrating_closed", "calibrating_open"}:
            raise ValueError("Source does not contain both complete pretest stages")
        result.update(included=True, status="included_source_pretest_only", directory="baseline-reference/",
                      calibration_version=baseline["calibration"].get("version"), source_sample_ranges=ranges)
        reference["source-session.json"] = json_text(recorded).encode("utf-8")
        reference["reference.json"] = json_text(result).encode("utf-8")
        return result, {"baseline-reference/" + name: content for name, content in reference.items()}
    except (OSError, ValueError, TypeError, KeyError, ImportError) as error:
        result["reason"] = str(error)
        return result, {}


def _successful_calibration_ranges(source, calibration):
    """Use stored sample counters at phase transitions, never clock guessing."""
    active, start, candidate, matched = None, None, [], None
    last_phase_reason = None
    for line in (source / "events.jsonl").read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("type") == "phase":
            cursor = event.get("sample_index")
            if not isinstance(cursor, int) or cursor < 0:
                raise ValueError("Source phase boundaries have no exact raw sample indices")
            if active in {"calibrating_closed", "calibrating_open"} and start is not None and cursor > start:
                candidate.append((active, start, cursor))
            reason = event.get("reason")
            if reason == "closed_calibration_started":
                candidate = []
            elif reason == "open_calibration_started":
                candidate = [part for part in candidate if part[0] == "calibrating_closed"]
            active, start, last_phase_reason = event.get("phase"), cursor, reason
        elif (event.get("type") == "calibration" and event.get("version") == calibration.get("version")
              and event.get("parameters") == calibration.get("parameters") and last_phase_reason == "calibration_passed"):
            if {part[0] for part in candidate} == {"calibrating_closed", "calibrating_open"}:
                matched = [(begin, end) for _, begin, end in candidate]
    if not matched:
        raise ValueError("Cannot identify the exact successful source calibration round")
    return matched
