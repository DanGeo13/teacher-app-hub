"""Hub REST endpoints for Atlas: the dashboard and the Pixel 9 companion app.

Both are authenticated human channels (session cookie + CSRF + exact Origin).
They decide approvals through the same ApprovalService as the Telegram bot.
"""

from __future__ import annotations

from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import Field

from ..auth import AuthenticatedSession
from ..models import ApiModel
from .service import INTERNAL_ACTIONS, AtlasError, AtlasService
from .verification import VerificationManager

STATUS_FOR_CODE = {"NOT_FOUND": 404, "SKILL_NOT_FOUND": 404}


class DecisionRequest(ApiModel):
    decision: Literal["approve", "reject"]
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    channel: Literal["dashboard", "companion"] = "dashboard"
    reason: str = Field(default="", max_length=200)


class AudioSegment(ApiModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = Field(max_length=2000)
    confidence: float = Field(ge=0, le=1)


class AudioChunk(ApiModel):
    offset_seconds: float = Field(ge=0)
    segments: list[AudioSegment] = Field(max_length=500)


class AudioUpload(ApiModel):
    source_device: str = Field(min_length=1, max_length=120)
    recorded_at: str = Field(min_length=10, max_length=40)
    chunks: list[AudioChunk] = Field(min_length=1, max_length=50)


def atlas_error_response(error: AtlasError) -> JSONResponse:
    return JSONResponse(status_code=STATUS_FOR_CODE.get(error.code, 409), content=error.to_dict())


def register_atlas_routes(
    router: APIRouter,
    atlas: AtlasService,
    verification: VerificationManager,
    require_session: Callable[..., AuthenticatedSession],
    require_csrf: Callable[..., AuthenticatedSession],
) -> None:
    @router.get("/atlas/obligations")
    def obligations(kind: str | None = Query(default=None), include_closed: bool = False,
                    _s: AuthenticatedSession = Depends(require_session)) -> Any:
        return atlas.list_obligations(kind, include_closed)

    @router.get("/atlas/records/{record_id}")
    def record(record_id: str, _s: AuthenticatedSession = Depends(require_session)) -> Any:
        return atlas.get_record(record_id)

    @router.post("/atlas/records/{record_id}/accept")
    def accept(record_id: str, session: AuthenticatedSession = Depends(require_csrf)) -> Any:
        return atlas.accept_record(record_id, session.actor)

    @router.get("/atlas/approvals")
    def approvals(_s: AuthenticatedSession = Depends(require_session)) -> Any:
        return atlas.list_pending_approvals()

    @router.post("/atlas/approvals/{approval_id}/decide")
    def decide(approval_id: str, value: DecisionRequest, session: AuthenticatedSession = Depends(require_csrf)) -> Any:
        approval = atlas.approvals.get(approval_id)
        if value.decision == "reject":
            atlas.approvals.reject(approval_id, session.actor, value.channel, value.reason)
            return {"approval": atlas.approval_view(approval_id), "executed": False}
        atlas.approvals.approve(approval_id, approval.action, approval.target, value.content_hash,
                                session.actor, value.channel)
        result: dict[str, Any] = {"approved": True, "executed": False}
        if approval.action in INTERNAL_ACTIONS:
            result["execution"] = atlas.execute_approved_action(approval_id, actor=session.actor)
            result["executed"] = True
        else:
            result["note"] = "Approved. No broker is installed for this action, so nothing was sent."
        return result

    @router.get("/atlas/review")
    def review(_s: AuthenticatedSession = Depends(require_session)) -> Any:
        return {"items": atlas.list_review_items()}

    @router.get("/atlas/health")
    def health(_s: AuthenticatedSession = Depends(require_session)) -> Any:
        return atlas.get_system_health()

    @router.get("/atlas/digest")
    def digest(channel: Literal["telegram", "voice", "dashboard"] = "dashboard",
               _s: AuthenticatedSession = Depends(require_session)) -> Any:
        return atlas.build_digest(channel)

    @router.post("/atlas/audio", status_code=201)
    def audio(value: AudioUpload, session: AuthenticatedSession = Depends(require_csrf)) -> Any:
        chunks = [{"offsetSeconds": c.offset_seconds, "segments": [s.model_dump() for s in c.segments]}
                  for c in value.chunks]
        return atlas.enqueue_audio(source_device=value.source_device, recorded_at=value.recorded_at,
                                   chunks=chunks, actor=session.actor)

    @router.post("/atlas/verification/run", status_code=202)
    def run_verification(_s: AuthenticatedSession = Depends(require_csrf)) -> Any:
        return verification.start()

    @router.get("/atlas/verification/latest")
    def latest_verification(_s: AuthenticatedSession = Depends(require_session)) -> Any:
        return verification.latest or {"status": "never_run", "steps": []}
