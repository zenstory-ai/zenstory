"""停止 / 断线一轮的收尾（agent.core.round_outcome）：空白占位文件的数据安全校验、终态落库、等消息 id。

见 .agents/notes/implemented/architecture/2026-10-09-stop-output-definition-and-round-outcome.md。
"""

import asyncio
import json
from uuid import uuid4

import pytest

from agent.core.round_outcome import (
    RunOutcome,
    build_stop_outcome,
    record_round_outcome,
    remove_empty_placeholders,
)
from models import ChatMessage, ChatSession, File, Project, User

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _round_outcome_on_test_db(monkeypatch):
    """remove_empty_placeholders / record_round_outcome open their own sessions."""
    import database
    import services.llama_index as llama_index
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(database, "create_session", TestSessionLocal)
    monkeypatch.setattr(llama_index, "schedule_index_delete", lambda **_kwargs: None)


def _project(db_session) -> Project:
    suffix = uuid4().hex[:8]
    user = User(
        username=f"round_outcome_{suffix}",
        email=f"round_outcome_{suffix}@example.com",
        hashed_password="x",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    project = Project(name="Round Outcome", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    return project


def _file(db_session, project: Project, title: str, **fields) -> File:
    file = File(project_id=project.id, title=title, content="", file_type="draft", **fields)
    db_session.add(file)
    db_session.commit()
    return file


def _candidate(file: File) -> dict[str, str]:
    return {"id": file.id, "title": file.title}


def _is_deleted(db_session, file: File) -> bool:
    db_session.expire_all()
    return db_session.get(File, file.id).is_deleted


# ------------------------------------------------------- remove_empty_placeholders 的每一道校验


def test_still_empty_placeholder_of_this_project_is_removed(db_session):
    project = _project(db_session)
    placeholder = _file(db_session, project, "第1章 最后一页")

    removed = remove_empty_placeholders(project.id, [_candidate(placeholder)])

    assert removed == [{"id": placeholder.id, "title": "第1章 最后一页"}]
    assert _is_deleted(db_session, placeholder) is True


def test_candidate_that_got_text_since_the_round_is_kept(db_session):
    """作者在新章节里打了字（或中断时补存的 <file> 残稿已落库）：一个字都不能丢。"""
    project = _project(db_session)
    placeholder = _file(db_session, project, "第1章")
    placeholder.content = "雨还在下。"
    db_session.add(placeholder)
    db_session.commit()

    assert remove_empty_placeholders(project.id, [_candidate(placeholder)]) == []
    assert _is_deleted(db_session, placeholder) is False
    db_session.expire_all()
    assert db_session.get(File, placeholder.id).content == "雨还在下。"


def test_whitespace_only_candidate_counts_as_empty(db_session):
    project = _project(db_session)
    placeholder = _file(db_session, project, "第2章")
    placeholder.content = " \n\t"
    db_session.add(placeholder)
    db_session.commit()

    assert [item["id"] for item in remove_empty_placeholders(project.id, [_candidate(placeholder)])] == [
        placeholder.id
    ]


def test_candidate_in_another_project_is_kept(db_session):
    project = _project(db_session)
    other_project = _project(db_session)
    other_empty = _file(db_session, other_project, "别的作品的空白页")

    assert remove_empty_placeholders(project.id, [_candidate(other_empty)]) == []
    assert _is_deleted(db_session, other_empty) is False


def test_candidate_with_children_is_kept(db_session):
    project = _project(db_session)
    parent = _file(db_session, project, "第一卷")
    _file(db_session, project, "第1章", parent_id=parent.id)

    assert remove_empty_placeholders(project.id, [_candidate(parent)]) == []
    assert _is_deleted(db_session, parent) is False


def test_candidate_whose_only_children_are_deleted_is_removed(db_session):
    project = _project(db_session)
    parent = _file(db_session, project, "第一卷")
    _file(db_session, project, "旧稿", parent_id=parent.id, is_deleted=True)

    assert [item["id"] for item in remove_empty_placeholders(project.id, [_candidate(parent)])] == [parent.id]


def test_folder_candidate_is_kept(db_session):
    project = _project(db_session)
    folder = File(project_id=project.id, title="正文", content="", file_type="folder")
    db_session.add(folder)
    db_session.commit()

    assert remove_empty_placeholders(project.id, [_candidate(folder)]) == []
    assert _is_deleted(db_session, folder) is False


def test_already_deleted_or_unknown_candidates_are_skipped(db_session):
    project = _project(db_session)
    gone = _file(db_session, project, "已删除", is_deleted=True)

    removed = remove_empty_placeholders(
        project.id, [_candidate(gone), {"id": "no-such-file", "title": "x"}, {"id": "", "title": "y"}]
    )

    assert removed == []


def test_only_the_safe_candidates_of_a_mixed_batch_are_removed(db_session):
    project = _project(db_session)
    empty = _file(db_session, project, "第3章")
    written = _file(db_session, project, "第2章")
    written.content = "正文"
    db_session.add(written)
    db_session.commit()

    removed = remove_empty_placeholders(project.id, [_candidate(written), _candidate(empty)])

    assert [item["id"] for item in removed] == [empty.id]
    assert _is_deleted(db_session, written) is False


# ------------------------------------------------------------------------- record_round_outcome


def _assistant(db_session, project: Project, metadata: dict | None) -> ChatMessage:
    chat = ChatSession(user_id=project.owner_id, project_id=project.id)
    db_session.add(chat)
    db_session.commit()
    message = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="",
        message_metadata=json.dumps(metadata) if metadata is not None else None,
    )
    db_session.add(message)
    db_session.commit()
    return message


def _metadata(db_session, message: ChatMessage) -> dict:
    db_session.expire_all()
    return json.loads(db_session.get(ChatMessage, message.id).message_metadata)


_OUTCOME = build_stop_outcome(stop_kind="user_stopped", charged=True, saved_output=True)


def test_resumable_stop_reason_is_kept_and_the_outcome_is_added(db_session):
    """停止到达之前这一轮已按 max_turns_exceeded 落库：保留它，「继续」入口不丢。"""
    project = _project(db_session)
    message = _assistant(db_session, project, {"stop_reason": "max_turns_exceeded", "usage": {"input": 1}})

    assert record_round_outcome(message.id, stop_kind="user_stopped", outcome=_OUTCOME) is True

    metadata = _metadata(db_session, message)
    assert metadata["stop_reason"] == "max_turns_exceeded"
    assert metadata["stop_outcome"] == _OUTCOME
    assert metadata["usage"] == {"input": 1}


@pytest.mark.parametrize("existing", [None, {}, {"stop_reason": "cancelled"}, {"stop_reason": "client_disconnected"}])
def test_missing_or_cancel_type_stop_reason_is_replaced(db_session, existing):
    project = _project(db_session)
    message = _assistant(db_session, project, existing)

    assert record_round_outcome(message.id, stop_kind="user_stopped", outcome=_OUTCOME) is True

    metadata = _metadata(db_session, message)
    assert metadata["stop_reason"] == "user_stopped"
    assert metadata["stop_outcome"] == _OUTCOME


# ---------------------------------------------------------------------- RunOutcome.wait_message_id


async def test_wait_message_id_returns_none_on_timeout():
    assert await RunOutcome().wait_message_id(0.01) is None


async def test_wait_message_id_returns_the_resolved_id():
    outcome = RunOutcome()
    outcome.resolve_message_id("m1")
    assert await outcome.wait_message_id(1.0) == "m1"


async def test_wait_message_id_does_not_swallow_cancellation():
    """等待中的请求被取消（客户端断开）：取消要传到调用方的断线路径，不能变成「没有 id」。"""
    outcome = RunOutcome()
    waiter = asyncio.create_task(outcome.wait_message_id(30.0))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    # 消息 id 之后照样能交回（后台的终态记录还要用它）。
    outcome.resolve_message_id("m2")
    assert await outcome.wait_message_id(1.0) == "m2"
