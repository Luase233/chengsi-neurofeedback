"""Local operator and optional paired LAN participant HTTP/WS API."""
import asyncio
import base64
import binascii
import io
from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from .models import CommandRequest, SessionRequest
from .network import COOKIE_NAME, LocalOnlyMiddleware, NetworkAccess
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
PUBLIC_FILES.update({"training-scenes.js", "training-scenes.css", "audio-programs.js", "ipad-connection.js", "ipad-connection.css"})


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


class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=100)


class PresentationGuidanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: Literal["headband", "headphones", "ready"]
    session_id: str | None = Field(default=None, min_length=1, max_length=100)


class PresentationPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=100)
    session_id: str | None = Field(default=None, min_length=1, max_length=100)
    phase: str = Field(min_length=1, max_length=50)
    wearStep: str = Field(min_length=1, max_length=50)
    capturedAt: float = Field(ge=0, allow_inf_nan=False)
    image: str = Field(min_length=1, max_length=102400)


def create_app(data_dir=None, service=None, lan=False, port=8768):
    sessions_root = Path(data_dir) if data_dir is not None else ROOT / "runtime" / "sessions"
    sessions = service or SessionService(sessions_root)
    access = NetworkAccess(lan=lan, port=port)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await sessions.shutdown()

    app = FastAPI(title="Chengsi local EEG backend", version="1.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.sessions = sessions
    app.state.network = access
    app.add_middleware(LocalOnlyMiddleware, access=access)

    @app.exception_handler(SessionConflict)
    async def conflict(_request, error):
        return JSONResponse({"detail": str(error), "snapshot": sessions.snapshot()}, status_code=409)

    @app.get("/api/health")
    async def health():
        return {"service": "chengsi-backend", "api_version": "1", "version": "1.1.0", "lan_enabled": access.lan, "status": "ok", "capture_backend": "brainflow-native-ble", "active_session_id": sessions.state["session_id"], "phase": sessions.state["phase"], "reconnect_policy": sessions.reconnect_policy}

    @app.get("/api/connection")
    async def connection():
        return JSONResponse(await asyncio.to_thread(access.connection), headers={"Cache-Control": "no-store"})

    @app.get("/api/connection/qr")
    async def connection_qr(url: str):
        if url not in (await asyncio.to_thread(access.connection))["participant_urls"]:
            raise HTTPException(422, "Select a current participant pairing URL")
        import qrcode
        from qrcode.image.svg import SvgPathImage
        image = qrcode.make(url, image_factory=SvgPathImage, border=3)
        buffer = io.BytesIO()
        image.save(buffer)
        return Response(buffer.getvalue(), media_type="image/svg+xml",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.post("/api/pair")
    async def pair(request: PairRequest, http_request: Request):
        if not access.valid_pair(request.token):
            raise HTTPException(403, "配对链接已失效，请向工作人员获取本次启动的新链接")
        response = JSONResponse({"paired": True}, headers={"Cache-Control": "no-store"})
        credential = access.issue_credential(access.credential(http_request.headers))
        response.set_cookie(COOKIE_NAME, credential, httponly=True, samesite="strict", path="/")
        return response

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

    def authorize_participant_write(http_request, client_id, heartbeat=False):
        if http_request.state.local_operator:
            return
        active = sessions._presentation_status()["client_id"]
        last = (sessions._presentation_lease or {}).get("client_id")
        if not access.authorize_client(http_request.state.participant_credential, client_id,
                                       active, last, heartbeat=heartbeat):
            raise HTTPException(403, "被试屏凭证与窗口不匹配，请关闭原展示页并等待连接超时后重新连接")

    @app.post("/api/presentation/heartbeat")
    async def presentation_heartbeat(request: PresentationHeartbeatRequest, http_request: Request):
        authorize_participant_write(http_request, request.client_id, heartbeat=True)
        try:
            return await sessions.presentation_heartbeat(request.client_id, request.ready, request.visible)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/sessions")
    async def create_session(request: SessionRequest):
        return await sessions.create(request)

    @app.post("/api/presentation/wear-confirmation")
    async def presentation_wear_confirmation(request: PresentationWearConfirmationRequest, http_request: Request):
        authorize_participant_write(http_request, request.client_id)
        return await sessions.presentation_wear_confirmation(request.client_id, request.step,
                                                              request.confirmed, request.session_id)

    @app.post("/api/presentation/wear-reset")
    async def presentation_wear_reset(request: PresentationWearResetRequest):
        return await sessions.presentation_wear_reset(request.step, request.session_id)

    @app.post("/api/presentation/guidance")
    async def presentation_guidance(request: PresentationGuidanceRequest):
        return await sessions.presentation_guidance(request.step, request.session_id)

    @app.post("/api/presentation/preview")
    async def presentation_preview(request: PresentationPreviewRequest, http_request: Request):
        authorize_participant_write(http_request, request.client_id)
        if not request.image.startswith("data:image/jpeg;base64,"):
            raise HTTPException(422, "Preview must be a JPEG data URL")
        try:
            jpeg = base64.b64decode(request.image.split(",", 1)[1], validate=True)
        except (ValueError, binascii.Error) as error:
            raise HTTPException(422, "Invalid JPEG data URL") from error
        if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise HTTPException(422, "Invalid JPEG data URL")
        return await sessions.presentation_preview(request.model_dump())

    @app.get("/api/presentation/preview")
    async def latest_presentation_preview():
        return JSONResponse(sessions.latest_presentation_preview(), headers={"Cache-Control": "no-store"})

    @app.post("/api/sessions/{session_id}/commands")
    async def command(session_id: str, request: CommandRequest, http_request: Request):
        if not http_request.state.local_operator and request.command != "cue_finished":
            raise HTTPException(403, "Only the local operator can control a session")
        authorize_participant_write(http_request, request.client_id)
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
        return FileResponse(ROOT / "index.html", headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})

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
        return FileResponse(candidate, media_type=media, headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})

    @app.get("/{filename}")
    async def public_file(filename: str):
        if filename not in PUBLIC_FILES or not (ROOT / filename).is_file():
            raise HTTPException(404, "File not found")
        media = "text/html; charset=utf-8" if filename.endswith(".html") else "text/javascript; charset=utf-8" if filename.endswith(".js") else "text/css; charset=utf-8"
        return FileResponse(ROOT / filename, media_type=media, headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})

    return app
