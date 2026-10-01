from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .approvals import ApprovalService
from .audit import AuditService
from .auth import AuthService, AuthenticatedSession
from .backup import BackupService
from .database import Database
from .errors import ApprovalError, ConflictError, NotFoundError
from .hermes_acp import (
    HermesIntegrationDisabledError,
    HermesProtocolError,
    HermesUnavailableError,
)
from .hermes_sessions import HermesSessionService
from .models import (
    AppCreate,
    AppRecord,
    AppUpdate,
    ApprovalConfirm,
    ApprovalCreate,
    ApprovalRecord,
    BackupRecord,
    HermesMessageRequest,
    HermesSessionRecord,
    LoginRequest,
    ReorderRequest,
    SessionResponse,
)
from .registry import RegistryService
from .runtime import probe_all
from .settings import Settings


def create_app(settings: Settings) -> FastAPI:
    settings.validate()
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    database = Database(settings.database_path)
    auth = AuthService(database, settings.admin_password, settings.session_ttl_seconds)
    registry = RegistryService(database)
    approvals = ApprovalService(database)
    audit = AuditService(database)
    backups = BackupService(database, settings.backup_dir)
    hermes_sessions = HermesSessionService(database, settings)

    app = FastAPI(
        title="Hermes Hub API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.auth = auth
    app.state.registry = registry
    app.state.approvals = approvals
    app.state.backups = backups
    app.state.hermes_sessions = hermes_sessions

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers[
            "Content-Security-Policy"
        ] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; worker-src 'self'; manifest-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, private"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_request: Request, error: NotFoundError):
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": str(error)}},
        )

    @app.exception_handler(ConflictError)
    async def conflict_handler(_request: Request, error: ConflictError):
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "REVISION_CONFLICT", "message": str(error)}},
        )

    @app.exception_handler(ApprovalError)
    async def approval_handler(_request: Request, error: ApprovalError):
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "APPROVAL_INVALID", "message": str(error)}},
        )

    router = APIRouter(prefix="/api")

    def require_origin(request: Request) -> None:
        origin = request.headers.get("origin")
        if not origin:
            raise HTTPException(status_code=403, detail="Origin header is required")
        # The configured allow-list is authoritative. Never derive trust from
        # Host or any X-Forwarded-* header supplied by an untrusted proxy.
        if origin not in settings.allowed_origins:
            raise HTTPException(status_code=403, detail="Origin is not allowed")

    def session_token(request: Request) -> str | None:
        return request.cookies.get(settings.cookie_name)

    def require_session(request: Request) -> AuthenticatedSession:
        session = auth.authenticate(session_token(request))
        if session is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return session

    def require_csrf(
        request: Request,
        x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> AuthenticatedSession:
        require_origin(request)
        if not x_csrf_token:
            raise HTTPException(status_code=403, detail="CSRF token is required")
        session = auth.authenticate(session_token(request), x_csrf_token)
        if session is None:
            raise HTTPException(status_code=403, detail="Invalid session or CSRF token")
        return session

    @router.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "database": {
                "schemaVersion": database.schema_version(),
                "integrity": database.integrity_check(),
            },
        }

    @router.post("/auth/login", response_model=SessionResponse)
    def login(value: LoginRequest, request: Request, response: Response):
        require_origin(request)
        result = auth.login(value.password)
        if result is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token, csrf, expires_at = result
        response.set_cookie(
            settings.cookie_name,
            token,
            max_age=settings.session_ttl_seconds,
            secure=settings.cookie_secure,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return SessionResponse(
            authenticated=True,
            actor="admin",
            csrf_token=csrf,
            expires_at=datetime.fromisoformat(expires_at),
        )

    @router.get("/auth/session", response_model=SessionResponse)
    def current_session(request: Request):
        token = session_token(request)
        session = auth.authenticate(token)
        if session is None:
            return SessionResponse(authenticated=False)
        return SessionResponse(
            authenticated=True,
            actor=session.actor,
            csrf_token=session.csrf_token,
            expires_at=datetime.fromisoformat(session.expires_at),
        )

    @router.post("/auth/logout", status_code=204)
    def logout(
        request: Request,
        response: Response,
        _session: AuthenticatedSession = Depends(require_csrf),
    ):
        auth.logout(session_token(request))
        response.delete_cookie(
            settings.cookie_name,
            path="/",
            secure=settings.cookie_secure,
            httponly=True,
            samesite="strict",
        )

    @router.get("/apps", response_model=list[AppRecord])
    def list_apps(
        hub: str | None = Query(default=None, pattern="^(teaching|personal)$"),
        include_archived: bool = False,
        search: str = Query(default="", max_length=120),
        _session: AuthenticatedSession = Depends(require_session),
    ):
        return registry.list(hub, include_archived, search)

    @router.post("/apps", response_model=AppRecord, status_code=201)
    def create_app_record(
        value: AppCreate,
        session: AuthenticatedSession = Depends(require_csrf),
    ):
        return registry.create(value, session.actor)

    @router.patch("/apps/{app_id}", response_model=AppRecord)
    def update_app_record(
        app_id: str,
        value: AppUpdate,
        session: AuthenticatedSession = Depends(require_csrf),
    ):
        return registry.update(app_id, value, session.actor)

    @router.post("/apps/reorder", response_model=list[AppRecord])
    def reorder_apps(
        value: ReorderRequest,
        session: AuthenticatedSession = Depends(require_csrf),
    ):
        return registry.reorder(value, session.actor)

    @router.get("/registry/export")
    def export_registry(_session: AuthenticatedSession = Depends(require_session)):
        payload = registry.export()
        payload["generatedAt"] = datetime.now().astimezone().isoformat()
        return payload

    @router.get("/runtime")
    async def runtime_status(_session: AuthenticatedSession = Depends(require_session)):
        return await asyncio.to_thread(probe_all, settings)

    @router.get("/audit")
    def audit_events(
        limit: int = Query(default=50, ge=1, le=200),
        _session: AuthenticatedSession = Depends(require_session),
    ):
        return audit.recent(limit)

    @router.post("/approvals", response_model=ApprovalRecord, status_code=201)
    def request_approval(
        value: ApprovalCreate,
        session: AuthenticatedSession = Depends(require_csrf),
    ):
        return approvals.create(value.action, value.target, value.content, session.actor)

    @router.post("/approvals/{approval_id}/approve", response_model=ApprovalRecord)
    def approve_action(
        approval_id: str,
        value: ApprovalConfirm,
        session: AuthenticatedSession = Depends(require_csrf),
    ):
        return approvals.approve(
            approval_id,
            value.action,
            value.target,
            value.content_hash,
            session.actor,
        )

    @router.get("/approvals", response_model=list[ApprovalRecord])
    def list_approvals(_session: AuthenticatedSession = Depends(require_session)):
        return approvals.list()

    @router.post("/backups/local", response_model=BackupRecord, status_code=201)
    def local_backup(_session: AuthenticatedSession = Depends(require_csrf)):
        return backups.create_snapshot()

    @router.post("/hermes/session")
    async def create_hermes_session(
        value: HermesMessageRequest,
        session: AuthenticatedSession = Depends(require_csrf),
    ):
        """Start a Hermes ACP session and stream one user message turn."""
        try:
            conversation = await hermes_sessions.open_new(value.message, session.actor)
        except HermesIntegrationDisabledError as error:
            raise HTTPException(
                status_code=503,
                detail={"code": "HERMES_INTEGRATION_DISABLED", "message": str(error)},
            ) from error
        except HermesUnavailableError as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "HERMES_UNAVAILABLE",
                    "message": str(error),
                    "diagnostics": error.diagnostics.to_dict(),
                },
            ) from error
        except HermesProtocolError as error:
            # The agent itself refused (for example no provider is configured);
            # surface its own remediation instead of a simulated session.
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "HERMES_SESSION_REFUSED",
                    "message": str(error),
                    "agentError": {"code": error.code, "data": error.data},
                },
            ) from error
        return StreamingResponse(
            hermes_sessions.stream_events(conversation),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/hermes/session/{session_id}/message")
    async def send_hermes_message(
        session_id: str,
        value: HermesMessageRequest,
        _session: AuthenticatedSession = Depends(require_csrf),
    ):
        """Continue a persisted Hermes session with one streamed user message."""
        try:
            conversation = await hermes_sessions.open_existing(
                session_id, value.message, _session.actor
            )
        except HermesIntegrationDisabledError as error:
            raise HTTPException(
                status_code=503,
                detail={"code": "HERMES_INTEGRATION_DISABLED", "message": str(error)},
            ) from error
        except HermesUnavailableError as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "HERMES_UNAVAILABLE",
                    "message": str(error),
                    "diagnostics": error.diagnostics.to_dict(),
                },
            ) from error
        except HermesProtocolError as error:
            # The agent itself refused (for example no provider is configured);
            # surface its own remediation instead of a simulated session.
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "HERMES_SESSION_REFUSED",
                    "message": str(error),
                    "agentError": {"code": error.code, "data": error.data},
                },
            ) from error
        return StreamingResponse(
            hermes_sessions.stream_events(conversation),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/hermes/sessions", response_model=list[HermesSessionRecord])
    def list_hermes_sessions(_session: AuthenticatedSession = Depends(require_session)):
        """Read-only session references for diagnostics; never message content."""
        return hermes_sessions.list()

    @router.post("/restricted-actions/{action}")
    def restricted_action(
        action: str,
        _session: AuthenticatedSession = Depends(require_csrf),
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "BROKER_NOT_IMPLEMENTED",
                "message": (
                    f"Restricted action '{action}' is disabled. "
                    "No external-write broker is installed."
                ),
            },
        )

    app.include_router(router)

    dist = settings.frontend_dist
    if dist is not None and dist.is_dir() and (dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    else:
        @app.get("/")
        def frontend_missing():
            return {
                "status": "frontend_not_built",
                "message": "Build Apps/system/hermes-hub/frontend before opening the Hub",
            }

    return app
