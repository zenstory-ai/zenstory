"""
Chat session API endpoints.

Provides FastAPI router for chat session management:
- GET /api/v1/chat/session/{project_id} - Get or create session for project
- GET /api/v1/chat/session/{project_id}/recent - Get recent session messages
- DELETE /api/v1/chat/session/{project_id} - Clear session
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, exists, literal
from sqlalchemy.orm import load_only
from sqlmodel import Session, col, func, select

from agent.loop.history import client_message_metadata, client_tool_calls
from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from core.i18n import (
    default_chat_session_title,
    new_chat_session_title,
    resolve_user_language,
)
from database import create_session, get_session
from models import ChatMessage, ChatMessageArchive, ChatMessageUxSummary, ChatSession, Project, User
from models.chat_message_archive import has_chat_message_archive_table
from models.chat_message_ux_summary import has_chat_message_ux_summary_table
from services.chat_feedback_service import chat_feedback_service
from services.chat_session_service import latest_active_chat_session, lock_active_chat_session
from services.core.auth_service import get_current_active_user
from services.infra.redis_client import delete_agent_wire_prefix_cache
from utils.logger import get_logger, log_with_context
from utils.permission import verify_project_access_sync

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


# ==================== Request/Response Models ====================


class MessageResponse(BaseModel):
    """Response model for a message."""

    id: str
    session_id: str
    role: str
    content: str
    tool_calls: str | None = None
    metadata: str | None = None
    created_at: datetime


class SessionResponse(BaseModel):
    """Response model for a session."""

    id: str
    user_id: str
    project_id: str
    title: str
    is_active: bool
    message_count: int
    created_at: datetime
    updated_at: datetime


class MessageFeedbackRequest(BaseModel):
    """Request model for message feedback."""

    vote: Literal["up", "down"]
    preset: str | None = Field(default=None, max_length=128)
    comment: str | None = Field(default=None, max_length=1000)


class MessageFeedback(BaseModel):
    """Feedback payload returned for a message."""

    vote: Literal["up", "down"]
    preset: str | None = None
    comment: str | None = None
    created_at: datetime
    updated_at: datetime


class MessageFeedbackResponse(BaseModel):
    """Response model for feedback submission."""

    message_id: str
    feedback: MessageFeedback
    updated_at: datetime


# ==================== Endpoints ====================


def _list_active_sessions(
    session: Session,
    project_id: str,
    user_id: str,
) -> list[ChatSession]:
    active = latest_active_chat_session(session, project_id=project_id, user_id=user_id)
    return [active] if active is not None else []


def _deactivate_and_create_session(
    session: Session,
    project_id: str,
    user_id: str,
    title: str,
) -> ChatSession:
    lock_active_chat_session(session, user_id=user_id, project_id=project_id)
    current_sessions = _list_active_sessions(session, project_id, user_id)
    deactivated_ids = [existing.id for existing in current_sessions]
    if current_sessions:
        for existing in current_sessions:
            existing.is_active = False
            session.add(existing)
        log_with_context(
            logger,
            20,  # INFO
            "Deactivated current active sessions",
            project_id=project_id,
            user_id=user_id,
            deactivated_count=len(current_sessions),
            latest_session_id=current_sessions[0].id,
        )

    session.flush()
    new_session = ChatSession(
        user_id=user_id,
        project_id=project_id,
        title=title,
        is_active=True,
        message_count=0,
    )
    session.add(new_session)
    session.commit()
    for deactivated_id in deactivated_ids:
        delete_agent_wire_prefix_cache(deactivated_id)
    session.refresh(new_session)
    return new_session


def _create_new_session_sync(
    project_id: str,
    user_id: str,
    title: str,
) -> ChatSession:
    """
    Create a fresh active chat session using a dedicated sync DB session.

    This helper is intended to run in a worker thread so the async API route
    does not execute DB mutation work on the event loop.
    """
    with create_session() as session:
        return _deactivate_and_create_session(session, project_id, user_id, title)


def _should_offload_session_work(session: Session) -> bool:
    """Only offload when running against PostgreSQL production-style sessions."""
    bind = session.get_bind() if hasattr(session, "get_bind") else None
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
    return dialect_name == "postgresql"


@router.get("/session/{project_id}", response_model=SessionResponse)
def get_or_create_session(
    project_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
    accept_language: Annotated[str | None, Header(alias="Accept-Language")] = None,
):
    """Get or create a chat session for a project."""
    user_id = current_user.id

    log_with_context(
        logger,
        20,  # INFO
        "get_or_create_session called",
        project_id=project_id,
        user_id=user_id,
    )

    # Check if project exists and is not deleted
    verify_project_access_sync(project_id, session, current_user)

    lock_active_chat_session(session, user_id=user_id, project_id=project_id)
    active_sessions = _list_active_sessions(session, project_id, user_id)
    chat_session = active_sessions[0] if active_sessions else None

    if not chat_session:
        lang = resolve_user_language(session, user_id, accept_language)
        session_title = default_chat_session_title(lang)
        log_with_context(
            logger,
            20,  # INFO
            "Creating new chat session",
            project_id=project_id,
            user_id=user_id,
            language=lang,
        )
        chat_session = ChatSession(
            user_id=user_id,
            project_id=project_id,
            title=session_title,
            is_active=True,
            message_count=0,
        )
        session.add(chat_session)
        session.commit()
        session.refresh(chat_session)
    else:
        log_with_context(
            logger,
            20,  # INFO
            "Found existing chat session",
            project_id=project_id,
            user_id=user_id,
            session_id=chat_session.id,
            message_count=chat_session.message_count,
        )

    return SessionResponse(
        id=chat_session.id,
        user_id=chat_session.user_id,
        project_id=chat_session.project_id,
        title=chat_session.title,
        is_active=chat_session.is_active,
        message_count=chat_session.message_count,
        created_at=chat_session.created_at,
        updated_at=chat_session.updated_at,
    )


def serialize_chat_session(chat_session: ChatSession) -> SessionResponse:
    """Serialize a ChatSession row into the shared REST response shape."""
    return SessionResponse(
        id=chat_session.id,
        user_id=chat_session.user_id,
        project_id=chat_session.project_id,
        title=chat_session.title,
        is_active=chat_session.is_active,
        message_count=chat_session.message_count,
        created_at=chat_session.created_at,
        updated_at=chat_session.updated_at,
    )


def load_active_session_recent_messages(
    session: Session,
    *,
    project_id: str,
    user_id: str,
    limit: int,
    message_id: str | None = None,
) -> tuple[SessionResponse | None, list[MessageResponse], bool]:
    """Read active session metadata and latest slimmed messages across hot + cold tables.

    Fetches up to ``limit + 1`` rows (probing ``chat_message_archive`` via an index-only
    watermark whenever needed) so callers know accurately whether older messages exist —
    including the exact boundary where ``len(hot) == limit`` and older rows live in
    ``chat_message_archive`` as well as interleaved hot/cold windows.
    """
    active_sessions = _list_active_sessions(session, project_id, user_id)
    chat_session = active_sessions[0] if active_sessions else None

    if not chat_session:
        return None, [], False

    fetch_limit = 1 if message_id is not None else limit + 1

    # Keep completion hydration scoped to the same ownership-checked active session.
    stmt = (
        select(ChatMessage)
        .options(
            load_only(
                ChatMessage.id,
                ChatMessage.session_id,
                ChatMessage.role,
                ChatMessage.content,
                ChatMessage.tool_calls,
                ChatMessage.message_metadata,
                ChatMessage.created_at,
            )
        )
        .where(ChatMessage.session_id == chat_session.id)
    )
    if message_id is not None:
        stmt = stmt.where(ChatMessage.id == message_id)
    stmt = stmt.order_by(ChatMessage.created_at.desc(), desc(ChatMessage.id)).limit(fetch_limit)
    hot_messages: list[ChatMessage | ChatMessageArchive] = list(session.exec(stmt).all())

    # Transparent cold-archive fallback:
    # - For targeted `message_id`, fall back to cold only when absent from hot.
    # - For recent windows, use a 1-row index-only watermark on (session_id, created_at, id)
    #   so hot-only and strictly-newer hot windows avoid full-row cold fetches, while
    #   interleaved hot/cold windows (e.g. older hot row skipped by FOR UPDATE SKIP LOCKED)
    #   still merge up to `fetch_limit` cold rows so newer cold rows are never hidden.
    has_more = message_id is None and len(hot_messages) > limit
    messages: list[ChatMessage | ChatMessageArchive] = list(reversed(hot_messages[:limit]))

    if has_chat_message_archive_table(session.connection()):
        if message_id is not None:
            if not hot_messages:
                cold_stmt = (
                    select(ChatMessageArchive)
                    .options(
                        load_only(
                            ChatMessageArchive.id,
                            ChatMessageArchive.session_id,
                            ChatMessageArchive.role,
                            ChatMessageArchive.content,
                            ChatMessageArchive.tool_calls,
                            ChatMessageArchive.message_metadata,
                            ChatMessageArchive.created_at,
                        )
                    )
                    .where(
                        ChatMessageArchive.session_id == chat_session.id,
                        ChatMessageArchive.id == message_id,
                    )
                    .limit(1)
                )
                cold_messages = list(session.exec(cold_stmt).all())
                messages = cold_messages
                has_more = False
        else:
            watermark_row = session.exec(
                select(ChatMessageArchive.created_at, ChatMessageArchive.id)
                .where(ChatMessageArchive.session_id == chat_session.id)
                .order_by(ChatMessageArchive.created_at.desc(), desc(ChatMessageArchive.id))
                .limit(1)
            ).first()
            if watermark_row is not None:
                cold_watermark = (watermark_row[0], watermark_row[1])
                hot_ids = [msg.id for msg in hot_messages]
                oldest_hot_key = (
                    (hot_messages[-1].created_at, hot_messages[-1].id) if hot_messages else None
                )
                interleaved = oldest_hot_key is not None and cold_watermark > oldest_hot_key

                if len(hot_messages) == limit and not interleaved:
                    # Exact hot == limit boundary with all cold rows <= oldest hot row:
                    # hot already holds the newest `limit` messages, so an index-only check
                    # suffices to prove whether any distinct older cold row exists (`has_more`).
                    older_cold_stmt = select(ChatMessageArchive.id).where(
                        ChatMessageArchive.session_id == chat_session.id
                    )
                    if hot_ids:
                        older_cold_stmt = older_cold_stmt.where(
                            col(ChatMessageArchive.id).notin_(hot_ids)
                        )
                    has_more = session.exec(older_cold_stmt.limit(1)).first() is not None
                elif len(hot_messages) < limit or interleaved:
                    cold_stmt = (
                        select(ChatMessageArchive)
                        .options(
                            load_only(
                                ChatMessageArchive.id,
                                ChatMessageArchive.session_id,
                                ChatMessageArchive.role,
                                ChatMessageArchive.content,
                                ChatMessageArchive.tool_calls,
                                ChatMessageArchive.message_metadata,
                                ChatMessageArchive.created_at,
                            )
                        )
                        .where(ChatMessageArchive.session_id == chat_session.id)
                    )
                    if hot_ids:
                        cold_stmt = cold_stmt.where(col(ChatMessageArchive.id).notin_(hot_ids))
                    cold_stmt = cold_stmt.order_by(
                        ChatMessageArchive.created_at.desc(), desc(ChatMessageArchive.id)
                    ).limit(fetch_limit)
                    cold_messages = list(session.exec(cold_stmt).all())
                    if cold_messages:
                        by_id: dict[str, ChatMessage | ChatMessageArchive] = {
                            msg.id: msg for msg in cold_messages
                        }
                        # Prefer hot row when an ID temporarily exists in both tables during archival.
                        for msg in hot_messages:
                            by_id[msg.id] = msg
                        merged_asc = sorted(by_id.values(), key=lambda msg: (msg.created_at, msg.id))
                        has_more = len(merged_asc) > limit
                        messages = merged_asc[-limit:]
    serialized = [
        MessageResponse(
            id=msg.id,
            session_id=msg.session_id,
            role=msg.role,
            content=msg.content,
            tool_calls=client_tool_calls(msg.tool_calls),
            metadata=client_message_metadata(msg.message_metadata),
            created_at=msg.created_at,
        )
        for msg in messages
    ]
    return serialize_chat_session(chat_session), serialized, has_more


@router.get("/session/{project_id}/recent", response_model=list[MessageResponse])
def get_recent_messages(
    project_id: str,
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
    message_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
):
    """Get recent history, optionally one known message in the active session."""
    user_id = current_user.id

    # Check if project exists and is not deleted
    verify_project_access_sync(project_id, session, current_user)

    _, serialized, _ = load_active_session_recent_messages(
        session,
        project_id=project_id,
        user_id=user_id,
        limit=limit,
        message_id=message_id,
    )
    return serialized


@router.post("/messages/{message_id}/feedback", response_model=MessageFeedbackResponse)
def submit_message_feedback(
    message_id: str,
    request: MessageFeedbackRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    """Submit feedback for a chat message."""
    result = chat_feedback_service.submit_feedback(
        session=session,
        current_user=current_user,
        message_id=message_id,
        vote=request.vote,
        preset=request.preset,
        comment=request.comment,
    )
    return MessageFeedbackResponse(**result)


@router.delete("/session/{project_id}")
def clear_session(
    project_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    """Clear all messages in a session."""
    user_id = current_user.id

    log_with_context(
        logger,
        20,  # INFO
        "clear_session called",
        project_id=project_id,
        user_id=user_id,
    )

    # Check if project exists and is not deleted
    project = session.get(Project, project_id)
    if not project or project.owner_id != user_id:
        log_with_context(
            logger,
            40,  # ERROR
            "clear_session: Not authorized",
            project_id=project_id,
            user_id=user_id,
        )
        raise APIException(error_code=ErrorCode.NOT_AUTHORIZED, status_code=403)
    if project.is_deleted:
        log_with_context(
            logger,
            40,  # ERROR
            "clear_session: Project not found (deleted)",
            project_id=project_id,
            user_id=user_id,
        )
        raise APIException(error_code=ErrorCode.PROJECT_NOT_FOUND, status_code=404)

    active_sessions = _list_active_sessions(session, project_id, user_id)
    chat_session = active_sessions[0] if active_sessions else None

    if not chat_session:
        log_with_context(
            logger,
            20,  # INFO
            "clear_session: No session to clear",
            project_id=project_id,
            user_id=user_id,
        )
        return {"success": True, "message": "No session to clear"}

    cleared_count = int(
        session.exec(
            select(func.count())
            .select_from(ChatMessage)
            .where(ChatMessage.session_id == chat_session.id)
        ).one()
    )
    has_archive_table = has_chat_message_archive_table(session.connection())
    if has_archive_table:
        cleared_count += int(
            session.exec(
                select(func.count())
                .select_from(ChatMessageArchive)
                .where(
                    ChatMessageArchive.session_id == chat_session.id,
                    ~exists(select(literal(1)).where(ChatMessage.id == ChatMessageArchive.id)),
                )
            ).one()
        )
    # Delete hot chat_message FIRST, then cold chat_message_archive, then ux_summary.
    # Matching archival's hot -> cold lock order prevents both deadlocks and ghost
    # resurrection where an in-flight archival commits rows to chat_message_archive
    # after clear_session already swept the cold table.
    session.exec(
        delete(ChatMessage).where(ChatMessage.session_id == chat_session.id)
    )
    if has_archive_table:
        session.exec(
            delete(ChatMessageArchive).where(ChatMessageArchive.session_id == chat_session.id)
        )
    if has_chat_message_ux_summary_table(session.connection()):
        session.exec(
            delete(ChatMessageUxSummary).where(ChatMessageUxSummary.session_id == chat_session.id)
        )

    chat_session.message_count = 0
    chat_session.updated_at = utcnow()
    cleared_session_id = chat_session.id
    session.commit()
    delete_agent_wire_prefix_cache(cleared_session_id)

    log_with_context(
        logger,
        20,  # INFO
        "clear_session completed",
        project_id=project_id,
        user_id=user_id,
        session_id=chat_session.id,
        cleared_count=cleared_count,
    )

    return {"success": True, "message": f"Cleared {cleared_count} messages"}


@router.post("/session/{project_id}/new", response_model=SessionResponse)
def create_new_session(
    project_id: str,
    title: str | None = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
    accept_language: Annotated[str | None, Header(alias="Accept-Language")] = None,
):
    """Create a new chat session (deactivates current one)."""
    user_id = current_user.id

    # Check if project exists and is not deleted before reading user preferences.
    verify_project_access_sync(project_id, session, current_user)
    effective_title = (
        title
        if title is not None and title.strip()
        else new_chat_session_title(resolve_user_language(session, user_id, accept_language))
    )

    log_with_context(
        logger,
        20,  # INFO
        "create_new_session called",
        project_id=project_id,
        user_id=user_id,
        title=effective_title,
    )

    if _should_offload_session_work(session):
        new_session = _create_new_session_sync(project_id, user_id, effective_title)
    else:
        new_session = _deactivate_and_create_session(session, project_id, user_id, effective_title)

    log_with_context(
        logger,
        20,  # INFO
        "create_new_session completed",
        project_id=project_id,
        user_id=user_id,
        new_session_id=new_session.id,
        title=effective_title,
    )

    return SessionResponse(
        id=new_session.id,
        user_id=new_session.user_id,
        project_id=new_session.project_id,
        title=new_session.title,
        is_active=new_session.is_active,
        message_count=new_session.message_count,
        created_at=new_session.created_at,
        updated_at=new_session.updated_at,
    )
