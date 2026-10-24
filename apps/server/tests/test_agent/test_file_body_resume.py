"""空文件纠偏轮把正文写回同一个文件：跨层合成回归（不调用模型、不计费）。

生产（prod-v2026.10.24.1）里 create_file 建好空文件后，下一次回复没写正文 run 就结束了：
MESSAGE_END 把捕获从 WAITING_START 复位成 IDLE；图的纠偏轮又要求 edit_file，而「建文件后的
下一次回复先写正文」守卫拒绝它，按守卫直接写 <file> 也只会落进聊天。

这里用真实的 writing_graph、runner（SDK 事件映射与钩子）、tools_adapter（守卫与熔断器）、
create_file、StreamAdapter（捕获与落库）、计费与回合回执，以及测试临时数据库；只把模型换成
按脚本输出的 SDK 事件流，路由换成固定的结构化决定。正文是合成文字。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session, select

from agent.core.round_outcome import RoundWrittenFiles
from agent.core.run_meter import AgentRunMeter
from agent.core.stream_billing import StreamBillingTracker
from agent.stream_adapter import StreamAdapter, StreamAdapterConfig
from agent.tools.mcp_tools import ToolContext
from models import File, Project, User
from tests.conftest import TestSessionLocal
from utils.text_metrics import count_words

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]

BODY = "\n".join(f"第{index}段合成正文，只用于核对落库、字数与聊天是否泄漏。" for index in range(1, 7))
CHAPTER = "第一章"


@dataclass
class Reply:
    """一次模型调用的脚本：先输出 text，再按顺序调用 tools。before 在这次调用开始前执行。"""

    text: str = ""
    tools: tuple[tuple[str, dict[str, Any]], ...] = ()
    before: Callable[[], None] | None = None
    # 输出完 text 之后一直等着（模拟仍在途的模型调用），用来测取消。
    hang: asyncio.Event | None = None


@dataclass
class ToolCall:
    name: str
    output: dict[str, Any]


@dataclass
class ScriptedModel:
    """每次 Runner.run_streamed 取下一组回复；工具调用走 SDK Agent 上真实的 FunctionTool。"""

    runs: list[list[Reply]]
    tool_calls: list[ToolCall] = field(default_factory=list)
    run_inputs: list[Any] = field(default_factory=list)
    _call_seq: int = 0

    def run_streamed(self, agent, *_args, input=None, hooks=None, **_kwargs):  # noqa: A002 - SDK kwarg
        self.run_inputs.append(input)
        replies = self.runs.pop(0) if self.runs else [Reply(text="（脚本外的多余一轮）")]
        return _ScriptedResult(self, agent, hooks, replies)

    def next_call_id(self) -> str:
        self._call_seq += 1
        return f"call-{self._call_seq}"

    def errors(self, error_type: str) -> list[ToolCall]:
        return [call for call in self.tool_calls if call.output.get("error_type") == error_type]


class _ScriptedResult:
    def __init__(self, model: ScriptedModel, agent: Any, hooks: Any, replies: list[Reply]) -> None:
        self._model = model
        self._agent = agent
        self._hooks = hooks
        self._replies = replies
        self.raw_responses: list[Any] = []

    def cancel(self, mode: str = "immediate") -> None:
        del mode

    def to_input_list(self) -> list[Any]:
        return []

    async def stream_events(self):
        tools = {tool.name: tool for tool in self._agent.tools}
        for reply in self._replies:
            if reply.before is not None:
                reply.before()
            await self._hooks.on_llm_start(None, self._agent, "", [])
            for start in range(0, len(reply.text), 17):
                yield SimpleNamespace(
                    type="raw_response_event",
                    data=SimpleNamespace(type="response.output_text.delta", delta=reply.text[start : start + 17]),
                )
            if reply.hang is not None:
                await reply.hang.wait()
            output = [SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text=reply.text)])]
            output += [SimpleNamespace(type="function_call") for _ in reply.tools]
            response = SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=10, output_tokens=5, total_tokens=15,
                    input_tokens_details=SimpleNamespace(cached_tokens=0),
                ),
                output=output,
            )
            self.raw_responses.append(response)
            await self._hooks.on_llm_end(None, self._agent, response)
            if not reply.tools:
                return  # 没有工具调用的回复就是这次 run 的最终输出
            results = []
            for name, arguments in reply.tools:
                call_id = self._model.next_call_id()
                raw_arguments = json.dumps(arguments, ensure_ascii=False)
                yield SimpleNamespace(
                    type="run_item_stream_event",
                    name="tool_called",
                    item=SimpleNamespace(
                        tool_name=name,
                        call_id=call_id,
                        raw_item={"name": name, "call_id": call_id, "arguments": raw_arguments},
                    ),
                )
                text = await tools[name].on_invoke_tool(None, raw_arguments)
                self._model.tool_calls.append(ToolCall(name, json.loads(text)))
                results.append(SimpleNamespace(tool=tools[name], output=text))
                yield SimpleNamespace(
                    type="run_item_stream_event",
                    name="tool_output",
                    item=SimpleNamespace(call_id=call_id, output=text),
                )
            if self._agent.tool_use_behavior(None, results).is_final_output:
                return  # 熔断 / 控制流工具：SDK 不再发起下一次模型调用


@dataclass
class Outcome:
    frames: list[Any]
    adapter: StreamAdapter
    state: dict[str, Any]

    def of(self, event_type: str) -> list[Any]:
        return [frame for frame in self.frames if frame.type.value == event_type]

    @property
    def chat_text(self) -> str:
        return "".join(str(frame.data.get("text") or "") for frame in self.of("content"))

    def file_text(self, file_id: str) -> str:
        return "".join(
            str(frame.data.get("chunk") or frame.data.get("content") or "")
            for frame in self.of("file_content")
            if frame.data.get("file_id") == file_id
        )

    def billing(self) -> tuple[str, bool]:
        tracker = StreamBillingTracker()
        for frame in self.frames:
            tracker.observe(frame.to_sse())
        return tracker.decide(client_disconnected=False, unexpected_exception=False)

    def delivery(self) -> dict[str, Any]:
        written = RoundWrittenFiles()
        for frame in self.frames:
            written.observe(frame.to_sse())
        return written.delivery(final_word_counts=self.adapter.get_saved_file_word_counts())


@pytest.fixture
def workspace(db_session: Session, monkeypatch):
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

    user = User(
        username="file-body-resume",
        email="file-body-resume@example.test",
        hashed_password="unused",
        email_verified=True,
    )
    project = Project(name="合成作品", owner_id=user.id, project_type="novel")
    db_session.add_all([user, project])
    db_session.commit()
    return SimpleNamespace(user=user, project=project, session=db_session)


def _routing(**overrides: Any) -> dict[str, Any]:
    routing = {
        "agent_type": "writer",
        "workflow_type": "quick",
        "reason": "synthetic",
        "confidence": 1.0,
        "read_only": False,
        "write_content": True,
        "scope": "写第一章正文",
        "requested_file_types": ["draft"],
    }
    routing.update(overrides)
    return routing


async def _run(workspace, model: ScriptedModel, *, routing: dict[str, Any] | None = None) -> Outcome:
    from agent.graph.writing_graph import run_writing_workflow_streaming

    message = "写第一章"
    meter = AgentRunMeter()
    state: dict[str, Any] = {
        "user_message": message,
        "router_message": message,
        "messages": [{"role": "user", "content": message}],
        "system_prompt": "synthetic system",
        "run_meter": meter,
        "project_id": workspace.project.id,
        "user_id": workspace.user.id,
        "session_id": "file-body-resume",
        "generation_mode": "fast",
    }
    router = AsyncMock(
        return_value={
            "current_agent": "writer",
            "workflow_plan": "quick",
            "workflow_agents": [],
            "routing_metadata": routing or _routing(),
        }
    )
    ToolContext.set_context(
        session=None,
        user_id=workspace.user.id,
        project_id=workspace.project.id,
        session_id="file-body-resume",
        create_session_func=TestSessionLocal,
        author_message=message,
        read_snapshots={},
    )
    adapter = StreamAdapter(
        StreamAdapterConfig(project_id=workspace.project.id, user_id=workspace.user.id, run_meter=meter)
    )
    try:
        with (
            patch.dict("os.environ", {"AGENT_ROUTER_STRATEGY": "llm"}),
            patch("agent.graph.writing_graph.router_node", router),
            patch("agents.Runner.run_streamed", side_effect=model.run_streamed),
            patch("agent.openai_agents.runner.build_failover_chat_model", return_value="synthetic-model"),
            patch("agent.openai_agents.runner._preferred_model_name", AsyncMock(return_value="synthetic-model")),
            patch("agent.openai_agents.usage_hooks.schedule_llm_usage_record"),
            patch(
                "agent.graph.writing_graph._auto_finalize_task_board_on_completion",
                AsyncMock(return_value=[]),
            ),
        ):
            frames = [
                frame
                async for frame in adapter.process_workflow_events(
                    run_writing_workflow_streaming(state=state, thread_id="file-body-resume")
                )
            ]
    finally:
        ToolContext.clear_context()
    return Outcome(frames=frames, adapter=adapter, state=state)


def _drafts(workspace) -> list[File]:
    workspace.session.expire_all()
    return list(
        workspace.session.exec(
            select(File).where(File.project_id == workspace.project.id, File.file_type == "draft")
        ).all()
    )


def _created_id(model: ScriptedModel) -> str:
    created = [call for call in model.tool_calls if call.name == "create_file"]
    assert len(created) == 1, "只建了一次文件"
    return created[0].output["data"]["id"]


CREATE = Reply(tools=(("create_file", {"title": CHAPTER, "file_type": "draft"}),))
# create_file 之后的那次回复没写正文就结束了 run（生产里的首个失败形态）。
NARRATE_ONLY = Reply(text="我先理一下这一章的节奏。")
WRITE_BODY = Reply(text=f"<file>\n{BODY}\n</file>\n\n第一章写好了。")


def _assert_body_saved_once(workspace, outcome: Outcome, model: ScriptedModel) -> str:
    file_id = _created_id(model)
    drafts = _drafts(workspace)
    assert [draft.id for draft in drafts] == [file_id], "没有重复创建、没有额外空文件"
    assert drafts[0].is_deleted is False
    assert drafts[0].content.strip() == BODY
    assert outcome.adapter.get_saved_file_word_counts() == {file_id: count_words(BODY)}
    assert any(frame.data.get("file_id") == file_id for frame in outcome.of("file_content_end"))
    # 正文与协议标记不进聊天
    assert "合成正文" not in outcome.chat_text
    assert "<file" not in outcome.chat_text and "</file" not in outcome.chat_text
    return file_id


async def test_correction_reply_streams_the_body_into_the_same_empty_file(workspace):
    model = ScriptedModel(runs=[[CREATE, NARRATE_ONLY], [WRITE_BODY]])

    outcome = await _run(workspace, model)

    file_id = _assert_body_saved_once(workspace, outcome, model)
    # 纠偏轮的指令点名同一个文件
    assert file_id in json.dumps(model.run_inputs[1], ensure_ascii=False)
    assert model.errors("file_body_pending") == []
    breaker = outcome.state["tool_failure_breaker"]
    assert breaker.is_open is False and breaker.total_failures == 0
    assert outcome.of("error") == []
    assert outcome.of("done")
    assert outcome.billing() == ("completed", False)
    delivery = outcome.delivery()
    assert delivery.get("unwritten_files") is None
    assert delivery["written_files"] == [{"id": file_id, "title": CHAPTER, "words": count_words(BODY)}]
    assert outcome.adapter._file_mutated is True


async def test_resumed_reply_still_refuses_edit_file_before_the_body(workspace):
    """守卫不放宽：纠偏轮里先调 edit_file 照样被拒、不执行；随后写 <file> 正文落库。"""
    attempt = Reply(tools=(("edit_file", {"id": "__target__", "edits": [{"op": "append", "text": "不应写入"}]}),))
    model = ScriptedModel(runs=[[CREATE, NARRATE_ONLY], [attempt, WRITE_BODY]])

    def bind_target_id() -> None:
        attempt.tools = (("edit_file", {"id": _created_id(model), "edits": [{"op": "append", "text": "不应写入"}]}),)

    attempt.before = bind_target_id

    outcome = await _run(workspace, model)

    _assert_body_saved_once(workspace, outcome, model)
    refused = model.errors("file_body_pending")
    assert [call.name for call in refused] == ["edit_file"]
    assert "不应写入" not in _drafts(workspace)[0].content
    breaker = outcome.state["tool_failure_breaker"]
    assert breaker.is_open is False and breaker.total_failures == 1


async def test_second_correction_resumes_the_same_file_once(workspace):
    """第一轮纠偏仍没写正文：第二次恢复同一个文件，正文只落一次。"""
    model = ScriptedModel(runs=[[CREATE, NARRATE_ONLY], [Reply(text="还在构思。")], [WRITE_BODY]])

    outcome = await _run(workspace, model)

    _assert_body_saved_once(workspace, outcome, model)
    assert len(model.run_inputs) == 3
    assert model.errors("file_body_pending") == []


def _author_renames(workspace, model: ScriptedModel) -> Callable[[], None]:
    def rename() -> None:
        from api import files as files_api

        files_api.update_file(
            _created_id(model),
            files_api.FileUpdate(title="作者改的标题"),
            current_user=workspace.user,
            session=workspace.session,
        )

    return rename


async def test_author_change_after_creation_blocks_the_resume(workspace):
    """建文件后作者动过这个文件（版本变了）：不恢复写入、不安排纠偏轮、不删作者的文件。"""
    model = ScriptedModel(runs=[[CREATE, NARRATE_ONLY], [WRITE_BODY]])
    NARRATE = Reply(text=NARRATE_ONLY.text, before=_author_renames(workspace, model))
    model.runs[0][1] = NARRATE

    outcome = await _run(workspace, model)

    assert len(model.run_inputs) == 1, "版本变了不跑纠偏轮"
    drafts = _drafts(workspace)
    assert len(drafts) == 1
    assert drafts[0].is_deleted is False and drafts[0].title == "作者改的标题"
    assert drafts[0].content == ""
    unfinished = [
        frame for frame in outcome.of("iteration_exhausted")
        if frame.data.get("layer") == "empty_file_correction"
    ]
    assert unfinished, "如实报告没写完的文件"
    assert "合成正文" not in outcome.chat_text


async def test_author_written_body_is_not_overwritten(workspace):
    """作者在等待期间写了正文：不纠偏、不恢复写入，作者的正文原样保留。"""
    model = ScriptedModel(runs=[[CREATE, NARRATE_ONLY], [WRITE_BODY]])

    def author_writes() -> None:
        from api import files as files_api

        files_api.update_file(
            _created_id(model),
            files_api.FileUpdate(content="作者自己写的开头。"),
            current_user=workspace.user,
            session=workspace.session,
        )

    model.runs[0][1] = Reply(text=NARRATE_ONLY.text, before=author_writes)

    outcome = await _run(workspace, model)

    assert len(model.run_inputs) == 1
    assert _drafts(workspace)[0].content == "作者自己写的开头。"
    assert outcome.adapter.get_saved_file_word_counts() == {}


async def test_deleted_target_is_not_resumed(workspace):
    model = ScriptedModel(runs=[[CREATE, NARRATE_ONLY], [WRITE_BODY]])

    def author_deletes() -> None:
        row = workspace.session.get(File, _created_id(model))
        row.is_deleted = True
        workspace.session.add(row)
        workspace.session.commit()

    model.runs[0][1] = Reply(text=NARRATE_ONLY.text, before=author_deletes)

    outcome = await _run(workspace, model)

    assert len(model.run_inputs) == 1
    workspace.session.expire_all()
    assert workspace.session.get(File, _created_id(model)).content == ""
    assert "合成正文" not in outcome.chat_text


async def test_pending_marker_for_another_projects_file_is_never_written(workspace):
    """待写标记指向别的项目的空文件：不恢复、不写、不删那个文件。"""
    other = Project(name="别的作品", owner_id=workspace.user.id, project_type="novel")
    workspace.session.add(other)
    workspace.session.commit()
    foreign = File(project_id=other.id, title="别处的空文件", file_type="draft", content="")
    workspace.session.add(foreign)
    workspace.session.commit()
    foreign_id = foreign.id

    def inject() -> None:
        ToolContext.set_pending_empty_file(foreign_id, "别处的空文件")

    model = ScriptedModel(runs=[[Reply(text="先看一下。", before=inject)], [WRITE_BODY]])

    outcome = await _run(workspace, model)

    assert len(model.run_inputs) == 1
    workspace.session.expire_all()
    stored = workspace.session.get(File, foreign_id)
    assert stored.content == "" and stored.is_deleted is False
    assert "合成正文" not in outcome.chat_text


@pytest.mark.parametrize("routing", [_routing(read_only=True), _routing(clarify_first=True)])
async def test_read_only_or_clarify_first_never_schedules_a_resume(workspace, routing):
    """只读 / 先问清楚：create_file 本身被拒；即使有待写标记也不安排纠偏、不写文件。"""
    target = File(project_id=workspace.project.id, title=CHAPTER, file_type="draft", content="")
    workspace.session.add(target)
    workspace.session.commit()
    target_id = target.id

    def inject() -> None:
        ToolContext.set_pending_empty_file(target_id, CHAPTER)

    model = ScriptedModel(runs=[[CREATE, Reply(text="只回答，不改文件。", before=inject)], [WRITE_BODY]])

    outcome = await _run(workspace, model, routing=routing)

    assert len(model.run_inputs) == 1
    assert [call.output["status"] for call in model.tool_calls] == ["error"]
    workspace.session.expire_all()
    assert workspace.session.get(File, target_id).content == ""
    assert "合成正文" not in outcome.chat_text


async def test_cancel_during_resumed_body_saves_only_the_streamed_part(workspace):
    """纠偏轮正文写到一半被取消：已串流的部分按保存回执落库，计数按实际保存。"""
    hang = asyncio.Event()
    partial = "\n".join(BODY.splitlines()[:3])
    model = ScriptedModel(runs=[[CREATE, NARRATE_ONLY], [Reply(text=f"<file>\n{partial}\n", hang=hang)]])
    seen: list[Any] = []
    holder: dict[str, StreamAdapter] = {}

    from agent.graph.writing_graph import run_writing_workflow_streaming

    message = "写第一章"
    meter = AgentRunMeter()
    state: dict[str, Any] = {
        "user_message": message,
        "router_message": message,
        "messages": [{"role": "user", "content": message}],
        "system_prompt": "synthetic system",
        "run_meter": meter,
        "generation_mode": "fast",
    }
    router = AsyncMock(
        return_value={
            "current_agent": "writer",
            "workflow_plan": "quick",
            "workflow_agents": [],
            "routing_metadata": _routing(),
        }
    )
    ToolContext.set_context(
        session=None,
        user_id=workspace.user.id,
        project_id=workspace.project.id,
        session_id="file-body-resume",
        create_session_func=TestSessionLocal,
        author_message=message,
        read_snapshots={},
    )
    adapter = StreamAdapter(
        StreamAdapterConfig(project_id=workspace.project.id, user_id=workspace.user.id, run_meter=meter)
    )
    holder["adapter"] = adapter
    body_started = asyncio.Event()

    async def consume() -> None:
        async for frame in adapter.process_workflow_events(
            run_writing_workflow_streaming(state=state, thread_id="file-body-resume")
        ):
            seen.append(frame)
            if frame.type.value == "file_content" and "合成正文" in str(frame.data.get("chunk") or ""):
                body_started.set()

    try:
        with (
            patch.dict("os.environ", {"AGENT_ROUTER_STRATEGY": "llm"}),
            patch("agent.graph.writing_graph.router_node", router),
            patch("agents.Runner.run_streamed", side_effect=model.run_streamed),
            patch("agent.openai_agents.runner.build_failover_chat_model", return_value="synthetic-model"),
            patch("agent.openai_agents.runner._preferred_model_name", AsyncMock(return_value="synthetic-model")),
            patch("agent.openai_agents.usage_hooks.schedule_llm_usage_record"),
        ):
            task = asyncio.create_task(consume())
            await asyncio.wait_for(body_started.wait(), timeout=10)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            counts = await adapter.settle_aborted_capture()
    finally:
        ToolContext.clear_context()

    file_id = _created_id(model)
    stored = _drafts(workspace)[0]
    assert stored.id == file_id
    assert stored.content.strip() == partial
    assert counts == {file_id: count_words(partial)}
    assert "合成正文" not in "".join(str(f.data.get("text") or "") for f in seen if f.type.value == "content")


# ---------------------------------------------------------------- StreamAdapter 只恢复自己在等的文件


def _bound_adapter(*, original_content_length: int = 0) -> StreamAdapter:
    adapter = StreamAdapter(StreamAdapterConfig(project_id="p", user_id="u"))
    adapter.set_pending_file_write(
        "f1", "draft", CHAPTER, original_content_length=original_content_length, baseline_updated_at="v1"
    )
    return adapter


async def _feed(adapter: StreamAdapter, event_type, data: dict[str, Any]) -> list[Any]:
    from agent.core.workflow_events import StreamEvent as WorkflowStreamEvent

    return [frame async for frame in adapter._process_workflow_event(WorkflowStreamEvent(type=event_type, data=data))]


async def _resume(adapter: StreamAdapter, file_id: str = "f1", version: str = "v1"):
    from agent.core.workflow_events import FileBodyResume, StreamEventType

    request = FileBodyResume(file_id=file_id, expected_updated_at=version)
    await _feed(adapter, StreamEventType.FILE_BODY_RESUME, {"request": request})
    return request


async def _end_agent_run(adapter: StreamAdapter) -> None:
    from agent.core.workflow_events import StreamEventType

    await _feed(adapter, StreamEventType.MESSAGE_END, {"stop_reason": "end_turn"})


@pytest.mark.parametrize(
    ("setup", "file_id", "version", "refusal"),
    [
        ("unbound", "f1", "v1", "not_bound"),
        ("bound", "f-other", "v1", "not_bound"),
        ("bound", "f1", "v2", "version_changed"),
        ("reused", "f1", "v1", "not_empty"),
    ],
)
async def test_adapter_refuses_files_it_is_not_safely_waiting_for(setup, file_id, version, refusal):
    from agent.core.stream_processor import StreamState

    if setup == "unbound":
        adapter = StreamAdapter(StreamAdapterConfig(project_id="p", user_id="u"))
    else:
        adapter = _bound_adapter(original_content_length=12 if setup == "reused" else 0)
        await _end_agent_run(adapter)

    request = await _resume(adapter, file_id, version)

    assert request.armed is False and request.refusal == refusal
    assert adapter._stream_processor.state == StreamState.IDLE


async def test_adapter_does_not_reset_a_capture_that_is_already_writing():
    from agent.core.stream_processor import StreamState
    from agent.core.workflow_events import StreamEventType

    adapter = _bound_adapter()
    await _feed(adapter, StreamEventType.TEXT, {"text": "<file>\n已经在写的正文"})

    request = await _resume(adapter)

    assert request.armed is False and request.refusal == "capture_active"
    assert adapter._stream_processor.state == StreamState.WRITING
    assert "已经在写的正文" in adapter._stream_processor.content_buffer + adapter._stream_processor.temp_buffer


async def test_adapter_refuses_after_a_fatal_stream_error():
    adapter = _bound_adapter()
    await _end_agent_run(adapter)
    adapter._fatal_stream_error = True

    request = await _resume(adapter)

    assert request.armed is False and request.refusal == "unavailable"


async def test_repeated_resume_is_idempotent_and_the_body_is_saved_once(monkeypatch):
    from agent.core.stream_processor import StreamState
    from agent.core.workflow_events import StreamEventType

    adapter = _bound_adapter()
    await _end_agent_run(adapter)
    saved: list[tuple[str, str]] = []

    async def fake_save(file_id, content, **_kwargs):
        saved.append((file_id, content))
        return True

    monkeypatch.setattr(adapter, "_save_file_content", fake_save)

    first = await _resume(adapter)
    second = await _resume(adapter)
    frames = await _feed(adapter, StreamEventType.TEXT, {"text": f"<file>\n{BODY}\n</file>\n"})

    assert first.armed and second.armed
    assert [(file_id, content.strip()) for file_id, content in saved] == [("f1", BODY)]
    assert adapter._stream_processor.state == StreamState.IDLE
    assert any(frame.type.value == "file_content_end" for frame in frames)
    assert all("合成正文" not in str(frame.data.get("text") or "") for frame in frames if frame.type.value == "content")
