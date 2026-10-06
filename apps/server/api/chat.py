"""
Chat session API endpoints.

Provides FastAPI router for chat session management:
- GET /api/v1/chat/session/{project_id} - Get or create session for project
- GET /api/v1/chat/session/{project_id}/messages - Get session messages
- DELETE /api/v1/chat/session/{project_id} - Clear session
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from services.auth import get_current_active_user
from sqlalchemy import delete, desc
from sqlalchemy.orm import load_only
from sqlmodel import Session, and_, func, select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import create_session, get_session
from models import ChatMessage, ChatSession, Project, User
from services.chat_feedback_service import chat_feedback_service
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
    stmt = (
        select(ChatSession)
        .where(
            and_(
                ChatSession.project_id == project_id,
                ChatSession.user_id == user_id,
                ChatSession.is_active,
            )
        )
        .order_by(
            desc(ChatSession.updated_at),
            desc(ChatSession.created_at),
            desc(ChatSession.id),
        )
    )
    return session.exec(stmt).all()


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
        current_sessions = _list_active_sessions(session, project_id, user_id)
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

        new_session = ChatSession(
            user_id=user_id,
            project_id=project_id,
            title=title,
            is_active=True,
            message_count=0,
        )
        session.add(new_session)
        session.commit()
        session.refresh(new_session)
        return new_session


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

    active_sessions = _list_active_sessions(session, project_id, user_id)
    chat_session = active_sessions[0] if active_sessions else None

    if not chat_session:
        log_with_context(
            logger,
            20,  # INFO
            "Creating new chat session",
            project_id=project_id,
            user_id=user_id,
        )
        chat_session = ChatSession(
            user_id=user_id,
            project_id=project_id,
            title="AI 助手对话",
            is_active=True,
            message_count=0,
        )
        session.add(chat_session)
        session.commit()
        session.refresh(chat_session)
    else:
        if len(active_sessions) > 1:
            stale_count = 0
            for stale in active_sessions[1:]:
                if stale.is_active:
                    stale.is_active = False
                    session.add(stale)
                    stale_count += 1
            if stale_count > 0:
                session.commit()
            log_with_context(
                logger,
                30,  # WARNING
                "Multiple active chat sessions detected; stale sessions deactivated",
                project_id=project_id,
                user_id=user_id,
                kept_session_id=chat_session.id,
                stale_count=stale_count,
            )

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


@router.get("/session/{project_id}/messages", response_model=list[MessageResponse])
def get_session_messages(
    project_id: str,
    limit: int = Query(50, ge=1, le=100),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    """Get messages for a project's chat session."""
    user_id = current_user.id

    log_with_context(
        logger,
        20,  # INFO
        "get_session_messages called",
        project_id=project_id,
        user_id=user_id,
        limit=limit,
    )

    # Check if project exists and is not deleted
    verify_project_access_sync(project_id, session, current_user)

    active_sessions = _list_active_sessions(session, project_id, user_id)
    chat_session = active_sessions[0] if active_sessions else None

    if not chat_session:
        log_with_context(
            logger,
            20,  # INFO
            "get_session_messages: No session found",
            project_id=project_id,
            user_id=user_id,
        )
        return []

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
        .order_by(ChatMessage.created_at.desc(), desc(ChatMessage.id))
        .limit(limit)
    )
    # 取最近 limit 条再反转为时间升序输出；asc+limit 会永远取最旧的一页，
    # 导致超过 limit 的会话尾部消息结构性不可达。
    messages = list(reversed(session.exec(stmt).all()))

    log_with_context(
        logger,
        20,  # INFO
        "get_session_messages completed",
        project_id=project_id,
        user_id=user_id,
        session_id=chat_session.id,
        message_count=len(messages),
    )

    return [
        MessageResponse(
            id=msg.id,
            session_id=msg.session_id,
            role=msg.role,
            content=msg.content,
            tool_calls=msg.tool_calls,
            metadata=msg.message_metadata,
            created_at=msg.created_at,
        )
        for msg in messages
    ]


@router.get("/session/{project_id}/recent", response_model=list[MessageResponse])
def get_recent_messages(
    project_id: str,
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    """Get recent messages for loading chat history."""
    user_id = current_user.id

    # Check if project exists and is not deleted
    verify_project_access_sync(project_id, session, current_user)

    active_sessions = _list_active_sessions(session, project_id, user_id)
    chat_session = active_sessions[0] if active_sessions else None

    if not chat_session:
        return []

    # Get last N messages
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
        .order_by(ChatMessage.created_at.desc(), desc(ChatMessage.id))
        .limit(limit)
    )
    messages = list(reversed(session.exec(stmt).all()))

    return [
        MessageResponse(
            id=msg.id,
            session_id=msg.session_id,
            role=msg.role,
            content=msg.content,
            tool_calls=msg.tool_calls,
            metadata=msg.message_metadata,
            created_at=msg.created_at,
        )
        for msg in messages
    ]


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
    session.exec(
        delete(ChatMessage).where(ChatMessage.session_id == chat_session.id)
    )

    chat_session.message_count = 0
    chat_session.updated_at = utcnow()
    session.commit()

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
    title: str = "新对话",
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    """Create a new chat session (deactivates current one)."""
    user_id = current_user.id

    log_with_context(
        logger,
        20,  # INFO
        "create_new_session called",
        project_id=project_id,
        user_id=user_id,
        title=title,
    )

    # Check if project exists and is not deleted
    verify_project_access_sync(project_id, session, current_user)
    if _should_offload_session_work(session):
        new_session = _create_new_session_sync(project_id, user_id, title)
    else:
        current_sessions = _list_active_sessions(session, project_id, user_id)
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

        new_session = ChatSession(
            user_id=user_id,
            project_id=project_id,
            title=title,
            is_active=True,
            message_count=0,
        )
        session.add(new_session)
        session.commit()
        session.refresh(new_session)

    log_with_context(
        logger,
        20,  # INFO
        "create_new_session completed",
        project_id=project_id,
        user_id=user_id,
        new_session_id=new_session.id,
        title=title,
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
