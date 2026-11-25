"""Narrow per-assistant-message UX health summary projection.

Stores strictly structured round classification, feedback vote/preset, and
undo/restore card counters (1:1 with assistant rows in ``chat_message``) so
admin UX health analytics never detoast multi-GB ``message_metadata`` or
``tool_calls`` JSON blobs.

Zero manuscript text, zero prompt text, zero user PII.
"""

from __future__ import annotations

import json
import weakref
from datetime import datetime
from typing import Any

from sqlalchemy import Column, ForeignKey, Index, String, event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Field, SQLModel

from .entities import ChatMessage

_UNDOABLE_CARD_TOOLS = frozenset({"edit_file", "write_chapter"})
_DELETE_CARD_TOOLS = frozenset({"delete_file"})
_FAILED_CARD_STATUSES = frozenset({"error", "failed", "pending"})
_RUNAWAY_STOP_REASONS = frozenset({"repetition_loop", "runaway_stop"})
_MAX_LABEL_LEN = 128


def parse_json_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_json_list(raw: str | None) -> list[Any]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


_ITERATION_EXHAUSTED_LAYER_PRIORITY = {
    "unexecuted_tool_text": (1, "unexecuted_tool_text"),
    "empty_file_correction": (2, "empty_file_correction"),
    "tool_call": (3, "max_turns_exceeded"),
    "collaboration": (4, "iteration_exhausted"),
}


def iteration_exhausted_abnormal_reason(metadata: dict[str, Any]) -> str | None:
    """Return a bounded abnormal reason when ``status_cards`` contains an ``iteration_exhausted`` card."""
    cards = metadata.get("status_cards")
    if not isinstance(cards, list):
        return None
    best_priority: int | None = None
    best_reason: str | None = None
    for card in cards:
        if not isinstance(card, dict) or card.get("type") != "iteration_exhausted":
            continue
        raw_limit = card.get("limit")
        if isinstance(raw_limit, str):
            normalized_limit = raw_limit.strip()
            if normalized_limit == "server_draining":
                return "server_draining"
            if normalized_limit == "wall_clock":
                return "run_deadline_exceeded"
        raw_layer = card.get("layer")
        layer = raw_layer.strip() if isinstance(raw_layer, str) else ""
        priority, reason = _ITERATION_EXHAUSTED_LAYER_PRIORITY.get(layer, (5, "iteration_exhausted"))
        if best_priority is None or priority < best_priority:
            best_priority = priority
            best_reason = reason
    return best_reason


def classify_round(metadata: dict[str, Any]) -> tuple[str, str | None]:
    """Classify one assistant round from ``message_metadata``.

    Returns ``(category, abnormal_reason)`` where ``category`` is one of
    ``healthy``, ``user_stopped``, ``client_disconnected``, or ``abnormal``.
    """
    raw_stop_reason = metadata.get("stop_reason")
    stop_reason = raw_stop_reason.strip() if isinstance(raw_stop_reason, str) else ""

    stop_outcome = metadata.get("stop_outcome")
    stop_outcome = stop_outcome if isinstance(stop_outcome, dict) else {}
    raw_outcome_reason = stop_outcome.get("reason")
    outcome_reason = raw_outcome_reason.strip() if isinstance(raw_outcome_reason, str) else ""

    effective_reason = stop_reason or outcome_reason or "unknown"

    if effective_reason == "user_stopped" or outcome_reason == "user_stopped":
        return "user_stopped", None
    if effective_reason == "client_disconnected" or outcome_reason == "client_disconnected":
        return "client_disconnected", None

    has_unexecuted_tool_text = bool(stop_outcome.get("unexecuted_tool_text"))
    has_unwritten_files = bool(stop_outcome.get("unwritten_files"))
    has_runaway_stop = (
        bool(metadata.get("runaway_stop"))
        or bool(stop_outcome.get("runaway_stop"))
        or effective_reason in _RUNAWAY_STOP_REASONS
    )
    exhausted_reason = iteration_exhausted_abnormal_reason(metadata)
    is_incomplete_outcome = outcome_reason == "incomplete"

    if (
        effective_reason == "end_turn"
        and not has_unexecuted_tool_text
        and not has_unwritten_files
        and not has_runaway_stop
        and exhausted_reason is None
        and not is_incomplete_outcome
    ):
        return "healthy", None

    if effective_reason == "end_turn":
        if has_unexecuted_tool_text:
            return "abnormal", "unexecuted_tool_text"
        if has_runaway_stop:
            return "abnormal", "runaway_stop"
        if exhausted_reason is not None:
            return "abnormal", exhausted_reason
        if has_unwritten_files:
            return "abnormal", "unwritten_files"
        return "abnormal", "incomplete"

    return "abnormal", effective_reason[:_MAX_LABEL_LEN]


