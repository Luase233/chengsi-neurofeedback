"""Paired iPad browsers can present a session but cannot operate the experiment."""
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import network
from backend.api import create_app
from backend.sessions import SessionService

LOCAL = "http://127.0.0.1:8768"
LAN = "http://192.168.20.10:8768"
JPEG = "data:image/jpeg;base64,/9j/2Q=="


@pytest.fixture
def clients(tmp_path, monkeypatch):
    monkeypatch.setattr(network, "lan_addresses", lambda: ["192.168.20.10", "10.0.0.10"])
    service = SessionService(tmp_path)
    app = create_app(tmp_path, service, lan=True)
    with TestClient(app, base_url=LOCAL) as operator:
        with TestClient(app, base_url=LAN, client=("192.168.20.30", 51000)) as participant:
            yield operator, participant, service, app


def pair(participant, app):
    result = participant.post("/api/pair", json={"token": app.state.network.pair_token}, headers={"Origin": LAN})
    assert result.status_code == 200, result.text
    return result


def claim(participant):
    result = participant.post("/api/presentation/heartbeat", json={"client_id": "ipad-A", "ready": True})
    assert result.status_code == 200, result.text


def test_loopback_remains_default_and_peer_cannot_spoof_operator(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, base_url=LOCAL) as local:
        health = local.get("/api/health").json()
        assert health["api_version"] == "1" and health["version"] == "1.1.0"
        assert health["lan_enabled"] is False
        assert local.get("/api/connection").json()["participant_urls"] == []
        assert local.post("/api/pair", json={"token": app.state.network.pair_token}).status_code == 403
    with TestClient(app, base_url=LOCAL, client=("192.168.20.30", 51000)) as remote:
        assert remote.get("/api/state").status_code == 403
        assert remote.get("/api/connection").status_code == 403


def test_pairing_limits_assets_and_protects_all_operator_routes(clients):
    operator, participant, _, app = clients
    connection = operator.get("/api/connection")
    assert connection.headers["cache-control"] == "no-store"
    info = connection.json()
    assert info["lan_enabled"] and info["port"] == 8768
    assert info["local_participant_url"] == LOCAL + "/participant.html"
    assert len(info["participant_urls"]) == 2
    for path in ("/participant.html", "/participant.js", "/audio-engine.js", "/participant.css"):
        assert participant.get(path).status_code == 200
    for path in ("/api/state", "/api/health", "/api/connection", "/operator.html", "/", "/app.js", "/server.py"):
        assert participant.get(path).status_code == 403
    assert participant.post("/api/pair", json={"token": "错误配对"}).status_code == 403
    response = pair(participant, app)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
    assert participant.get("/api/state").status_code == 200
    for path in ("/api/devices", "/api/connection", "/api/connection/qr", "/api/sessions", "/api/sessions/abc", "/api/sessions/abc/export", "/api/participant-history", "/api/training-plan", "/api/presentation/preview"):
        assert participant.get(path).status_code == 403, path
    for path in ("/api/sessions", "/api/presentation/wear-reset", "/api/presentation/guidance"):
        assert participant.post(path, json={}).status_code == 403
    for command in ("finish", "start_training", "set_presentation", "disconnect", "inject_dropout"):
        payload = {"command": command, "command_id": "blocked"}
        if command == "set_presentation":
            payload["volume"] = .9
        assert participant.post("/api/sessions/abc/commands", json=payload).status_code == 403
    # Bind this credential before issuing participant acknowledgements.
    claim(participant)
    # A cue acknowledgement reaches the state machine, which rejects a nonexistent session.
    assert participant.post("/api/sessions/abc/commands", json={"command": "cue_finished", "command_id": "cue", "client_id": "ipad-A", "cue_request_id": "cue"}).status_code == 409


def test_origin_host_and_websocket_authorization(clients):
    operator, participant, _, app = clients
    with pytest.raises(WebSocketDisconnect) as rejected:
        with participant.websocket_connect(LAN.replace("http:", "ws:") + "/ws/live"):
            pass
    assert rejected.value.code == 4403
    pair(participant, app)
    for headers in ({"Host": "localhost:8768"}, {"Host": "192.168.20.99:8768"},
                    {"Host": "192.168.20.10.evil.test:8768"}, {"Host": "192.168.20.10:80"},
                    {"Origin": "http://evil.test"}, {"Origin": "https://192.168.20.10:8768"},
                    {"Origin": LAN + "/path"}, {"Sec-Fetch-Site": "cross-site"},
                    {"Host": "127.0.0.1:8768", "X-Forwarded-For": "127.0.0.1"}):
        assert participant.get("/api/state", headers=headers).status_code == 403, headers
    with participant.websocket_connect(LAN.replace("http:", "ws:") + "/ws/live", headers={"Origin": LAN}) as socket:
        assert socket.receive_json()["phase"] == "idle"
    with pytest.raises(WebSocketDisconnect):
        with participant.websocket_connect(LAN.replace("http:", "ws:") + "/ws/live", headers={"Origin": "http://evil.test"}):
            pass
    assert operator.get("/api/state", headers={"Host": "evil.test:8768"}).status_code == 403


