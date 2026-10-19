"""
Activation event model.

Stores user lifecycle activation milestones for funnel analytics.
"""

from datetime import datetime

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from config.datetime_utils import utcnow

from .utils import generate_uuid

ACTIVATION_EVENT_SIGNUP_SUCCESS = "signup_success"
ACTIVATION_EVENT_PROJECT_CREATED = "project_created"
ACTIVATION_EVENT_FIRST_FILE_SAVED = "first_file_saved"
ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED = "first_ai_action_accepted"
# 作者主动发出的第一条 AI 消息：预扣额度成功后记录（api/agent.py）。
ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT = "first_ai_message_sent"
# 作者第一次有一轮 AI 回复正常结束并计费（stream_billing 的 completed）。
ACTIVATION_EVENT_FIRST_AI_REPLY_SUCCEEDED = "first_ai_reply_succeeded"

ACTIVATION_EVENT_NAMES = {
    ACTIVATION_EVENT_SIGNUP_SUCCESS,
    ACTIVATION_EVENT_PROJECT_CREATED,
    ACTIVATION_EVENT_FIRST_FILE_SAVED,
    ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
    ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT,
    ACTIVATION_EVENT_FIRST_AI_REPLY_SUCCEEDED,
}


class ActivationEvent(SQLModel, table=True):
    """User activation milestone event."""

    __tablename__ = "activation_event"
    __table_args__ = (
        UniqueConstraint("user_id", "event_name", name="uq_activation_event_user_event"),
    )

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    project_id: str | None = Field(default=None, foreign_key="project.id", index=True)
    event_name: str = Field(max_length=64, index=True)
    event_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow, index=True)