def count_tool_cards(calls: list[Any]) -> tuple[int, int, int, int]:
    """Count ``(edit_cards, edit_undone, delete_cards, delete_restored)`` for one message."""
    edit_cards = 0
    edit_undone = 0
    delete_cards = 0
    delete_restored = 0

    for call in calls:
        if not isinstance(call, dict):
            continue
        status = str(call.get("status") or "").strip().lower()
        if status in _FAILED_CARD_STATUSES or call.get("error"):
            continue
        tool_name = call.get("name")
        result = call.get("result")
        data = (
            result.get("data")
            if isinstance(result, dict) and isinstance(result.get("data"), dict)
            else result
        )

        if tool_name in _UNDOABLE_CARD_TOOLS and isinstance(data, dict):
            undo = data.get("undo")
            if isinstance(undo, dict):
                edit_cards += 1
                if undo.get("undone") is True:
                    edit_undone += 1
        elif tool_name in _DELETE_CARD_TOOLS:
            if result is True:
                delete_cards += 1
            elif isinstance(data, dict) and "error" not in data:
                applied = (
                    data.get("mutation_applied") is True
                    or data.get("restored") is True
                    or data.get("deleted_at") is not None
                )
                if applied:
                    delete_cards += 1
                    if data.get("restored") is True:
                        delete_restored += 1

    return edit_cards, edit_undone, delete_cards, delete_restored


def extract_feedback_vote_and_preset(metadata: dict[str, Any]) -> tuple[str | None, str | None]:
    feedback = metadata.get("feedback")
    if not isinstance(feedback, dict):
        return None, None
    vote = feedback.get("vote")
    if vote not in ("up", "down"):
        return None, None
    raw_preset = feedback.get("preset")
    preset = (
        raw_preset.strip()[:_MAX_LABEL_LEN]
        if isinstance(raw_preset, str) and raw_preset.strip()
        else None
    )
    return vote, preset


def build_chat_message_ux_summary_row(
    *,
    message_id: str,
    session_id: str,
    created_at: datetime,
    raw_metadata: str | None,
    raw_tool_calls: str | None,
) -> dict[str, Any]:
    """Compute a complete ``chat_message_ux_summary`` row dict from raw JSON columns."""
    metadata = parse_json_dict(raw_metadata)
    round_category, abnormal_reason = classify_round(metadata)
    feedback_vote, feedback_preset = extract_feedback_vote_and_preset(metadata)
    if raw_tool_calls and (
        '"edit_file"' in raw_tool_calls
        or '"write_chapter"' in raw_tool_calls
        or '"delete_file"' in raw_tool_calls
    ):
        edit_cards, edit_undone, delete_cards, delete_restored = count_tool_cards(
            parse_json_list(raw_tool_calls)
        )
    else:
        edit_cards = edit_undone = delete_cards = delete_restored = 0

    naive_created_at = created_at.replace(tzinfo=None) if created_at.tzinfo is not None else created_at
    return {
        "message_id": message_id,
        "session_id": session_id,
        "created_at": naive_created_at,
        "round_category": round_category,
        "abnormal_reason": abnormal_reason,
        "feedback_vote": feedback_vote,
        "feedback_preset": feedback_preset,
        "ai_edit_cards": edit_cards,
        "ai_edit_undone": edit_undone,
        "ai_delete_cards": delete_cards,
        "ai_delete_restored": delete_restored,
    }


