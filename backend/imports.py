"""Validate exported evidence before atomically archiving it, without rescoring."""
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import unicodedata
import zipfile
import zlib
from datetime import datetime

from .baselines import BaselineStore, compatibility
from .signal import CALIBRATION_VERSION, validate_calibration
from .storage import session_path

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_ENTRIES = 512
MAX_JSON_BYTES = 8 * 1024 * 1024
REQUIRED_FILES = {"session.json", "raw.csv", "features.csv", "events.jsonl"}
OPTIONAL_FILES = {"baseline.json", "edf-segments.json", "edf-notes.txt"}
REFERENCE_FILES = {"raw.csv", "source-session.json", "reference.json", "edf-segments.json"}
CHANNELS = {"TP9", "AF7", "AF8", "TP10"}


class SessionImportError(ValueError):
    def __init__(self, detail, status_code=422, code="invalid_archive"):
        super().__init__(detail)
        self.status_code, self.code = status_code, code


def _invalid(detail="ZIP 不是完整有效的澄思会话导出包"):
    raise SessionImportError(detail)


def _too_large():
    raise SessionImportError("ZIP 超过 64 MiB 或展开后超过 512 MiB", 413, "archive_too_large")


def _member_path(info):
    name = info.orig_filename
    if (not name or len(name) > 1024 or "\\" in name or ":" in name
            or any(ord(char) < 32 for char in name) or name.startswith("/")):
        _invalid("ZIP 包含不安全的文件路径")
    parts = name.removesuffix("/").split("/")
    reserved = {"con", "prn", "aux", "nul"} | {f"{prefix}{i}" for prefix in ("com", "lpt") for i in range(1, 10)}
    if any(part in {"", ".", ".."} or len(part) > 255 or part.endswith((".", " "))
           or part.split(".", 1)[0].casefold() in reserved for part in parts):
        _invalid("ZIP 包含不安全的文件路径")
    kind = stat.S_IFMT(info.external_attr >> 16)
    if kind not in {0, stat.S_IFREG, stat.S_IFDIR} or (kind == stat.S_IFDIR) != info.is_dir() and kind != 0:
        _invalid("ZIP 不能包含符号链接或特殊文件")
    if info.flag_bits & 1:
        _invalid("不支持加密的 ZIP")
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        _invalid("ZIP 使用了不支持的压缩格式")
    if info.is_dir():
        if name != "baseline-reference/":
            _invalid("ZIP 包含非会话导出目录")
    elif len(parts) == 1:
        if name not in REQUIRED_FILES | OPTIONAL_FILES and not name.endswith(".edf"):
            _invalid("ZIP 包含非会话导出文件")
    elif len(parts) == 2 and parts[0] == "baseline-reference":
        if parts[1] not in REFERENCE_FILES and not parts[1].endswith(".edf"):
            _invalid("ZIP 包含非会话导出文件")
    else:
        _invalid("ZIP 包含非会话导出文件")
    return name, unicodedata.normalize("NFC", name.removesuffix("/")).casefold()


def _json(path):
    if path.stat().st_size > MAX_JSON_BYTES:
        _invalid("会话 JSON 元数据过大")
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _invalid("JSON 包含重复字段")
            result[key] = value
        return result
    def reject_constant(_value):
        _invalid("JSON 包含无效数字")
    def finite_float(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            _invalid("JSON 包含无效数字")
        return parsed
    with path.open(encoding="utf-8-sig") as handle:
        value = json.load(handle, object_pairs_hook=unique_pairs, parse_constant=reject_constant, parse_float=finite_float)
    if not isinstance(value, dict):
        _invalid("JSON 元数据必须为对象")
    return value


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _same_json(first, second):
    # JSON booleans are distinct from numbers, unlike Python's True == 1.
    if isinstance(first, bool) or isinstance(second, bool):
        return type(first) is type(second) and first == second
    if isinstance(first, dict) and isinstance(second, dict):
        return first.keys() == second.keys() and all(_same_json(value, second[key]) for key, value in first.items())
    if isinstance(first, list) and isinstance(second, list):
        return len(first) == len(second) and all(_same_json(a, b) for a, b in zip(first, second))
    if _number(first) and _number(second):
        return first == second
    return type(first) is type(second) and first == second


def _validate_session(state):
    session_path(Path(), state.get("session_id"))
    if (state.get("mode") not in {"live", "synthetic", "replay"}
            or not isinstance(state.get("participant_id"), str) or not 1 <= len(state["participant_id"]) <= 80
            or not isinstance(state.get("source"), str) or not isinstance(state.get("phase"), str)
            or not isinstance(state.get("algorithm_config"), dict)
            or not isinstance(state.get("connection"), dict)
            or state.get("source_kind") == "baseline_reference"):
        _invalid("会话身份、模式或算法记录无效")
    channels = state["connection"].get("channels")
    if not isinstance(channels, list) or len(channels) != 4 or set(channels) != CHANNELS:
        _invalid("会话必须保留四个原始 Muse EEG 通道")
    rate = state["connection"].get("sample_rate")
    if not _number(rate) or rate <= 0:
        _invalid("会话采样率无效")


def _csv(path, required, numeric=(), sequential=None):
    count = 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, strict=True)
        header = next(reader, None)
        if not header or len(header) != len(set(header)) or not required <= set(header):
            _invalid("CSV 缺少必要字段或字段重复")
        positions = {name: header.index(name) for name in numeric if name in header}
        index = header.index(sequential) if sequential else None
        for row in reader:
            if len(row) != len(header):
                _invalid("CSV 行不完整")
            for position in positions.values():
                if row[position] != "":
                    # Raw artifacts (including non-finite samples) remain evidence.
                    # Parse them, but never filter, fill, normalize or rescore them.
                    float(row[position])
            if index is not None and int(row[index]) != count:
                _invalid("CSV 样本或特征序号不完整")
            count += 1
    return count


