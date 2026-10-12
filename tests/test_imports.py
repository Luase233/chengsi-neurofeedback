"""ZIP imports preserve evidence and cannot overwrite sessions or calibrations."""
import copy
import asyncio
import io
import json
from pathlib import Path
import stat
import struct
import threading
from uuid import uuid4
import zipfile

from fastapi.testclient import TestClient
from starlette.requests import Request
import numpy as np
import pytest

from backend import api, imports, network
from backend.api import create_app
from backend.baselines import BaselineStore
from backend.sessions import SessionService, idle_state
from backend.signal import LiveProcessor, fit_calibration
from backend.storage import SessionStore, export_session

LOCAL = "http://127.0.0.1:8768"
CHANNELS = ["TP9", "AF7", "AF8", "TP10"]


def make_archive(root, *, edf=False, mode="live", baseline=True, reference=False):
    root.mkdir(parents=True, exist_ok=True)
    state = idle_state()
    state.update(session_id=str(uuid4()), participant_id="import-test", mode=mode,
                 source="muse" if mode == "live" else mode, phase="completed",
                 created_at="2026-10-01T01:00:00Z", updated_at="2026-10-01T01:05:00Z",
                 algorithm_config=LiveProcessor().config,
                 provenance={"mode": mode, "is_synthetic": mode == "synthetic", "original_source": "muse" if mode == "live" else mode})
    state["connection"].update(sample_rate=256.0, channels=CHANNELS, units="uV")
    state["device_metadata"] = {"provider": "brainflow-native-ble", "board_id": 38, "device_address": "00:11:22:33:44:55"}
    features = [{"window_start": 1700000000, "window_end": 1700000002, "valid": True,
                 "attention_raw": .15, "meditation_raw": 2, "stage": "calibrating_closed"},
                {"window_start": 1700000002, "window_end": 1700000004, "valid": True,
                 "attention_raw": .3, "meditation_raw": 1.5, "stage": "calibrating_open"}]
    parameters = fit_calibration(features[:1], features[1:], min_valid_windows=1)
    state["calibration"].update(valid=True, parameters=parameters, version=1, closed_seconds=60, open_seconds=60)
    state["summary"] = {"mean_score": 58.123, "mean_meditation": 43.789, "valid_seconds": 1.0}
    record = BaselineStore(root).save(state, features) if baseline and mode != "replay" else None
    if record:
        state["calibration"] = BaselineStore.apply(record, reused=False)

    def write(saved, phases, include_baseline):
        store = SessionStore(root, saved["session_id"])
        store.configure_raw(CHANNELS)
        offset = 0
        for phase, count, reason in phases:
            store.event("phase", phase=phase, reason=reason, sample_index=offset)
            index = np.arange(offset, offset + count)
            samples = np.column_stack([np.sin(index * .13 + channel) * 80 + channel * 20 for channel in range(4)])
            store.raw(1700000000 + index / 256, samples, phase)
            offset += count
        store.event("phase", phase="ready", reason="calibration_passed", sample_index=offset)
        store.event("calibration", version=1, parameters=parameters)
        for feature in features:
            store.feature({**feature, "score": 58.123, "meditation": 43.789}, feature["stage"], 1)
        if include_baseline:
            store.save_baseline(record)
        store.save(saved)
        store.close()
        return root / saved["session_id"]

    phases = [("calibrating_closed", 256, "closed_calibration_started"),
              ("calibrating_open", 256, "open_calibration_started")]
    directory = write(state, phases, record is not None)
    if reference:
        source_id = state["session_id"]
        state = copy.deepcopy(state)
        state["session_id"] = str(uuid4())
        state["calibration"] = BaselineStore.apply(record, reused=True)
        assert state["calibration"]["source_session_id"] == source_id
        directory = write(state, [("training", 256, "training_started")], True)
    return export_session(directory, include_edf=edf, sessions_root=root), state, record


