"""New-user screen milestones reported by the web app.

The server records the milestones it can observe itself (signup, project, first AI
message). Screen milestones go through this endpoint, which keeps the handlers of
those screens (persona answer, file read) unchanged:
authenticated, once per account and event (``activation_event`` unique key), with a
fixed field set of enums and booleans so no manuscript text or prompt can be sent.
See feature/2026-10-31-new-user-screen-activation-events.md.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from sqlmodel import Session

from database import get_session
from middleware.rate_limit import check_rate_limit
from models import (
    ACTIVATION_EVENT_AI_FILE_OPENED,
    ACTIVATION_EVENT_AI_SEND_BLOCKED_QUOTA,
    ACTIVATION_EVENT_PERSONA_SUBMITTED,
    User,
)
from services.core.auth_service import get_current_active_user
from services.features.activation_event_service import activation_event_service
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/activation", tags=["activation"])

ClientActivationEventName = Literal["persona_submitted", "dashboard_viewed", "ai_file_opened", "ai_send_blocked_quota"]


class ClientActivationEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_name: ClientActivationEventName
    project_id: str | None = Field(default=None, min_length=1, max_length=64)
    file_id: str | None = Field(default=None, min_length=1, max_length=64)
    is_mobile: StrictBool | None = None
    # persona_submitted: the author skipped the question instead of answering it.
    skipped: StrictBool | None = None
    # ai_file_opened: the author had the file open while the AI wrote it (recorded when it finished).
    while_ai_writing: StrictBool | None = None
    # ai_send_blocked_quota: where the send was blocked, and whether the server refused it.
    surface: Literal["chat", "dashboard"] | None = None
    server_refused: StrictBool | None = None

    @model_validator(mode="after")
    def _required_per_event(self) -> "ClientActivationEventRequest":
        if self.event_name == ACTIVATION_EVENT_AI_FILE_OPENED and not self.file_id:
            raise ValueError("file_id is required for ai_file_opened")
        if self.event_name == ACTIVATION_EVENT_AI_SEND_BLOCKED_QUOTA and self.surface is None:
            raise ValueError("surface is required for ai_send_blocked_quota")
        if self.event_name == ACTIVATION_EVENT_PERSONA_SUBMITTED and self.skipped is None:
            raise ValueError("skipped is required for persona_submitted")
        return self

    def event_metadata(self) -> dict:
        if self.event_name == ACTIVATION_EVENT_AI_FILE_OPENED:
            fields = {"is_mobile": self.is_mobile, "while_ai_writing": self.while_ai_writing}
        elif self.event_name == ACTIVATION_EVENT_PERSONA_SUBMITTED:
            fields = {"skipped": self.skipped}
        elif self.event_name == ACTIVATION_EVENT_AI_SEND_BLOCKED_QUOTA:
            fields = {"surface": self.surface, "server_refused": self.server_refused, "is_mobile": self.is_mobile}
        else:
            fields = {"is_mobile": self.is_mobile}
        return {key: value for key, value in fields.items() if value is not None}


class ClientActivationEventResponse(BaseModel):
    recorded: bool


@router.post("/events", response_model=ClientActivationEventResponse)
def record_client_activation_event(
    body: ClientActivationEventRequest,
    http_request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    """Record one screen milestone for the current account; repeats are no-ops."""
    # Per account, not per account and IP: a phone changing networks keeps one budget.
    allowed, _ = check_rate_limit(
        http_request, f"activation_events:{current_user.id}", 60, 60, include_client_ip=False
    )
    if not allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")

    user_id = current_user.id
    try:
        recorded = activation_event_service.record_client_event(
            session,
            user_id=user_id,
            event_name=body.event_name,
            project_id=body.project_id if body.event_name != ACTIVATION_EVENT_AI_FILE_OPENED else None,
            file_id=body.file_id if body.event_name == ACTIVATION_EVENT_AI_FILE_OPENED else None,
            event_metadata=body.event_metadata(),
        )
    except Exception as exc:
        # An observation point: the client ignores failures, so answer "not recorded".
        session.rollback()
        log_with_context(
            logger,
            logging.WARNING,
            "Failed to record client activation event",
            user_id=user_id,
            event_name=body.event_name,
            error_type=type(exc).__name__,
        )
        recorded = False
    return ClientActivationEventResponse(recorded=recorded)
