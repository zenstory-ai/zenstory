"""单循环 agent 按章号写正文（write_chapter）与 edit_file 的正文守卫：跨层合成回归。

真实的 loop engine、runner（SDK 事件映射与钩子）、tools_adapter、write_chapter / create_file / edit_file、
StreamAdapter（捕获、整章替换落库、diff 与撤销事件）、计费与回合回执，以及测试临时数据库；
只把模型换成按脚本输出的 SDK 事件流。正文都是合成文字，不调用付费模型。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import select

from agent.core.run_meter import AgentRunMeter
from agent.stream_adapter import StreamAdapter, StreamAdapterConfig
from agent.tools.tool_context import AgentRequestContext
from models import File, Project, User
from models.file_version import FileVersion
from services.features.file_version_service import FileVersionService
from tests.conftest import TestSessionLocal
from tests.test_agent.test_file_body_resume import Outcome, Reply, ScriptedModel

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]

@pytest.fixture
def workspace(db_session, monkeypatch):
    """工具与 StreamAdapter 自己开的 session 落在测试库上（同 test_file_body_resume 的 workspace）。"""
    import database
    from services.features.activation_event_service import activation_event_service

    def get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(database, "get_session", get_session)
    monkeypatch.setattr(database, "create_session", TestSessionLocal)
    monkeypatch.setattr(database, "is_postgres", False)
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_k: None)
    user = User(username="loop-write-chapter", email="loop-write-chapter@example.test", hashed_password="unused", email_verified=True)
    project = Project(name="合成作品", owner_id=user.id, project_type="novel")
    db_session.add_all([user, project])
    db_session.commit()
    return SimpleNamespace(user=user, project=project, session=db_session)


NEW_BODY = "\n".join(f"第{index}段合成新稿，只用于核对整章替换。" for index in range(1, 6))


def _ai_chapter(session, project, title: str, content: str, parent_id: str | None = None) -> File:
    file = File(project_id=project.id, title=title, content=content, file_type="draft", parent_id=parent_id)
    session.add(file)
    session.flush()
    FileVersionService().create_version(
        session, file.id, content, change_type="create", change_source="ai", commit=False, skip_quota=True
    )
    session.commit()
    session.refresh(file)
    return file


def _ai_body_file(
    session, project, title: str, content: str, *, file_type: str, parent_id: str | None = None
) -> File:
    file = File(
        project_id=project.id,
        title=title,
        content=content,
        file_type=file_type,
        parent_id=parent_id,
    )
    session.add(file)
    session.flush()
    FileVersionService().create_version(
        session, file.id, content, change_type="create", change_source="ai", commit=False, skip_quota=True
    )
    session.commit()
    session.refresh(file)
    return file


def _author_saves(session, file: File, content: str) -> None:
    FileVersionService().create_version(
        session, file.id, content, change_type="edit", change_source="user", skip_quota=True
    )
    current = session.get(File, file.id)
    from config.datetime_utils import advance_timestamp, utcnow

    current.content = content
    current.updated_at = advance_timestamp(current.updated_at, now=utcnow())
    session.add(current)
    session.commit()


def _content(workspace, file_id: str) -> str:
    workspace.session.expire_all()
    return workspace.session.get(File, file_id).content or ""


def _drafts(workspace) -> list[File]:
    workspace.session.expire_all()
    return list(
        workspace.session.exec(
            select(File).where(File.project_id == workspace.project.id, File.file_type == "draft")
        ).all()
    )


async def _run_loop(
    workspace, model: ScriptedModel, message: str = "把第三章换成新稿", **extra_state: Any
) -> Outcome:
    from agent.loop.engine import run_loop_agent_streaming

    meter = AgentRunMeter()
    req_ctx = AgentRequestContext(
        session=None,
        user_id=workspace.user.id,
        project_id=workspace.project.id,
        session_id="loop-write-chapter",
        create_session_func=TestSessionLocal,
        author_message=message,
        read_snapshots={},
    )
    state: dict[str, Any] = {
        **extra_state,
        "user_message": message,
        "router_message": message,
        "system_prompt": "synthetic loop system",
        "run_meter": meter,
        "project_id": workspace.project.id,
        "user_id": workspace.user.id,
        "session_id": "loop-write-chapter",
        "request_context": req_ctx,
    }
    adapter = StreamAdapter(
        StreamAdapterConfig(
            project_id=workspace.project.id,
            user_id=workspace.user.id,
            run_meter=meter,
            request_context=req_ctx,
        )
    )
    with (
        patch("agents.Runner.run_streamed", side_effect=model.run_streamed),
        patch("agent.openai_agents.runner.build_failover_chat_model", return_value="synthetic-model"),
        patch("agent.openai_agents.runner._preferred_model_name", AsyncMock(return_value="synthetic-model")),
        patch("agent.openai_agents.usage_hooks.schedule_llm_usage_record"),
    ):
        frames = [frame async for frame in adapter.process_workflow_events(run_loop_agent_streaming(state))]
    return Outcome(frames=frames, adapter=adapter, state=state)


def _results(outcome: Outcome, tool_name: str) -> list[dict[str, Any]]:
    return [frame.data for frame in outcome.of("tool_result") if frame.data.get("tool_name") == tool_name]


# ---------------------------------------------------------------------------
# 按章号定位：替换的只是这一章
# ---------------------------------------------------------------------------


async def test_write_chapter_replaces_exactly_that_chapter_with_diff_and_undo(workspace):
    one = _ai_chapter(workspace.session, workspace.project, "第1章 雨夜", "合成第一章原文。")
    two = _ai_chapter(workspace.session, workspace.project, "第2章 渡口", "合成第二章原文。")
    three = _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", "合成第三章原文。\n第二段原文。")
    model = ScriptedModel(runs=[[
        Reply(text="", tools=(("write_chapter", {"chapter": 3, "title": "第三章 新稿", "file_type": "draft"}),)),
        Reply(text=f"<file>\n{NEW_BODY}\n</file>\n第三章已经换成新稿。"),
    ]])

    outcome = await _run_loop(workspace, model)

    armed = model.tool_calls[0].output
    assert armed["status"] == "success"
    assert armed["data"]["action"] == "replaced" and armed["data"]["id"] == three.id
    assert armed["data"]["previous_length"] == len("合成第三章原文。\n第二段原文。")
    # 只有第3章换了，作者的第1、2章一个字没动，也没有多出文件。
    assert _content(workspace, three.id).rstrip("\n") == NEW_BODY
    assert _content(workspace, one.id) == "合成第一章原文。"
    assert _content(workspace, two.id) == "合成第二章原文。"
    assert len(_drafts(workspace)) == 3
    assert NEW_BODY not in outcome.chat_text

    # 编辑器直接展示已保存正文；事件保留前后稿供草稿冲突和恢复使用。
    assert outcome.file_text(three.id).rstrip("\n") == NEW_BODY
    [edit_end] = outcome.of("file_edit_end")
    assert edit_end.data["original_content"] == "合成第三章原文。\n第二段原文。"
    assert edit_end.data["new_content"] == _content(workspace, three.id)
    # 同一张卡补发真实结果：保存、字数、撤销锚点。
    final = _results(outcome, "write_chapter")[-1]["data"]
    assert final["body_saved"] is True and final["mutation_applied"] is True
    assert final["new_word_count"] > 0 and final["previous_word_count"] > 0
    assert "覆盖了已有的第3章" in final["result"]
    undo = final["undo"]
    versions = workspace.session.exec(select(FileVersion).where(FileVersion.file_id == three.id)).all()
    restored = FileVersionService()._get_contents_for_versions(
        workspace.session, {three.id: next(v for v in versions if v.version_number == undo["before_version_number"])}
    )[three.id]
    assert restored == "合成第三章原文。\n第二段原文。"
    assert any(v.change_source == "ai" and v.change_summary == "AI 整章替换" for v in versions)

    reason, refundable = outcome.billing()
    assert refundable is False
    delivery = outcome.delivery()
    assert [item["id"] for item in delivery["written_files"]] == [three.id]


BODY_ONE = "\n".join(f"第{index}段第一章合成新稿。" for index in range(1, 5))


async def test_second_write_chapter_in_the_same_reply_waits_for_the_armed_replacement(workspace):
    # 同一回复里先 write_chapter(1) 定为整章替换、再 write_chapter(2)：第 1 章还有旧正文，
    # 但它的替换正文还没写，第二次调用必须按「上一个文件正文未写」拒绝，不能把等待目标改到第 2 章。
    one = _ai_chapter(workspace.session, workspace.project, "第1章 雨夜", "合成第一章原文。")
    two = _ai_chapter(workspace.session, workspace.project, "第2章 渡口", "合成第二章原文。")
    model = ScriptedModel(runs=[[
        Reply(text="", tools=(
            ("write_chapter", {"chapter": 1, "title": "第1章", "file_type": "draft"}),
            ("write_chapter", {"chapter": 2, "title": "第2章", "file_type": "draft"}),
        )),
        Reply(text=f"<file>\n{BODY_ONE}\n</file>\n第一章换好了。"),
    ]])

    outcome = await _run_loop(workspace, model, "把第一章和第二章都重写")

    first, second = (c.output for c in model.tool_calls[:2])
    assert first["data"]["action"] == "replaced" and first["data"]["id"] == one.id
    assert second["status"] == "error" and second["error_type"] == "pending_empty_file_unwritten"
    assert _content(workspace, one.id).rstrip("\n") == BODY_ONE
    assert _content(workspace, two.id) == "合成第二章原文。"
    final = [r for r in _results(outcome, "write_chapter") if (r.get("data") or {}).get("id") == one.id][-1]["data"]
    assert final["body_saved"] is True


async def test_create_file_in_the_same_reply_waits_for_the_armed_replacement(workspace):
    one = _ai_chapter(workspace.session, workspace.project, "第1章 雨夜", "合成第一章原文。")
    model = ScriptedModel(runs=[[
        Reply(text="", tools=(
            ("write_chapter", {"chapter": 1, "title": "第1章", "file_type": "draft"}),
            ("create_file", {"title": "人物卡", "file_type": "character"}),
        )),
        Reply(text=f"<file>\n{BODY_ONE}\n</file>\n第一章换好了。"),
    ]])

    await _run_loop(workspace, model, "重写第一章并新建人物卡")

    created = model.tool_calls[1].output
    assert created["status"] == "error" and created["error_type"] == "pending_empty_file_unwritten"
    workspace.session.expire_all()
    titles = [f.title for f in workspace.session.exec(select(File).where(File.project_id == workspace.project.id)).all()]
    assert "人物卡" not in titles
    assert _content(workspace, one.id).rstrip("\n") == BODY_ONE


@pytest.mark.parametrize("original", ["", "合成第一章原文。"], ids=["empty", "written"])
async def test_body_written_before_write_chapter_replaces_that_chapter(workspace, original):
    # 同一回复里先写 <file> 正文、再 write_chapter 已有的一章：正文就是这一章的替换稿，
    # 走整章替换的落库路径（版本核对、diff、撤销），不作为对话放出，也不再要一次正文。
    one = _ai_chapter(workspace.session, workspace.project, "第1章 雨夜", original)
    model = ScriptedModel(runs=[[
        Reply(text=f"<file>\n{BODY_ONE}\n</file>\n", tools=(
            ("write_chapter", {"chapter": 1, "title": "第1章", "file_type": "draft"}),
        )),
        Reply(text="第一章写好了。"),
    ]])

    outcome = await _run_loop(workspace, model, "写第一章")

    armed = model.tool_calls[0].output["data"]
    assert armed["action"] == "replaced" and armed["body_in_reply"] is True
    assert BODY_ONE not in outcome.chat_text
    assert _content(workspace, one.id).rstrip("\n") == BODY_ONE
    final = _results(outcome, "write_chapter")[-1]["data"]
    assert final["body_saved"] is True and final["mutation_applied"] is True
    assert [frame.data["file_id"] for frame in outcome.of("file_edit_end")] == [one.id]
    assert model.errors("file_body_pending") == []


async def test_write_chapter_creates_a_missing_chapter_through_the_file_body_protocol(workspace):
    _ai_chapter(workspace.session, workspace.project, "第1章 雨夜", "合成第一章原文。")
    model = ScriptedModel(runs=[[
        Reply(text="", tools=(("write_chapter", {"chapter": 2, "title": "渡口", "file_type": "draft"}),)),
        Reply(text=f"<file>\n{NEW_BODY}\n</file>\n第二章写好了。"),
    ]])

    outcome = await _run_loop(workspace, model, "写第二章")

    created = model.tool_calls[0].output["data"]
    assert created["action"] == "created" and created["title"] == "第2章 渡口"
    assert _content(workspace, created["id"]).rstrip("\n") == NEW_BODY
    assert [frame.data["file_id"] for frame in outcome.of("file_created")] == [created["id"]]
    assert outcome.of("file_edit_end") == []
    assert sorted(f.title for f in _drafts(workspace)) == ["第1章 雨夜", "第2章 渡口"]


REWRITE_BODY = "\n".join(f"第{index}段合成重写稿，不应落库。" for index in range(1, 6))


def _ai_versions(workspace, file_id: str) -> int:
    workspace.session.expire_all()
    rows = workspace.session.exec(select(FileVersion).where(FileVersion.file_id == file_id)).all()
    return sum(1 for row in rows if row.change_source == "ai")


@pytest.mark.parametrize("existing", [False, True], ids=["created", "replaced"])
async def test_chapter_written_this_request_is_not_rewritten_by_write_chapter(workspace, existing):
    # 这一章的正文本请求里已经由 write_chapter 写完并保存：续跑（自查）里再 write_chapter 这一章时拒绝，
    # 正文与版本都不变，模型被指向 edit_file。新建与整章替换两条保存路径都记下这个事实。
    if existing:
        _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", "合成第三章原文。\n第二段原文。")
    call = ("write_chapter", {"chapter": 3, "title": "第3章 灯塔", "file_type": "draft"})
    model = ScriptedModel(runs=[
        [Reply(text="", tools=(call,)), Reply(text=f"<file>\n{NEW_BODY}\n</file>")],
        [Reply(text="", tools=(call,)), Reply(text=f"<file>\n{REWRITE_BODY}\n</file>\n第三章写好了。")],
    ])

    outcome = await _run_loop(workspace, model, "写第三章")

    first, second = (c.output for c in model.tool_calls[:2])
    file_id = first["data"]["id"]
    assert first["data"]["action"] == ("replaced" if existing else "created")
    assert second["status"] == "error" and second["error_type"] == "chapter_written_this_turn"
    assert second["id"] == file_id and "edit_file" in second["error"]
    assert _content(workspace, file_id).rstrip("\n") == NEW_BODY
    assert _ai_versions(workspace, file_id) == (1 + (1 if existing else 0))
    assert len(_drafts(workspace)) == 1
    assert outcome.of("error") == []


async def test_first_replacement_in_a_new_request_is_still_allowed(workspace):
    # 记录只在一个请求里有效：上一轮写好的章节，下一轮照常可以整章替换。
    call = ("write_chapter", {"chapter": 3, "title": "第3章 灯塔", "file_type": "draft"})
    first_turn = ScriptedModel(runs=[[Reply(text="", tools=(call,)), Reply(text=f"<file>\n{NEW_BODY}\n</file>\n写好了。")]])
    await _run_loop(workspace, first_turn, "写第三章")
    file_id = first_turn.tool_calls[0].output["data"]["id"]

    second_turn = ScriptedModel(runs=[[Reply(text="", tools=(call,)), Reply(text=f"<file>\n{REWRITE_BODY}\n</file>\n换好了。")]])
    await _run_loop(workspace, second_turn, "把第三章整章重写")

    assert second_turn.tool_calls[0].output["data"]["action"] == "replaced"
    assert _content(workspace, file_id).rstrip("\n") == REWRITE_BODY


def test_chapter_body_record_outlives_the_pending_entry_and_resets_per_request():
    from agent.tools.mcp_tools import _PendingEmptyFileState

    state = _PendingEmptyFileState()
    state.mark_chapter_body_saved("file-a")
    state.discard("file-a")
    state.discard()
    assert state.chapter_body_saved("file-a") is True
    assert state.chapter_body_saved("file-b") is False
    state.clear_all()
    assert state.chapter_body_saved("file-a") is False


async def test_replacement_is_refused_when_the_author_saved_the_chapter_meanwhile(workspace):
    three = _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", "合成第三章原文。")
    model = ScriptedModel(runs=[[
        Reply(text="", tools=(("write_chapter", {"chapter": 3, "title": "第3章", "file_type": "draft"}),)),
        Reply(
            text=f"<file>\n{NEW_BODY}\n</file>\n换好了。",
            before=lambda: _author_saves(workspace.session, three, "作者刚改过的第三章。"),
        ),
    ]])

    outcome = await _run_loop(workspace, model)

    assert _content(workspace, three.id) == "作者刚改过的第三章。"
    assert "整章替换没有保存" in outcome.chat_text
    final = _results(outcome, "write_chapter")[-1]["data"]
    assert final["body_saved"] is False and final["mutation_applied"] is False
    assert outcome.of("file_edit_end") == []
    assert "error" not in [frame.type.value for frame in outcome.frames]


async def test_refused_replacement_ends_the_round_before_the_reply_claims_it(workspace):
    three = _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", "合成第三章原文。")
    model = ScriptedModel(runs=[[
        Reply(text="", tools=(("write_chapter", {"chapter": 3, "title": "第3章", "file_type": "draft"}),)),
        Reply(
            text=f"<file>\n{NEW_BODY}\n</file>\n第三章已重写，现在一千字。",
            tools=(("write_chapter", {"chapter": 4, "title": "第4章", "file_type": "draft"}),),
            before=lambda: _author_saves(workspace.session, three, "作者刚改过的第三章。"),
        ),
        Reply(text=f"<file>\n{NEW_BODY}\n</file>\n第四章写好了。"),
    ]])

    outcome = await _run_loop(workspace, model)

    assert _content(workspace, three.id) == "作者刚改过的第三章。"
    assert "整章替换没有保存" in outcome.chat_text
    # 模型在同一次回复里接着说的「已重写」不发给作者，这一轮也不再往下写第4章。
    assert "已重写" not in outcome.chat_text
    # 模型可能已经跑在前面建了第4章的空文件：作者没看到它，随这一轮结束撤销。
    assert outcome.of("file_created") == [f for f in outcome.of("file_created") if f.data["file_id"] == three.id]
    # 建它的工具线程可能在这一轮结束之后才落库：它自己撤销，这里等它跑完。
    for _ in range(100):
        created_ahead = [f for f in _drafts(workspace) if f.id != three.id]
        if all(f.is_deleted for f in created_ahead):
            break
        await asyncio.sleep(0.02)
    assert all(f.is_deleted for f in created_ahead)
    assert outcome.of("done") and outcome.of("error") == []


async def test_a_second_write_chapter_for_an_armed_replacement_keeps_the_replace_path(workspace):
    three = _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", "合成第三章原文。\n第二段原文。")
    call = ("write_chapter", {"chapter": 3, "title": "第3章", "file_type": "draft"})
    model = ScriptedModel(runs=[[
        # 同一次回复里调用两次（并行或重试）。
        Reply(text="", tools=(call, call)),
        Reply(text=f"<file>\n{NEW_BODY}\n</file>\n第三章换好了。"),
    ]])

    outcome = await _run_loop(workspace, model)

    first, second = (c.output for c in model.tool_calls[:2])
    assert first["data"]["action"] == "replaced"
    assert second["status"] == "error" and second["error_type"] == "chapter_replace_pending"
    assert "action" not in second
    # 正文仍按整章替换提交：diff 审阅、撤销锚点、原长度都在，没有当成新建的空章节。
    assert _content(workspace, three.id).rstrip("\n") == NEW_BODY
    [edit_end] = outcome.of("file_edit_end")
    assert edit_end.data["original_content"] == "合成第三章原文。\n第二段原文。"
    final = _results(outcome, "write_chapter")[-1]["data"]
    assert final["body_saved"] is True and "undo" in final
    assert outcome.adapter._pending_file_write is None
    assert len(_drafts(workspace)) == 1


async def test_replacement_armed_without_a_body_is_retried_within_the_turn(workspace):
    three = _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", "合成第三章原文。\n第二段原文。")
    call = ("write_chapter", {"chapter": 3, "title": "第3章", "file_type": "draft"})
    model = ScriptedModel(runs=[
        # 定位之后的回复没有 <file>（模型写出了残缺的工具调用文字）。
        [Reply(text="", tools=(call,)), Reply(text="<<tool_call>")],
        # 纠偏轮：系统已为这一章恢复正文捕获，模型直接写 <file>。
        [Reply(text=f"<file>\n{NEW_BODY}\n</file>\n第三章换好了。")],
    ])

    outcome = await _run_loop(workspace, model)

    [first] = (c.output for c in model.tool_calls)
    assert first["data"]["action"] == "replaced"
    assert NEW_BODY not in outcome.chat_text
    assert _content(workspace, three.id).rstrip("\n") == NEW_BODY
    [edit_end] = outcome.of("file_edit_end")
    assert edit_end.data["original_content"] == "合成第三章原文。\n第二段原文。"
    assert _results(outcome, "write_chapter")[-1]["data"]["body_saved"] is True
    assert len(_drafts(workspace)) == 1


async def test_replacement_never_given_a_body_leaves_the_chapter_unchanged(workspace):
    three = _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", "合成第三章原文。")
    call = ("write_chapter", {"chapter": 3, "title": "第3章", "file_type": "draft"})
    model = ScriptedModel(runs=[
        [Reply(text="", tools=(call,)), Reply(text="我来改。")],
        [Reply(text="原文先不动了。")],
    ])

    outcome = await _run_loop(workspace, model)

    assert _content(workspace, three.id) == "合成第三章原文。"
    assert not outcome.of("file_edit_end")
    assert len(_drafts(workspace)) == 1
    assert outcome.state["request_context"].has_pending_empty_file() is False


async def test_unclosed_replacement_body_never_overwrites_the_chapter(workspace):
    three = _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", "合成第三章原文。")
    model = ScriptedModel(runs=[[
        Reply(text="", tools=(("write_chapter", {"chapter": 3, "title": "第3章", "file_type": "draft"}),)),
        Reply(text="<file>\n只写了一个开头"),
    ]])

    outcome = await _run_loop(workspace, model)

    assert _content(workspace, three.id) == "合成第三章原文。"
    assert "原文保持不变" in outcome.chat_text


async def test_replacement_left_armed_at_tool_call_exhaustion_reports_it_was_not_saved(workspace):
    """工具轮数耗尽时整章替换还在等正文：原文不动，作者看到固定的「未保存」说明，不被当成已有正文静默清掉。"""
    three = _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", "合成第三章原文。")
    model = ScriptedModel(runs=[[
        Reply(
            text="", tools=(("write_chapter", {"chapter": 3, "title": "第3章", "file_type": "draft"}),),
            exceeds_max_turns=True,
        ),
    ]])

    outcome = await _run_loop(workspace, model)

    assert _content(workspace, three.id) == "合成第三章原文。"
    assert outcome.of("iteration_exhausted")
    assert outcome.chat_text.count("本次改写未保存，原文保持不变。") == 1
    assert outcome.state["request_context"].has_pending_empty_file() is False


async def test_provider_error_mid_replacement_body_reports_it_was_not_saved():
    """整章替换的正文写到一半模型出错：截断补全被拒、原文不动；出错帧之前给作者固定的「未保存」说明。"""
    from agent.core.workflow_events import StreamEvent, StreamEventType

    adapter = StreamAdapter()
    card = {"id": "chapter-3", "action": "replaced", "chapter": 3, "awaiting_body": True}
    adapter.set_pending_file_write(
        "chapter-3", "draft", "第3章", original_content_length=40,
        baseline_updated_at="v1", replace_tool_use_id="call-1", replace_card=card,
    )
    replaced = []
    adapter._replace_file_body_sync = lambda *args: replaced.append(args) or {}

    async def workflow():
        yield StreamEvent(StreamEventType.TEXT, {"text": "<file>\n只写了一个开头"})
        yield StreamEvent(StreamEventType.ERROR, {"error": "provider failed", "error_type": "provider_error"})

    frames = [frame async for frame in adapter.process_workflow_events(workflow())]
    kinds = [frame.type.value for frame in frames]
    chat = "".join(str(frame.data.get("text") or "") for frame in frames if frame.type.value == "content")

    assert replaced == []
    assert chat.count("本次改写未保存，原文保持不变。") == 1
    assert kinds.index("error") > max(i for i, kind in enumerate(kinds) if kind == "content")
    assert adapter.get_replacement_receipts() == {}


def _run_input_text(model: ScriptedModel, index: int) -> str:
    return json.dumps(model.run_inputs[index], ensure_ascii=False, default=str)


def _latest_note(model: ScriptedModel, index: int) -> str:
    """第 index 次 run 输入里最新的一条（引擎这次追加的续跑说明）。"""
    return json.dumps(model.run_inputs[index][-1], ensure_ascii=False, default=str)


async def test_unclosed_replacement_body_is_retried_without_claiming_it_was_saved(workspace):
    three = _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", "合成第三章原文，一共二十个字左右的样子。")
    model = ScriptedModel(runs=[
        [
            Reply(text="", tools=(("write_chapter", {"chapter": 3, "title": "第3章", "file_type": "draft"}),)),
            Reply(text="<file>\n只写了一个开头"),
        ],
        [Reply(text=f"<file>\n{NEW_BODY}\n</file>\n第三章换好了。")],
    ])

    outcome = await _run_loop(workspace, model)

    # 没写完的那一次不保存，续跑走整章替换的重试（为这一章恢复正文捕获），不说「已保存」。
    retry_input = _run_input_text(model, 1)
    assert "重新打开正文写入" in retry_input
    assert "正文已保存" not in retry_input
    assert _content(workspace, three.id).rstrip("\n") == NEW_BODY
    assert _results(outcome, "write_chapter")[-1]["data"]["body_saved"] is True
    # 重试成功：作者不会先看到「未保存」再看到新稿。
    assert "未保存" not in outcome.chat_text


async def test_unclosed_replacement_body_without_retry_budget_reports_the_original_unchanged(workspace, monkeypatch):
    from agent.core import empty_files

    monkeypatch.setattr(empty_files, "MAX_FILE_CORRECTION_ATTEMPTS", 0)
    original = "合成第三章原文，一共二十个字左右的样子。"
    three = _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", original)
    model = ScriptedModel(runs=[
        [
            Reply(text="", tools=(("write_chapter", {"chapter": 3, "title": "第3章", "file_type": "draft"}),)),
            Reply(text="<file>\n只写了一个开头"),
        ],
        [Reply(text="第三章没有换成，原文没动。")],
    ])

    outcome = await _run_loop(workspace, model)

    assert _content(workspace, three.id) == original
    # 引擎放弃这次整章替换时给作者同一句固定说明。
    assert outcome.chat_text.count("本次改写未保存，原文保持不变。") == 1
    followup = _run_input_text(model, 1)
    assert "正文已保存" not in followup
    assert "原文保持不变" in followup
    # 不列这一章的原稿字数（那不是这次写出的正文）。
    from utils.text_metrics import count_words

    assert f"{count_words(original)} 字" not in followup


async def test_second_unclosed_replacement_of_the_same_chapter_is_never_called_saved(workspace, monkeypatch):
    from agent.core import empty_files

    monkeypatch.setattr(empty_files, "MAX_FILE_CORRECTION_ATTEMPTS", 0)
    original = "合成第三章原文，一共二十个字左右的样子。"
    three = _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", original)
    write_three = ("write_chapter", {"chapter": 3, "title": "第3章", "file_type": "draft"})
    model = ScriptedModel(runs=[
        [Reply(text="", tools=(write_three,)), Reply(text="<file>\n只写了一个开头")],
        [Reply(text="", tools=(write_three,)), Reply(text="<file>\n又只写了一个开头")],
        [Reply(text="第三章没有换成，原文没动。")],
    ])

    await _run_loop(workspace, model)

    assert _content(workspace, three.id) == original
    # 同一章第二次没写完：仍如实说没保存，不退回「正文已保存：（见下）」。
    for index in (1, 2):
        followup = _latest_note(model, index)
        assert "正文已保存" not in followup
        assert "原文保持不变" in followup


async def test_rearmed_replacement_that_fails_again_is_reported_next_to_a_saved_chapter(workspace, monkeypatch):
    from agent.core import empty_files

    monkeypatch.setattr(empty_files, "MAX_FILE_CORRECTION_ATTEMPTS", 0)
    original = "合成第三章原文，一共二十个字左右的样子。"
    _ai_chapter(workspace.session, workspace.project, "第2章 渡口", "合成第二章原文。")
    three = _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", original)
    write_two = ("write_chapter", {"chapter": 2, "title": "第2章", "file_type": "draft"})
    write_three = ("write_chapter", {"chapter": 3, "title": "第3章", "file_type": "draft"})
    model = ScriptedModel(runs=[
        [Reply(text="", tools=(write_three,)), Reply(text="<file>\n只写了一个开头")],
        [Reply(text="", tools=(write_two,)), Reply(text=f"<file>\n{NEW_BODY}\n</file>", tools=(write_three,)),
         Reply(text="<file>\n又只写了一个开头")],
        [Reply(text="第二章换好了，第三章没有换成。")],
    ])

    await _run_loop(workspace, model)

    assert _content(workspace, three.id) == original
    # 第二章保存了，但第三章重新定位后又没写完：最新的续跑说明仍要如实说第三章没保存。
    followup = _latest_note(model, 2)
    assert "原文保持不变" in followup
    assert "《第3章 灯塔》的整章替换没有保存" in followup


async def test_author_saved_chapter_can_be_rewritten_with_an_exact_recovery_anchor(workspace):
    three = _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", "合成第三章原文。")
    _author_saves(workspace.session, three, "作者自己写的第三章。")
    two = _ai_chapter(workspace.session, workspace.project, "第2章 渡口", "第二章不在本次改写范围。")
    model = ScriptedModel(runs=[[
        Reply(text="", tools=(("write_chapter", {"chapter": 3, "title": "第3章", "file_type": "draft"}),)),
        Reply(text=f"<file>\n{NEW_BODY}\n</file>\n第三章已重写，原稿可撤销恢复。"),
    ]])

    outcome = await _run_loop(workspace, model)

    assert model.tool_calls[0].output["status"] == "success"
    assert _content(workspace, three.id).rstrip("\n") == NEW_BODY
    assert workspace.session.get(File, three.id).title == "第3章 灯塔"
    assert _content(workspace, two.id) == "第二章不在本次改写范围。"
    final = _results(outcome, "write_chapter")[-1]["data"]
    assert final["body_saved"] is True
    undo = final["undo"]
    assert FileVersionService().get_content_at_version(
        workspace.session, three.id, undo["before_version_number"]
    ) == "作者自己写的第三章。"
    assert {row.id for row in _drafts(workspace)} == {two.id, three.id}


# ---------------------------------------------------------------------------
# 工具层：解析、歧义、空章节、create_file 不建正文
# ---------------------------------------------------------------------------


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def loop_tools(workspace):
    req_ctx = AgentRequestContext(
        session=workspace.session,
        user_id=workspace.user.id,
        project_id=workspace.project.id,
        session_id="loop-tools",
        current_agent="loop",
    )
    yield SimpleNamespace(**vars(workspace), req_ctx=req_ctx)
    req_ctx.clear()


async def test_create_file_refuses_body_files_on_the_loop_and_keeps_notes(loop_tools):
    from agent.tools.mcp_tools import LOOP_BODY_CREATE_ERROR_TYPE, create_file

    draft = _payload(await create_file({"title": "第1章", "file_type": "draft"}, request_context=loop_tools.req_ctx))
    script = _payload(await create_file({"title": "第1集", "file_type": "script"}, request_context=loop_tools.req_ctx))
    untyped = _payload(await create_file({"title": "第1章"}, request_context=loop_tools.req_ctx))
    outline = _payload(
        await create_file(
            {"title": "第1章 细纲", "file_type": "outline", "content": "合成"},
            request_context=loop_tools.req_ctx,
        )
    )

    for refused in (draft, script, untyped):
        assert refused["status"] == "error" and refused["error_type"] == LOOP_BODY_CREATE_ERROR_TYPE
        assert "write_chapter" in refused["error"]
    assert outline["status"] == "success"
    assert _drafts(loop_tools) == []


async def test_write_chapter_resolves_strictly_by_number_and_volume(loop_tools):
    from agent.tools.mcp_tools import WRITE_CHAPTER_AMBIGUOUS_ERROR_TYPE, write_chapter

    session, project = loop_tools.session, loop_tools.project
    vol_one = File(project_id=project.id, title="卷一", file_type="folder")
    vol_two = File(project_id=project.id, title="卷二", file_type="folder")
    session.add_all([vol_one, vol_two])
    session.commit()
    first_v1 = _ai_chapter(session, project, "第一章 起航", "合成。", parent_id=vol_one.id)
    _ai_chapter(session, project, "第1章 新卷", "合成。", parent_id=vol_two.id)
    _ai_chapter(session, project, "第2章 夜航", "合成。", parent_id=vol_one.id)
    # 同标题开头不是章号的资料文件不算这一章。
    session.add(File(project_id=project.id, title="第1章 修改意见", content="合成", file_type="document"))
    session.commit()

    ambiguous = _payload(
        await write_chapter({"chapter": 1, "title": "第1章", "file_type": "draft"}, request_context=loop_tools.req_ctx)
    )
    in_volume = _payload(
        await write_chapter(
            {"chapter": 1, "title": "第1章", "file_type": "draft", "parent_id": vol_one.id},
            request_context=loop_tools.req_ctx,
        )
    )
    loop_tools.req_ctx.clear_pending_empty_file()
    mismatch = _payload(
        await write_chapter({"chapter": 4, "title": "第三章 错位", "file_type": "draft"}, request_context=loop_tools.req_ctx)
    )
    not_body = _payload(
        await write_chapter({"chapter": 1, "title": "大纲", "file_type": "outline"}, request_context=loop_tools.req_ctx)
    )

    assert ambiguous["status"] == "error" and ambiguous["error_type"] == WRITE_CHAPTER_AMBIGUOUS_ERROR_TYPE
    assert {c["id"] for c in ambiguous["candidates"]} >= {first_v1.id}
    assert in_volume["status"] == "success" and in_volume["data"]["id"] == first_v1.id
    assert in_volume["data"]["action"] == "replaced"
    assert mismatch["status"] == "error" and not_body["status"] == "error"
    # 定位不改数据库。
    assert _content(loop_tools, first_v1.id) == "合成。"


async def test_write_chapter_resolves_parent_id_the_way_create_file_does(loop_tools):
    from agent.tools.mcp_tools import write_chapter

    session, project = loop_tools.session, loop_tools.project
    body = File(project_id=project.id, title="正文", file_type="folder")
    session.add(body)
    session.commit()
    two = _ai_chapter(session, project, "第2章 渡口", "合成第二章。", parent_id=body.id)
    three = _ai_chapter(session, project, "第3章 灯塔", "合成第三章。", parent_id=body.id)

    # 模型把上一章的文件 id 当成 parent_id：create_file 会上挂到正文目录，查找也一样。
    armed = _payload(
        await write_chapter(
            {"chapter": 3, "title": "第3章", "file_type": "draft", "parent_id": two.id},
            request_context=loop_tools.req_ctx,
        )
    )

    assert armed["status"] == "success" and armed["data"]["action"] == "replaced"
    assert armed["data"]["id"] == three.id
    assert len(_drafts(loop_tools)) == 2


async def test_write_chapter_needs_a_folder_when_volumes_restart_numbering(loop_tools):
    from agent.tools.mcp_tools import WRITE_CHAPTER_AMBIGUOUS_ERROR_TYPE, write_chapter

    session, project = loop_tools.session, loop_tools.project
    vol_one = File(project_id=project.id, title="卷一", file_type="folder")
    vol_two = File(project_id=project.id, title="卷二", file_type="folder")
    session.add_all([vol_one, vol_two])
    session.commit()
    first = _ai_chapter(session, project, "第1章 起航", "合成卷一第一章。", parent_id=vol_one.id)
    _ai_chapter(session, project, "第2章 夜航", "合成卷一第二章。", parent_id=vol_one.id)
    _ai_chapter(session, project, "第1章 新卷", "合成卷二第一章。", parent_id=vol_two.id)
    _ai_chapter(session, project, "第3章 远方", "合成卷二第三章。", parent_id=vol_two.id)

    # 卷一有第2章、卷二没有：没给 parent_id 时不把卷一的第2章当成要替换的那一章。
    unsure = _payload(
        await write_chapter({"chapter": 2, "title": "第2章", "file_type": "draft"}, request_context=loop_tools.req_ctx)
    )
    in_volume_two = _payload(
        await write_chapter(
            {"chapter": 2, "title": "第2章 归途", "file_type": "draft", "parent_id": vol_two.id},
            request_context=loop_tools.req_ctx,
        )
    )

    assert unsure["status"] == "error" and unsure["error_type"] == WRITE_CHAPTER_AMBIGUOUS_ERROR_TYPE
    assert "parent_id" in unsure["error"]
    assert in_volume_two["status"] == "success" and in_volume_two["data"]["action"] == "created"
    assert in_volume_two["data"]["parent_id"] == vol_two.id
    assert _content(loop_tools, first.id) == "合成卷一第一章。"


@pytest.fixture
def screenplay_tools(workspace):
    project = workspace.project
    project.project_type = "screenplay"
    script_folder = File(id=f"{project.id}-script-folder", project_id=project.id, title="剧本", file_type="folder")
    notes_folder = File(id=f"{project.id}-material-folder", project_id=project.id, title="素材", file_type="folder")
    workspace.session.add_all([project, script_folder, notes_folder])
    workspace.session.commit()
    episode = File(
        project_id=project.id, title="第3集 雨夜", content="合成第三集。", file_type="script", parent_id=script_folder.id
    )
    workspace.session.add(episode)
    workspace.session.commit()
    FileVersionService().create_version(
        workspace.session, episode.id, "合成第三集。", change_type="create", change_source="ai", skip_quota=True
    )
    req_ctx = AgentRequestContext(
        session=workspace.session,
        user_id=workspace.user.id,
        project_id=project.id,
        session_id="loop-screenplay",
        current_agent="loop",
    )
    yield SimpleNamespace(
        **vars(workspace),
        script_folder=script_folder,
        notes_folder=notes_folder,
        episode=episode,
        req_ctx=req_ctx,
    )
    req_ctx.clear()


def _scripts(workspace) -> list[File]:
    workspace.session.expire_all()
    return list(
        workspace.session.exec(
            select(File).where(File.project_id == workspace.project.id, File.file_type == "script")
        ).all()
    )


async def test_screenplay_draft_folder_resolves_to_the_script_folder(screenplay_tools):
    from agent.tools.mcp_tools import write_chapter

    project = screenplay_tools.project
    armed = _payload(
        await write_chapter(
            {"chapter": 3, "title": "第3集", "file_type": "script", "parent_id": f"{project.id}-draft-folder"},
            request_context=screenplay_tools.req_ctx,
        )
    )

    assert armed["status"] == "success" and armed["data"]["action"] == "replaced"
    assert armed["data"]["id"] == screenplay_tools.episode.id
    assert len(_scripts(screenplay_tools)) == 1


async def test_loop_create_file_cannot_make_an_episode_out_of_a_document(screenplay_tools):
    from agent.tools.mcp_tools import LOOP_BODY_CREATE_ERROR_TYPE, create_file

    folder = screenplay_tools.script_folder
    old_version = _payload(
        await create_file(
            {"title": "第3集 雨夜（旧版）", "file_type": "document", "parent_id": folder.id},
            request_context=screenplay_tools.req_ctx,
        )
    )
    same_title = _payload(
        await create_file(
            {"title": "第3集 雨夜", "file_type": "document", "parent_id": folder.id},
            request_context=screenplay_tools.req_ctx,
        )
    )
    kept_in_notes = _payload(
        await create_file(
            {
                "title": "雨夜 旧版",
                "file_type": "document",
                "parent_id": screenplay_tools.notes_folder.id,
                "content": "合成旧版。",
            },
            request_context=screenplay_tools.req_ctx,
        )
    )

    for refused in (old_version, same_title):
        assert refused["status"] == "error" and refused["error_type"] == LOOP_BODY_CREATE_ERROR_TYPE
    assert kept_in_notes["status"] == "success" and kept_in_notes["data"]["file_type"] == "document"
    assert [f.id for f in _scripts(screenplay_tools)] == [screenplay_tools.episode.id]
    assert _content(screenplay_tools, screenplay_tools.episode.id) == "合成第三集。"
    assert screenplay_tools.req_ctx.has_pending_empty_file() is False


async def test_created_chapter_without_a_body_is_refunded_and_removable(loop_tools):
    from agent.core.events import tool_result_event
    from agent.core.stream_billing import StreamBillingTracker
    from agent.tools.mcp_tools import write_chapter

    created = _payload(
        await write_chapter({"chapter": 5, "title": "雨夜", "file_type": "draft"}, request_context=loop_tools.req_ctx)
    )
    assert created["data"]["action"] == "created"

    for stop in ("user_stopped", "error"):
        tracker = StreamBillingTracker()
        tracker.observe(
            tool_result_event(tool_name="write_chapter", status="success", data=created["data"], tool_use_id="c1").to_sse()
        )
        assert tracker.write_succeeded is False and tracker.produced_output is False
        if stop == "user_stopped":
            assert tracker.decide(
                client_disconnected=False, unexpected_exception=False, user_stopped=True, provider_attempted=True
            ) == ("user_stopped", False)
            assert tracker.decide(
                client_disconnected=False, unexpected_exception=False, user_stopped=True, provider_attempted=False
            ) == ("user_stopped_before_model", True)
        else:
            assert tracker.decide(
                client_disconnected=False, unexpected_exception=True, provider_attempted=True, terminal_model_failure=True
            ) == ("upstream_model_error", True)
            assert tracker.decide(
                client_disconnected=False, unexpected_exception=True, provider_attempted=True, terminal_model_failure=False
            ) == ("non_model_error", False)
        assert [entry["id"] for entry in tracker.removable_placeholders()] == [created["data"]["id"]]


async def test_write_chapter_empty_builds_missing_chapters_and_leaves_existing_ones(loop_tools):
    from agent.tools.mcp_tools import write_chapter

    existing = _ai_chapter(loop_tools.session, loop_tools.project, "第1章 雨夜", "合成第一章原文。")
    results = [
        _payload(
            await write_chapter(
                {"chapter": n, "title": f"第{n}章", "file_type": "draft", "empty": True},
                request_context=loop_tools.req_ctx,
            )
        )
        for n in (1, 2, 3)
    ]

    assert [r["status"] for r in results] == ["success"] * 3
    assert results[0]["data"]["action"] == "exists" and results[0]["data"]["id"] == existing.id
    assert [r["data"]["action"] for r in results[1:]] == ["created", "created"]
    assert loop_tools.req_ctx.has_pending_empty_file() is False
    assert _content(loop_tools, existing.id) == "合成第一章原文。"
    assert sorted(f.title for f in _drafts(loop_tools)) == ["第1章 雨夜", "第2章", "第3章"]


@pytest.mark.parametrize(
    ("requested_type", "wrong_type", "wrong_title", "requested_title"),
    [
        ("draft", "script", "第1章 分镜", "第1章 新稿"),
        ("script", "draft", "第1集 小说", "第1集 新稿"),
    ],
)
@pytest.mark.parametrize("empty", [True, False], ids=["existence-check", "replacement-arming"])
async def test_write_chapter_ignores_same_number_from_the_other_body_type(
    loop_tools, requested_type, wrong_type, wrong_title, requested_title, empty
):
    from agent.tools.mcp_tools import write_chapter

    wrong = _ai_body_file(
        loop_tools.session,
        loop_tools.project,
        wrong_title,
        "另一种正文类型的内容。",
        file_type=wrong_type,
    )

    created = _payload(
        await write_chapter(
            {"chapter": 1, "title": requested_title, "file_type": requested_type, "empty": empty},
            request_context=loop_tools.req_ctx,
        )
    )

    assert created["status"] == "success" and created["data"]["action"] == "created"
    assert created["data"]["id"] != wrong.id
    assert loop_tools.req_ctx.pending_chapter_replacement(wrong.id) is None
    assert loop_tools.session.get(File, created["data"]["id"]).file_type == requested_type
    assert _content(loop_tools, wrong.id) == "另一种正文类型的内容。"


@pytest.mark.parametrize(
    ("requested_type", "wrong_type", "correct_title", "wrong_title"),
    [
        ("draft", "script", "第1章 正文", "第2章 分镜"),
        ("script", "draft", "第1集 分镜", "第2集 小说"),
    ],
)
async def test_other_body_type_folders_do_not_make_replacement_ambiguous(
    loop_tools, requested_type, wrong_type, correct_title, wrong_title
):
    from agent.tools.mcp_tools import write_chapter

    session, project = loop_tools.session, loop_tools.project
    correct_folder = File(project_id=project.id, title="目标正文", file_type="folder")
    wrong_folder = File(project_id=project.id, title="另一类型正文", file_type="folder")
    session.add_all([correct_folder, wrong_folder])
    session.commit()
    correct = _ai_body_file(
        session,
        project,
        correct_title,
        "目标正文原稿。",
        file_type=requested_type,
        parent_id=correct_folder.id,
    )
    _ai_body_file(
        session,
        project,
        wrong_title,
        "另一类型正文。",
        file_type=wrong_type,
        parent_id=wrong_folder.id,
    )

    armed = _payload(
        await write_chapter(
            {"chapter": 1, "title": correct_title, "file_type": requested_type},
            request_context=loop_tools.req_ctx,
        )
    )

    assert armed["status"] == "success" and armed["data"]["action"] == "replaced"
    assert armed["data"]["id"] == correct.id


async def test_repeating_write_chapter_for_the_pending_chapter_hands_back_the_same_file(loop_tools):
    from agent.tools.mcp_tools import write_chapter

    first = _payload(
        await write_chapter({"chapter": 5, "title": "第5章", "file_type": "draft"}, request_context=loop_tools.req_ctx)
    )
    again = _payload(
        await write_chapter({"chapter": 5, "title": "第5章", "file_type": "draft"}, request_context=loop_tools.req_ctx)
    )

    assert first["data"]["action"] == "created"
    assert again["status"] == "success" and again["data"]["id"] == first["data"]["id"]
    assert again["data"]["awaiting_body"] is True
    assert len(_drafts(loop_tools)) == 1


# ---------------------------------------------------------------------------
# edit_file 的正文守卫
# ---------------------------------------------------------------------------

_LONG = "这是一段足够长的合成正文，用来核对逐字重复段落的检查，" * 4


async def _edit(req_ctx: AgentRequestContext, file_id: str, edits: list[dict[str, Any]]) -> dict:
    from agent.tools.mcp_tools import edit_file

    req_ctx.record_read_snapshot(file_id, _content_of(file_id))
    return _payload(await edit_file({"id": file_id, "edits": edits}, request_context=req_ctx))


def _content_of(file_id: str) -> str:
    with TestSessionLocal() as session:
        return session.get(File, file_id).content or ""


async def test_loop_edit_file_refuses_whole_body_replacement_and_new_duplicates(loop_tools):
    original = f"第一段原文。\n{_LONG}\n第三段原文。"
    chapter = _ai_chapter(loop_tools.session, loop_tools.project, "第1章", original)

    whole = await _edit(loop_tools.req_ctx, chapter.id, [{"op": "replace", "old": original, "new": "全新的一章。"}])
    # 一处 replace 盖住了每一段（old 只差首尾几个字）：同样是整章替换。
    nearly_whole = await _edit(
        loop_tools.req_ctx, chapter.id, [{"op": "replace", "old": original[1:-1], "new": "全新的一章。"}]
    )
    duplicate = await _edit(
        loop_tools.req_ctx, chapter.id, [{"op": "insert_after", "anchor": "第三段原文。", "text": f"\n{_LONG}"}]
    )
    local = await _edit(
        loop_tools.req_ctx, chapter.id, [{"op": "replace", "old": "第三段原文。", "new": "第三段改过了。"}]
    )

    assert whole["status"] == "error" and whole["error_type"] == "whole_body_replace"
    assert "write_chapter" in whole["error"]
    assert nearly_whole["status"] == "error" and nearly_whole["error_type"] == "whole_body_replace"
    assert duplicate["status"] == "error" and duplicate["error_type"] == "duplicated_paragraph"
    assert local["status"] == "success"
    assert _content_of(chapter.id) == f"第一段原文。\n{_LONG}\n第三段改过了。"


async def test_per_paragraph_polish_of_an_author_saved_chapter_is_an_edit_not_a_replacement(loop_tools):
    original = "他走进雨里。\n她没有回头。\n灯灭了。"
    chapter = _ai_chapter(loop_tools.session, loop_tools.project, "第1章 雨夜", "合成。")
    _author_saves(loop_tools.session, chapter, original)

    polished = await _edit(loop_tools.req_ctx, chapter.id, [
        {"op": "replace", "old": "他走进雨里。", "new": "他慢慢走进雨里。"},
        {"op": "replace", "old": "她没有回头。", "new": "她始终没有回头。"},
        {"op": "replace", "old": "灯灭了。", "new": "灯忽然灭了。"},
    ])

    assert polished["status"] == "success"
    assert _content_of(chapter.id) == "他慢慢走进雨里。\n她始终没有回头。\n灯忽然灭了。"


async def test_existing_duplicates_and_notes_files_are_not_blocked(loop_tools):
    from agent.tools.mcp_tools import create_file

    already = _ai_chapter(loop_tools.session, loop_tools.project, "第2章", f"{_LONG}\n中间。\n{_LONG}")
    kept = await _edit(loop_tools.req_ctx, already.id, [{"op": "replace", "old": "中间。", "new": "中间改过。"}])
    outline = _payload(
        await create_file(
            {"title": "大纲", "file_type": "outline", "content": "旧大纲。"},
            request_context=loop_tools.req_ctx,
        )
    )
    rewritten_outline = await _edit(
        loop_tools.req_ctx, outline["data"]["id"], [{"op": "replace", "old": "旧大纲。", "new": "新大纲。"}]
    )

    assert kept["status"] == "success"
    assert rewritten_outline["status"] == "success"


# ---------------------------------------------------------------------------
# 历史回放与撤销卡
# ---------------------------------------------------------------------------


def _replacement_history_items(*, file_id: str, body_saved: bool) -> list[dict[str, Any]]:
    from agent.loop.history import _row_items

    calls = [
        {
            "name": "write_chapter",
            "arguments": {"chapter": 3, "title": "第3章", "file_type": "draft"},
            "status": "success",
            "result": {
                "id": file_id,
                "title": "第3章 灯塔",
                "chapter": 3,
                "action": "replaced",
                "awaiting_body": not body_saved,
                "next_step": "下一次回复写正文",
                "body_saved": body_saved,
            },
        },
        {
            "name": "update_project",
            "arguments": {"notes": "不应执行"},
            "status": "error",
            "error": "file_body_pending",
        },
    ]
    row = SimpleNamespace(
        id="stopped-row",
        role="assistant",
        content="\n\n",
        tool_calls=json.dumps(calls, ensure_ascii=False),
        message_metadata=json.dumps(
            {
                "engine": "loop",
                "stop_reason": "user_stopped",
                "display_events": [
                    {"type": "thinking_content", "content": "合成思考"},
                    {"type": "tool_call", "tool_call_index": 0},
                    {"type": "tool_call", "tool_call_index": 1},
                    {"type": "content", "content": "\n\n"},
                ],
            },
            ensure_ascii=False,
        ),
    )
    loader = SimpleNamespace(_build_status_cards_history_content=lambda _cards: "")
    return _row_items(loader, row)


def test_history_replays_an_unsaved_replacement_as_not_saved():
    from agent.loop.history import _tool_output_text

    not_saved_items = _replacement_history_items(file_id="chapter-3", body_saved=False)
    saved_items = _replacement_history_items(file_id="chapter-3", body_saved=True)
    not_saved = json.loads(not_saved_items[1]["output"])
    done = json.loads(saved_items[1]["output"])
    created = {"id": "outline-1", "action": "created", "awaiting_body": True, "next_step": "写正文"}
    created_before = dict(created)
    historical_create = json.loads(
        _tool_output_text({"name": "create_file", "status": "success", "result": created})
    )

    for items in (not_saved_items, saved_items):
        calls = [item for item in items if item.get("type") == "function_call"]
        outputs = [item for item in items if item.get("type") == "function_call_output"]
        assert len(calls) == len(outputs) == 2
        assert [item["call_id"] for item in calls] == [item["call_id"] for item in outputs]
        assert "合成思考" not in json.dumps(items, ensure_ascii=False)

    assert not_saved["data"]["body_saved"] is False
    assert not_saved["data"]["write_target_active"] is False
    assert "awaiting_body" not in not_saved["data"] and "next_step" not in not_saved["data"]
    assert "没有保存" in not_saved["data"]["result"]
    assert done["data"]["body_saved"] is True
    assert done["data"]["write_target_active"] is False
    assert historical_create["data"]["body_saved"] is True
    assert historical_create["data"]["write_target_active"] is False
    assert "awaiting_body" not in historical_create["data"] and "next_step" not in historical_create["data"]
    assert created == created_before


async def test_stopped_replacement_history_does_not_arm_a_read_only_next_turn(workspace):
    original = "合成第三章原文。\n第二段原文。"
    three = _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", original)
    history_items = _replacement_history_items(file_id=three.id, body_saved=False)
    model = ScriptedModel(runs=[[Reply(text="第三章仍是旧稿，这一轮没有修改。")]])

    outcome = await _run_loop(
        workspace,
        model,
        "先别写，只告诉我上一轮保存了没有",
        history_items=history_items,
    )

    # 脚本模型不证明语义选择；这里只锁定历史事实不会自动恢复写入目标。
    assert model.run_inputs[0][:-1] == history_items
    assert model.tool_calls == []
    assert outcome.of("file_content") == [] and outcome.of("file_edit_end") == []
    assert _content(workspace, three.id) == original
    assert _ai_versions(workspace, three.id) == 1


async def test_next_turn_reacquires_target_after_an_unsaved_replacement(workspace):
    original = "合成第三章原文。\n第二段原文。"
    two = _ai_chapter(workspace.session, workspace.project, "第2章 渡口", "合成第二章原文。")
    three = _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", original)
    history_items = _replacement_history_items(file_id=three.id, body_saved=False)
    call = ("write_chapter", {"chapter": 3, "title": "第3章", "file_type": "draft"})
    model = ScriptedModel(runs=[[
        Reply(text="", tools=(call,)),
        Reply(text=f"<file>\n{NEW_BODY}\n</file>\n第三章已重新写完。"),
    ]])

    outcome = await _run_loop(
        workspace,
        model,
        "继续，把第三章整章写完",
        history_items=history_items,
    )

    fresh = model.tool_calls[0].output
    assert fresh["status"] == "success"
    assert fresh["data"]["action"] == "replaced" and fresh["data"]["id"] == three.id
    assert _content(workspace, three.id).rstrip("\n") == NEW_BODY
    assert _content(workspace, two.id) == "合成第二章原文。"
    assert NEW_BODY not in outcome.chat_text
    versions = workspace.session.exec(select(FileVersion).where(FileVersion.file_id == three.id)).all()
    before = next(version for version in versions if version.version_number == 1)
    assert FileVersionService()._get_contents_for_versions(workspace.session, {three.id: before})[three.id] == original


AI_FIRST = "第一段 AI 初稿，合成文字。\n\n第二段 AI 初稿，合成文字。"
AI_SECOND = "第一段 AI 第二稿，合成文字。\n\n第二段 AI 第二稿，合成文字。"
AUTHOR_TEXT = "第一段作者自己的写法，合成文字。\n\n第二段作者自己的写法，合成文字。"


def _save(session, file: File, content: str, *, change_type: str, change_source: str, summary: str | None = None):
    FileVersionService().create_version(
        session, file.id, content, change_type=change_type, change_source=change_source,
        change_summary=summary, skip_quota=True,
    )
    current = session.get(File, file.id)
    current.content = content
    session.add(current)
    session.commit()


def _number_of(workspace, file_id: str, content: str) -> int:
    workspace.session.expire_all()
    versions = workspace.session.exec(select(FileVersion).where(FileVersion.file_id == file_id)).all()
    service = FileVersionService()
    return max(
        v.version_number for v in versions
        if service._get_contents_for_versions(workspace.session, {file_id: v})[file_id] == content
    )


async def _write_chapter_one(workspace) -> dict[str, Any]:
    model = ScriptedModel(runs=[[
        Reply(text="", tools=(("write_chapter", {"chapter": 1, "title": "第1章", "file_type": "draft"}),)),
        Reply(text=f"<file>\n{NEW_BODY}\n</file>\n第一章换好了。"),
    ]])
    await _run_loop(workspace, model, "把第一章整章重写")
    return model.tool_calls[0].output


@pytest.mark.parametrize("back_to_ai", ["undo", "review_reject_all"])
async def test_going_back_to_byte_identical_ai_text_stays_replaceable(workspace, back_to_ai):
    # AI 写的初稿 → AI 整章替换 → 作者撤销 / 审阅里全部拒绝：正文与 AI 初稿一字不差，仍是 AI 的正文。
    one = _ai_chapter(workspace.session, workspace.project, "第1章 雨夜", AI_FIRST)
    _save(workspace.session, one, AI_SECOND, change_type="ai_edit", change_source="ai", summary="AI 整章替换")
    if back_to_ai == "undo":
        FileVersionService().rollback_to_version(
            workspace.session, one.id, _number_of(workspace, one.id, AI_FIRST), workspace.user.id
        )
    else:
        _save(workspace.session, one, AI_FIRST, change_type="ai_edit", change_source="user", summary="AI edit (reviewed)")
    assert _content(workspace, one.id) == AI_FIRST

    armed = await _write_chapter_one(workspace)

    assert armed["status"] == "success" and armed["data"]["action"] == "replaced"
    assert _content(workspace, one.id).rstrip("\n") == NEW_BODY


async def test_second_undo_back_to_ai_text_after_an_earlier_restore_stays_replaceable(workspace):
    # AI 初稿 → 作者改写 → 作者恢复到 AI 初稿 → AI 整章替换 → 作者撤销：又回到 AI 初稿，仍是 AI 的正文。
    one = _ai_chapter(workspace.session, workspace.project, "第1章 雨夜", AI_FIRST)
    first_ai = _number_of(workspace, one.id, AI_FIRST)
    _save(workspace.session, one, AUTHOR_TEXT, change_type="edit", change_source="user")
    FileVersionService().rollback_to_version(workspace.session, one.id, first_ai, workspace.user.id)
    _save(workspace.session, one, AI_SECOND, change_type="ai_edit", change_source="ai", summary="AI 整章替换")
    FileVersionService().rollback_to_version(
        workspace.session, one.id, _number_of(workspace, one.id, AI_FIRST), workspace.user.id
    )
    assert _content(workspace, one.id) == AI_FIRST

    armed = await _write_chapter_one(workspace)

    assert armed["status"] == "success" and armed["data"]["action"] == "replaced"
    assert _content(workspace, one.id).rstrip("\n") == NEW_BODY


async def test_restored_author_text_can_be_rewritten_again(workspace):
    # 作者正文 → AI 整章替换 → 作者恢复作者正文 → AI 再替换 → 作者撤销：比对越过那次恢复，仍找到作者版本。
    one = _ai_chapter(workspace.session, workspace.project, "第1章 雨夜", AI_FIRST)
    _save(workspace.session, one, AUTHOR_TEXT, change_type="edit", change_source="user")
    author_version = _number_of(workspace, one.id, AUTHOR_TEXT)
    _save(workspace.session, one, AI_SECOND, change_type="ai_edit", change_source="ai", summary="AI 整章替换")
    FileVersionService().rollback_to_version(workspace.session, one.id, author_version, workspace.user.id)
    _save(workspace.session, one, AI_SECOND, change_type="ai_edit", change_source="ai", summary="AI 整章替换")
    FileVersionService().rollback_to_version(
        workspace.session, one.id, _number_of(workspace, one.id, AUTHOR_TEXT), workspace.user.id
    )

    armed = await _write_chapter_one(workspace)

    assert armed["status"] == "success"
    assert _content(workspace, one.id).rstrip("\n") == NEW_BODY
    assert _number_of(workspace, one.id, AUTHOR_TEXT) > 0


@pytest.mark.parametrize("back_to_author", ["restore_author_version", "mixed_review_save"])
async def test_author_text_reached_by_restore_or_review_remains_rewriteable(workspace, back_to_author):
    one = _ai_chapter(workspace.session, workspace.project, "第1章 雨夜", AI_FIRST)
    if back_to_author == "restore_author_version":
        _save(workspace.session, one, AUTHOR_TEXT, change_type="edit", change_source="user")
        _save(workspace.session, one, AI_SECOND, change_type="ai_edit", change_source="ai", summary="AI 整章替换")
        FileVersionService().rollback_to_version(
            workspace.session, one.id, _number_of(workspace, one.id, AUTHOR_TEXT), workspace.user.id
        )
        expected = AUTHOR_TEXT
    else:
        _save(workspace.session, one, AI_SECOND, change_type="ai_edit", change_source="ai", summary="AI 整章替换")
        expected = "第一段 AI 初稿，合成文字。\n\n第二段 AI 第二稿，合成文字。"
        _save(workspace.session, one, expected, change_type="ai_edit", change_source="user", summary="AI edit (reviewed)")

    armed = await _write_chapter_one(workspace)

    assert armed["status"] == "success"
    assert _content(workspace, one.id).rstrip("\n") == NEW_BODY
    assert _number_of(workspace, one.id, expected) > 0


async def test_undo_marks_a_write_chapter_card(workspace):
    from datetime import datetime

    from models import ChatMessage, ChatSession
    from services.chat_tool_cards import record_edit_card_undone

    chat = ChatSession(user_id=workspace.user.id, project_id=workspace.project.id, title="t", is_active=True)
    workspace.session.add(chat)
    workspace.session.commit()
    after = "2026-11-16T00:00:00+00:00"
    card = {
        "id": "call-1",
        "name": "write_chapter",
        "status": "success",
        "result": {"id": "file-1", "action": "replaced", "undo": {"before_version_number": 2, "expected_after_updated_at": after}},
    }
    message = ChatMessage(session_id=chat.id, role="assistant", content="换好了。", tool_calls=json.dumps([card]))
    workspace.session.add(message)
    workspace.session.commit()

    marked = record_edit_card_undone(
        workspace.session,
        user_id=workspace.user.id,
        project_id=workspace.project.id,
        file_id="file-1",
        message_id=message.id,
        tool_use_id="call-1",
        before_version_number=2,
        expected_after_updated_at=datetime.fromisoformat(after),
    )
    assert marked is True


# ---------------------------------------------------------------------------
# 上一轮在思考阶段撞上输出上限：本轮第一次 run 带上「简短思考、先交付第一部分」
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("previous_truncations", [0, 1])
async def test_turn_after_an_output_truncation_asks_for_short_thinking_once(workspace, previous_truncations):
    from agent.core.output_truncation import OUTPUT_TRUNCATED_RESUME

    model = ScriptedModel(runs=[
        [
            Reply(text="", tools=(("write_chapter", {"chapter": 1, "title": "雨夜", "file_type": "draft"}),)),
            Reply(text=f"<file>\n{NEW_BODY}\n</file>"),
        ],
        [Reply(text="第一章写好了。")],
    ])

    outcome = await _run_loop(workspace, model, "继续", previous_output_truncations=previous_truncations)

    first, followup = _run_input_text(model, 0), _run_input_text(model, 1)
    if previous_truncations:
        assert first.count(OUTPUT_TRUNCATED_RESUME) == 1
        # 只对本轮第一次 run 加：续跑（从上一次完整输入接着来时那一条已在里面）不再追加。
        assert followup.count(OUTPUT_TRUNCATED_RESUME) <= 1
    else:
        assert OUTPUT_TRUNCATED_RESUME not in first
    # 不写回跨轮历史（history_items）。
    assert OUTPUT_TRUNCATED_RESUME not in json.dumps(outcome.state.get("history_items"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# 新章正文写到输出上限被截断（runner 的结构化 output_capped）：保存到截断处、从断点续写
# ---------------------------------------------------------------------------

CUT_BODY = "\n".join(f"第{index}段合成正文，写到输出上限之前的部分。" for index in range(1, 12)) + "\n他刚推开门，"
REST_BODY = "门外站着一个合成人物。\n最后一段合成正文。"


def _cap_tokens() -> int:
    from agent.openai_agents import runner

    return runner.AGENT_OPENAI_AGENTS_MAX_OUTPUT_TOKENS


async def test_new_chapter_cut_at_the_output_cap_is_kept_and_continued_from_the_break(workspace):
    def _append_rest(reply: Reply) -> None:
        [chapter] = _drafts(workspace)
        reply.tools = (("edit_file", {"id": chapter.id, "edits": [{"op": "append", "text": REST_BODY}]}),)

    continuation = Reply(text="")
    continuation.before = lambda: _append_rest(continuation)
    model = ScriptedModel(runs=[
        [
            Reply(text="", tools=(("write_chapter", {"chapter": 1, "title": "雨夜", "file_type": "draft"}),)),
            Reply(text=f"<file>\n{CUT_BODY}", output_tokens=_cap_tokens()),
        ],
        [continuation, Reply(text="第一章写完了。")],
    ])

    outcome = await _run_loop(workspace, model, "写第一章")

    [chapter] = _drafts(workspace)
    # 截断处之前的正文照样保存（作者不丢字），续写接在断点之后。
    assert _content(workspace, chapter.id).startswith(CUT_BODY.rstrip("，"))
    assert _content(workspace, chapter.id).rstrip("\n").endswith(REST_BODY)
    followup = _latest_note(model, 1)
    # 续跑说明如实说正文被输出上限截断、只保存到截断处，给出断点附近的原文，要求用 append 续写。
    assert "输出上限" in followup
    assert CUT_BODY[-60:].replace("\n", "\\n") in followup
    assert "append" in followup
    assert "写完的正文已保存" not in followup
    # 附的字数是截断处的数，标明续写后会变、最终以 edit_file 结果为准，不当作这一章的真实字数。
    assert "[截断处已保存的字数]" in followup
    assert "new_word_count" in followup
    assert "[本轮正文字数]" not in followup
    # 作者看到的是「写到上限、已保存到中断处」，不是一章写完。
    assert "输出上限" in outcome.chat_text
    assert CUT_BODY not in outcome.chat_text


async def test_cut_body_still_continues_when_saved_word_count_cannot_be_read(workspace, monkeypatch):
    """字数只是续写提示的附加信息；临时查询失败不能让已落库的截断正文失去续写机会。"""
    from agent.loop import run_support

    def _append_rest(reply: Reply) -> None:
        [chapter] = _drafts(workspace)
        reply.tools = (("edit_file", {"id": chapter.id, "edits": [{"op": "append", "text": REST_BODY}]}),)

    def _word_count_unavailable(_file_ids: list[str], *_args: Any, **_kwargs: Any):
        raise RuntimeError("synthetic word-count read failure")

    continuation = Reply(text="")
    continuation.before = lambda: _append_rest(continuation)
    model = ScriptedModel(runs=[
        [
            Reply(text="", tools=(("write_chapter", {"chapter": 1, "title": "雨夜", "file_type": "draft"}),)),
            Reply(text=f"<file>\n{CUT_BODY}", output_tokens=_cap_tokens()),
        ],
        [continuation, Reply(text="第一章写完了。")],
    ])
    monkeypatch.setattr(run_support, "collect_body_word_counts", _word_count_unavailable)

    outcome = await _run_loop(workspace, model, "写第一章")

    [chapter] = _drafts(workspace)
    assert _content(workspace, chapter.id).startswith(CUT_BODY.rstrip("，"))
    assert _content(workspace, chapter.id).rstrip("\n").endswith(REST_BODY)
    followup = _latest_note(model, 1)
    assert "输出上限" in followup and "append" in followup
    assert "[截断处已保存的字数]" not in followup
    assert "输出上限" in outcome.chat_text


async def test_unclosed_new_chapter_below_the_cap_keeps_the_saved_note(workspace):
    """没有撞上输出上限的未闭合正文（模型漏写 </file>）照旧按已保存续跑，不说被截断。"""
    model = ScriptedModel(runs=[
        [
            Reply(text="", tools=(("write_chapter", {"chapter": 1, "title": "雨夜", "file_type": "draft"}),)),
            Reply(text=f"<file>\n{NEW_BODY}"),
        ],
        [Reply(text="第一章写好了。")],
    ])

    outcome = await _run_loop(workspace, model, "写第一章")

    followup = _latest_note(model, 1)
    assert "写完的正文已保存" in followup
    assert "输出上限" not in followup
    assert "输出上限" not in outcome.chat_text


async def test_replacement_cut_at_the_output_cap_is_retried_as_cut_not_as_missing(workspace):
    three = _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", "合成第三章原文，一共二十个字左右的样子。")
    model = ScriptedModel(runs=[
        [
            Reply(text="", tools=(("write_chapter", {"chapter": 3, "title": "第3章", "file_type": "draft"}),)),
            Reply(text="<file>\n只写了一个开头", output_tokens=_cap_tokens()),
        ],
        [Reply(text=f"<file>\n{NEW_BODY}\n</file>\n第三章换好了。")],
    ])

    outcome = await _run_loop(workspace, model)

    retry = _latest_note(model, 1)
    # 同样的重试（为这一章恢复正文捕获），但说明的是被输出上限截断、没有保存，而不是「没写出新正文」。
    assert "重新打开正文写入" in retry
    assert "输出上限" in retry
    assert "正文已保存" not in retry
    assert _content(workspace, three.id).rstrip("\n") == NEW_BODY
    assert "未保存" not in outcome.chat_text


# ---------------------------------------------------------------------------
# 段首缩进：第一段和后面的段落一样原样落库（新建与整章替换同一口径）
# ---------------------------------------------------------------------------

INDENTED_BODY = "　　第一段合成正文，他说\"走吧\"。\n\n　　第二段合成正文。\n\n　　第三段合成正文。"


@pytest.mark.parametrize("lead", ["", "\n"])
@pytest.mark.parametrize("existing", [False, True], ids=["created", "replaced"])
async def test_streamed_chapter_keeps_the_first_paragraph_indent(workspace, lead, existing):
    if existing:
        _ai_chapter(workspace.session, workspace.project, "第1章 雨夜", "　　合成原文。")
    model = ScriptedModel(runs=[[
        Reply(text="", tools=(("write_chapter", {"chapter": 1, "title": "雨夜", "file_type": "draft"}),)),
        Reply(text=f"<file>{lead}{INDENTED_BODY}\n</file>\n第一章写好了。"),
    ]])

    outcome = await _run_loop(workspace, model, "写第一章")

    data = model.tool_calls[0].output["data"]
    assert data["action"] == ("replaced" if existing else "created")
    saved = _content(workspace, data["id"])
    paragraphs = [line for line in saved.split("\n") if line.strip()]
    assert len(paragraphs) == 3
    assert all(line.startswith("　　") and not line.startswith("　　　") for line in paragraphs), saved
    # 编辑器实时打字看到的也是带缩进的第一段。
    assert outcome.file_text(data["id"]).startswith("　　第一段")
    if existing:
        [edit_end] = outcome.of("file_edit_end")
        assert edit_end.data["new_content"] == saved


UNINDENTED_FIRST_BODY = "第一段合成正文。\n\n　　第二段合成正文。\n\n　　第三段合成正文。\n\n　　第四段合成正文。"


@pytest.mark.parametrize("existing", [False, True], ids=["created", "replaced"])
async def test_streamed_chapter_missing_only_the_first_indent_is_saved_indented(workspace, existing):
    if existing:
        _ai_chapter(workspace.session, workspace.project, "第1章 雨夜", "　　合成原文。")
    model = ScriptedModel(runs=[[
        Reply(text="", tools=(("write_chapter", {"chapter": 1, "title": "雨夜", "file_type": "draft"}),)),
        Reply(text=f"<file>\n{UNINDENTED_FIRST_BODY}\n</file>\n第一章写好了。"),
    ]])

    outcome = await _run_loop(workspace, model, "写第一章")

    data = model.tool_calls[0].output["data"]
    assert data["action"] == ("replaced" if existing else "created")
    saved = _content(workspace, data["id"])
    assert saved.rstrip("\n") == "　　" + UNINDENTED_FIRST_BODY
    # 补两个全角空格不改字数：模型和作者看到的字数就是落库正文的字数。
    from utils.text_metrics import count_words

    assert outcome.adapter.get_saved_file_word_counts()[data["id"]] == count_words(saved)
    if existing:
        # diff 审阅里的新稿就是落库的正文。
        [edit_end] = outcome.of("file_edit_end")
        assert edit_end.data["new_content"] == saved
        final = _results(outcome, "write_chapter")[-1]["data"]
        assert final["new_word_count"] == count_words(saved)


async def test_rewriting_a_chapter_keeps_its_own_unindented_first_paragraph(workspace):
    # 原文第一段本来就顶格（作者的写法）：AI 整章写回同一段时不补缩进，也不算一次改动。
    chapter = _ai_chapter(workspace.session, workspace.project, "第1章 雨夜", UNINDENTED_FIRST_BODY + "\n")
    model = ScriptedModel(runs=[[
        Reply(text="", tools=(("write_chapter", {"chapter": 1, "title": "雨夜", "file_type": "draft"}),)),
        Reply(text=f"<file>\n{UNINDENTED_FIRST_BODY}\n</file>\n第一章照原样写回了。"),
    ]])

    outcome = await _run_loop(workspace, model, "把第一章原样写回")

    assert model.tool_calls[0].output["data"]["action"] == "replaced"
    assert _content(workspace, chapter.id) == UNINDENTED_FIRST_BODY + "\n"
    final = _results(outcome, "write_chapter")[-1]["data"]
    assert final["mutation_applied"] is False


async def test_new_chapter_without_parent_id_goes_to_the_draft_folder(loop_tools):
    from agent.tools.mcp_tools import write_chapter

    session, project = loop_tools.session, loop_tools.project
    session.add(File(id=f"{project.id}-draft-folder", project_id=project.id, title="正文", file_type="folder"))
    session.commit()

    created = _payload(
        await write_chapter({"chapter": 1, "title": "第1章 起航", "file_type": "draft"}, request_context=loop_tools.req_ctx)
    )

    assert created["status"] == "success" and created["data"]["action"] == "created"
    assert created["data"]["parent_id"] == f"{project.id}-draft-folder"


async def test_new_chapter_without_parent_id_follows_the_one_folder_chapters_already_use(loop_tools):
    from agent.tools.mcp_tools import write_chapter

    session, project = loop_tools.session, loop_tools.project
    session.add(File(id=f"{project.id}-draft-folder", project_id=project.id, title="正文", file_type="folder"))
    volume = File(project_id=project.id, title="卷一", file_type="folder")
    session.add(volume)
    session.commit()
    _ai_chapter(session, project, "第1章 起航", "合成。", parent_id=volume.id)

    created = _payload(
        await write_chapter({"chapter": 2, "title": "第2章 夜航", "file_type": "draft"}, request_context=loop_tools.req_ctx)
    )

    assert created["status"] == "success" and created["data"]["action"] == "created"
    assert created["data"]["parent_id"] == volume.id


async def test_new_episode_without_parent_id_goes_to_the_script_folder(workspace):
    from agent.tools.mcp_tools import write_chapter

    project = workspace.project
    project.project_type = "screenplay"
    workspace.session.add(project)
    workspace.session.add(
        File(id=f"{project.id}-script-folder", project_id=project.id, title="剧本", file_type="folder")
    )
    workspace.session.commit()
    req_ctx = AgentRequestContext(
        session=workspace.session,
        user_id=workspace.user.id,
        project_id=project.id,
        session_id="loop-tools",
        current_agent="loop",
    )
    try:
        created = _payload(
            await write_chapter({"chapter": 1, "title": "第1集 雨夜", "file_type": "script"}, request_context=req_ctx)
        )
    finally:
        req_ctx.clear()

    assert created["status"] == "success" and created["data"]["action"] == "created"
    assert created["data"]["parent_id"] == f"{project.id}-script-folder"


async def test_write_chapter_replacement_updates_title_atomically_on_save(workspace):
    """P2-3(a)：write_chapter 替换已有章节时传入新章名，落库同一事务更新标题；只传裸章号（如「第1章」）则保留原标题。"""
    one = _ai_chapter(workspace.session, workspace.project, "第1章 雨夜", "第一章旧正文。")
    three = _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", "第三章旧正文。\n第二段。")
    model = ScriptedModel(runs=[[
        Reply(text="", tools=(("write_chapter", {"chapter": 3, "title": "破晓", "file_type": "draft"}),)),
        Reply(text=f"<file>\n{NEW_BODY}\n</file>\n", tools=(
            ("write_chapter", {"chapter": 1, "title": "第1章", "file_type": "draft"}),
        )),
        Reply(text=f"<file>\n{BODY_ONE}\n</file>\n两章都换好了。"),
    ]])

    outcome = await _run_loop(workspace, model, "重写第三章改名破晓，并重写第一章")

    armed_three = model.tool_calls[0].output
    assert armed_three["status"] == "success"
    assert armed_three["data"]["new_title"] == "第3章 破晓"
    assert "diff 审阅" not in armed_three["data"]["next_step"]
    workspace.session.expire_all()
    refreshed_three = workspace.session.get(File, three.id)
    refreshed_one = workspace.session.get(File, one.id)
    assert refreshed_three.title == "第3章 破晓"
    assert refreshed_three.content.rstrip("\n") == NEW_BODY
    # 第一章只传了裸章号「第1章」，保留原有副标题「第1章 雨夜」。
    assert refreshed_one.title == "第1章 雨夜"
    assert refreshed_one.content.rstrip("\n") == BODY_ONE
    final_three = [r["data"] for r in _results(outcome, "write_chapter") if r["data"].get("id") == three.id][-1]
    assert final_three["body_saved"] is True and final_three["title"] == "第3章 破晓"


async def test_replace_body_commits_a_title_change_when_the_body_is_unchanged(workspace):
    """整章保存返回了与原稿相同的正文时，仍原子落库新章名与顺序。"""
    from agent.tools.file_ops.edit import FileEditor

    original = "第三章原文。\n第二段原文。"
    chapter = _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", original)

    result = FileEditor(workspace.session, workspace.user.id).replace_body(
        chapter.id,
        original,
        expected_updated_at=chapter.updated_at.isoformat() if chapter.updated_at else None,
        title="第4章 破晓",
    )

    workspace.session.expire_all()
    refreshed = workspace.session.get(File, chapter.id)
    assert result["mutation_applied"] is True
    assert result["new_content"] == original
    assert refreshed.title == "第4章 破晓"
    assert refreshed.order == 4


async def test_replace_body_preserves_the_old_title_when_the_new_title_now_conflicts(workspace):
    """定位章节后若同目录出现同名文件，落库时不覆盖原章名。"""
    from agent.tools.file_ops.edit import FileEditor

    original = "第三章原文。\n第二段原文。"
    chapter = _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", original)
    _ai_chapter(workspace.session, workspace.project, "第3章 破晓", "同名目标。")

    result = FileEditor(workspace.session, workspace.user.id).replace_body(
        chapter.id,
        original,
        expected_updated_at=chapter.updated_at.isoformat() if chapter.updated_at else None,
        title="第3章 破晓",
    )

    workspace.session.expire_all()
    assert result["mutation_applied"] is False
    assert workspace.session.get(File, chapter.id).title == "第3章 灯塔"


@pytest.mark.parametrize("file_metadata", [None, "{not-json", 42])
def test_imported_chapter_lookup_ignores_unusable_metadata(file_metadata):
    """导入章节没有可用的结构化章号时，不把它误认为目标章节。"""
    from agent.tools.mcp_tools import _row_chapter_number

    row = SimpleNamespace(title="导入稿件", file_type="draft", file_metadata=file_metadata)

    assert _row_chapter_number(row, unit="章", expected_file_type="draft") is None


def test_prefixed_import_title_without_a_sequence_is_not_a_chapter_heading():
    """导入标题有书名前缀但没有章号时，不把后半段当成章节标题。"""
    from agent.tools.mcp_tools import _prefixed_chapter_heading

    assert _prefixed_chapter_heading("剑来 - 序章", "章") is None


def test_empty_chapter_title_has_no_subtitle():
    """空章名不能被当成需要同步落库的新副标题。"""
    from agent.tools.mcp_tools import _chapter_title_has_subtitle

    assert _chapter_title_has_subtitle("\x00  ", "章") is False


def test_imported_chapter_lookup_reads_mapping_metadata():
    """导入器已解析成映射的 file_metadata 也能定位章号。"""
    from agent.tools.mcp_tools import _row_chapter_number

    row = SimpleNamespace(
        title="导入稿件",
        file_type="draft",
        file_metadata={"source": "upload", "chapter_number": 7},
    )

    assert _row_chapter_number(row, unit="章", expected_file_type="draft") == 7


def test_imported_chapter_lookup_rejects_the_other_body_type():
    """导入元数据中的同号分集不能命中小说章节查找。"""
    from agent.tools.mcp_tools import _row_chapter_number

    row = SimpleNamespace(
        title="导入稿件",
        file_type="script",
        file_metadata={"episode_number": 7},
    )

    assert _row_chapter_number(row, unit="章", expected_file_type="draft") is None


@pytest.mark.parametrize("second_tool", ["rename_file", "edit_file"])
async def test_same_reply_rename_or_edit_on_armed_write_chapter_is_refused_and_preserves_streamed_save(
    workspace, second_tool: str
):
    """P2-3(b)：同一回复里对刚 write_chapter 锁定待写的章节调 rename_file 或 edit_file 时拒绝执行、不推进 updated_at，确保随后的 <file> 整章替换正常保存。"""
    three = _ai_chapter(workspace.session, workspace.project, "第3章 灯塔", "合成第三章原文。\n第二段原文。")
    baseline_updated_at = three.updated_at
    second_call = (
        ("rename_file", {"id": three.id, "title": "第3章 破晓"})
        if second_tool == "rename_file"
        else ("edit_file", {"id": three.id, "edits": [{"op": "replace", "old": "合成第三章原文。", "new": "局部改动。"}]})
    )
    model = ScriptedModel(runs=[[
        Reply(
            text="",
            tools=(
                ("write_chapter", {"chapter": 3, "title": "第3章 破晓", "file_type": "draft"}),
                second_call,
            ),
        ),
        Reply(text=f"<file>\n{NEW_BODY}\n</file>\n第三章已经重写好了。"),
    ]])

    outcome = await _run_loop(workspace, model, "重写第三章并改名")

    refused = model.tool_calls[1].output
    assert refused["status"] == "error"
    assert refused["error_type"] == "chapter_replace_pending"
    assert refused["mutation_applied"] is False
    assert baseline_updated_at is not None
    # 随后的 <file> 整章替换未被乐观锁拒绝，新正文与新标题正常落库。
    assert _content(workspace, three.id).rstrip("\n") == NEW_BODY
    workspace.session.expire_all()
    assert workspace.session.get(File, three.id).title == "第3章 破晓"
    final = _results(outcome, "write_chapter")[-1]["data"]
    assert final["body_saved"] is True and final["mutation_applied"] is True


async def test_write_chapter_recognizes_uploaded_chapter_via_file_metadata(workspace):
    """P3-H：导入多章小说拆出的章节标题形如「书名 - 第1章 …」、章号存于 file_metadata.chapter_number，write_chapter 应识别已有章节而非重复新建。"""
    from agent.tools.mcp_tools import write_chapter

    uploaded = File(
        project_id=workspace.project.id,
        title="剑来 - 第1章 惊蛰",
        content="导入的第一章正文。",
        file_type="draft",
        file_metadata=json.dumps({"source": "upload", "chapter_number": 1}, ensure_ascii=False),
    )
    workspace.session.add(uploaded)
    workspace.session.flush()
    FileVersionService().create_version(
        workspace.session, uploaded.id, uploaded.content, change_type="create", change_source="ai", commit=False, skip_quota=True
    )
    workspace.session.commit()
    workspace.session.refresh(uploaded)

    req_ctx = AgentRequestContext(
        session=workspace.session,
        user_id=workspace.user.id,
        project_id=workspace.project.id,
        session_id="loop-upload-seq",
        author_message="重写第一章",
        current_agent="loop",
    )
    try:
        armed = _payload(
            await write_chapter({"chapter": 1, "title": "第1章", "file_type": "draft"}, request_context=req_ctx)
        )
    finally:
        req_ctx.clear()

    assert armed["status"] == "success"
    assert armed["data"]["action"] == "replaced"
    assert armed["data"]["id"] == uploaded.id
    assert len(_drafts(workspace)) == 1


async def test_write_chapter_uploaded_prefixed_title_preserves_or_updates_without_double_chapter_prefix(workspace):
    """P2-3 × P3-H：导入章节形如「剑来 - 第1章 惊蛰」时，传同名标题保持不变，传「剑来 - 第1章 风起」在落库时更新为该标题而非拼出「第1章 剑来 - 第1章 …」，章号冲突仍报错。"""
    from agent.tools.mcp_tools import WRITE_CHAPTER_INVALID_ERROR_TYPE, write_chapter

    ch1 = File(
        project_id=workspace.project.id,
        title="剑来 - 第1章 惊蛰",
        content="导入第一章旧稿。\n第二段。",
        file_type="draft",
        file_metadata=json.dumps({"source": "upload", "chapter_number": 1}, ensure_ascii=False),
    )
    ch2 = File(
        project_id=workspace.project.id,
        title="剑来 - 第2章 渡口",
        content="导入第二章旧稿。\n第二段。",
        file_type="draft",
        file_metadata=json.dumps({"source": "upload", "chapter_number": 2}, ensure_ascii=False),
    )
    workspace.session.add_all([ch1, ch2])
    workspace.session.flush()
    for ch in (ch1, ch2):
        FileVersionService().create_version(
            workspace.session, ch.id, ch.content, change_type="create", change_source="ai", commit=False, skip_quota=True
        )
    workspace.session.commit()

    req_ctx = AgentRequestContext(
        session=workspace.session,
        user_id=workspace.user.id,
        project_id=workspace.project.id,
        session_id="loop-upload-mismatch",
        author_message="检查章号冲突",
        current_agent="loop",
    )
    try:
        mismatch = _payload(
            await write_chapter(
                {"chapter": 1, "title": "剑来 - 第2章 风起", "file_type": "draft"},
                request_context=req_ctx,
            )
        )
    finally:
        req_ctx.clear()
    assert mismatch["status"] == "error" and mismatch["error_type"] == WRITE_CHAPTER_INVALID_ERROR_TYPE

    model = ScriptedModel(runs=[[
        Reply(text="", tools=(("write_chapter", {"chapter": 1, "title": "剑来 - 第1章 惊蛰", "file_type": "draft"}),)),
        Reply(text=f"<file>\n{BODY_ONE}\n</file>\n", tools=(
            ("write_chapter", {"chapter": 2, "title": "剑来 - 第2章 风起", "file_type": "draft"}),
        )),
        Reply(text=f"<file>\n{NEW_BODY}\n</file>\n两章重写完成。"),
    ]])

    await _run_loop(workspace, model, "重写前两章，第二章改名风起")

    armed_one = model.tool_calls[0].output
    assert "new_title" not in armed_one["data"]
    armed_two = model.tool_calls[1].output
    assert armed_two["data"]["new_title"] == "剑来 - 第2章 风起"

    workspace.session.expire_all()
    assert workspace.session.get(File, ch1.id).title == "剑来 - 第1章 惊蛰"
    assert workspace.session.get(File, ch2.id).title == "剑来 - 第2章 风起"
    assert _content(workspace, ch1.id).rstrip("\n") == BODY_ONE
    assert _content(workspace, ch2.id).rstrip("\n") == NEW_BODY


async def test_refused_replacement_clears_pending_new_title_from_card():
    """整章替换被拒存（如并发冲突）时，补发的卡片不保留未生效的 new_title。"""
    from agent.core.workflow_events import StreamEvent, StreamEventType
    from agent.tools.file_ops.crud import ConcurrentFileWriteError

    adapter = StreamAdapter()
    card = {
        "id": "chapter-3",
        "title": "第3章 灯塔",
        "new_title": "第3章 破晓",
        "action": "replaced",
        "chapter": 3,
    }
    adapter.set_pending_file_write(
        "chapter-3",
        "draft",
        "第3章 灯塔",
        original_content_length=40,
        baseline_updated_at="v1",
        replace_tool_use_id="call-refused-title",
        replace_card=card,
        target_title="第3章 破晓",
    )

    def _refuse(*_args, **_kwargs):
        raise ConcurrentFileWriteError("stale")

    async def _workflow():
        yield StreamEvent(StreamEventType.TEXT, {"text": f"<file>\n{NEW_BODY}\n</file>\n"})
        yield StreamEvent(StreamEventType.MESSAGE_END, {"stop_reason": "end_turn"})

    adapter._replace_file_body_sync = _refuse
    frames = [frame async for frame in adapter.process_workflow_events(_workflow())]
    [tool_frame] = [f for f in frames if f.type.value == "tool_result"]
    assert tool_frame.data["data"]["body_saved"] is False
    assert tool_frame.data["data"]["title"] == "第3章 灯塔"
    assert "new_title" not in tool_frame.data["data"]


@pytest.mark.parametrize(
    ("existing_title", "passed_title", "expected_title"),
    [
        ("剑来 - 第1章 惊蛰", "第1章 惊蛰", "剑来 - 第1章 惊蛰"),
        ("剑来 - 第1章 惊蛰", "惊蛰", "剑来 - 第1章 惊蛰"),
        ("剑来 - 第1章 惊蛰", "风起", "剑来 - 第1章 风起"),
        ("剑来 -  第1章 惊蛰", "风起", "剑来 - 第1章 风起"),
        ("剑来 - 第1章 惊蛰", "第1章 风起", "剑来 - 第1章 风起"),
        ("剑来 - 第1章 惊蛰", "第1章", "剑来 - 第1章 惊蛰"),
    ],
)
async def test_uploaded_prefixed_chapter_preserves_book_prefix_across_replace_titles(
    workspace, existing_title, passed_title, expected_title
):
    chapter = _ai_chapter(
        workspace.session,
        workspace.project,
        existing_title,
        "导入原文。",
    )

    model = ScriptedModel(
        runs=[
            [
                Reply(text="", tools=(("write_chapter", {"chapter": 1, "title": passed_title, "file_type": "draft"}),)),
                Reply(text=f"<file>\n{NEW_BODY}\n</file>\n已重写。"),
            ]
        ]
    )
    await _run_loop(workspace, model)
    workspace.session.expire_all()
    assert workspace.session.get(File, chapter.id).title == expected_title


def test_stamp_body_receipts_drops_unsaved_new_title_and_promotes_committed_abort_replacement():
    from agent.service import _stamp_body_receipts
    from agent.stream_adapter import StreamAdapter

    adapter = StreamAdapter()
    adapter._note_replacement_receipt("call-saved", 380)
    adapter._note_replacement_card_update(
        "call-saved",
        {"title": "第2章 新名", "undo": {"before_version_number": 1, "expected_after_updated_at": "t2"}},
    )
    adapter._note_replacement_receipt("call-empty-abort", 120)

    calls = [
        {
            "id": "call-unsaved",
            "name": "write_chapter",
            "status": "success",
            "result": {"id": "f1", "title": "第1章 旧名", "new_title": "第1章 新名", "action": "replaced", "awaiting_body": True},
        },
        {
            "id": "call-saved",
            "name": "write_chapter",
            "status": "success",
            "result": {"id": "f2", "title": "第2章 旧名", "new_title": "第2章 新名", "action": "replaced", "awaiting_body": True},
        },
        {
            "id": "call-empty-abort",
            "name": "write_chapter",
            "status": "success",
            "result": {"id": "f3", "title": "第3章 旧名", "new_title": "第3章 未落库名", "action": "replaced", "awaiting_body": True},
        },
    ]
    _stamp_body_receipts(
        calls,
        receipts={},
        replacement_receipts=adapter.get_replacement_receipts(),
        replacement_card_updates=adapter.get_replacement_card_updates(),
    )
    assert calls[0]["result"]["body_saved"] is False
    assert "new_title" not in calls[0]["result"]
    assert calls[0]["result"]["title"] == "第1章 旧名"

    assert calls[1]["result"]["body_saved"] is True
    assert calls[1]["result"]["new_word_count"] == 380
    assert calls[1]["result"]["title"] == "第2章 新名"
    assert "new_title" not in calls[1]["result"]
    assert calls[1]["result"]["undo"]["before_version_number"] == 1

    assert calls[2]["result"]["body_saved"] is True
    assert calls[2]["result"]["new_word_count"] == 120
    assert calls[2]["result"]["title"] == "第3章 旧名"
    assert "new_title" not in calls[2]["result"]

