"""
Chat message feedback service.

Stores like/dislike feedback in ChatMessage.message_metadata["feedback"].
"""

import json
from datetime import datetime
from typing import Any, Literal

from sqlmodel import Session

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import ChatMessage, ChatSession, User
from utils.permission import verify_project_access_sync

FeedbackVote = Literal["up", "down"]


def _load_message_metadata(raw_metadata: str | None) -> dict[str, Any]:
    """Parse message_metadata JSON safely."""
    if not raw_metadata:
        return {}
    try:
        parsed = json.loads(raw_metadata)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_text(value: str | None) -> str | None:
    """Trim optional text and convert blank values to None."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse ISO timestamp safely."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class ChatFeedbackService:
    """Service for writing feedback to chat messages."""

    def submit_feedback(
        self,
        *,
        session: Session,
        current_user: User,
        message_id: str,
        vote: FeedbackVote,
        preset: str | None = None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """
        Submit or update feedback for one chat message.

        Returns:
            Dict compatible with MessageFeedbackResponse.
        """
        message = session.get(ChatMessage, message_id)
        if not message:
            raise APIException(
                error_code=ErrorCode.CHAT_MESSAGE_NOT_FOUND,
                status_code=404,
            )
        if message.role != "assistant":
            raise APIException(
                error_code=ErrorCode.BAD_REQUEST,
                status_code=400,
                detail="Feedback is only supported for assistant messages",
            )

        chat_session = session.get(ChatSession, message.session_id)
        if not chat_session:
            raise APIException(
                error_code=ErrorCode.CHAT_SESSION_NOT_FOUND,
                status_code=404,
            )

        # Permission check: user must own the project tied to this message.
        verify_project_access_sync(chat_session.project_id, session, current_user)

        metadata = _load_message_metadata(message.message_metadata)
        current_feedback = metadata.get("feedback")
        current_feedback = current_feedback if isinstance(current_feedback, dict) else {}

        now = utcnow()
        created_at = _parse_timestamp(current_feedback.get("created_at")) or now

        feedback_payload = {
            "vote": vote,
            "preset": _normalize_text(preset),
            "comment": _normalize_text(comment),
            "created_at": created_at.isoformat(),
            "updated_at": now.isoformat(),
        }

        metadata["feedback"] = feedback_payload
        message.message_metadata = json.dumps(metadata, ensure_ascii=False)

        session.add(message)
        session.commit()
        session.refresh(message)

        return {
            "message_id": message.id,
            "feedback": {
                "vote": feedback_payload["vote"],
                "preset": feedback_payload["preset"],
                "comment": feedback_payload["comment"],
                "created_at": created_at,
                "updated_at": now,
            },
            "updated_at": now,
        }


chat_feedback_service = ChatFeedbackService()
