"""空文件纠偏轮把正文写回同一个文件：跨层合成回归（不调用模型、不计费）。

create_file 建好空文件后，下一次回复没写正文 run 就结束了：MESSAGE_END 把捕获从 WAITING_START
复位成 IDLE；纠偏轮要为同一个文件恢复正文捕获，「建文件后的下一次回复先写正文」守卫仍然有效。

这里用真实的单循环 agent（loop/engine.py）、runner（SDK 事件映射与钩子）、tools_adapter（守卫与熔断器）、
create_file、StreamAdapter（捕获与落库）、计费与回合回执，以及测试临时数据库；只把模型换成
按脚本输出的 SDK 事件流。单循环 agent 的 create_file 只建资料文件（正文类走 write_chapter），这里用大纲文件。
正文是合成文字。
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
from agents import RunContextWrapper
from sqlmodel import Session, select

from agent.core.round_outcome import RoundWrittenFiles
from agent.core.run_meter import AgentRunMeter
from agent.core.stream_billing import StreamBillingTracker
from agent.stream_adapter import StreamAdapter, StreamAdapterConfig
from agent.tools.tool_context import AgentRequestContext
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
    # 这次调用的工具执行完后 SDK 撞上 max_turns（runner 据此发 tool_call 层的轮数耗尽）。
    exceeds_max_turns: bool = False
    # 这次调用的 output_tokens（usage）；写到 runner 的输出上限时模拟被 max_tokens 截断。
    output_tokens: int = 5


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

    def run_streamed(self, agent, *_args, input=None, hooks=None, context=None, **_kwargs):  # noqa: A002 - SDK kwarg
        self.run_inputs.append(input)
        replies = self.runs.pop(0) if self.runs else [Reply(text="（脚本外的多余一轮）")]
        return _ScriptedResult(self, agent, hooks, replies, context=context)

    def next_call_id(self) -> str:
        self._call_seq += 1
        return f"call-{self._call_seq}"

    def errors(self, error_type: str) -> list[ToolCall]:
        return [call for call in self.tool_calls if call.output.get("error_type") == error_type]


class _ScriptedResult:
    def __init__(self, model: ScriptedModel, agent: Any, hooks: Any, replies: list[Reply], *, context: Any = None) -> None:
        self._model = model
        self._agent = agent
        self._hooks = hooks
        self._replies = replies
        self._context = context
        self.raw_responses: list[Any] = []

    def cancel(self, mode: str = "immediate") -> None:
        del mode

    def to_input_list(self) -> list[Any]:
        return []

    async def stream_events(self):
        from services.usage.cost_budget import notify_model_transport_start

        run_ctx_wrapper = RunContextWrapper(self._context)
        tools = {tool.name: tool for tool in self._agent.tools}
        for reply in self._replies:
            if reply.before is not None:
                reply.before()
            await self._hooks.on_llm_start(run_ctx_wrapper, self._agent, "", [])
            notify_model_transport_start()
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
                    input_tokens=10, output_tokens=reply.output_tokens, total_tokens=10 + reply.output_tokens,
                    input_tokens_details=SimpleNamespace(cached_tokens=0),
                ),
                output=output,
            )
            self.raw_responses.append(response)
            await self._hooks.on_llm_end(run_ctx_wrapper, self._agent, response)
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
                tool_ctx = SimpleNamespace(context=self._context, tool_call_id=call_id)
                text = await tools[name].on_invoke_tool(tool_ctx, raw_arguments)
                self._model.tool_calls.append(ToolCall(name, json.loads(text)))
                results.append(SimpleNamespace(tool=tools[name], output=text))
                yield SimpleNamespace(
                    type="run_item_stream_event",
                    name="tool_output",
                    item=SimpleNamespace(call_id=call_id, output=text),
                )
            if reply.exceeds_max_turns:
                from agents.exceptions import MaxTurnsExceeded

                raise MaxTurnsExceeded("synthetic max_turns")
            if self._agent.tool_use_behavior(run_ctx_wrapper, results).is_final_output:
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


async def _run(
    workspace,
    model: ScriptedModel,
    *,
    meter: AgentRunMeter | None = None,
    request_context: AgentRequestContext | None = None,
    message: str = "写第一章",
) -> Outcome:
    from agent.loop.engine import run_loop_agent_streaming

    meter = meter or AgentRunMeter()
    req_ctx = request_context or AgentRequestContext(
        session=None,
        user_id=workspace.user.id,
        project_id=workspace.project.id,
        session_id="file-body-resume",
        create_session_func=TestSessionLocal,
        author_message=message,
        read_snapshots={},
    )
    state: dict[str, Any] = {
        "user_message": message,
        "router_message": message,
        "messages": [],
        "system_prompt": "synthetic system",
        "run_meter": meter,
        "project_id": workspace.project.id,
        "user_id": workspace.user.id,
        "session_id": "file-body-resume",
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
    try:
        with (
            patch("agents.Runner.run_streamed", side_effect=model.run_streamed),
            patch("agent.openai_agents.runner.build_failover_chat_model", return_value="synthetic-model"),
            patch("agent.openai_agents.runner._preferred_model_name", AsyncMock(return_value="synthetic-model")),
            patch("agent.openai_agents.usage_hooks.schedule_llm_usage_record"),
        ):
            frames = [
                frame async for frame in adapter.process_workflow_events(run_loop_agent_streaming(state))
            ]
    finally:
        req_ctx.clear()
    return Outcome(frames=frames, adapter=adapter, state=state)


def _drafts(workspace) -> list[File]:
    workspace.session.expire_all()
    return list(
        workspace.session.exec(
            select(File).where(File.project_id == workspace.project.id, File.file_type == "outline")
        ).all()
    )


def _created_id(model: ScriptedModel) -> str:
    created = [call for call in model.tool_calls if call.name == "create_file"]
    assert len(created) == 1, "只建了一次文件"
    return created[0].output["data"]["id"]


CREATE = Reply(tools=(("create_file", {"title": CHAPTER, "file_type": "outline"}),))
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
    """守卫不放宽：纠偏轮里先调 edit_file 照样被拒、不执行，并结束这一纠偏轮；
    第二次纠偏恢复同一个文件，写 <file> 正文落库（仍在既有纠偏配额内）。"""
    attempt = Reply(tools=(("edit_file", {"id": "__target__", "edits": [{"op": "append", "text": "不应写入"}]}),))
    model = ScriptedModel(runs=[[CREATE, NARRATE_ONLY], [attempt], [WRITE_BODY]])

    def bind_target_id() -> None:
        attempt.tools = (("edit_file", {"id": _created_id(model), "edits": [{"op": "append", "text": "不应写入"}]}),)

    attempt.before = bind_target_id

    outcome = await _run(workspace, model)

    _assert_body_saved_once(workspace, outcome, model)
    assert len(model.run_inputs) == 3, "被拒的纠偏轮结束后，第二次纠偏写正文"
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


class _ContinuedInputResult(_ScriptedResult):
    """to_input_list 像真实 SDK 一样返回这次的输入加上模型的回复（续跑走 _continued_run_input）。"""

    def __init__(self, model, agent, hooks, replies, run_input, *, context=None):
        super().__init__(model, agent, hooks, replies, context=context)
        self._input = run_input

    def to_input_list(self) -> list[Any]:
        items = list(self._input) if isinstance(self._input, list) else [{"role": "user", "content": str(self._input)}]
        return items + [{"role": "assistant", "content": reply.text} for reply in self._replies]


class _ContinuedInputModel(ScriptedModel):
    def run_streamed(self, agent, *_args, input=None, hooks=None, context=None, **_kwargs):  # noqa: A002 - SDK kwarg
        self.run_inputs.append(input)
        replies = self.runs.pop(0) if self.runs else [Reply(text="（脚本外的多余一轮）")]
        return _ContinuedInputResult(self, agent, hooks, replies, input, context=context)


async def test_every_correction_run_ends_on_a_fresh_instruction(workspace):
    """两次纠偏的说明文字相同：第二次续跑的输入仍以这条说明结尾，不停在上一条助手回复上。"""
    model = _ContinuedInputModel(runs=[[CREATE, NARRATE_ONLY], [Reply(text="还在构思。")], [WRITE_BODY]])

    await _run(workspace, model)

    assert len(model.run_inputs) == 3
    file_id = _created_id(model)
    for index in (1, 2):
        last = model.run_inputs[index][-1]
        assert last.get("role") == "user" and file_id in str(last.get("content"))


@pytest.mark.parametrize("blank", ["", "   \n  \n"])
async def test_empty_or_blank_body_keeps_the_file_pending_until_the_round_stops_unfinished(workspace, blank):
    """<file></file> 或只有空白：不算写成。文件仍待写，按有界纠偏补写；补写用完后回滚空文件、如实收尾，

    不能因为模型写了完成标记就按正常完成结束（也不再交接 / 审稿 / 宣告完成）。
    """
    blank_body = Reply(text=f"<file>{blank}</file>\n\n第一章写好了。[TASK_COMPLETE]")
    model = ScriptedModel(runs=[[CREATE, blank_body], [Reply(text=f"<file>{blank}</file>")], [Reply(text=f"<file>{blank}</file>")]])

    outcome = await _run(workspace, model)

    file_id = _created_id(model)
    assert len(model.run_inputs) == 3, "空白正文不解除待写，两次纠偏都照常安排"
    assert outcome.adapter.get_saved_file_word_counts() == {}
    assert not any(frame.data.get("file_id") == file_id for frame in outcome.of("file_content_end"))
    workspace.session.expire_all()
    stored = workspace.session.get(File, file_id)
    assert stored.content == "" and stored.is_deleted is True, "仍为空的本轮产物被回滚"
    assert outcome.of("workflow_complete") == [], "没写成就不宣告完成"
    # 前端认得的结构化未完成状态：哪份已撤销、哪份没能撤销（只有 id 与标题）。
    [card] = [f.data for f in outcome.of("iteration_exhausted") if f.data.get("layer") == "empty_file_correction"]
    assert card["rolled_back_files"] == [{"id": file_id, "title": CHAPTER}]
    assert card.get("unfinished_files") in (None, [])
    tracker = StreamBillingTracker()
    for frame in outcome.frames:
        tracker.observe(frame.to_sse())
    assert tracker.write_succeeded is False and tracker.produced_output is False
    delivery = outcome.delivery()
    assert delivery.get("written_files") is None
    assert delivery.get("unwritten_files") is None, "已撤销的空文件不再列为「未确认保存」"


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

    req_ctx = AgentRequestContext(
        session=None,
        user_id=workspace.user.id,
        project_id=workspace.project.id,
        session_id="file-body-resume",
        create_session_func=TestSessionLocal,
        author_message="写第一章",
        read_snapshots={},
    )

    def inject() -> None:
        req_ctx.set_pending_empty_file(foreign_id, "别处的空文件")

    model = ScriptedModel(runs=[[Reply(text="先看一下。", before=inject)], [WRITE_BODY]])

    outcome = await _run(workspace, model, request_context=req_ctx)

    assert len(model.run_inputs) == 1
    workspace.session.expire_all()
    stored = workspace.session.get(File, foreign_id)
    assert stored.content == "" and stored.is_deleted is False
    assert "合成正文" not in outcome.chat_text


async def test_cancel_during_resumed_body_saves_only_the_streamed_part(workspace):
    """纠偏轮正文写到一半被取消：已串流的部分按保存回执落库，计数按实际保存。"""
    hang = asyncio.Event()
    partial = "\n".join(BODY.splitlines()[:3])
    model = ScriptedModel(runs=[[CREATE, NARRATE_ONLY], [Reply(text=f"<file>\n{partial}\n", hang=hang)]])
    seen: list[Any] = []
    holder: dict[str, StreamAdapter] = {}

    from agent.loop.engine import run_loop_agent_streaming

    message = "写第一章"
    meter = AgentRunMeter()
    req_ctx = AgentRequestContext(
        session=None,
        user_id=workspace.user.id,
        project_id=workspace.project.id,
        session_id="file-body-resume",
        create_session_func=TestSessionLocal,
        author_message=message,
        read_snapshots={},
    )
    state: dict[str, Any] = {
        "user_message": message,
        "router_message": message,
        "messages": [],
        "system_prompt": "synthetic system",
        "run_meter": meter,
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
    holder["adapter"] = adapter
    body_started = asyncio.Event()

    async def consume() -> None:
        async for frame in adapter.process_workflow_events(run_loop_agent_streaming(state)):
            seen.append(frame)
            if frame.type.value == "file_content" and "合成正文" in str(frame.data.get("chunk") or ""):
                body_started.set()

    try:
        with (
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
        req_ctx.clear()

    file_id = _created_id(model)
    stored = _drafts(workspace)[0]
    assert stored.id == file_id
    assert stored.content.strip() == partial
    assert counts == {file_id: count_words(partial)}
    assert "合成正文" not in "".join(str(f.data.get("text") or "") for f in seen if f.type.value == "content")


# ---------------------------------------------------------------- StreamAdapter 只恢复自己在等的文件


@pytest.fixture
def guard() -> AgentRequestContext:
    """真实的待写守卫：f1 是本请求建好、还没写正文的文件。"""
    req_ctx = AgentRequestContext(session=None, user_id="u", project_id="p", session_id="s")
    req_ctx.begin_model_reply()
    req_ctx.set_pending_empty_file("f1", CHAPTER)
    try:
        yield req_ctx
    finally:
        req_ctx.clear()


def _bound_adapter(*, original_content_length: int = 0, request_context: AgentRequestContext | None = None) -> StreamAdapter:
    adapter = StreamAdapter(StreamAdapterConfig(project_id="p", user_id="u", request_context=request_context))
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
async def test_adapter_refuses_files_it_is_not_safely_waiting_for(guard, setup, file_id, version, refusal):
    from agent.core.stream_processor import StreamState

    if setup == "unbound":
        adapter = StreamAdapter(StreamAdapterConfig(project_id="p", user_id="u", request_context=guard))
    else:
        adapter = _bound_adapter(original_content_length=12 if setup == "reused" else 0, request_context=guard)
        await _end_agent_run(adapter)

    request = await _resume(adapter, file_id, version)

    assert request.armed is False and request.refusal == refusal
    assert adapter._stream_processor.state == StreamState.IDLE


async def test_adapter_does_not_reset_a_capture_that_is_already_writing(guard):
    from agent.core.stream_processor import StreamState
    from agent.core.workflow_events import StreamEventType

    adapter = _bound_adapter(request_context=guard)
    await _feed(adapter, StreamEventType.TEXT, {"text": "<file>\n已经在写的正文"})

    request = await _resume(adapter)

    assert request.armed is False and request.refusal == "capture_active"
    assert adapter._stream_processor.state == StreamState.WRITING
    assert "已经在写的正文" in adapter._stream_processor.content_buffer + adapter._stream_processor.temp_buffer


async def test_adapter_refuses_after_a_fatal_stream_error(guard):
    adapter = _bound_adapter(request_context=guard)
    await _end_agent_run(adapter)
    adapter._fatal_stream_error = True

    request = await _resume(adapter)

    assert request.armed is False and request.refusal == "unavailable"


async def test_repeated_resume_is_idempotent_and_the_body_is_saved_once(guard, monkeypatch):
    from agent.core.stream_processor import StreamState
    from agent.core.workflow_events import StreamEventType

    adapter = _bound_adapter(request_context=guard)
    await _end_agent_run(adapter)
    saved: list[tuple[str, str]] = []

    async def fake_save(file_id, content, **_kwargs):
        saved.append((file_id, content))
        return True

    monkeypatch.setattr(adapter, "_save_file_content", fake_save)

    # 上一程没被捕获的闭合块让守卫以为「正文已写出」：恢复后这个标记必须作废。
    guard.mark_latest_pending_body_streamed()
    first = await _resume(adapter)
    second = await _resume(adapter)
    guard.begin_model_reply()
    assert [entry["id"] for entry in guard.overdue_file_bodies()] == ["f1"], "恢复后下一次回复仍须先写正文"
    frames = await _feed(adapter, StreamEventType.TEXT, {"text": f"<file>\n{BODY}\n</file>\n"})

    assert first.armed and second.armed
    assert [(file_id, content.strip()) for file_id, content in saved] == [("f1", BODY)]
    assert adapter._stream_processor.state == StreamState.IDLE
    assert any(frame.type.value == "file_content_end" for frame in frames)
    assert all("合成正文" not in str(frame.data.get("text") or "") for frame in frames if frame.type.value == "content")


@pytest.mark.parametrize("case", ["no_request_context", "guard_cleared", "other_file_pending", "rearm_raises"])
async def test_adapter_does_not_arm_unless_the_exact_pending_guard_is_restored(case, monkeypatch):
    """armed 要同时证明捕获与这个文件的待写守卫都已恢复；守卫缺失 / 不对 / 恢复失败时不开捕获。"""
    from agent.core.stream_processor import StreamState

    req_ctx = None
    if case != "no_request_context":
        req_ctx = AgentRequestContext(session=None, user_id="u", project_id="p", session_id="s")
        req_ctx.set_pending_empty_file("f2" if case == "other_file_pending" else "f1", CHAPTER)
        if case == "guard_cleared":
            req_ctx.clear_pending_empty_file("f1")
        if case == "rearm_raises":
            def boom(_file_id):
                raise RuntimeError("synthetic guard failure")

            monkeypatch.setattr(req_ctx, "rearm_pending_file_body", boom)
    try:
        adapter = _bound_adapter(request_context=req_ctx)
        await _end_agent_run(adapter)

        request = await _resume(adapter)

        assert request.armed is False and request.refusal == "guard_missing"
        assert adapter._stream_processor.state == StreamState.IDLE
    finally:
        if req_ctx is not None:
            req_ctx.clear()


# ---------------------------------------------------------------- 补不成时的回滚只删没被动过的空产物


async def test_author_rename_during_the_last_failed_correction_keeps_the_file(workspace):
    """最后一次纠偏期间作者改了标题（正文仍空）：补不成也不软删作者动过的文件，如实报告未完成。

    （独立审查的跨层复现：此前回滚只比较正文，作者改名的空文件照样被删。）
    """
    last = Reply(text="<file>   </file>")
    model = ScriptedModel(runs=[[CREATE, NARRATE_ONLY], [Reply(text="还在构思。")], [last]])
    last.before = _author_renames(workspace, model)

    outcome = await _run(workspace, model)

    assert len(model.run_inputs) == 3
    drafts = _drafts(workspace)
    assert len(drafts) == 1
    assert drafts[0].title == "作者改的标题"
    assert drafts[0].is_deleted is False and drafts[0].content == ""
    [card] = [f.data for f in outcome.of("iteration_exhausted") if f.data.get("layer") == "empty_file_correction"]
    assert card["unfinished_files"] == [{"id": drafts[0].id, "title": CHAPTER}], "保留下来的文件报告为未完成"
    assert card.get("rolled_back_files") in (None, [])
    assert outcome.of("workflow_complete") == []


def _rollback_with(workspace, entries):
    from agent.core.empty_files import rollback_unfinished_empty_files

    req_ctx = AgentRequestContext(
        session=workspace.session, user_id=workspace.user.id, project_id=workspace.project.id, session_id="s"
    )
    try:
        return rollback_unfinished_empty_files(entries, request_context=req_ctx)
    finally:
        req_ctx.clear()


def _empty_draft(workspace, **fields):
    from agent.tools.file_ops.serialization import serialize_file

    draft = File(project_id=workspace.project.id, title=CHAPTER, file_type="draft", content="", **fields)
    workspace.session.add(draft)
    workspace.session.commit()
    workspace.session.refresh(draft)
    return draft, serialize_file(draft)["updated_at"]


def test_rollback_deletes_an_untouched_empty_artifact_with_its_creation_version(workspace):
    draft, created_at_version = _empty_draft(workspace)

    rolled_back = _rollback_with(workspace, [{"file_id": draft.id, "title": CHAPTER, "updated_at": created_at_version}])

    assert rolled_back == {draft.id}
    workspace.session.expire_all()
    assert workspace.session.get(File, draft.id).is_deleted is True


@pytest.mark.parametrize("case", ["no_creation_version", "changed_since_creation", "has_body", "other_project"])
def test_rollback_keeps_files_it_cannot_prove_untouched(workspace, case):
    draft, created_at_version = _empty_draft(workspace)
    entry = {"file_id": draft.id, "title": CHAPTER, "updated_at": created_at_version}
    if case == "no_creation_version":
        entry.pop("updated_at")
    elif case == "changed_since_creation":
        from api import files as files_api

        files_api.update_file(draft.id, files_api.FileUpdate(title="作者改的标题"), current_user=workspace.user, session=workspace.session)
    elif case == "has_body":
        draft.content = "作者写的一句。"
        workspace.session.add(draft)
        workspace.session.commit()
    else:
        other = Project(name="别的作品", owner_id=workspace.user.id)
        workspace.session.add(other)
        workspace.session.commit()
        draft.project_id = other.id
        workspace.session.add(draft)
        workspace.session.commit()

    rolled_back = _rollback_with(workspace, [entry])

    assert rolled_back == set()
    workspace.session.expire_all()
    assert workspace.session.get(File, draft.id).is_deleted is False


def test_rollback_keeps_a_file_changed_between_verification_and_delete(workspace, monkeypatch):
    """核验通过之后、软删之前文件被改了（版本前进）：带版本条件的 UPDATE 不命中，文件保留。"""
    from datetime import timedelta

    from sqlalchemy import text

    draft, created_at_version = _empty_draft(workspace)
    session = workspace.session
    original_exec = session.exec
    calls = {"n": 0}

    def exec_with_concurrent_change(statement, *args, **kwargs):
        result = original_exec(statement, *args, **kwargs)
        calls["n"] += 1
        if calls["n"] == 1:  # 核验读完之后，另一个写入把版本推前
            session.connection().execute(
                text("UPDATE file SET updated_at = :later WHERE id = :id"),
                {"later": draft.updated_at + timedelta(seconds=5), "id": draft.id},
            )
        return result

    monkeypatch.setattr(session, "exec", exec_with_concurrent_change)

    rolled_back = _rollback_with(workspace, [{"file_id": draft.id, "title": CHAPTER, "updated_at": created_at_version}])
    monkeypatch.undo()

    assert rolled_back == set()
    session.expire_all()
    assert session.get(File, draft.id).is_deleted is False


# ------------------------------------- 只有本轮新建的空文件可以回滚：复用的既有空白剧集不是本轮产物


def _blank_episode(workspace) -> tuple[File, File]:
    """剧本作品里作者此前就有的空白第1集（本轮之前建好、从未写过）。"""
    workspace.project.project_type = "screenplay"
    workspace.session.add(workspace.project)
    folder = File(id=f"{workspace.project.id}-script-folder", project_id=workspace.project.id, title="剧本",
                  content="", file_type="folder")
    workspace.session.add(folder)
    workspace.session.flush()
    episode = File(project_id=workspace.project.id, parent_id=folder.id, title="第1集", content="",
                   file_type="script", order=1)
    workspace.session.add(episode)
    workspace.session.commit()
    workspace.session.refresh(episode)
    return folder, episode


def test_reused_blank_episode_is_writable_but_never_a_rollback_target(workspace):
    """create_file 复用了已有的空白剧集：照样等正文，但没有「本轮建好时的版本」，回滚不删它。

    （独立审查的合成复现：复用结果的版本被当成本轮建档版本，从未动过的旧空白剧集被软删。）
    """
    from agent.core.empty_files import rollback_unfinished_empty_files, with_creation_versions
    from agent.tools.mcp_tools import _create_file_sync

    folder, episode = _blank_episode(workspace)
    req_ctx = AgentRequestContext(
        session=workspace.session,
        user_id=workspace.user.id,
        project_id=workspace.project.id,
        session_id="s",
        author_message="写第1集",
        read_snapshots={},
    )
    try:
        _create_file_sync({"title": "第1集", "file_type": "script", "parent_id": folder.id, "order": 1}, req_ctx)
        # 复用的就是这一集（没有另建），它仍在等正文。
        assert req_ctx.get_pending_empty_files() == [{"file_id": episode.id, "title": "第1集"}]
        rolled_back = rollback_unfinished_empty_files(
            with_creation_versions(req_ctx.get_pending_empty_files(), request_context=req_ctx),
            request_context=req_ctx,
        )
    finally:
        req_ctx.clear()

    assert rolled_back == set()
    workspace.session.expire_all()
    assert workspace.session.get(File, episode.id).is_deleted is False


# ------------------------------- 工具调用轮数耗尽时还有空文件：同一张结构化卡片带上这些文件的最终事实


def _rejected_lookup(before: Callable[[], None] | None = None) -> Reply:
    """建文件后的下一次回复没写正文就去查资料，随后撞上 max_turns。"""
    return Reply(text="我先查一下前文。", tools=(("search_files", {"query": "前文"}),), before=before,
                 exceeds_max_turns=True)


def _exhaustion_card(outcome: Outcome) -> dict[str, Any]:
    [card] = outcome.of("iteration_exhausted")
    assert card.data["layer"] == "tool_call", "层不变：计费仍按失控停止识别"
    return card.data


def _assert_round_stops_without_completion(outcome: Outcome) -> None:
    assert outcome.of("workflow_complete") == [] and outcome.of("handoff") == []
    assert "撤销" not in outcome.chat_text, "文件事实在卡片里，不再另发一段文字说明"
    assert outcome.of("done")


async def test_tool_turn_cap_with_an_untouched_new_empty_file_rolls_it_back_on_the_card(workspace):
    model = ScriptedModel(runs=[[CREATE, _rejected_lookup()]])

    outcome = await _run(workspace, model)

    file_id = _created_id(model)
    workspace.session.expire_all()
    assert workspace.session.get(File, file_id).is_deleted is True
    card = _exhaustion_card(outcome)
    assert card["rolled_back_files"] == [{"id": file_id, "title": CHAPTER}]
    assert card.get("unfinished_files") in (None, [])
    _assert_round_stops_without_completion(outcome)
    assert outcome.delivery() == {}, "已撤销的空文件不再列为「未确认保存」"
    assert outcome.billing() == ("completed", False), "已进入模型执行的工具轮数耗尽照常计费"
    # 真实的 tool_result 帧带着建好时的版本：停止退还的占位候选拿得到它。
    tracker = StreamBillingTracker()
    for frame in outcome.frames:
        tracker.observe(frame.to_sse())
    created_version = model.tool_calls[0].output["data"]["updated_at"]
    assert tracker.removable_placeholders() == [{"id": file_id, "title": CHAPTER, "updated_at": created_version}]


async def test_tool_turn_cap_keeps_a_new_empty_file_the_author_renamed_and_names_it_unfinished(workspace):
    model = ScriptedModel(runs=[[CREATE]])
    model.runs[0].append(_rejected_lookup(before=_author_renames(workspace, model)))

    outcome = await _run(workspace, model)

    file_id = _created_id(model)
    workspace.session.expire_all()
    kept = workspace.session.get(File, file_id)
    assert kept.is_deleted is False and kept.title == "作者改的标题"
    card = _exhaustion_card(outcome)
    assert card["unfinished_files"] == [{"id": file_id, "title": CHAPTER}]
    assert card.get("rolled_back_files") in (None, [])
    _assert_round_stops_without_completion(outcome)
    assert outcome.delivery() == {"unwritten_files": [{"id": file_id, "title": CHAPTER}]}
    assert outcome.billing() == ("completed", False)


# ---------------------------------------------------------------------------
# 2026-10-25 事故：待补正文被拒后应回到纠偏，而不是盲试到熔断；待补期间只能取消那份文件
# ---------------------------------------------------------------------------

NEXT_CREATE = ("create_file", {"title": "第二章", "file_type": "outline"})


def _pending_draft_id(model: ScriptedModel) -> str:
    created = [call for call in model.tool_calls if call.name == "create_file" and call.output.get("status") == "success"]
    assert len(created) == 1, "只有建第一章那次 create_file 执行了"
    return created[0].output["data"]["id"]


def _assert_pending_body_saved_once(workspace, outcome: Outcome, model: ScriptedModel) -> str:
    file_id = _pending_draft_id(model)
    stored = workspace.session.get(File, file_id)
    workspace.session.refresh(stored)
    assert stored.is_deleted is False and stored.content.strip() == BODY
    assert outcome.adapter.get_saved_file_word_counts() == {file_id: count_words(BODY)}
    assert [f.data.get("file_id") for f in outcome.of("file_content_end")] == [file_id]
    assert "合成正文" not in outcome.chat_text
    return file_id


def _seed_written_draft(workspace, title: str = "第三章", parent_id: str | None = None) -> File:
    draft = File(project_id=workspace.project.id, title=title, file_type="draft",
                 content="这一章已经写好的合成正文。", parent_id=parent_id)
    workspace.session.add(draft)
    workspace.session.commit()
    workspace.session.refresh(draft)
    return draft


async def test_first_pending_refusal_ends_the_run_and_the_correction_writes_the_same_file(workspace):
    """事故形态：建好第一章后没写正文，连续重试建第二章。首次拒绝后就交回纠偏轮，不再盲试到熔断。"""
    retry = Reply(tools=(NEXT_CREATE,))
    model = ScriptedModel(runs=[[CREATE, retry, retry, retry], [WRITE_BODY]])

    outcome = await _run(workspace, model)

    file_id = _assert_pending_body_saved_once(workspace, outcome, model)
    assert len(model.errors("file_body_pending")) == 1, "被拒一次就结束当前 run"
    assert len(model.run_inputs) == 2
    assert file_id in json.dumps(model.run_inputs[1], ensure_ascii=False), "纠偏轮点名同一个文件"
    assert [d.title for d in _drafts(workspace)] == [CHAPTER], "第二章没有被建出来"
    breaker = outcome.state["tool_failure_breaker"]
    assert breaker.is_open is False and breaker.total_failures == 1
    assert outcome.of("error") == []
    assert len(outcome.of("done")) == 1
    assert outcome.billing() == ("completed", False)
    # 三次模型调用的真实用量都入账（被提前结束的 run 也发了正常 MESSAGE_END）
    assert outcome.adapter.get_last_message_metadata()["usage"]["input_tokens"] == 30
    failures = outcome.state["run_meter"].timing_report()["tool_failures"]
    assert failures == {"create_file": {"count": 1, "rolled_back_batches": 0, "error_types": {"file_body_pending": 1}}}


async def test_pending_refusal_and_a_delete_of_another_draft_in_one_batch_deletes_nothing(workspace):
    other = _seed_written_draft(workspace)
    other_id, other_version = other.id, other.updated_at
    batch = Reply(tools=(("delete_file", {"id": other_id}), NEXT_CREATE))
    model = ScriptedModel(runs=[[CREATE, batch], [WRITE_BODY]])

    outcome = await _run(workspace, model)

    workspace.session.expire_all()
    kept = workspace.session.get(File, other_id)
    assert kept.is_deleted is False and kept.content == "这一章已经写好的合成正文。"
    assert kept.updated_at == other_version
    assert [call.name for call in model.errors("file_body_pending")] == ["delete_file", "create_file"]
    _assert_pending_body_saved_once(workspace, outcome, model)
    assert outcome.of("error") == []


async def test_recursive_delete_of_the_pending_files_folder_is_refused(workspace):
    folder = File(project_id=workspace.project.id, title="正文", file_type="folder", content="")
    workspace.session.add(folder)
    workspace.session.commit()
    sibling = _seed_written_draft(workspace, parent_id=folder.id)
    folder_id, sibling_id = folder.id, sibling.id
    create_in_folder = Reply(tools=(("create_file", {"title": CHAPTER, "file_type": "outline", "parent_id": folder_id}),))
    model = ScriptedModel(runs=[[create_in_folder, Reply(tools=(("delete_file", {"id": folder_id, "recursive": True}),))],
                                [WRITE_BODY]])

    outcome = await _run(workspace, model)

    workspace.session.expire_all()
    assert workspace.session.get(File, folder_id).is_deleted is False
    assert workspace.session.get(File, sibling_id).is_deleted is False
    _assert_pending_body_saved_once(workspace, outcome, model)


async def test_cancelling_the_exact_pending_file_settles_the_adapter_and_the_guard(workspace):
    """精确取消待补文件：真的删掉它；同一 run 之后的 <file> 不再被已删文件的绑定吞掉，边界处守卫清除、不纠偏。"""
    cancel = Reply(tools=(("delete_file", {"id": "__target__"}),))
    # 取消后同一 run 里又写了一个 <file> 块（之后有收尾文字，不触发「以 </file> 结尾就续写」）。
    after_cancel = Reply(text="<file>\n另一段合成正文。\n</file>\n\n第一章先不写了。")
    model = ScriptedModel(runs=[[CREATE, cancel, after_cancel], [WRITE_BODY]])

    def bind_target_id() -> None:
        cancel.tools = (("delete_file", {"id": _created_id(model)}),)

    cancel.before = bind_target_id

    outcome = await _run(workspace, model)

    file_id = _created_id(model)
    workspace.session.expire_all()
    stored = workspace.session.get(File, file_id)
    assert stored.is_deleted is True and stored.content == ""
    assert outcome.adapter.get_saved_file_word_counts() == {}
    assert outcome.of("file_content_end") == []
    assert "另一段合成正文" in outcome.chat_text, "等待中的缓冲按既有语义回到对话"
    assert len(model.run_inputs) == 1, "已取消的文件不安排纠偏轮"
    assert outcome.of("iteration_exhausted") == []
    assert outcome.of("error") == [] and len(outcome.of("done")) == 1


async def test_no_resume_is_armed_when_the_round_is_out_of_time(workspace):
    """run 边界核验空文件时请求时间已不够再跑一轮：不先恢复捕获，按既有路径回滚并报告未完成。

    模型调用预算在 run 内用完时由 runner 的上限终态结束（见下一个用例）。
    """
    meter = AgentRunMeter(wrap_up_after_s=0)
    model = ScriptedModel(runs=[[CREATE, NARRATE_ONLY], [WRITE_BODY]])

    outcome = await _run(workspace, model, meter=meter)

    file_id = _created_id(model)
    assert len(model.run_inputs) == 1, "不跑纠偏轮"
    workspace.session.expire_all()
    assert workspace.session.get(File, file_id).is_deleted is True
    [card] = [f.data for f in outcome.of("iteration_exhausted") if f.data.get("layer") == "empty_file_correction"]
    assert card["rolled_back_files"] == [{"id": file_id, "title": CHAPTER}]
    assert outcome.of("workflow_complete") == []
    assert len(outcome.of("done")) == 1


async def test_model_call_budget_terminal_keeps_priority_over_the_pending_correction(workspace):
    """预算在 run 内用完时 runner 以既有的模型调用上限终态结束，不进纠偏。"""
    meter = AgentRunMeter(max_model_calls=2)

    def use_up_model_calls() -> None:
        meter.model_calls = meter.max_model_calls  # 脚本模型不经过 runner 的计数过滤器

    model = ScriptedModel(runs=[[CREATE, Reply(tools=(NEXT_CREATE,), before=use_up_model_calls)], [WRITE_BODY]])

    outcome = await _run(workspace, model, meter=meter)

    assert len(model.run_inputs) == 1
    [error] = outcome.of("error")
    assert error.data["code"] == "ERR_AGENT_MODEL_CALL_LIMIT"
    assert outcome.of("done") == []


async def test_request_failure_cap_still_stops_before_any_correction(workspace):
    """前置拒绝仍计入请求累计 10 次上限：到了上限就按熔断终止，不为纠偏放宽总预算。"""
    # edit_file 的无效 ID 已在第三次按恢复失败族停止；这里用彼此不同的改名失败，
    # 单独锁住“九次普通失败 + 一次待写前置拒绝”的请求累计兜底。
    misses = Reply(tools=tuple(
        ("rename_file", {"id": f"missing-{index}", "title": "新标题"}) for index in range(9)
    ))
    model = ScriptedModel(runs=[[misses, CREATE, Reply(tools=(NEXT_CREATE,))], [WRITE_BODY]])

    outcome = await _run(workspace, model)

    breaker = outcome.state["tool_failure_breaker"]
    assert breaker.is_open and breaker.trip.reason == "too_many_tool_failures"
    assert len(model.run_inputs) == 1, "熔断优先，不再安排纠偏轮"
    assert len(outcome.of("error")) == 1 and outcome.of("done") == []


async def test_refusals_in_every_correction_round_end_unfinished_and_roll_back(workspace):
    """纠偏轮里每次都先调冲突写工具被拒：用完既有纠偏配额后回滚空文件、如实报告未完成，不熔断、不宣告完成。"""
    conflicting = Reply(tools=(NEXT_CREATE,))
    model = ScriptedModel(runs=[[CREATE, NARRATE_ONLY], [conflicting], [conflicting], [WRITE_BODY]])

    outcome = await _run(workspace, model)

    file_id = _pending_draft_id(model)
    assert len(model.run_inputs) == 3, "只用既有的 2 次纠偏配额"
    assert len(model.errors("file_body_pending")) == 2
    workspace.session.expire_all()
    assert workspace.session.get(File, file_id).is_deleted is True
    [card] = [f.data for f in outcome.of("iteration_exhausted") if f.data.get("layer") == "empty_file_correction"]
    assert card["rolled_back_files"] == [{"id": file_id, "title": CHAPTER}]
    assert outcome.state["tool_failure_breaker"].is_open is False
    assert outcome.of("error") == [] and outcome.of("workflow_complete") == []
    assert len(outcome.of("done")) == 1


async def test_non_conflicting_query_files_during_pending_body_stays_in_same_run(workspace):
    """待写正文期间调用只读 search_files 正常执行并附带 pending_file_reminder，不触发 file_body_pending 拆轮。"""
    lookup = Reply(tools=(("search_files", {"query": "前文"}),))
    model = ScriptedModel(runs=[[CREATE, lookup, WRITE_BODY]])

    outcome = await _run(workspace, model)

    _assert_body_saved_once(workspace, outcome, model)
    assert len(model.run_inputs) == 1, "不因只读查询触发 FILE_BODY_RESUME 拆轮"
    assert model.errors("file_body_pending") == []
    query_calls = [call for call in model.tool_calls if call.name == "search_files"]
    assert len(query_calls) == 1
    assert query_calls[0].output["status"] == "success"
    assert CHAPTER in query_calls[0].output["pending_file_reminder"]
    breaker = outcome.state["tool_failure_breaker"]
    assert breaker.is_open is False and breaker.total_failures == 0


async def test_same_target_create_file_repeat_absorbs_in_same_run(workspace):
    """待写正文期间重复 create_file 同一个文件：走既有幂等吸收，不触发 file_body_pending 拆轮，不建重名空文件。"""
    model = ScriptedModel(runs=[[CREATE, CREATE, WRITE_BODY]])

    outcome = await _run(workspace, model)

    drafts = _drafts(workspace)
    assert len(drafts) == 1
    assert drafts[0].is_deleted is False
    assert drafts[0].content.strip() == BODY
    assert len(model.run_inputs) == 1
    assert model.errors("file_body_pending") == []
    creates = [call for call in model.tool_calls if call.name == "create_file"]
    assert len(creates) == 2
    assert creates[1].output["status"] == "success"
    assert creates[1].output["mutation_applied"] is False
    assert creates[1].output["data"]["id"] == creates[0].output["data"]["id"]
    assert creates[1].output["data"]["awaiting_body"] is True
    assert "不要再调用 create_file" in creates[1].output["data"]["next_step"]
    assert outcome.state["tool_failure_breaker"].total_failures == 0


async def test_same_chapter_write_chapter_repeat_absorbs_in_same_run(workspace):
    """待写正文期间重复 write_chapter 同一章：走既有吸收逻辑，不触发 file_body_pending 拆轮，不建重复章节。"""
    write_ch1 = Reply(tools=(("write_chapter", {"chapter": 1, "title": "开篇", "file_type": "draft"}),))
    # draft 正文保存后会正常进入 1 次保存后自查续跑（不是 FILE_BODY_RESUME 纠偏轮）。
    model = ScriptedModel(runs=[[write_ch1, write_ch1, WRITE_BODY], [Reply(text="自查完成。")]])

    outcome = await _run(workspace, model)

    workspace.session.expire_all()
    saved_drafts = list(
        workspace.session.exec(
            select(File).where(File.project_id == workspace.project.id, File.file_type == "draft")
        ).all()
    )
    assert len(saved_drafts) == 1
    assert saved_drafts[0].is_deleted is False
    assert saved_drafts[0].content.strip() == BODY
    assert len(model.run_inputs) == 2
    assert "自查" in json.dumps(model.run_inputs[1], ensure_ascii=False)
    assert model.errors("file_body_pending") == []
    wc_calls = [call for call in model.tool_calls if call.name == "write_chapter"]
    assert len(wc_calls) == 2
    assert wc_calls[1].output["status"] == "success"
    assert wc_calls[1].output["mutation_applied"] is False
    assert wc_calls[1].output["data"]["id"] == wc_calls[0].output["data"]["id"]
    assert wc_calls[1].output["data"]["awaiting_body"] is True
    assert "不要再调用 write_chapter" in wc_calls[1].output["data"]["next_step"]
    assert outcome.state["tool_failure_breaker"].total_failures == 0


async def test_after_an_exact_cancel_the_same_run_goes_on_to_create_and_write_the_next_file(workspace):
    """取消待补文件后，同一个 run 里建下一份并写完正文：不再被已删文件的守卫拒绝、不被提前结束成「完成」。"""
    cancel = Reply(tools=(("delete_file", {"id": "__target__"}),))
    write_next = Reply(text=f"<file>\n{BODY}\n</file>\n\n第二章写好了。")
    model = ScriptedModel(runs=[[CREATE, cancel, Reply(tools=(NEXT_CREATE,)), write_next]])

    def bind_target_id() -> None:
        cancel.tools = (("delete_file", {"id": _created_id(model)}),)

    cancel.before = bind_target_id

    outcome = await _run(workspace, model)

    assert model.errors("file_body_pending") == []
    assert len(model.run_inputs) == 1
    created = [call.output["data"]["id"] for call in model.tool_calls if call.name == "create_file"]
    first_id, second_id = created
    workspace.session.expire_all()
    assert workspace.session.get(File, first_id).is_deleted is True
    assert workspace.session.get(File, second_id).content.strip() == BODY
    assert outcome.adapter.get_saved_file_word_counts() == {second_id: count_words(BODY)}
    assert outcome.of("error") == [] and len(outcome.of("done")) == 1


async def test_armed_resume_tells_the_editor_again_that_the_file_is_streaming(guard):
    """恢复捕获时立刻补发 file_created：上一程的 file_content_end 已解除编辑器的流式锁，
    纠偏轮写 <file> 之前的思考期间不能让作者未保存的文字自动保存推进版本、作废重试。"""
    from agent.core.workflow_events import FileBodyResume, StreamEventType

    adapter = _bound_adapter(request_context=guard)
    await _end_agent_run(adapter)

    request = FileBodyResume(file_id="f1", expected_updated_at="v1")
    frames = await _feed(adapter, StreamEventType.FILE_BODY_RESUME, {"request": request})

    assert request.armed is True
    created = [frame for frame in frames if frame.type.value == "file_created"]
    assert len(created) == 1
    assert created[0].data.get("file_id") == "f1" and created[0].data.get("title") == CHAPTER

    refused = FileBodyResume(file_id="f1", expected_updated_at="v2")
    adapter_refusing = _bound_adapter(request_context=guard)
    await _end_agent_run(adapter_refusing)
    refused_frames = await _feed(adapter_refusing, StreamEventType.FILE_BODY_RESUME, {"request": refused})
    assert refused.armed is False
    assert not [frame for frame in refused_frames if frame.type.value == "file_created"]


@pytest.mark.parametrize(
    "leaked_prefix",
    [
        "]<]minimax[>[<tool_call>",
        "<tool_call>",
        "]<]minimax[>[tool_call>",
    ],
)
async def test_leaked_tool_call_in_waiting_start_stops_early_and_recovers_via_file_body_resume(
    workspace, leaked_prefix
):
    """WAITING_START 开头吐出泄漏 tool_call 协议头时立即早停取消，不消费后续上万字符，
    立即走 FILE_BODY_RESUME 纠偏写完章节；聊天零泄漏、用量入账、wire_prefix_cache 干净。"""
    from agent.loop.wire_prefix_cache import is_well_formed_wire_prefix

    cancelled_modes: list[str] = []
    streamed_chars_before_cancel = 0

    class _TrackingResult(_ScriptedResult):
        is_complete: bool = False

        def cancel(self, mode: str = "immediate") -> None:
            cancelled_modes.append(mode)
            if mode == "immediate":
                self.is_complete = True

        def to_input_list(self) -> list[Any]:
            # Simulate SDK returning the completed turn-1 items (up through write_chapter output)
            # without the cancelled in-flight turn-2 leaked text.
            return [
                {"role": "user", "content": "写第一章"},
                {"type": "function_call", "call_id": "call-wc-1", "name": "write_chapter", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call-wc-1", "output": '{"status":"success"}'},
            ]

        async def stream_events(self):
            nonlocal streamed_chars_before_cancel
            async for ev in super().stream_events():
                if getattr(ev, "type", "") == "raw_response_event":
                    delta = getattr(getattr(ev, "data", None), "delta", "")
                    if isinstance(delta, str) and not cancelled_modes:
                        streamed_chars_before_cancel += len(delta)
                yield ev

    class _TrackingModel(ScriptedModel):
        def run_streamed(self, agent, *_args, input=None, hooks=None, context=None, **_kwargs):  # noqa: A002
            self.run_inputs.append(input)
            replies = self.runs.pop(0) if self.runs else [Reply(text="完成")]
            return _TrackingResult(self, agent, hooks, replies, context=context)

    write_ch = Reply(tools=(("write_chapter", {"chapter": 1, "title": "第一章 开篇", "file_type": "draft"}),))
    # Simulate a 10,000-char silent black hole after the leaked marker
    leaked_reply = Reply(text=f"\n  {leaked_prefix}\n" + ('{"name":"query_files","arguments":{}}' * 300))
    recovered_body = Reply(text=f"<file>\n{BODY}\n</file>\n\n第一章已写完。")
    self_check_done = Reply(text="自查过了，没有需要改的地方。")

    model = _TrackingModel(runs=[[write_ch, leaked_reply], [recovered_body], [self_check_done]])
    outcome = await _run(workspace, model)

    assert cancelled_modes == ["immediate"], "应在命中泄漏协议头时立即取消当前流"
    assert streamed_chars_before_cancel < 120, f"应远早于 10k 字符黑洞早停，实际读了 {streamed_chars_before_cancel} 字"
    assert len(model.run_inputs) >= 2, "早停后应立即进入 FILE_BODY_RESUME 纠偏轮"
    # Second run input keeps turn-1 wire items + correction prompt
    assert any(
        isinstance(item, dict) and item.get("type") == "function_call_output" and item.get("call_id") == "call-wc-1"
        for item in model.run_inputs[1]
    )
    assert leaked_prefix not in outcome.chat_text
    assert "<tool_call>" not in outcome.chat_text
    assert outcome.of("error") == [] and len(outcome.of("done")) == 1

    wc_call = next(call for call in model.tool_calls if call.name == "write_chapter")
    file_id = wc_call.output["data"]["id"]
    # Quick Win #1: next_step carries short-thinking & no-extra-tool-call guidance
    next_step = wc_call.output["data"]["next_step"]
    assert "构思已在调用前完成" in next_step
    assert "<file>" in next_step
    assert "read_files" in next_step

    workspace.session.expire_all()
    row = workspace.session.get(File, file_id)
    assert row is not None and row.content.strip() == BODY
    assert outcome.adapter.get_saved_file_word_counts() == {file_id: count_words(BODY)}
    metadata = outcome.adapter.get_last_message_metadata()
    assert metadata["stop_reason"] == "end_turn"
    assert isinstance(metadata["usage"], dict) and metadata["usage"].get("input_tokens", 0) >= 20
    # Verify completed run_items at turn end (or sanitized items) never violate wire_prefix_cache contract
    assert is_well_formed_wire_prefix([
        {"role": "user", "content": "写第一章"},
        {"type": "function_call", "call_id": "call-wc-1", "name": "write_chapter", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call-wc-1", "output": '{"status":"success"}'},
        {"role": "assistant", "content": "第一章已写完。"},
    ])


async def test_legitimate_file_prose_mentioning_tool_call_never_triggers_early_stop(workspace):
    """合法 <file> 正文里提到 <tool_call>（或代码围栏里的示例）绝不触发早停。"""
    cancelled_modes: list[str] = []

    class _NoCancelResult(_ScriptedResult):
        def cancel(self, mode: str = "immediate") -> None:
            cancelled_modes.append(mode)

    class _NoCancelModel(ScriptedModel):
        def run_streamed(self, agent, *_args, input=None, hooks=None, context=None, **_kwargs):  # noqa: A002
            self.run_inputs.append(input)
            replies = self.runs.pop(0) if self.runs else [Reply(text="完成")]
            return _NoCancelResult(self, agent, hooks, replies, context=context)

    write_ch = Reply(tools=(("write_chapter", {"chapter": 1, "title": "第一章 日志", "file_type": "draft"}),))
    prose_with_tag = f"{BODY}\n屏幕上闪过一行 <tool_call> 的调试字样。"
    legit_reply = Reply(text=f"<file>\n{prose_with_tag}\n</file>\n\n写完了。")
    self_check_done = Reply(text="自查过了，没有需要改的地方。")

    model = _NoCancelModel(runs=[[write_ch, legit_reply], [self_check_done]])
    outcome = await _run(workspace, model)

    assert cancelled_modes == []
    wc_call = next(call for call in model.tool_calls if call.name == "write_chapter")
    file_id = wc_call.output["data"]["id"]
    workspace.session.expire_all()
    assert workspace.session.get(File, file_id).content.strip() == prose_with_tag
    assert outcome.of("error") == [] and len(outcome.of("done")) == 1


async def test_write_chapter_replace_next_step_includes_short_thinking_guidance_and_recovers_on_leak(workspace):
    """整章替换的 next_step 同样携带短思考与禁调工具引导，且遇泄漏 tool_call 时早停并恢复替换原文。"""
    existing = File(
        project_id=workspace.project.id,
        title="第一章 旧版",
        file_type="draft",
        content="旧版正文内容，需要整章重写。",
    )
    workspace.session.add(existing)
    workspace.session.commit()
    workspace.session.refresh(existing)

    cancelled_modes: list[str] = []

    class _TrackingResult(_ScriptedResult):
        is_complete: bool = False

        def cancel(self, mode: str = "immediate") -> None:
            cancelled_modes.append(mode)
            if mode == "immediate":
                self.is_complete = True

    class _TrackingModel(ScriptedModel):
        def run_streamed(self, agent, *_args, input=None, hooks=None, context=None, **_kwargs):  # noqa: A002
            self.run_inputs.append(input)
            replies = self.runs.pop(0) if self.runs else [Reply(text="完成")]
            return _TrackingResult(self, agent, hooks, replies, context=context)

    replace_call = Reply(tools=(("write_chapter", {"chapter": 1, "title": "第一章 重写", "file_type": "draft"}),))
    leaked_reply = Reply(text="]<]minimax[>[<tool_call>\n" + ("x" * 4000))
    recovered_body = Reply(text=f"<file>\n{BODY}\n</file>\n\n第一章已整章替换。")
    self_check_done = Reply(text="自查过了，没有需要改的地方。")

    model = _TrackingModel(runs=[[replace_call, leaked_reply], [recovered_body], [self_check_done]])
    outcome = await _run(workspace, model)

    assert cancelled_modes == ["immediate"]
    wc_call = next(call for call in model.tool_calls if call.name == "write_chapter")
    assert wc_call.output["data"]["action"] == "replaced"
    next_step = wc_call.output["data"]["next_step"]
    assert "构思已在调用前完成" in next_step
    assert "read_files" in next_step
    assert "<file>" in next_step

    workspace.session.expire_all()
    refreshed = workspace.session.get(File, existing.id)
    assert refreshed is not None and refreshed.content.strip() == BODY
    assert "<tool_call>" not in outcome.chat_text
    assert outcome.of("error") == [] and len(outcome.of("done")) == 1


async def test_leaked_tool_call_early_stop_preserves_original_author_message_when_to_input_list_is_unbalanced(
    workspace,
):
    """早停时即便 SDK to_input_list 含未配对尾项或抛错，FILE_BODY_RESUME 纠偏轮也绝不丢失作者原话。"""
    from agent.openai_agents.runner import _sanitized_early_stop_items

    class _UnbalancedResult:
        def __init__(self, items):
            self._items = items

        def to_input_list(self):
            return self._items

    # 尾段有未配对的 function_call 与悬空 reasoning：保留最长成对前缀而非整表返回 None
    trimmed = _sanitized_early_stop_items(
        _UnbalancedResult(
            [
                {"role": "user", "content": "写宁宸第一章"},
                {"type": "function_call", "call_id": "c1", "name": "create_file", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "c1", "output": '{"status":"success"}'},
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "未完成思考"}]},
                {"type": "function_call", "call_id": "c2", "name": "query_files", "arguments": "{}"},
            ]
        )
    )
    assert trimmed is not None
    assert len(trimmed) == 3
    assert trimmed[0] == {"role": "user", "content": "写宁宸第一章"}
    assert trimmed[-1]["call_id"] == "c1"

    # 即便 to_input_list() 在取消后直接失败，早停回退到 run_input 后纠偏轮仍保留作者原话
    class _BrokenInputListResult(_ScriptedResult):
        is_complete: bool = False

        def cancel(self, mode: str = "immediate") -> None:
            if mode == "immediate":
                self.is_complete = True

        def to_input_list(self) -> list[Any]:
            raise RuntimeError("cancelled mid-turn")

    class _TrackingModel(ScriptedModel):
        def run_streamed(self, agent, *_args, input=None, hooks=None, context=None, **_kwargs):  # noqa: A002
            self.run_inputs.append(input)
            replies = self.runs.pop(0) if self.runs else [Reply(text="完成")]
            return _BrokenInputListResult(self, agent, hooks, replies, context=context)

    write_ch = Reply(tools=(("write_chapter", {"chapter": 1, "title": "第一章 开篇", "file_type": "draft"}),))
    leaked_reply = Reply(text="]<]minimax[>[<tool_call>\n" + ("x" * 2000))
    recovered_body = Reply(text=f"<file>\n{BODY}\n</file>")
    self_check_done = Reply(text="第一章已写完。")

    model = _TrackingModel(runs=[[write_ch, leaked_reply], [recovered_body], [self_check_done]])
    outcome = await _run(workspace, model, message="我想写《阁下不讲道理》，主角宁宸穿越到大玄皇朝，写第1章。")

    assert len(model.run_inputs) >= 2
    second_run_text = "\n".join(
        str(item.get("content") or "") for item in model.run_inputs[1] if isinstance(item, dict)
    )
    assert "宁宸" in second_run_text, "纠偏轮输入绝不能丢掉本轮作者原始需求"
    assert "系统提醒" in second_run_text
    assert outcome.of("error") == [] and len(outcome.of("done")) == 1