def repack(payload, changes=None, extra=(), omit=()):
    changes = changes or {}
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(payload)) as source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            if info.filename not in omit:
                target.writestr(info.filename, changes.get(info.filename, source.read(info)))
        for info, content in extra:
            target.writestr(info, content)
    return output.getvalue()


def post(client, payload, **kwargs):
    return client.post("/api/sessions/import", content=payload, headers={"Content-Type": "application/zip"}, **kwargs)


def inventory(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("edf,reference", [(False, False), (True, False), (True, True)])
def test_normal_and_edf_import_keep_all_bytes_and_restore_reusable_baseline(tmp_path, edf, reference):
    payload, state, baseline = make_archive(tmp_path / "source", edf=edf, reference=reference)
    destination = tmp_path / "destination"
    with TestClient(create_app(destination), base_url=LOCAL) as client:
        before = client.get("/api/state").json()
        response = post(client, payload)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result == {"status": "imported", "session_id": state["session_id"], "participant_id": "import-test",
                          "mode": "live", "baseline_status": "restored",
                          "warnings": ["baseline_reference_only"] if reference else []}
        assert client.get("/api/state").json()["session_id"] == before["session_id"] is None
        rows = client.get("/api/sessions").json()["sessions"]
        assert [row["session_id"] for row in rows] == [state["session_id"]]
        stored = destination / state["session_id"]
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            assert inventory(stored) == {name: archive.read(name) for name in archive.namelist()}
        assert json.loads((stored / "session.json").read_bytes()) == state
        assert BaselineStore(destination).find(state) == baseline
        if reference:
            assert not (destination / baseline["source_session_id"]).exists()
        current_files = inventory(destination)
        duplicate = post(client, payload)
        assert duplicate.status_code == 200
        assert duplicate.json()["status"] == "already_imported"
        assert duplicate.json()["baseline_status"] == "existing"
        assert inventory(destination) == current_files
        assert not list(destination.glob(".import-*"))


@pytest.mark.parametrize("mode", ["synthetic", "replay"])
def test_modes_and_provenance_are_never_relabelled(tmp_path, mode):
    payload, state, _ = make_archive(tmp_path / "source", mode=mode, baseline=False)
    root = tmp_path / "destination"
    with TestClient(create_app(root), base_url=LOCAL) as client:
        result = post(client, payload)
        assert result.status_code == 200
        assert result.json()["mode"] == mode
        assert result.json()["baseline_status"] == "not_included"
        stored = json.loads((root / state["session_id"] / "session.json").read_bytes())
        assert stored["mode"] == mode and stored["provenance"] == state["provenance"]


def test_same_session_id_different_content_is_conflict_with_no_writes(tmp_path):
    payload, state, _ = make_archive(tmp_path / "source")
    root = tmp_path / "destination"
    with TestClient(create_app(root), base_url=LOCAL) as client:
        assert post(client, payload).status_code == 200
        before = inventory(root)
        changed = repack(payload, {"events.jsonl": b'{"type":"different"}\n'})
        response = post(client, changed)
        assert response.status_code == 409 and response.json()["code"] == "session_conflict"
        assert inventory(root) == before
        assert not list(root.glob(".import-*"))


@pytest.mark.parametrize("edf_first", [False, True])
@pytest.mark.parametrize("reference", [False, True])
def test_plain_and_edf_variants_are_idempotent_without_adding_or_overwriting_attachments(tmp_path, edf_first, reference):
    source = tmp_path / "source"
    plain, state, baseline = make_archive(source, reference=reference)
    edf = export_session(source / state["session_id"], include_edf=True, sessions_root=source)
    transport_state = copy.deepcopy(state)
    transport_state.update(seq=123, sent_at="2026-10-13T01:00:00Z", updated_at="2026-10-13T01:00:00Z")
    transport_state["presentation"]["heartbeat_age_ms"] = 17.25
    edf = repack(edf, {"session.json": json.dumps(transport_state, indent=2)})
    first, second = (edf, plain) if edf_first else (plain, edf)
    root = tmp_path / "destination"
    with TestClient(create_app(root), base_url=LOCAL) as client:
        imported = post(client, first)
        assert imported.status_code == 200 and imported.json()["baseline_status"] == "restored"
        before = inventory(root)
        duplicate = post(client, second)
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["status"] == "already_imported"
        assert duplicate.json()["baseline_status"] == "existing"
        assert ("attachments_not_added" in duplicate.json()["warnings"]) is not edf_first
        assert inventory(root) == before
        assert BaselineStore(root).find(state) == baseline
        assert len(client.get("/api/sessions").json()["sessions"]) == 1
        assert not list(root.glob(".import-*"))


@pytest.mark.parametrize("change", ["raw", "features", "summary", "participant", "mode", "calibration", "algorithm", "provenance", "presentation", "baseline", "valid_boolean"])
def test_only_export_transport_fields_are_ignored_and_core_changes_still_conflict(tmp_path, change):
    payload, state, baseline = make_archive(tmp_path / "source")
    altered = copy.deepcopy(state)
    if change in {"raw", "features"}:
        filename = "raw.csv" if change == "raw" else "features.csv"
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            content = archive.read(filename)
        content = (content.replace(b"1700000000.000000000", b"1700000000.000000001", 1) if change == "raw"
                   else content.replace(b"58.123", b"59.123", 1))
        changed = repack(payload, {filename: content})
    elif change == "baseline":
        baseline["created_at"] = "2026-01-01T00:00:00Z"
        changed = repack(payload, {"baseline.json": json.dumps(baseline)})
    else:
        if change == "summary":
            altered["summary"]["mean_score"] += 1
        elif change == "participant":
            altered["participant_id"] = "other-participant"
        elif change == "mode":
            altered["mode"] = "synthetic"
        elif change == "calibration":
            altered["calibration"]["parameters"]["attention"]["center_log"] += .01
        elif change == "algorithm":
            altered["algorithm_config"]["smooth_alpha"] = .111
        elif change == "provenance":
            altered["provenance"]["original_source"] = "other-source"
        elif change == "presentation":
            altered["presentation"]["training_scene"] = "lake-house"
        elif change == "valid_boolean":
            altered["calibration"]["valid"] = 1
        changed = repack(payload, {"session.json": json.dumps(altered)})
    root = tmp_path / "destination"
    with TestClient(create_app(root), base_url=LOCAL) as client:
        assert post(client, payload).status_code == 200
        before = inventory(root)
        response = post(client, changed)
        assert response.status_code == 409 and response.json()["code"] == "session_conflict"
        assert inventory(root) == before


def test_overlapping_edf_attachments_must_remain_identical(tmp_path):
    payload, _, _ = make_archive(tmp_path / "source", edf=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        name = next(name for name in archive.namelist() if name.endswith(".edf"))
        altered = bytearray(archive.read(name))
    altered[-1] ^= 1  # A different genuine sample, with an intact EDF header.
    changed = repack(payload, {name: bytes(altered)})
    root = tmp_path / "destination"
    with TestClient(create_app(root), base_url=LOCAL) as client:
        assert post(client, payload).status_code == 200
        before = inventory(root)
        response = post(client, changed)
        assert response.status_code == 409 and response.json()["code"] == "session_conflict"
        assert inventory(root) == before


def test_optional_baseline_can_restore_index_without_merging_existing_session_files(tmp_path):
    full, state, baseline = make_archive(tmp_path / "source")
    plain = repack(full, omit={"baseline.json"})
    root = tmp_path / "destination"
    with TestClient(create_app(root), base_url=LOCAL) as client:
        assert post(client, plain).json()["baseline_status"] == "not_included"
        before = inventory(root / state["session_id"])
        restored = post(client, full)
        assert restored.status_code == 200
        assert restored.json()["status"] == "already_imported"
        assert restored.json()["baseline_status"] == "restored"
        assert restored.json()["warnings"] == ["attachments_not_added"]
        assert inventory(root / state["session_id"]) == before
        assert BaselineStore(root).find(state) == baseline
        duplicate = post(client, full)
        assert duplicate.json()["baseline_status"] == "existing"


def test_existing_baseline_is_not_overwritten_or_partially_imported(tmp_path):
    payload, state, record = make_archive(tmp_path / "source")
    root = tmp_path / "destination"
    root.mkdir()
    path = BaselineStore(root)._participant_directory(state["participant_id"], state["mode"]) / (record["baseline_id"] + ".json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({**record, "created_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")
    before = inventory(root)
    with TestClient(create_app(root), base_url=LOCAL) as client:
        response = post(client, payload)
        assert response.status_code == 409 and response.json()["code"] == "baseline_conflict"
        assert inventory(root) == before
        assert not (root / state["session_id"]).exists()


@pytest.mark.parametrize("problem", ["mode", "participant_id", "baseline_id", "compatibility", "parameters", "trajectory", "legacy"])
def test_unusable_baseline_stays_evidence_but_cannot_enter_index(tmp_path, problem):
    payload, state, baseline = make_archive(tmp_path / "source")
    altered = copy.deepcopy(baseline)
    if problem == "parameters":
        altered["calibration"]["parameters"]["attention"]["scale_log"] = 0
    elif problem == "legacy":
        altered["calibration_method"] = "sorted-rest-minmax-v1.0"
    elif problem == "trajectory":
        altered["trajectory"] = []
    elif problem == "baseline_id":
        altered[problem] = str(uuid4())
    elif problem == "compatibility":
        altered[problem]["sample_rate"] = 128
    else:
        altered[problem] = "other"
    changed = repack(payload, {"baseline.json": json.dumps(altered)})
    root = tmp_path / "destination"
    with TestClient(create_app(root), base_url=LOCAL) as client:
        response = post(client, changed)
        assert response.status_code == 200
        assert response.json()["baseline_status"] == "unusable"
        assert response.json()["warnings"] == ["baseline_unusable"]
        assert not (root / "_baselines").exists()
        assert json.loads((root / state["session_id"] / "baseline.json").read_bytes()) == altered


@pytest.mark.parametrize("phase", ["connecting", "connected", "ready", "training", "paused", "preparing_training", "calibrating_open", "disconnected"])
def test_active_session_rejects_import_before_processing_body(tmp_path, phase):
    service = SessionService(tmp_path)
    service.state["phase"] = phase
    with TestClient(create_app(tmp_path, service), base_url=LOCAL) as client:
        response = post(client, b"not even a zip")
        assert response.status_code == 409 and response.json()["code"] == "active_session"
        assert not inventory(tmp_path)


def test_activity_that_started_during_validation_also_blocks_final_publish(tmp_path, monkeypatch):
    payload, state, _ = make_archive(tmp_path / "source")
    root = tmp_path / "destination"
    service = SessionService(root)
    original = api.prepare_import
    def validated(*args):
        result = original(*args)
        service.state["phase"] = "training"
        return result
    monkeypatch.setattr(api, "prepare_import", validated)
    with TestClient(create_app(root, service), base_url=LOCAL) as client:
        response = post(client, payload)
        assert response.status_code == 409 and response.json()["code"] == "active_session"
        assert not inventory(root) and not list(root.glob(".import-*"))


def test_participant_and_spoofed_remote_operator_cannot_import(tmp_path, monkeypatch):
    monkeypatch.setattr(network, "lan_addresses", lambda: ["192.168.20.10"])
    app = create_app(tmp_path, lan=True)
    with TestClient(app, base_url="http://192.168.20.10:8768", client=("192.168.20.30", 51000)) as participant:
        assert participant.post("/api/pair", json={"token": app.state.network.pair_token}).status_code == 200
        assert post(participant, b"zip").status_code == 403
    with TestClient(app, base_url=LOCAL, client=("192.168.20.30", 51000)) as remote:
        assert post(remote, b"zip").status_code == 403
    assert not inventory(tmp_path)


@pytest.mark.parametrize("name", ["../outside.edf", "/absolute.edf", "C:/outside.edf", "dir\\file.edf", "file:ads",
                                  "baseline-reference/../session.json", "CON.edf", "nul.edf", "a.edf.", "baseline-reference/a.edf "])
def test_unsafe_zip_paths_are_rejected_without_writes(tmp_path, name):
    payload, _, _ = make_archive(tmp_path / "source", baseline=False)
    root = tmp_path / "destination"
    with TestClient(create_app(root), base_url=LOCAL) as client:
        response = post(client, repack(payload, extra=[(name, b"unsafe")]))
        assert response.status_code == 422
        assert not inventory(root) and not list(root.glob(".import-*"))
    assert not (tmp_path / "outside.edf").exists()


def test_duplicate_entries_and_symlinks_are_rejected(tmp_path):
    payload, _, _ = make_archive(tmp_path / "source", baseline=False)
    symlink = zipfile.ZipInfo("link.edf")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    root = tmp_path / "destination"
    with TestClient(create_app(root), base_url=LOCAL) as client:
        with pytest.warns(UserWarning, match="Duplicate"):
            duplicate = repack(payload, extra=[("session.json", b"{}")])
        assert post(client, duplicate).status_code == 422
        assert post(client, repack(payload, extra=[(symlink, b"../outside")])).status_code == 422
        assert not inventory(root)


@pytest.mark.parametrize("problem", ["not_zip", "missing", "uuid", "json", "csv_header", "csv_row", "csv_index", "events", "edf"])
def test_corrupt_or_incomplete_export_is_rejected(tmp_path, problem):
    payload, state, _ = make_archive(tmp_path / "source", edf=problem == "edf", baseline=False)
    if problem == "not_zip":
        payload = b"not a zip"
    elif problem == "missing":
        payload = repack(payload, omit={"features.csv"})
    elif problem == "uuid":
        payload = repack(payload, {"session.json": json.dumps({**state, "session_id": state["session_id"].replace("-", "")})})
    elif problem == "json":
        payload = repack(payload, {"session.json": "{invalid"})
    elif problem == "csv_header":
        payload = repack(payload, {"raw.csv": "timestamp,AF7\n1,2\n"})
    elif problem == "csv_row":
        payload = repack(payload, {"raw.csv": "timestamp,sample_index,phase,TP9,AF7,AF8,TP10\n1,0,training,2\n"})
    elif problem == "csv_index":
        payload = repack(payload, {"raw.csv": "timestamp,sample_index,phase,TP9,AF7,AF8,TP10\n1,5,training,1,2,3,4\n"})
    elif problem == "events":
        payload = repack(payload, {"events.jsonl": "{truncated"})
    elif problem == "edf":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            name = next(name for name in archive.namelist() if name.endswith(".edf"))
        payload = repack(payload, {name: b"truncated"})
    root = tmp_path / "destination"
    with TestClient(create_app(root), base_url=LOCAL) as client:
        response = post(client, payload)
        assert response.status_code == 422, response.text
        assert not inventory(root) and not list(root.glob(".import-*"))


def test_crc_corruption_and_encryption_flags_are_rejected(tmp_path):
    payload, _, _ = make_archive(tmp_path / "source", baseline=False)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        files = {info.filename: archive.read(info) for info in archive.infolist()}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    original = output.getvalue()
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        info = archive.getinfo("raw.csv")
    offset = info.header_offset
    name_length, extra_length = struct.unpack_from("<HH", original, offset + 26)
    damaged = bytearray(original)
    damaged[offset + 30 + name_length + extra_length] ^= 1
    encrypted = bytearray(original)
    struct.pack_into("<H", encrypted, offset + 6, 1)
    central = encrypted.find(b"PK\x01\x02")
    struct.pack_into("<H", encrypted, central + 8, 1)
    root = tmp_path / "destination"
    with TestClient(create_app(root), base_url=LOCAL) as client:
        assert post(client, bytes(damaged)).status_code == 422
        assert post(client, bytes(encrypted)).status_code == 422
        assert not inventory(root)


def test_upload_and_expansion_limits_also_cover_chunked_bodies(tmp_path, monkeypatch):
    root = tmp_path / "destination"
    payload, _, _ = make_archive(tmp_path / "source", baseline=False)
    with TestClient(create_app(root), base_url=LOCAL) as client:
        assert client.post("/api/sessions/import", content=payload).status_code == 415
        monkeypatch.setattr(api, "MAX_ARCHIVE_BYTES", 32)
        assert post(client, b"x" * 33).status_code == 413
        streamed = client.post("/api/sessions/import", content=iter([b"x" * 20, b"y" * 20]), headers={"Content-Type": "application/zip"})
        assert streamed.status_code == 413
        monkeypatch.setattr(api, "MAX_ARCHIVE_BYTES", imports.MAX_ARCHIVE_BYTES)
        monkeypatch.setattr(imports, "MAX_EXPANDED_BYTES", 1024)
        assert post(client, payload).status_code == 413
        monkeypatch.setattr(imports, "MAX_EXPANDED_BYTES", 512 * 1024 * 1024)
        monkeypatch.setattr(imports, "MAX_ENTRIES", 3)
        assert post(client, payload).status_code == 422
        assert not inventory(root)


def test_publish_failure_rolls_back_new_baseline(tmp_path, monkeypatch):
    payload, state, _ = make_archive(tmp_path / "source")
    root = tmp_path / "destination"
    root.mkdir()
    prepared = imports.prepare_import(io.BytesIO(payload), root)
    def failed_rename(*_args):
        raise OSError("simulated publication failure")
    monkeypatch.setattr(imports.os, "rename", failed_rename)
    try:
        with pytest.raises(OSError, match="simulated"):
            prepared.publish(root)
        assert not (root / state["session_id"]).exists()
        assert not list((root / "_baselines").rglob("*.json"))
    finally:
        prepared.close()
    assert not inventory(root) and not list(root.glob(".import-*"))


@pytest.mark.parametrize("invalid_payload", [False, True])
def test_cancelled_preparation_keeps_upload_open_waits_for_worker_and_never_publishes(tmp_path, monkeypatch, invalid_payload):
    payload, _, _ = make_archive(tmp_path / "source")
    if invalid_payload:
        payload = b"invalid ZIP"
    root = tmp_path / "destination"
    app = create_app(root)
    endpoint = next(route.endpoint for route in app.routes if route.path == "/api/sessions/import")
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    upload_holder = {}
    original = api.prepare_import
    def delayed_prepare(upload, sessions_root):
        upload_holder["file"] = upload
        started.set()
        try:
            assert release.wait(3), "test did not release preparation worker"
            assert not upload.closed, "cancelled request closed the worker's upload"
            return original(upload, sessions_root)
        finally:
            finished.set()
    monkeypatch.setattr(api, "prepare_import", delayed_prepare)

    async def scenario():
        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}
        request = Request({"type": "http", "method": "POST", "path": "/api/sessions/import",
                           "headers": [(b"content-type", b"application/zip")],
                           "state": {"local_operator": True}}, receive)
        task = asyncio.create_task(endpoint(request))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(0)  # Deliver cancellation while the worker waits.
            assert not task.done()
            assert not upload_holder["file"].closed
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
        assert upload_holder["file"].closed
        assert app.state.sessions.state["phase"] == "idle"
    asyncio.run(scenario())
    assert not inventory(root) and not list(root.glob(".import-*"))