def test_connection_qr_is_local_and_only_encodes_current_pairing_urls(clients):
    operator, participant, _, app = clients
    url = operator.get("/api/connection").json()["participant_urls"][0]
    qr = operator.get("/api/connection/qr", params={"url": url})
    assert qr.status_code == 200 and "image/svg+xml" in qr.headers["content-type"]
    assert b"<svg" in qr.content
    assert operator.get("/api/connection/qr", params={"url": "https://evil.test/"}).status_code == 422
    pair(participant, app)
    assert participant.get("/api/connection/qr", params={"url": url}).status_code == 403


def test_guidance_is_server_owned_and_visible_across_devices(clients):
    operator, participant, service, app = clients
    pair(participant, app)
    claim(participant)
    before = operator.post("/api/presentation/wear-reset", json={"step": "headband"}).json()
    request = before["presentation"]["guidance_request"]
    assert request["step"] == "headband" and request["session_id"] is None and request["id"]
    with participant.websocket_connect(LAN.replace("http:", "ws:") + "/ws/live") as socket:
        assert socket.receive_json()["presentation"]["guidance_request"] == request
    assert operator.post("/api/presentation/guidance", json={"step": "ready"}).status_code == 409
    for step in ("headband", "headphones"):
        reset = operator.post("/api/presentation/guidance", json={"step": step})
        assert reset.status_code == 200
        assert participant.post("/api/presentation/wear-confirmation", json={"client_id": "ipad-A", "step": step, "confirmed": True}).status_code == 200
    ready = operator.post("/api/presentation/guidance", json={"step": "ready"}).json()
    assert ready["presentation"]["guidance_request"]["step"] == "ready"
    assert ready["presentation"]["guidance_request"]["id"] != request["id"]
    assert operator.post("/api/presentation/guidance", json={"step": "headband", "session_id": "old-session"}).status_code == 409
    service.state["phase"] = "training"
    for route in ("wear-reset", "guidance"):
        assert operator.post("/api/presentation/" + route, json={"step": "headband"}).status_code == 409


def test_preview_is_bounded_in_memory_and_invalidated_by_owner_and_session(clients):
    operator, participant, service, app = clients
    pair(participant, app)
    claim(participant)
    frame = {"client_id": "ipad-A", "session_id": None, "phase": "idle", "wearStep": "waiting", "capturedAt": 123456, "image": JPEG}
    assert participant.post("/api/presentation/preview", json=frame).status_code == 200
    preview = operator.get("/api/presentation/preview").json()
    assert preview["frame"]["image"] == JPEG and preview["age_ms"] >= 0
    assert "received_at" in preview["frame"]
    assert "data:image" not in participant.get("/api/state").text
    assert participant.post("/api/presentation/preview", json={**frame, "client_id": "ipad-B"}).status_code == 403
    assert participant.post("/api/presentation/preview", json={**frame, "session_id": "previous"}).status_code == 409
    assert participant.post("/api/presentation/preview", json={**frame, "phase": "training"}).status_code == 409
    for image in ("data:image/png;base64,/9j/2Q==", "data:image/jpeg;base64,bad", "data:image/jpeg;base64,YWJj"):
        assert participant.post("/api/presentation/preview", json={**frame, "image": image}).status_code == 422
    assert participant.post("/api/presentation/preview", json={**frame, "image": "x" * 103000}).status_code == 422
    assert participant.post("/api/presentation/preview", json={**frame, "image": "x" * 120000}).status_code == 413
    service._presentation_lease["seen_mono"] -= 5.1
    assert operator.get("/api/presentation/preview").json() == {"frame": None, "age_ms": None}
    claim(participant)
    assert participant.post("/api/presentation/preview", json=frame).status_code == 200
    service.state["session_id"] = "new-session"
    assert operator.get("/api/presentation/preview").json()["frame"] is None


def test_restart_invalidates_old_pair_and_cookie(tmp_path, monkeypatch):
    monkeypatch.setattr(network, "lan_addresses", lambda: ["192.168.20.10"])
    first, second = create_app(tmp_path / "one", lan=True), create_app(tmp_path / "two", lan=True)
    with TestClient(second, base_url=LAN, client=("192.168.20.30", 51000)) as participant:
        assert participant.post("/api/pair", json={"token": first.state.network.pair_token}).status_code == 403
        participant.cookies.set(network.COOKIE_NAME, first.state.network.issue_credential())
        assert participant.get("/api/state").status_code == 403