def _events(path):
    with path.open(encoding="utf-8-sig") as handle:
        while True:
            line = handle.readline(MAX_JSON_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_JSON_BYTES:
                _invalid("会话事件记录过大")
            if not line.strip():
                continue
            event = json.loads(line, parse_constant=lambda value: _invalid("事件包含无效数字"))
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                _invalid("会话事件记录无效")


def _validate_edf_file(path):
    # Check the EDF header and expected record length, without decoding samples.
    with path.open("rb") as handle:
        header = handle.read(256)
        if len(header) != 256 or header[:8].strip() != b"0":
            _invalid("EDF 附属文件不完整")
        size, records, channels = int(header[184:192]), int(header[236:244]), int(header[252:256])
        if not 1 <= channels <= 64 or size != 256 * (channels + 1) or records < 0:
            _invalid("EDF 附属文件头无效")
        signals = handle.read(size - 256)
        if len(signals) != size - 256:
            _invalid("EDF 附属文件不完整")
        counts = [int(signals[216 * channels + 8 * i:216 * channels + 8 * (i + 1)]) for i in range(channels)]
        if any(count <= 0 for count in counts) or path.stat().st_size != size + records * sum(counts) * 2:
            _invalid("EDF 附属文件长度无效")


def _edf_manifest(folder, state_id, kind, files):
    manifest = _json(folder / "edf-segments.json")
    if manifest.get("source_session_id") != state_id or manifest.get("source_kind") != kind:
        _invalid("EDF 清单来源与会话不一致")
    segments = manifest.get("segments")
    if not isinstance(segments, list) or not segments:
        _invalid("EDF 清单缺少分段")
    referenced = set()
    for segment in segments:
        name = segment.get("file") if isinstance(segment, dict) else None
        if not isinstance(name, str) or name not in files or not name.endswith(".edf"):
            _invalid("EDF 清单引用了缺失文件")
        referenced.add(name)
    if referenced != {name for name in files if name.endswith(".edf")}:
        _invalid("EDF 文件与清单不一致")


def _baseline_usable(record, state):
    """Only an intact frozen v2 calibration can enter the participant index."""
    try:
        session_path(Path(), record["baseline_id"])
        session_path(Path(), record["source_session_id"])
        calibration, current = record["calibration"], state["calibration"]
        parameters = calibration["parameters"]
        validate_calibration(parameters)
        stamp = datetime.fromisoformat(record["created_at"].replace("Z", "+00:00"))
        trajectory = record["trajectory"]
        if (type(record.get("schema_version")) is not int or record["schema_version"] != 1
                or not isinstance(record.get("imported"), bool) or record["mode"] not in {"live", "synthetic"}
                or record["mode"] != state["mode"] or record["participant_id"] != state["participant_id"]
                or not _same_json(record["compatibility"], compatibility(state))
                or record["calibration_method"] != CALIBRATION_VERSION or parameters.get("version") != CALIBRATION_VERSION
                or calibration.get("valid") is not True or current.get("valid") is not True
                or current.get("baseline_id") != record["baseline_id"]
                or current.get("source_session_id") != record["source_session_id"]
                or not _same_json(current.get("parameters"), parameters) or stamp.tzinfo is None
                or type(calibration.get("version")) is not int or calibration["version"] < 1
                or current.get("version") != calibration["version"]
                or (record["source_session_id"] != state["session_id"] and current.get("reused") is not True)
                or not isinstance(trajectory, list) or not trajectory
                or not all(_number(calibration.get(key)) and calibration[key] > 0 for key in ("closed_seconds", "open_seconds"))):
            return False
        if state["mode"] == "live" and (state["source"] in {"synthetic", "replay"} or (state.get("provenance") or {}).get("is_synthetic")):
            return False
        valid_stages = set()
        for point in trajectory:
            if (not isinstance(point, dict) or not isinstance(point.get("valid"), bool)
                    or point.get("stage") not in {"closed", "open"} or not _number(point.get("time"))
                    or any(value is not None and (not _number(value) or not 0 <= value <= 100)
                           for value in (point.get("score"), point.get("meditation")))):
                return False
            if point["valid"]:
                if not all(_number(point.get(key)) for key in ("score", "meditation", "window_start", "window_end")):
                    return False
                if point["window_end"] < point["window_start"]:
                    return False
                valid_stages.add(point["stage"])
        return valid_stages == {"closed", "open"}
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def _record_metadata(state):
    """Ignore only transport/export timing, keeping all recorded EEG semantics."""
    result = dict(state)
    for key in ("seq", "sent_at", "updated_at"):
        result.pop(key, None)
    if isinstance(result.get("presentation"), dict):
        result["presentation"] = dict(result["presentation"])
        result["presentation"].pop("heartbeat_age_ms", None)
    return result


def _same_record(left, right):
    """Plain and EDF exports can represent the same immutable core record.

    Compare overlapping optional evidence, but never add or replace attachments
    in an existing archive. Return the incoming attachments that were not saved.
    """
    def inventory(directory):
        result = {}
        for path in directory.rglob("*"):
            if path.is_symlink():
                return None
            if path.is_file():
                result[path.relative_to(directory).as_posix()] = path
        return result
    def same_bytes(first, second):
        if first.stat().st_size != second.stat().st_size:
            return False
        with first.open("rb") as a, second.open("rb") as b:
            return hashlib.file_digest(a, "sha256").digest() == hashlib.file_digest(b, "sha256").digest()
    try:
        if not right.is_dir() or right.is_symlink():
            return False, set()
        incoming, existing = inventory(left), inventory(right)
        if incoming is None or existing is None or not REQUIRED_FILES <= existing.keys():
            return False, set()
        if not _same_json(_record_metadata(_json(incoming["session.json"])), _record_metadata(_json(existing["session.json"]))):
            return False, set()
        for name in (incoming.keys() & existing.keys()) - {"session.json"}:
            equal = (_same_json(_json(incoming[name]), _json(existing[name])) if name == "baseline.json"
                     else same_bytes(incoming[name], existing[name]))
            if not equal:
                return False, set()
        return True, incoming.keys() - existing.keys()
    except (SessionImportError, OSError, ValueError, TypeError, KeyError):
        return False, set()


class PreparedImport:
    def __init__(self, temporary, directory, state, baseline, baseline_status, warnings):
        self.temporary, self.directory, self.state = temporary, directory, state
        self.baseline, self.baseline_status, self.warnings = baseline, baseline_status, warnings

    def close(self):
        self.temporary.cleanup()

    def publish(self, sessions_root):
        """Called only while holding SessionService.lock; never overwrite files."""
        state = self.state
        destination = session_path(sessions_root, state["session_id"])
        exists = destination.exists() or destination.is_symlink()
        warnings = list(self.warnings)
        if exists:
            same, missing_attachments = _same_record(self.directory, destination)
            if not same:
                raise SessionImportError("相同会话编号已存在，但数据内容不同；未覆盖任何记录", 409, "session_conflict")
            if missing_attachments:
                warnings.append("attachments_not_added")
        baseline_target = None
        baseline_status = self.baseline_status
        if self.baseline is not None:
            folder = BaselineStore(sessions_root)._participant_directory(state["participant_id"], state["mode"])
            baseline_target = folder / (self.baseline["baseline_id"] + ".json")
            if baseline_target.exists() or baseline_target.is_symlink():
                try:
                    same_baseline = not baseline_target.is_symlink() and baseline_target.is_file() and _same_json(_json(baseline_target), self.baseline)
                except (SessionImportError, OSError, ValueError, TypeError):
                    same_baseline = False
                if not same_baseline:
                    raise SessionImportError("相同基线编号已存在，但数据内容不同；未覆盖任何记录", 409, "baseline_conflict")
                baseline_status = "existing"
            else:
                baseline_status = "restored"
        created_baseline = False
        try:
            if baseline_target is not None and baseline_status == "restored":
                baseline_target.parent.mkdir(parents=True, exist_ok=True)
                # Hard-linking a validated same-volume temporary file creates the
                # complete baseline atomically and fails if a target already exists.
                os.link(self.directory / "baseline.json", baseline_target)
                created_baseline = True
            if not exists:
                os.rename(self.directory, destination)
        except BaseException:
            if created_baseline:
                baseline_target.unlink()
            raise
        return {"status": "already_imported" if exists else "imported", "session_id": state["session_id"],
                "participant_id": state["participant_id"], "mode": state["mode"],
                "baseline_status": baseline_status, "warnings": warnings}


def prepare_import(archive_file, sessions_root):
    """Extract only validated export entries, checking every file's CRC to EOF."""
    temporary = tempfile.TemporaryDirectory(prefix=".import-", dir=sessions_root)
    directory = Path(temporary.name) / "session"
    directory.mkdir()
    try:
        archive_file.seek(0, os.SEEK_END)
        if archive_file.tell() > MAX_ARCHIVE_BYTES:
            _too_large()
        archive_file.seek(0)
        with zipfile.ZipFile(archive_file) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES:
                _invalid("ZIP 文件条目过多")
            names, normalized, declared = set(), set(), 0
            for info in entries:
                name, key = _member_path(info)
                if key in normalized:
                    _invalid("ZIP 包含重复文件或目录")
                normalized.add(key)
                names.add(name)
                declared += info.file_size
                if declared > MAX_EXPANDED_BYTES:
                    _too_large()
            if not REQUIRED_FILES <= names:
                _invalid("ZIP 缺少 session.json、raw.csv、features.csv 或 events.jsonl")
            expanded = 0
            for info in entries:
                if info.is_dir():
                    continue
                target = directory / info.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with archive.open(info) as source, target.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        expanded += len(chunk)
                        written += len(chunk)
                        if expanded > MAX_EXPANDED_BYTES:
                            _too_large()
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if written != info.file_size:
                    _invalid("ZIP 文件内容不完整")
        state = _json(directory / "session.json")
        _validate_session(state)
        _csv(directory / "raw.csv", {"timestamp", "sample_index", "phase"} | CHANNELS,
             ("timestamp", "packet_num", *CHANNELS), "sample_index")
        _csv(directory / "features.csv", {"feature_index", "phase", "window_start", "window_end", "valid", "score", "meditation"},
             ("window_start", "window_end", "attention_raw", "meditation_raw", "score", "meditation"), "feature_index")
        _events(directory / "events.jsonl")
        baseline, baseline_status, warnings = None, "not_included", []
        if "baseline.json" in names:
            record = _json(directory / "baseline.json")
            if _baseline_usable(record, state):
                baseline, baseline_status = record, "restored"
            else:
                baseline_status = "unusable"
                warnings.append("baseline_unusable")
        for path in directory.rglob("*.edf"):
            _validate_edf_file(path)
        root_files = {name for name in names if "/" not in name}
        if "edf-segments.json" in root_files:
            _edf_manifest(directory, state["session_id"], "session", root_files)
        elif any(name.endswith(".edf") for name in root_files):
            _invalid("EDF 包缺少分段清单")
        reference_files = {name.split("/", 1)[1] for name in names if name.startswith("baseline-reference/") and not name.endswith("/")}
        if reference_files:
            if not REFERENCE_FILES <= reference_files:
                _invalid("历史基线引用包不完整")
            reference_folder = directory / "baseline-reference"
            source = _json(reference_folder / "source-session.json")
            reference = _json(reference_folder / "reference.json")
            source_id = source.get("session_id")
            session_path(Path(), source_id)
            current = state.get("calibration") or {}
            if (reference.get("source_session_id") != source_id or current.get("source_session_id") != source_id
                    or source_id == state["session_id"] or reference.get("baseline_id") != current.get("baseline_id")):
                _invalid("历史基线引用来源不一致")
            _edf_manifest(reference_folder, source_id, "baseline_reference", reference_files)
            _csv(reference_folder / "raw.csv", {"timestamp", "sample_index", "phase"} | CHANNELS, ("timestamp", *CHANNELS))
            warnings.append("baseline_reference_only")
        return PreparedImport(temporary, directory, state, baseline, baseline_status, warnings)
    except SessionImportError:
        temporary.cleanup()
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, EOFError,
            csv.Error, zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, zlib.error) as error:
        temporary.cleanup()
        raise SessionImportError("ZIP 内容损坏或会话格式无效") from error
    except BaseException:
        temporary.cleanup()
        raise