class ChatMessageUxSummary(SQLModel, table=True):
    """Narrow, non-TOAST per-assistant-message UX metrics row."""

    __tablename__ = "chat_message_ux_summary"
    __table_args__ = (
        Index("ix_chat_message_ux_summary_created_at", "created_at"),
        Index("ix_chat_message_ux_summary_session_id", "session_id"),
    )

    message_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("chat_message.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    session_id: str = Field(max_length=64)
    # Naive UTC, matching ChatMessage.created_at.
    created_at: datetime = Field(default_factory=datetime.utcnow)
    round_category: str = Field(max_length=24)
    abnormal_reason: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)
    feedback_vote: str | None = Field(default=None, max_length=8)
    feedback_preset: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)
    ai_edit_cards: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    ai_edit_undone: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    ai_delete_cards: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    ai_delete_restored: int = Field(default=0, sa_column_kwargs={"server_default": "0"})


_SUMMARY_TABLE = ChatMessageUxSummary.__table__
_REQUIRED_ATTRS = frozenset({"id", "session_id", "role", "created_at", "message_metadata", "tool_calls"})
# Engines where chat_message_ux_summary has already been observed. Never caches False
# so late table creation (e.g. create_all or migration) is picked up immediately.
_ENGINES_WITH_SUMMARY_TABLE: weakref.WeakSet[Any] = weakref.WeakSet()


def has_chat_message_ux_summary_table(connection: Any) -> bool:
    """Return True if ``chat_message_ux_summary`` exists on ``connection`` (cached once present)."""
    engine = getattr(connection, "engine", None)
    if engine is not None and engine in _ENGINES_WITH_SUMMARY_TABLE:
        return True
    dialect = getattr(connection, "dialect", None)
    if dialect is None:
        return False
    exists = bool(dialect.has_table(connection, ChatMessageUxSummary.__tablename__))
    if exists and engine is not None:
        _ENGINES_WITH_SUMMARY_TABLE.add(engine)
    return exists


def _upsert_summary_on_connection(connection: Any, target: ChatMessage) -> None:
    if not target.id or not target.session_id or target.created_at is None:
        return
    if not has_chat_message_ux_summary_table(connection):
        return
    row = build_chat_message_ux_summary_row(
        message_id=target.id,
        session_id=target.session_id,
        created_at=target.created_at,
        raw_metadata=target.message_metadata,
        raw_tool_calls=target.tool_calls,
    )
    update_cols = {k: v for k, v in row.items() if k != "message_id"}
    dialect_name = getattr(getattr(connection, "dialect", None), "name", "")
    if dialect_name == "postgresql":
        stmt = (
            pg_insert(_SUMMARY_TABLE)
            .values(**row)
            .on_conflict_do_update(index_elements=["message_id"], set_=update_cols)
        )
    elif dialect_name == "sqlite":
        stmt = (
            sqlite_insert(_SUMMARY_TABLE)
            .values(**row)
            .on_conflict_do_update(index_elements=["message_id"], set_=update_cols)
        )
    else:
        connection.execute(_SUMMARY_TABLE.delete().where(_SUMMARY_TABLE.c.message_id == target.id))
        stmt = _SUMMARY_TABLE.insert().values(**row)
    connection.execute(stmt)


@event.listens_for(ChatMessage, "after_insert")
def _sync_ux_summary_after_insert(_mapper: Any, connection: Any, target: ChatMessage) -> None:
    if target.role != "assistant":
        return
    _upsert_summary_on_connection(connection, target)


@event.listens_for(ChatMessage, "after_update")
def _sync_ux_summary_after_update(_mapper: Any, connection: Any, target: ChatMessage) -> None:
    state = sa_inspect(target)
    # Partial load_only updates (e.g. historical query_files archival) intentionally leave
    # unrelated columns unloaded and never change UX card counts or round metadata.
    if _REQUIRED_ATTRS & state.unloaded:
        return
    if target.role != "assistant":
        if state.attrs.role.history.has_changes() and has_chat_message_ux_summary_table(connection):
            connection.execute(_SUMMARY_TABLE.delete().where(_SUMMARY_TABLE.c.message_id == target.id))
        return
    if not (
        state.attrs.message_metadata.history.has_changes()
        or state.attrs.tool_calls.history.has_changes()
        or state.attrs.role.history.has_changes()
        or state.attrs.created_at.history.has_changes()
        or state.attrs.session_id.history.has_changes()
    ):
        return
    _upsert_summary_on_connection(connection, target)
