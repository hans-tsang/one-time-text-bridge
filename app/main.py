"""FastAPI application: routes, middleware, and startup/shutdown wiring.

Privacy notes:
- Message text is never put in URLs, logs, or exception messages.
- Only generic error pages are shown for invalid/expired/consumed links.
- Raw tokens are never logged.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
from contextlib import asynccontextmanager

import qrcode
from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import settings
from app.database import SessionLocal, init_db
from app.security import (
    CSRF_COOKIE_NAME,
    apply_security_headers,
    client_ip,
    issue_csrf_token,
    rate_limiter,
    verify_csrf_token,
)
from app.services.messages import (
    ALLOWED_EXPIRY_MINUTES,
    DEFAULT_EXPIRY_MINUTES,
    MessageError,
    create_message,
    delete_message,
    get_valid_message,
    reveal_and_consume,
)
from app.cleanup import run_cleanup_forever, run_cleanup_once

# Configure logging to never include message bodies/tokens; only route-level
# events are logged by the framework's default access logger.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.main")

templates = Jinja2Templates(directory="app/templates")

_cleanup_task: asyncio.Task | None = None
_live_note_connections: dict[str, set[WebSocket]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_for_startup()
    init_db()
    run_cleanup_once()
    global _cleanup_task
    _cleanup_task = asyncio.create_task(run_cleanup_forever())
    try:
        yield
    finally:
        if _cleanup_task:
            _cleanup_task.cancel()
            try:
                await _cleanup_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="One-Time Text Bridge", lifespan=lifespan, root_path=settings.root_path)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

if settings.allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)


class HTTPSRedirectAndHeadersMiddleware(BaseHTTPMiddleware):
    """Redirects to HTTPS in production (trusting X-Forwarded-Proto only
    from configured trusted proxies, e.g. Caddy) and applies security
    headers to every response.
    """

    async def dispatch(self, request: Request, call_next):
        if settings.is_production:
            peer = request.client.host if request.client else None
            if peer in settings.trusted_proxy_ips:
                proto = request.headers.get("x-forwarded-proto", request.url.scheme)
            else:
                proto = request.url.scheme
            if proto != "https":
                https_url = request.url.replace(scheme="https")
                return RedirectResponse(str(https_url), status_code=308)
        response = await call_next(request)
        return apply_security_headers(response)


app.add_middleware(HTTPSRedirectAndHeadersMiddleware)


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _rate_limit_or_429(request: Request, bucket: str) -> JSONResponse | None:
    key = f"{bucket}:{client_ip(request)}"
    if not rate_limiter.is_allowed(key):
        return JSONResponse(status_code=429, content={"detail": "Too many requests. Please try again later."})
    return None


def _set_csrf_cookie(response, token: str) -> None:
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="strict",
        secure=settings.is_production,
        max_age=3600,
    )


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/create", response_class=HTMLResponse)
async def create_form(request: Request) -> HTMLResponse:
    csrf_token = issue_csrf_token()
    response = templates.TemplateResponse(
        request,
        "create.html",
        {
            "max_length": settings.max_message_length,
            "max_upload_bytes": settings.max_upload_bytes,
            "expiry_options": ALLOWED_EXPIRY_MINUTES,
            "default_expiry": DEFAULT_EXPIRY_MINUTES,
            "csrf_token": csrf_token,
            "error": None,
        },
    )
    _set_csrf_cookie(response, csrf_token)
    return response


@app.post("/create", response_class=HTMLResponse)
async def create_submit(
    request: Request,
    text: str = Form(""),
    upload: UploadFile | None = File(None),
    expiry_minutes: int = Form(DEFAULT_EXPIRY_MINUTES),
    share_mode: str = Form("one_time"),
    consent: str | None = Form(None),
    csrf_token: str = Form(...),
) -> HTMLResponse:
    limited = _rate_limit_or_429(request, "create")
    if limited:
        return limited

    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not verify_csrf_token(csrf_token) or cookie_token != csrf_token:
        return HTMLResponse("Invalid or expired form submission. Please go back and try again.", status_code=400)

    if consent != "on":
        new_csrf = issue_csrf_token()
        response = templates.TemplateResponse(
            request,
            "create.html",
            {
                "max_length": settings.max_message_length,
                "max_upload_bytes": settings.max_upload_bytes,
                "expiry_options": ALLOWED_EXPIRY_MINUTES,
                "default_expiry": expiry_minutes,
                "csrf_token": new_csrf,
                "error": "You must confirm the content does not contain confidential information.",
            },
            status_code=400,
        )
        _set_csrf_cookie(response, new_csrf)
        return response

    if share_mode not in {"one_time", "live"}:
        return HTMLResponse("Invalid share mode.", status_code=400)

    if upload and not upload.filename:
        upload = None
    file_data = await upload.read() if upload else None
    db = next(_get_db())
    try:
        message, raw_token = create_message(
            db,
            text=text,
            expiry_minutes=expiry_minutes,
            filename=upload.filename if upload else None,
            content_type=upload.content_type if upload else None,
            file_data=file_data,
            is_live_note=share_mode == "live",
        )
    except MessageError as exc:
        new_csrf = issue_csrf_token()
        response = templates.TemplateResponse(
            request,
            "create.html",
            {
                "max_length": settings.max_message_length,
                "max_upload_bytes": settings.max_upload_bytes,
                "expiry_options": ALLOWED_EXPIRY_MINUTES,
                "default_expiry": expiry_minutes,
                "csrf_token": new_csrf,
                "error": str(exc),
            },
            status_code=400,
        )
        _set_csrf_cookie(response, new_csrf)
        return response
    finally:
        db.close()

    route_name = "live_note" if message.is_live_note else "receive"
    receive_path = request.url_for(route_name, raw_token=raw_token).path
    receive_url = f"{settings.base_url.rstrip('/')}{receive_path}"

    qr_img = qrcode.make(receive_url)
    buffer = io.BytesIO()
    qr_img.save(buffer, format="PNG")
    qr_data_uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

    new_csrf = issue_csrf_token()
    response = templates.TemplateResponse(
        request,
        "created.html",
        {
            "receive_url": receive_url,
            "receive_path": receive_path,
            "expires_at": message.expires_at,
            "expiry_minutes": expiry_minutes,
            "qr_data_uri": qr_data_uri,
            "csrf_token": new_csrf,
        },
    )
    _set_csrf_cookie(response, new_csrf)
    return response


@app.get("/live/{raw_token}", response_class=HTMLResponse)
async def live_note(request: Request, raw_token: str) -> HTMLResponse:
    limited = _rate_limit_or_429(request, "live")
    if limited:
        return limited

    db = next(_get_db())
    try:
        message = get_valid_message(db, raw_token)
    finally:
        db.close()

    if message is None or not message.is_live_note:
        return templates.TemplateResponse(request, "unavailable.html", {}, status_code=404)

    return templates.TemplateResponse(
        request,
        "live.html",
        {"raw_token": raw_token, "text": message.text, "max_length": settings.max_message_length},
    )


@app.websocket("/live/{raw_token}/ws")
async def live_note_socket(websocket: WebSocket, raw_token: str) -> None:
    db = SessionLocal()
    try:
        message = get_valid_message(db, raw_token)
    finally:
        db.close()

    if message is None or not message.is_live_note:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    connections = _live_note_connections.setdefault(raw_token, set())
    connections.add(websocket)
    try:
        await websocket.send_json({"type": "sync", "text": message.text})
        while True:
            payload = await websocket.receive_json()
            text = payload.get("text") if isinstance(payload, dict) else None
            if not isinstance(text, str) or len(text) > settings.max_message_length:
                continue

            db = SessionLocal()
            try:
                current = get_valid_message(db, raw_token)
                if current is None or not current.is_live_note:
                    await websocket.close(code=1008)
                    return
                current.text = text
                db.commit()
            finally:
                db.close()

            for connection in list(connections):
                try:
                    await connection.send_json({"type": "update", "text": text})
                except Exception:
                    connections.discard(connection)
    except WebSocketDisconnect:
        pass
    finally:
        connections.discard(websocket)
        if not connections:
            _live_note_connections.pop(raw_token, None)


@app.get("/r/{raw_token}", response_class=HTMLResponse)
async def receive(request: Request, raw_token: str) -> HTMLResponse:
    limited = _rate_limit_or_429(request, "receive")
    if limited:
        return limited

    db = next(_get_db())
    try:
        message = get_valid_message(db, raw_token)
    finally:
        db.close()

    if message is None:
        return templates.TemplateResponse(request, "unavailable.html", {}, status_code=404)

    csrf_token = issue_csrf_token()
    response = templates.TemplateResponse(
        request,
        "receive.html",
        {
            "raw_token": raw_token,
            "revealed": False,
            "text": None,
            "file_name": message.file_name,
            "csrf_token": csrf_token,
        },
    )
    _set_csrf_cookie(response, csrf_token)
    return response


@app.post("/r/{raw_token}/reveal", response_class=HTMLResponse)
async def receive_reveal(request: Request, raw_token: str, csrf_token: str = Form(...)) -> Response:
    limited = _rate_limit_or_429(request, "receive")
    if limited:
        return limited

    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not verify_csrf_token(csrf_token) or cookie_token != csrf_token:
        return templates.TemplateResponse(request, "unavailable.html", {}, status_code=400)

    db = next(_get_db())
    try:
        message = reveal_and_consume(db, raw_token)
    finally:
        db.close()

    if message is None:
        return templates.TemplateResponse(request, "unavailable.html", {}, status_code=404)

    if message.file_data is not None and message.file_name is not None:
        response = Response(
            content=message.file_data,
            media_type=message.file_content_type or "application/octet-stream",
        )
        response.headers["Content-Disposition"] = f'attachment; filename="{message.file_name}"'
        return response

    new_csrf = issue_csrf_token()
    response = templates.TemplateResponse(
        request,
        "receive.html",
        {
            "raw_token": raw_token,
            "revealed": True,
            "text": message.text,
            "file_name": None,
            "csrf_token": new_csrf,
        },
    )
    _set_csrf_cookie(response, new_csrf)
    return response


@app.post("/r/{raw_token}/delete", response_class=HTMLResponse)
async def receive_delete(request: Request, raw_token: str, csrf_token: str = Form(...)) -> HTMLResponse:
    limited = _rate_limit_or_429(request, "receive")
    if limited:
        return limited

    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not verify_csrf_token(csrf_token) or cookie_token != csrf_token:
        return templates.TemplateResponse(request, "unavailable.html", {}, status_code=400)

    db = next(_get_db())
    try:
        deleted = delete_message(db, raw_token)
    finally:
        db.close()

    if not deleted:
        return templates.TemplateResponse(request, "unavailable.html", {}, status_code=404)

    return templates.TemplateResponse(request, "unavailable.html", {"deleted_by_user": True})
