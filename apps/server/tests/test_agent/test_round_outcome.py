"""停止 / 断线一轮的收尾（agent.core.round_outcome）：空白占位文件的数据安全校验、终态落库、等消息 id、
本轮写入了哪些文件。

见 .agents/notes/implemented/architecture/2026-10-09-stop-output-definition-and-round-outcome.md。
"""

import asyncio
import json
from uuid import uuid4

import pytest

from agent.core.round_outcome import (
    RoundWrittenFiles,
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
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(database, "create_session", TestSessionLocal)


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
    # 与 StreamBillingTracker 一样带上 create_file 结果里的版本（这些文件建好后没被动过）。
    return {"id": file.id, "title": file.title, "updated_at": file.updated_at.isoformat()}


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


def test_placeholder_the_author_renamed_after_creation_is_kept(db_session):
    """本轮新建的空文件，作者在别处改了标题（版本前进）：退还的停止不把它当占位文件删掉。

    候选从计费 tracker 看到的 create_file 结果来，带着建好时的版本；删除前比的是这个版本，
    不是删除时刚读到的版本。"""
    from agent.core.stream_billing import StreamBillingTracker
    from config.datetime_utils import advance_timestamp, utcnow

    project = _project(db_session)
    placeholder = _file(db_session, project, "AI 起的标题")
    tracker = StreamBillingTracker()
    tracker.observe(_created(placeholder.id, placeholder.title, updated_at=placeholder.updated_at.isoformat()))
    candidates = tracker.removable_placeholders()

    placeholder.title = "作者改的标题"
    placeholder.updated_at = advance_timestamp(placeholder.updated_at, now=utcnow())
    db_session.add(placeholder)
    db_session.commit()

    assert remove_empty_placeholders(project.id, candidates) == []
    assert _is_deleted(db_session, placeholder) is False
    assert db_session.get(File, placeholder.id).title == "作者改的标题"


def test_placeholder_without_its_creation_version_is_kept(db_session):
    """说不出建好时版本的候选不删（宁可留一个空文件，也不删可能被作者动过的文件）。"""
    project = _project(db_session)
    placeholder = _file(db_session, project, "第4章")

    assert remove_empty_placeholders(project.id, [{"id": placeholder.id, "title": "第4章"}]) == []
    assert _is_deleted(db_session, placeholder) is False


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


def test_removable_keeps_files_whose_capture_save_was_attempted():
    """补存过正文的文件不当空白占位移除：失败 / 超时（回执 None）时后台线程可能稍后才提交。"""
    outcome = RunOutcome(keep_file_ids={"typed"}, saved_file_word_counts={"timed_out": None, "saved": 12})
    candidates = [{"id": fid, "title": fid} for fid in ("blank", "typed", "timed_out", "saved")]

    assert outcome.removable(candidates) == [{"id": "blank", "title": "blank"}]


# ---------------------------------------------------------------------- RoundWrittenFiles


def _frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _created(file_id: str, title: str, content: str = "", **extra) -> str:
    return _frame(
        "tool_result",
        {
            "tool_name": "create_file",
            "status": "success",
            "data": {"id": file_id, "title": title, "content": content, "file_type": "draft", **extra},
        },
    )


def _edited(file_id: str, title: str, *, mutation_applied: bool = True) -> str:
    return _frame(
        "tool_result",
        {
            "tool_name": "edit_file",
            "status": "success",
            "data": {"id": file_id, "title": title, "mutation_applied": mutation_applied},
        },
    )


def _written(*frames: str) -> list[dict]:
    files = RoundWrittenFiles()
    for frame in frames:
        files.observe(frame)
    return files.files()


def test_partial_delivery_uses_save_receipts_not_create_shells_or_stream_end():
    files = RoundWrittenFiles()
    files.observe(_created("filled", "林舟"))
    files.observe(_frame("file_content", {"file_id": "filled", "chunk": "林舟推开门。"}))
    files.observe(_frame("file_content_end", {"file_id": "filled"}))
    files.observe(_created("empty", "周念"))
    files.observe(_frame("file_content_end", {"file_id": "empty"}))
    assert files.delivery(final_word_counts={"filled": 5}) == {
        "written_files": [{"id": "filled", "title": "林舟", "words": 5}],
        "unwritten_files": [{"id": "empty", "title": "周念"}],
    }
    # Even file_content_end does not prove the attempted body was persisted.
    assert files.delivery(final_word_counts={"filled": None}) == {
        "unwritten_files": [{"id": "filled", "title": "林舟"}, {"id": "empty", "title": "周念"}],
    }
    # A later confirmed write completes the same file; initial empty create is
    # never an error, and a fully written round has no unfinished-file warning.
    files.observe(_frame("file_content", {"file_id": "empty", "chunk": "周念关上门。"}))
    assert "unwritten_files" not in files.delivery(final_word_counts={"filled": 5, "empty": 5})


def test_file_the_author_asked_to_keep_empty_is_neither_written_nor_unwritten():
    """作者只要建标题（create_file empty_by_request=true）：空文件本身就是交付物，不算「未确认保存」，
    也不算写进了正文；同一轮里普通的空壳照旧列为未确认保存。"""
    files = RoundWrittenFiles()
    files.observe(_frame("router_decided", {"routing_metadata": {"write_content": False}}))
    files.observe(_created("ch-10", "第10章", empty_by_request=True))

    assert files.delivery(final_word_counts={}) == {}
    assert files.completion_receipt(final_word_counts={}, any_write_succeeded=False) == {}

    files.observe(_created("ch-11", "第11章"))
    assert files.delivery(final_word_counts={}) == {"unwritten_files": [{"id": "ch-11", "title": "第11章"}]}


def test_partial_delivery_does_not_invent_work_for_old_or_deleted_files():
    files = RoundWrittenFiles()
    files.observe(_created("old", "已有角色", reused_existing=True))
    files.observe(_created("folder", "目录", file_type="folder"))
    files.observe(_created("deleted", "作者不再需要"))
    files.observe(_frame("tool_call", {"tool_name": "delete_file", "tool_use_id": "delete-1", "arguments": {"id": "deleted"}}))
    files.observe(_frame("tool_result", {"tool_name": "delete_file", "tool_use_id": "delete-1", "status": "success", "data": True}))
    assert files.delivery(final_word_counts={"deleted": 5}) == {}


def test_partial_delivery_keeps_placeholder_when_delete_did_not_apply():
    files = RoundWrittenFiles()
    files.observe(_created("empty", "周念"))
    files.observe(_frame("tool_call", {"tool_name": "delete_file", "tool_use_id": "delete-1", "arguments": {"id": "empty"}}))
    files.observe(_frame("tool_result", {"tool_name": "delete_file", "tool_use_id": "delete-1", "status": "success", "data": False}))
    assert files.delivery(final_word_counts={}) == {"unwritten_files": [{"id": "empty", "title": "周念"}]}


def test_written_files_list_a_chapter_created_with_its_body_and_its_word_count():
    """create_file 直接带正文（U088 那种「第2章写进去了、回复只剩一句开场白」）：列出标题与编辑器口径的字数。"""
    assert _written(_created("ch-2", "第2章 入宅", "雨还在下。Rain 落在窗上。")) == [
        {"id": "ch-2", "title": "第2章 入宅", "words": 9},
    ]


def test_written_files_count_the_streamed_body_across_chunks():
    """空章节 + 流式正文：字数按整段正文算，被切在两帧之间的英文单词只算一次。"""
    assert _written(
        _created("ch-1", "第1章"),
        _frame("file_created", {"file_id": "ch-1", "file_type": "draft", "title": "第1章"}),
        _frame("file_content", {"file_id": "ch-1", "chunk": "雨还在下。Ra"}),
        _frame("file_content", {"file_id": "ch-1", "chunk": "in 落在窗上。"}),
        _frame("file_content_end", {"file_id": "ch-1"}),
    ) == [{"id": "ch-1", "title": "第1章", "words": 9}]


def test_written_files_skip_blank_placeholders_reads_failures_and_folders():
    assert _written(
        _created("ch-blank", "第3章"),
        _frame("file_content", {"file_id": "ch-blank", "chunk": "  \n"}),
        _frame("tool_result", {"tool_name": "query_files", "status": "success", "data": {"id": "x", "title": "大纲"}}),
        _frame("tool_result", {"tool_name": "create_file", "status": "error", "data": {"id": "e", "title": "失败"}}),
        _created("folder-1", "第一卷", "不会有正文", file_type="folder"),
        _edited("ch-old", "第0章", mutation_applied=False),
    ) == []


def test_written_files_list_edits_without_a_word_count():
    """改动了已有文件：列出标题，字数未知（本轮只改了其中几处）就不写。"""
    assert _written(
        _created("ch-1", "第1章", "开头一句。"),
        _edited("ch-1", "第1章"),
        _edited("ch-5", "第5章"),
    ) == [
        {"id": "ch-1", "title": "第1章"},
        {"id": "ch-5", "title": "第5章"},
    ]


def _reused_frame(file_id: str, title: str, existing_body: str) -> str:
    """生产里复用分支真正发给前端的 tool_result 帧：crud 结果经 mcp_tools 省略正文，再由事件构造器序列化。"""
    from agent.core.events import tool_result_event
    from agent.tools.mcp_tools import _elide_reused_file_content

    crud_result = {
        "id": file_id,
        "title": title,
        "file_type": "script",
        "content": existing_body,
        "reused_existing": True,
        "original_content_length": len(existing_body),
    }
    return tool_result_event(
        tool_name="create_file",
        status="success",
        data=_elide_reused_file_content(crud_result),
    ).to_sse()


def test_written_files_skip_a_reused_chapter_whose_existing_body_the_stream_may_not_replace():
    """幂等复用命中已写满的分集：中断时残稿不会覆盖原文（截断覆盖保护），不能说它「已保存」。

    帧用生产形状：复用分支的 content 已被省略成 ""，只剩 content_elided / original_content_length。
    """
    frame = _reused_frame("ep-2", "第2集", "原有的整集正文。" * 200)
    assert json.loads(frame.split("data: ", 1)[1])["data"]["content"] == ""
    assert _written(
        frame,
        _frame("file_content", {"file_id": "ep-2", "chunk": "新写的开头"}),
    ) == []


def test_written_files_skip_a_reused_chapter_known_only_by_its_original_length():
    """上游只给了 original_content_length（没有 content / content_elided）：同样按「原本非空」处理。"""
    assert _written(
        _created("ep-3", "第3集", reused_existing=True, original_content_length=4000),
        _frame("file_content", {"file_id": "ep-3", "chunk": "新写的开头"}),
    ) == []


def test_written_files_list_a_reused_empty_episode_once_the_stream_writes_it():
    """复用到的是空白分集（原本没有正文）：流式写下的正文会落库，照常列出并计字数。"""
    assert _written(
        _reused_frame("ep-4", "第4集", ""),
        _frame("file_content", {"file_id": "ep-4", "chunk": "新写的开头"}),
    ) == [{"id": "ep-4", "title": "第4集", "words": 5}]


def test_written_files_keep_the_order_of_first_write_once_each():
    assert _written(
        _created("ch-2", "第2章"),
        _created("ch-3", "第3章", "第三章正文。"),
        _frame("file_content", {"file_id": "ch-2", "chunk": "第二章正文。"}),
        _frame("file_content", {"file_id": "ch-2", "chunk": "又一句。"}),
    ) == [
        {"id": "ch-3", "title": "第3章", "words": 5},
        {"id": "ch-2", "title": "第2章", "words": 8},
    ]


def test_stop_outcome_lists_written_files_only_when_there_are_some():
    """旧消息、只有并行写章 / 删除这类拿不到文件的写入：没有 written_files，前端照旧显示。"""
    assert "written_files" not in build_stop_outcome(
        stop_kind="client_disconnected", charged=True, saved_output=True, written_files=[]
    )
    assert build_stop_outcome(
        stop_kind="client_disconnected",
        charged=True,
        saved_output=True,
        written_files=[{"id": "ch-2", "title": "第2章 入宅", "words": 9}],
    )["written_files"] == [{"id": "ch-2", "title": "第2章 入宅", "words": 9}]


def test_plain_done_frame_carries_no_receipt_keys():
    """Optional receipt fields are omitted, not sent as null (found in a live run, #267)."""
    from agent.core.events import done_event

    data = done_event(assistant_message_id="m1").data

    assert not {"stop_reason", "produced_output", "written_files", "unwritten_files", "no_file_changes"} & set(data)
