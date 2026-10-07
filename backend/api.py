"""Loopback-only, same-origin HTTP/WS API and explicit public asset routes."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from .models import CommandRequest, SessionRequest
from .history import participant_history
from .providers import discover_muse
from .sessions import SessionConflict, SessionService
from .training_plan import plan_snapshot
from .storage import EDFExportError, export_session, list_sessions, read_session, session_path

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_FILES = {"app.js", "audio-engine.js", "easy-going-profile.js", "feedback-policy.js", "calibration-cues.js", "renderer.js", "silk-material.js", "session-client.js", "style.css", "session.css"}
ASSET_SUFFIXES = {".ogg", ".opus", ".wav", ".mp3", ".json", ".webp", ".png", ".jpg", ".jpeg", ".svg", ".woff", ".woff2"}
PUBLIC_FILES.update({"operator.html", "operator.css", "participant.html", "participant.js", "participant.css"})
PUBLIC_FILES.update({"operator-workspace.js", "operator-workspace.css", "operator-signal.css", "participant-visuals.js"})
PUBLIC_FILES.update({"training-scenes.js", "training-scenes.css", "audio-programs.js"})


class PresentationHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=100)
    ready: bool
    visible: bool = True


class PresentationWearResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: Literal["headband", "headphones"]
    session_id: str | None = Field(default=None, min_length=1, max_length=100)


class PresentationWearConfirmationRequest(PresentationWearResetRequest):
    client_id: str = Field(min_length=1, max_length=100)
    confirmed: bool


class LocalOnlyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
        host = headers.get("host", "").lower()
        allowed = urlsplit("http://" + host).hostname in {"127.0.0.1", "localhost", "::1"}
        origin = headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            allowed = allowed and parsed.scheme == "http" and parsed.netloc.lower() == host
        if not allowed:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4403, "reason": "Local same-origin access only"})
            else:
                await JSONResponse({"detail": "Local same-origin access only"}, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def create_app(data_dir=None, service=None):
    sessions_root = Path(data_dir) if data_dir is not None else ROOT / "runtime" / "sessions"
    sessions = service or SessionService(sessions_root)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await sessions.shutdown()

    app = FastAPI(title="Chengsi local EEG backend", version="1", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.sessions = sessions
    app.add_middleware(LocalOnlyMiddleware)

    @app.exception_handler(SessionConflict)
    async def conflict(_request, error):
        return JSONResponse({"detail": str(error), "snapshot": sessions.snapshot()}, status_code=409)

    @app.get("/api/health")
    async def health():
        return {"service": "chengsi-backend", "api_version": "1", "status": "ok", "capture_backend": "brainflow-native-ble", "active_session_id": sessions.state["session_id"], "phase": sessions.state["phase"], "reconnect_policy": sessions.reconnect_policy}

    @app.get("/api/devices")
    async def devices():
        if sessions.state["mode"] == "live" and sessions.state["connection"]["status"] in {"connected", "connecting"}:
            return {"devices": [{"name": sessions.state["connection"]["device_name"], "address": (sessions.state.get("device_metadata") or {}).get("device_address"), "connected": True}], "error": None}
        try:
            return {"devices": await discover_muse(timeout=5), "error": None}
        except Exception as error:
            return {"devices": [], "error": f"蓝牙扫描失败：{type(error).__name__}: {error}"}

    @app.get("/api/state")
    async def state():
        return sessions.snapshot()

    @app.post("/api/presentation/heartbeat")
    async def presentation_heartbeat(request: PresentationHeartbeatRequest):
        try:
            return await sessions.presentation_heartbeat(request.client_id, request.ready, request.visible)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/sessions")
    async def create_session(request: SessionRequest):
        return await sessions.create(request)

    @app.post("/api/presentation/wear-confirmation")
    async def presentation_wear_confirmation(request: PresentationWearConfirmationRequest):
        return await sessions.presentation_wear_confirmation(request.client_id, request.step,
                                                              request.confirmed, request.session_id)

    @app.post("/api/presentation/wear-reset")
    async def presentation_wear_reset(request: PresentationWearResetRequest):
        return await sessions.presentation_wear_reset(request.step, request.session_id)

    @app.post("/api/sessions/{session_id}/commands")
    async def command(session_id: str, request: CommandRequest):
        return await sessions.command(session_id, request)

    @app.get("/api/sessions")
    async def stored_sessions():
        return {"sessions": list_sessions(sessions_root, sessions.state["session_id"])}

    @app.get("/api/participant-history")
    async def history(participant_id: str, mode: str = "live"):
        if mode not in {"live", "synthetic", "replay"} or not participant_id or len(participant_id) > 80:
            raise HTTPException(422, "Invalid participant or mode")
        return await asyncio.to_thread(participant_history, sessions_root, participant_id, mode, sessions.state["session_id"])

    @app.get("/api/training-plan")
    async def training_plan(participant_id: str, mode: str = "live", training_seconds: int = 60):
        if mode not in {"live", "synthetic"} or not participant_id or len(participant_id) > 80 or training_seconds not in {60, 120}:
            raise HTTPException(422, "Invalid participant, mode, or round duration")
        return await asyncio.to_thread(plan_snapshot, sessions_root, participant_id, mode, training_seconds)

    def existing_directory(session_id):
        try:
            path = session_path(sessions_root, session_id)
        except ValueError:
            raise HTTPException(404, "Session not found")
        if not (path / "session.json").is_file():
            raise HTTPException(404, "Session not found")
        return path

    @app.get("/api/sessions/{session_id}")
    async def stored_session(session_id: str):
        path = existing_directory(session_id)
        if session_id == sessions.state["session_id"]:
            return sessions.snapshot()
        try:
            return read_session(path)
        except (OSError, ValueError):
            raise HTTPException(500, "Session metadata could not be read")

    @app.get("/api/sessions/{session_id}/export")
    async def session_export(session_id: str, edf: bool = False):
        path = existing_directory(session_id)
        try:
            if session_id == sessions.state["session_id"] and sessions._store:
                async with sessions.lock:
                    if sessions.state["phase"] not in {"completed", "error"}:
                        raise HTTPException(409, "请先结束当前会话，再导出一致的数据记录")
                    await sessions._stop_acquisition()
                    sessions._save()
                    sessions._store.flush()
                    archive = await asyncio.to_thread(export_session, path, edf, sessions_root)
            else:
                archive = await asyncio.to_thread(export_session, path, edf, sessions_root)
        except EDFExportError as error:
            raise HTTPException(422, str(error)) from error
        return Response(archive, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="chengsi-{session_id}.zip"', "Cache-Control": "no-store"})

    @app.websocket("/ws/live")
    async def live(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(sessions.snapshot())
                await asyncio.sleep(0.5)
        except (WebSocketDisconnect, RuntimeError, OSError):
            return

    @app.get("/")
    async def index():
        return FileResponse(ROOT / "index.html", headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})

    @app.get("/assets/{asset_path:path}")
    async def asset(asset_path: str):
        base = (ROOT / "assets").resolve()
        candidate = (base / asset_path).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            raise HTTPException(404, "Asset not found")
        if not candidate.is_file() or candidate.suffix.lower() not in ASSET_SUFFIXES:
            raise HTTPException(404, "Asset not found")
        media = "audio/ogg" if candidate.suffix in {".ogg", ".opus"} else None
        return FileResponse(candidate, media_type=media, headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})

    @app.get("/{filename}")
    async def public_file(filename: str):
        if filename not in PUBLIC_FILES or not (ROOT / filename).is_file():
            raise HTTPException(404, "File not found")
        media = "text/html; charset=utf-8" if filename.endswith(".html") else "text/javascript; charset=utf-8" if filename.endswith(".js") else "text/css; charset=utf-8"
        return FileResponse(ROOT / filename, media_type=media, headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})

    return app
