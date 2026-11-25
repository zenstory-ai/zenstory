"""Regression tests for Backend Stream Latency & Perceived-Wait Governance.

Covers:
1. Tool-argument streaming progress events (`runner.py` + `stream_adapter.py`):
   immediate `thinking` status on `response.output_item.added` (`function_call`),
   throttled progress on `response.function_call_arguments.delta`, transient SSE
   mapping without DB persistence or billing mutation.
2. Compact `<project_files>` manifest inside `<turn_context>` (`turn_context.py`):
   deterministic ordering, bounded 150-file cap with `... (+N more)` footer,
   TOAST-safe metadata-only query (never reading `File.content`), and prefix-cache stability.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import event as sa_event
from sqlmodel import Session

from agent.core.events import EventType
from agent.core.stream_billing import StreamBillingTracker
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.loop.turn_context import (
    PROJECT_FILES_CLOSE,
    PROJECT_FILES_MANIFEST_MAX_FILES,
    PROJECT_FILES_OPEN,
    build_loop_turn_context,
)
from agent.openai_agents.runner import (
    TOOL_ARG_PROGRESS_BYTES,
    TOOL_ARG_PROGRESS_INTERVAL_S,
    TOOL_PREPARING_STEP,
    run_openai_agents_streaming_agent,
)
from agent.stream_adapter import StreamAdapter, StreamAdapterConfig
from models import ChatMessage, ChatSession, File, Project, User
from models.file_model import stamp_word_count
from services.core.auth_service import hash_password


def _make_user_project(db_session: Session) -> tuple[User, Project, ChatSession]:
    suffix = uuid4().hex[:8]
    user = User(
        email=f"latency-{suffix}@example.com",
        username=f"latency_{suffix}",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name=f"雾港笔记-{suffix}", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    chat = ChatSession(user_id=user.id, project_id=project.id, title="对话", is_active=True)
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    return user, project, chat


def _parse_sse_chunks(chunks: list[str]) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for chunk in chunks:
        for block in chunk.split("\n\n"):
            lines = [line for line in block.splitlines() if line]
            ev = next((line[6:].strip() for line in lines if line.startswith("event:")), None)
            data = next((line[5:].strip() for line in lines if line.startswith("data:")), None)
            if ev and data:
                events.append((ev, json.loads(data)))
    return events


# ---------------------------------------------------------------------------
# 1. Tool-argument streaming progress events (runner.py + stream_adapter.py)
# ---------------------------------------------------------------------------


class _FakeStreamedResult:
    def __init__(self, sdk_events: list[Any]) -> None:
        self._sdk_events = sdk_events
        self.raw_responses: list[Any] = []

    async def stream_events(self):
        for ev in self._sdk_events:
            yield ev

    def to_input_list(self) -> list[dict[str, Any]]:
        return [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}]


def _raw_ev(payload: Any) -> Any:
    return SimpleNamespace(type="raw_response_event", data=payload)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_runner_emits_tool_preparing_and_throttled_arg_progress(monkeypatch):
    """output_item.added emits immediately; function_call_arguments.delta throttles by bytes/time."""
    now = [100.0]
    monkeypatch.setattr("agent.openai_agents.runner.time.monotonic", lambda: now[0])

    # 20 chunks of 200 ASCII bytes = 4,000 bytes for edit_file -> should throttle around every 1,500 bytes (2 emits),
    # NOT 20 emits. Plus a small query_files call (30 bytes) -> 1 initial emit, 0 progress emits.
    sdk_events: list[Any] = [
        _raw_ev(SimpleNamespace(type="response.created")),
        _raw_ev(SimpleNamespace(type="response.reasoning_text.delta", delta="先规划修改方案")),
        _raw_ev(
            SimpleNamespace(
                type="response.output_item.added",
                output_index=0,
                item=SimpleNamespace(type="function_call", id="fc_edit", name="edit_file"),
            )
        ),
    ]
    for _ in range(20):
        sdk_events.append(
            _raw_ev(
                SimpleNamespace(
                    type="response.function_call_arguments.delta",
                    item_id="fc_edit",
                    output_index=0,
                    delta="a" * 200,
                )
            )
        )
    sdk_events.extend(
        [
            _raw_ev(SimpleNamespace(type="response.function_call_arguments.done", item_id="fc_edit")),
            _raw_ev(
                SimpleNamespace(
                    type="response.output_item.added",
                    output_index=1,
                    item=SimpleNamespace(type="function_call", id="fc_query", name="query_files"),
                )
            ),
            _raw_ev(
                SimpleNamespace(
                    type="response.function_call_arguments.delta",
                    item_id="fc_query",
                    output_index=1,
                    delta='{"mode":"list"}',
                )
            ),
            _raw_ev(SimpleNamespace(type="response.output_text.delta", delta="已完成。")),
        ]
    )

    fake_result = _FakeStreamedResult(sdk_events)

    with (
        patch("agents.Runner.run_streamed", return_value=fake_result),
        patch("agent.openai_agents.runner._build_agent", return_value=SimpleNamespace(name="loop")),
        patch("agent.openai_agents.runner._preferred_model_name", AsyncMock(return_value="mock-model")),
    ):
        collected = [
            ev
            async for ev in run_openai_agents_streaming_agent(
                {"user_message": "改一下第一章", "language": "zh"},
                agent_type="loop",
                system_prompt="sys",
            )
        ]

    thinking_events = [ev for ev in collected if ev.type == StreamEventType.THINKING]
    reasoning_events = [ev for ev in thinking_events if "thinking" in ev.data]
    status_events = [ev for ev in thinking_events if "message" in ev.data]

    assert [ev.data["thinking"] for ev in reasoning_events] == ["先规划修改方案"]
    # Initial edit_file status + 2 throttled progress updates (at 1600 and 3200 bytes) + 1 query_files status.
    assert len(status_events) == 4
    assert status_events[0].data["message"] == "正在准备编辑文件…"
    assert status_events[0].data["step"] == TOOL_PREPARING_STEP
    assert status_events[0].data["tool_name"] == "edit_file"
    assert "正在生成文件修改内容" in status_events[1].data["message"]
    assert "正在生成文件修改内容" in status_events[2].data["message"]
    assert status_events[3].data["message"] == "正在准备检索文件…"
    assert status_events[3].data["tool_name"] == "query_files"
    assert TOOL_ARG_PROGRESS_BYTES == 1_500


@pytest.mark.asyncio
@pytest.mark.unit
async def test_runner_throttles_slow_tool_arg_deltas_by_time_interval_en(monkeypatch):
    """Slowly trickling edit_file deltas still emit time-based heartbeats in English."""
    clock = [10.0]
    monkeypatch.setattr("agent.openai_agents.runner.time.monotonic", lambda: clock[0])

    async def slow_stream():
        yield _raw_ev(
            SimpleNamespace(
                type="response.output_item.added",
                output_index=0,
                item=SimpleNamespace(type="function_call", id="fc_1", name="edit_file"),
            )
        )
        # 0.5s later, tiny chunk -> should NOT emit yet.
        clock[0] = 10.5
        yield _raw_ev(
            SimpleNamespace(type="response.function_call_arguments.delta", item_id="fc_1", delta='{"edits":[')
        )
        # 2.5s after initial emit -> SHOULD emit time-based progress update.
        clock[0] = 10.0 + TOOL_ARG_PROGRESS_INTERVAL_S + 0.5
        yield _raw_ev(
            SimpleNamespace(type="response.function_call_arguments.delta", item_id="fc_1", delta='{"old":"x"}')
        )

    fake_result = SimpleNamespace(
        stream_events=slow_stream,
        raw_responses=[],
        to_input_list=lambda: [{"role": "user", "content": "edit"}],
    )
    with (
        patch("agents.Runner.run_streamed", return_value=fake_result),
        patch("agent.openai_agents.runner._build_agent", return_value=SimpleNamespace(name="loop")),
        patch("agent.openai_agents.runner._preferred_model_name", AsyncMock(return_value="mock-model")),
    ):
        collected = [
            ev
            async for ev in run_openai_agents_streaming_agent(
                {"user_message": "Edit chapter 1", "language": "en"},
                agent_type="loop",
                system_prompt="sys",
            )
        ]

    statuses = [ev.data for ev in collected if ev.type == StreamEventType.THINKING and "message" in ev.data]
    assert len(statuses) == 2
    assert statuses[0]["message"] == "Preparing file edits…"
    assert statuses[1]["message"].startswith("Generating file edits (~")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_runner_parallel_tool_calls_with_fake_responses_id_do_not_collide(monkeypatch):
    """Chat Completions stamps FAKE_RESPONSES_ID on every tool item; parallel calls must key by output_index."""
    from agents.models.fake_id import FAKE_RESPONSES_ID

    now = [50.0]
    monkeypatch.setattr("agent.openai_agents.runner.time.monotonic", lambda: now[0])

    sdk_events: list[Any] = [
        _raw_ev(SimpleNamespace(type="response.created")),
        _raw_ev(
            SimpleNamespace(
                type="response.output_item.added",
                output_index=0,
                item=SimpleNamespace(type="function_call", id=FAKE_RESPONSES_ID, call_id="call_0", name="edit_file"),
            )
        ),
        _raw_ev(
            SimpleNamespace(
                type="response.output_item.added",
                output_index=1,
                item=SimpleNamespace(type="function_call", id=FAKE_RESPONSES_ID, call_id="call_1", name="create_file"),
            )
        ),
        # Interleave deltas across both parallel calls sharing FAKE_RESPONSES_ID.
        _raw_ev(
            SimpleNamespace(
                type="response.function_call_arguments.delta",
                item_id=FAKE_RESPONSES_ID,
                output_index=0,
                delta="e" * 1_600,
            )
        ),
        _raw_ev(
            SimpleNamespace(
                type="response.function_call_arguments.delta",
                item_id=FAKE_RESPONSES_ID,
                output_index=1,
                delta="c" * 800,
            )
        ),
        # Call 0 finishes while call 1 is still streaming — must NOT pop call 1's tracker!
        _raw_ev(
            SimpleNamespace(
                type="response.output_item.done",
                output_index=0,
                item=SimpleNamespace(type="function_call", id=FAKE_RESPONSES_ID, call_id="call_0", name="edit_file"),
            )
        ),
        _raw_ev(
            SimpleNamespace(
                type="response.function_call_arguments.delta",
                item_id=FAKE_RESPONSES_ID,
                output_index=1,
                delta="c" * 800,
            )
        ),
        _raw_ev(
            SimpleNamespace(
                type="response.output_item.done",
                output_index=1,
                item=SimpleNamespace(type="function_call", id=FAKE_RESPONSES_ID, call_id="call_1", name="create_file"),
            )
        ),
    ]

    fake_result = _FakeStreamedResult(sdk_events)
    with (
        patch("agents.Runner.run_streamed", return_value=fake_result),
        patch("agent.openai_agents.runner._build_agent", return_value=SimpleNamespace(name="loop")),
        patch("agent.openai_agents.runner._preferred_model_name", AsyncMock(return_value="mock-model")),
    ):
        collected = [
            ev
            async for ev in run_openai_agents_streaming_agent(
                {"user_message": "并行操作", "language": "zh"},
                agent_type="loop",
                system_prompt="sys",
            )
        ]

    statuses = [ev.data for ev in collected if ev.type == StreamEventType.THINKING and "message" in ev.data]
    assert len(statuses) == 4
    assert statuses[0]["tool_name"] == "edit_file" and statuses[0]["message"] == "正在准备编辑文件…"
    assert statuses[1]["tool_name"] == "create_file" and statuses[1]["message"] == "正在准备创建文件…"
    assert statuses[2]["tool_name"] == "edit_file" and "1600" in statuses[2]["message"]
    assert statuses[3]["tool_name"] == "create_file" and "1600" in statuses[3]["message"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_adapter_maps_tool_preparing_to_transient_thinking_sse_without_polluting_billing():
    """StreamAdapter emits transient SSE `thinking` (not `thinking_content`) and leaves billing untouched."""
    adapter = StreamAdapter(StreamAdapterConfig(project_id="p1", user_id="u1"))

    async def workflow():
        yield StreamEvent(type=StreamEventType.THINKING, data={"thinking": "内部思考"})
        yield StreamEvent(
            type=StreamEventType.THINKING,
            data={"message": "正在准备编辑文件…", "step": TOOL_PREPARING_STEP, "tool_name": "edit_file"},
        )
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    sse_events = [ev async for ev in adapter.process_workflow_events(workflow())]
    thinking_content_evts = [ev for ev in sse_events if ev.type == EventType.THINKING_CONTENT]
    thinking_status_evts = [ev for ev in sse_events if ev.type == EventType.THINKING]

    assert len(thinking_content_evts) == 1
    assert thinking_content_evts[0].data["content"] == "内部思考"
    assert len(thinking_status_evts) == 1
    assert thinking_status_evts[0].data == {"message": "正在准备编辑文件…", "step": TOOL_PREPARING_STEP}

    tracker = StreamBillingTracker()
    for ev in sse_events:
        if ev.type != EventType.DONE:
            tracker.observe(ev.to_sse())
    assert tracker.produced_output is False
    assert tracker.first_output_ms is None


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
async def test_service_yields_tool_preparing_thinking_sse_without_persisting_to_display_events(db_session: Session):
    """Transient tool-preparing `thinking` frames reach SSE but never pollute persisted `display_events`."""
    from agent.service import AgentService

    user, project, chat = _make_user_project(db_session)

    def fake_loop(_state, **_kwargs):
        async def stream():
            yield StreamEvent(type=StreamEventType.THINKING, data={"thinking": "思考一下怎么改"})
            yield StreamEvent(
                type=StreamEventType.THINKING,
                data={"message": "正在准备编辑文件…", "step": TOOL_PREPARING_STEP, "tool_name": "edit_file"},
            )
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "已经改好了。"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return stream()

    with patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop):
        chunks = [
            c
            async for c in AgentService().process_stream(
                project_id=project.id,
                user_id=user.id,
                message="改一下开头",
                session=db_session,
                session_id=chat.id,
            )
        ]

    events = _parse_sse_chunks(chunks)
    tool_prep_events = [
        data for ev, data in events if ev == "thinking" and data.get("step") == TOOL_PREPARING_STEP
    ]
    assert tool_prep_events == [{"message": "正在准备编辑文件…", "step": TOOL_PREPARING_STEP}]

    done_data = next(data for ev, data in events if ev == "done")
    db_session.expire_all()
    saved = db_session.get(ChatMessage, done_data["assistant_message_id"])
    assert saved is not None
    meta = json.loads(saved.message_metadata or "{}")
    display_events = meta.get("display_events") or []
    assert any(item.get("type") == "thinking_content" and "思考一下怎么改" in item.get("content", "") for item in display_events)
    assert all("正在准备编辑文件" not in json.dumps(item, ensure_ascii=False) for item in display_events)


# ---------------------------------------------------------------------------
# 2. Compact `<project_files>` manifest inside `<turn_context>`
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_turn_context_injects_compact_deterministic_project_files_manifest(db_session: Session):
    """<project_files> lists non-deleted non-folder files deterministically with word counts when known."""
    user, project, _chat = _make_user_project(db_session)

    folder = File(project_id=project.id, title="正文", file_type="folder", content="")
    deleted = File(project_id=project.id, title="已删草稿", file_type="draft", content="不应出现", is_deleted=True)
    ch10 = File(project_id=project.id, title="第10章 尾声", file_type="draft", content="尾声正文内容。")
    ch2 = File(project_id=project.id, title="第2章 渡口", file_type="draft", content="渡口风声很紧。")
    ch1 = File(project_id=project.id, title="第1章 雨夜", file_type="draft", content="雨夜里灯塔亮了。")
    stamp_word_count(ch1)
    outline = File(project_id=project.id, title="总大纲", file_type="outline", content="第一卷大纲")
    character = File(project_id=project.id, title="林默", file_type="character", content="主角林默")
    db_session.add_all([folder, deleted, ch10, ch2, ch1, outline, character])
    db_session.commit()

    turn = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={"current_file_id": ch2.id},
        language="zh",
        author_message="继续写第3章",
    )

    assert PROJECT_FILES_OPEN in turn.text and PROJECT_FILES_CLOSE in turn.text
    manifest = turn.text.split(PROJECT_FILES_OPEN, 1)[1].split(PROJECT_FILES_CLOSE, 1)[0].strip()
    lines = [line for line in manifest.splitlines() if line.strip()]

    # Folders and soft-deleted files excluded.
    assert folder.id not in manifest
    assert deleted.id not in manifest

    # Reference files (outline, character) precede drafts, and drafts sort numerically (1, 2, 10).
    ids_in_order = [line.split("id=", 1)[1].split(",", 1)[0].rstrip(")") for line in lines]
    assert ids_in_order == [outline.id, character.id, ch1.id, ch2.id, ch10.id]

    # Cached draft (ch1) and loaded focus file (ch2) carry accurate word counts; uncached ch10 omits word count
    # instead of falsely claiming 0 words.
    ch1_line = next(line for line in lines if ch1.id in line)
    ch2_line = next(line for line in lines if ch2.id in line)
    ch10_line = next(line for line in lines if ch10.id in line)
    assert "字)" in ch1_line and "0字" not in ch1_line
    assert "字)" in ch2_line and "0字" not in ch2_line
    assert "0字" not in ch10_line


@pytest.mark.unit
def test_turn_context_project_files_manifest_caps_at_150_with_footer(db_session: Session):
    """Projects larger than PROJECT_FILES_MANIFEST_MAX_FILES (150) are capped with a `... (+N more)` footer."""
    assert PROJECT_FILES_MANIFEST_MAX_FILES == 150
    user, project, _chat = _make_user_project(db_session)

    files = [
        File(project_id=project.id, title=f"第{idx}章", file_type="draft", content="")
        for idx in range(1, 156)
    ]
    db_session.add_all(files)
    db_session.commit()

    turn_zh = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={},
        language="zh",
        author_message="列出目录",
    )
    manifest_zh = turn_zh.text.split(PROJECT_FILES_OPEN, 1)[1].split(PROJECT_FILES_CLOSE, 1)[0].strip()
    item_lines_zh = [line for line in manifest_zh.splitlines() if line.startswith("- [")]
    assert len(item_lines_zh) == 150
    assert "... (+5 more" in manifest_zh

    turn_en = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={},
        language="en",
        author_message="list files",
    )
    manifest_en = turn_en.text.split(PROJECT_FILES_OPEN, 1)[1].split(PROJECT_FILES_CLOSE, 1)[0].strip()
    assert "... (+5 more" in manifest_en


@pytest.mark.unit
def test_turn_context_project_files_query_is_toast_safe(db_session: Session):
    """_project_files_block must never select File.content or compute length(File.content)."""
    from agent.loop.turn_context import _project_files_block

    user, project, _chat = _make_user_project(db_session)
    db_session.add(File(project_id=project.id, title="第1章", file_type="draft", content="很长的正文" * 500))
    db_session.commit()

    executed_sql: list[str] = []

    def _capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        executed_sql.append(statement.lower())

    engine = db_session.get_bind()
    sa_event.listen(engine, "before_cursor_execute", _capture)
    try:
        block = _project_files_block(db_session, project_id=project.id, force_en=False)
    finally:
        sa_event.remove(engine, "before_cursor_execute", _capture)

    assert PROJECT_FILES_OPEN in block
    assert executed_sql
    for sql in executed_sql:
        assert "file.content" not in sql
        assert "length(" not in sql


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
async def test_project_files_manifest_preserves_static_prefix_across_turns(db_session: Session):
    """Adding new project files updates <project_files> in <turn_context> without mutating system_prompt/history."""
    from agent.service import AgentService

    user, project, chat = _make_user_project(db_session)
    ch1 = File(project_id=project.id, title="第1章", file_type="draft", content="第一章内容。")
    db_session.add(ch1)
    db_session.commit()

    captured: list[dict[str, Any]] = []

    def fake_loop(state, **_kwargs):
        captured.append(
            {
                "system_prompt": state["system_prompt"],
                "history_items": list(state.get("history_items") or []),
                "user_message": state["user_message"],
            }
        )

        async def stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "收到。"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return stream()

    with patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop):
        async for _ in AgentService().process_stream(
            project_id=project.id,
            user_id=user.id,
            message="第一轮",
            session=db_session,
            session_id=chat.id,
        ):
            pass

        ch2 = File(project_id=project.id, title="第2章", file_type="draft", content="第二章内容。")
        db_session.add(ch2)
        db_session.commit()

        async for _ in AgentService().process_stream(
            project_id=project.id,
            user_id=user.id,
            message="第二轮",
            session=db_session,
            session_id=chat.id,
        ):
            pass

    assert len(captured) == 2
    assert captured[0]["system_prompt"] == captured[1]["system_prompt"]
    assert ch1.id not in captured[0]["system_prompt"] and ch2.id not in captured[1]["system_prompt"]
    assert PROJECT_FILES_OPEN in captured[0]["user_message"] and ch1.id in captured[0]["user_message"]
    assert ch2.id not in captured[0]["user_message"]
    assert ch1.id in captured[1]["user_message"] and ch2.id in captured[1]["user_message"]