def test_discovery_lists_physical_lan_interfaces_before_virtual(monkeypatch):
    monkeypatch.setattr(network, "_interface_addresses", lambda: [("utun1", "10.20.0.2"), ("en0", "192.168.1.12"), ("en1", "10.0.0.2"), ("lo0", "127.0.0.1"), ("en2", "169.254.1.2"), ("en3", "8.8.8.8")])
    addresses = network.lan_addresses()
    assert addresses[:2] == ["10.0.0.2", "192.168.1.12"]
    assert addresses.index("10.20.0.2") > addresses.index("192.168.1.12")
    assert "127.0.0.1" not in addresses and "8.8.8.8" not in addresses


def test_paired_browser_cannot_impersonate_another_using_visible_client_id(clients):
    operator, participant, service, app = clients
    pair(participant, app)
    claim(participant)
    first_cookie = participant.cookies.get(network.COOKIE_NAME)
    with TestClient(app, base_url=LAN, client=("192.168.20.40", 52000)) as other:
        pair(other, app)
        assert other.cookies.get(network.COOKIE_NAME) != first_cookie
        # B can read A's public ID but cannot register or write as A.
        assert other.get("/api/state").json()["presentation"]["client_id"] == "ipad-A"
        assert other.post("/api/presentation/heartbeat", json={"client_id": "ipad-A", "ready": False}).status_code == 403
        assert other.post("/api/presentation/wear-confirmation", json={"client_id": "ipad-A", "step": "headband", "confirmed": True}).status_code == 403
        assert other.post("/api/sessions/abc/commands", json={"command": "cue_finished", "command_id": "spoof", "client_id": "ipad-A", "cue_request_id": "cue"}).status_code == 403
        frame = {"client_id": "ipad-A", "session_id": None, "phase": "idle", "wearStep": "waiting", "capturedAt": 123456, "image": JPEG}
        assert other.post("/api/presentation/preview", json=frame).status_code == 403
        assert service._presentation_status()["participant_ready"] is True
        # B's real ID still cannot take the active lease, then can take over on expiry.
        assert other.post("/api/presentation/heartbeat", json={"client_id": "ipad-B", "ready": True}).status_code == 409
        service._presentation_lease["seen_mono"] -= 5.1
        assert other.post("/api/presentation/heartbeat", json={"client_id": "ipad-B", "ready": True}).status_code == 200
        assert participant.post("/api/presentation/heartbeat", json={"client_id": "ipad-B", "ready": False}).status_code == 403


def test_same_browser_refresh_repair_and_new_tab_binding(clients):
    _, participant, service, app = clients
    pair(participant, app)
    claim(participant)
    original = participant.cookies.get(network.COOKIE_NAME)
    # Re-scanning rotates the secret while preserving that browser's existing ID.
    pair(participant, app)
    assert participant.cookies.get(network.COOKIE_NAME) != original
    claim(participant)
    assert participant.post("/api/presentation/heartbeat", json={"client_id": "new-tab", "ready": True}).status_code == 403
    service._presentation_lease["seen_mono"] -= 5.1
    assert participant.post("/api/presentation/heartbeat", json={"client_id": "new-tab", "ready": True}).status_code == 200
    assert participant.post("/api/presentation/heartbeat", json={"client_id": "ipad-A", "ready": True}).status_code == 403


def test_replacing_participant_clears_previous_ready_guidance(clients):
    operator, participant, service, app = clients
    pair(participant, app)
    claim(participant)
    for step in ("headband", "headphones"):
        assert participant.post("/api/presentation/wear-confirmation", json={"client_id": "ipad-A", "step": step, "confirmed": True}).status_code == 200
    assert operator.post("/api/presentation/guidance", json={"step": "ready"}).status_code == 200
    # A transient same-window reconnect retains its own guidance.
    service._presentation_lease["seen_mono"] -= 5.1
    claim(participant)
    assert service.state["presentation"]["guidance_request"]["step"] == "ready"
    service._presentation_lease["seen_mono"] -= 5.1
    with TestClient(app, base_url=LAN, client=("192.168.20.40", 52000)) as other:
        pair(other, app)
        response = other.post("/api/presentation/heartbeat", json={"client_id": "ipad-B", "ready": True})
        assert response.status_code == 200
        presentation = response.json()["presentation"]
        assert presentation["guidance_request"] is None
        assert not presentation["wear_confirmation"]["headband"]
        assert not presentation["wear_confirmation"]["headphones"]


def test_remote_cannot_adopt_a_local_participants_client_id(clients):
    operator, participant, service, app = clients
    assert operator.post("/api/presentation/heartbeat", json={"client_id": "local-display", "ready": True}).status_code == 200
    pair(participant, app)
    assert participant.post("/api/presentation/heartbeat", json={"client_id": "local-display", "ready": False}).status_code == 403
    service._presentation_lease["seen_mono"] -= 5.1
    assert participant.post("/api/presentation/heartbeat", json={"client_id": "local-display", "ready": True}).status_code == 403
    claim(participant)
