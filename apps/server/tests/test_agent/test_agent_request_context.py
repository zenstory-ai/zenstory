"""Targeted regression tests for Arch-PR 2: AgentRequestContext + RunContextWrapper.

Covers:
1. End-to-end RunContextWrapper[AgentRequestContext] threading through real
   OpenAI Agents SDK ``Runner.run_streamed`` into FunctionTool.on_invoke_tool,
   RunHooks (on_llm_start / on_llm_end), call_model_input_filter, and
   tool_use_behavior (_stop_run_after_tools).
2. Per-tool short-lived DB Session isolation across concurrent ``asyncio.to_thread``
   worker threads (distinct Session per call, deterministically closed in finally
   even on exception; caller-owned Session reused without closing and rolled back
   on exception when create_session_func is absent).
3. Parallel ``tool_call_scope`` safety under concurrent ``asyncio.gather`` /
   ``asyncio.to_thread`` ``edit_file`` invocations (no UI payload collision or
   cross-contamination across distinct tool_call_ids).
4. Decoupled overdue-file-body policy: ``FailoverChatModel`` has zero coupling to
   ``AgentRequestContext`` and never injects ``tool_choice="none"``, while
   ``tools_adapter`` + ``_stop_run_after_tools`` enforce the guard strictly per
   ``RunContextWrapper[AgentRequestContext]`` without cross-request leakage.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from agents import Model, RunContextWrapper
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseTextDeltaEvent,
    ResponseUsage,
)
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails
from sqlmodel import Session

from agent.core.request_context import AgentRequestContext, unwrap_request_context
from agent.core.workflow_events import StreamEventType
from agent.openai_agents import model as model_module
from agent.openai_agents import runner as runner_module
from agent.openai_agents.tools_adapter import build_agent_function_tools
from models import File, Project, User
from tests.conftest import TestSessionLocal

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _make_response(resp_id: str, output: list[Any]) -> Response:
    return Response(
        id=resp_id,
        created_at=0.0,
        model="synthetic-sdk-model",
        object="response",
        output=output,
        parallel_tool_calls=True,
        tool_choice="auto",
        tools=[],
        status="completed",
        usage=ResponseUsage(
            input_tokens=12,
            input_tokens_details=InputTokensDetails(cached_tokens=0, cache_write_tokens=0),
            output_tokens=6,
            output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
            total_tokens=18,
        ),
    )


class _ScriptedSdkModel(Model):
    """Minimal real ``agents.Model`` subclass to drive genuine ``Runner.run_streamed``."""

    def __init__(self, turns: list[list[Any]]) -> None:
        self._turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError("streaming path only")

    def stream_response(
        self,
        system_instructions: str | None,
        input: Any,
        model_settings: Any,
        tools: list[Any],
        output_schema: Any,
        handoffs: list[Any],
        tracing: Any,
        *,
        previous_response_id: str | None = None,
        conversation_id: str | None = None,
        prompt: Any = None,
    ) -> AsyncIterator[Any]:
        turn_index = len(self.calls)
        self.calls.append(
            {
                "system_instructions": system_instructions,
                "input": input,
                "model_settings": model_settings,
                "tools": tools,
            }
        )
        output_items = self._turns[turn_index] if turn_index < len(self._turns) else []

        async def _events() -> AsyncIterator[Any]:
            seq = 0
            for item in output_items:
                if isinstance(item, ResponseOutputMessage):
                    for part in item.content:
                        if isinstance(part, ResponseOutputText) and part.text:
                            yield ResponseTextDeltaEvent(
                                type="response.output_text.delta",
                                content_index=0,
                                delta=part.text,
                                item_id=item.id,
                                output_index=0,
                                sequence_number=seq,
                                logprobs=[],
                            )
                            seq += 1
            resp = _make_response(f"resp-{turn_index}", output_items)
            yield ResponseCompletedEvent(
                type="response.completed",
                response=resp,
                sequence_number=seq,
            )

        return _events()


def _make_user_and_project(db_session: Session, tag: str) -> tuple[User, Project]:
    suffix = uuid4().hex[:8]
    user = User(
        email=f"reqctx-{tag}-{suffix}@example.com",
        username=f"reqctx_{tag}_{suffix}",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(name=f"ReqCtx {tag} {suffix}", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return user, project


# ---------------------------------------------------------------------------
# 1. End-to-end RunContextWrapper[AgentRequestContext] through real Runner.run_streamed
# ---------------------------------------------------------------------------


async def test_real_sdk_runner_threads_agent_request_context_through_hooks_tools_and_filters(
    db_session: Session,
):
    user, project = _make_user_and_project(db_session, "sdk-e2e")
    chapter = File(
        project_id=project.id,
        title="第一章 初雪",
        file_type="draft",
        content="雪落了一整夜，灯还亮着。",
    )
    db_session.add(chapter)
    db_session.commit()
    db_session.refresh(chapter)

    req_ctx = AgentRequestContext(
        session=None,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-sdk-e2e",
        create_session_func=TestSessionLocal,
        current_agent="loop",
        read_snapshots={},
    )

    scripted_model = _ScriptedSdkModel(
        turns=[
            [
                ResponseFunctionToolCall(
                    id="fc-1",
                    call_id="call-sdk-read-1",
                    name="query_files",
                    arguments=json.dumps({"id": chapter.id}),
                    type="function_call",
                    status="completed",
                )
            ],
            [
                ResponseOutputMessage(
                    id="msg-2",
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[ResponseOutputText(type="output_text", text="已读取第一章。", annotations=[])],
                )
            ],
        ]
    )

    observed_filter_contexts: list[AgentRequestContext | None] = []
    orig_filter_call = runner_module._ModelCallCountingFilter.__call__

    def _spy_filter(self, filter_data):
        observed_filter_contexts.append(unwrap_request_context(getattr(filter_data, "context", None)))
        return orig_filter_call(self, filter_data)

    state: dict[str, Any] = {
        "user_message": "读第一章",
        "messages": [],
        "system_prompt": "synthetic system",
        "project_id": project.id,
        "user_id": user.id,
        "session_id": "sess-sdk-e2e",
        "request_context": req_ctx,
    }
    with (
        patch("agent.openai_agents.runner.build_failover_chat_model", return_value=scripted_model),
        patch("agent.openai_agents.runner._preferred_model_name", AsyncMock(return_value="synthetic-sdk-model")),
        patch("agent.openai_agents.usage_hooks.schedule_llm_usage_record"),
        patch.object(runner_module._ModelCallCountingFilter, "__call__", _spy_filter),
    ):
        events = [
            ev
            async for ev in runner_module.run_openai_agents_streaming_agent(
                state,
                agent_type="loop",
                system_prompt="synthetic system",
                request_context=req_ctx,
            )
        ]

    # 1) Both LLM calls ran; reply_seq advanced via RunHooks.on_llm_start(RunContextWrapper[AgentRequestContext]).
    assert len(scripted_model.calls) == 2
    assert req_ctx.pending_empty_file_state._reply_seq == 2

    # 2) call_model_input_filter received RunContextWrapper wrapping the exact same req_ctx.
    assert len(observed_filter_contexts) == 2
    assert all(ctx is req_ctx for ctx in observed_filter_contexts)

    # 3) FunctionTool.on_invoke_tool executed query_files using req_ctx, and on_llm_start promoted pending reads.
    assert req_ctx.has_seen_file(chapter.id) is True

    # 4) Stream emitted tool_use, tool_result, text, and clean end_turn message_end.
    event_types = [ev.type for ev in events]
    assert StreamEventType.TOOL_USE in event_types
    assert StreamEventType.TOOL_RESULT in event_types
    assert StreamEventType.TEXT in event_types
    end_events = [ev for ev in events if ev.type == StreamEventType.MESSAGE_END]
    assert end_events and end_events[-1].data["stop_reason"] == "end_turn"


async def test_real_sdk_runner_stops_on_overdue_file_body_via_run_context_wrapper(db_session: Session):
    """Overdue file body refusal + _stop_run_after_tools work purely through RunContextWrapper."""
    user, project = _make_user_and_project(db_session, "sdk-overdue")
    req_ctx = AgentRequestContext(
        session=None,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-sdk-overdue",
        create_session_func=TestSessionLocal,
        current_agent="loop",
    )
    # Arm a pending empty file at reply_seq=0; the first LLM call increments reply_seq to 1 (> 0 -> overdue).
    req_ctx.set_pending_empty_file("file-pending-1", "第一章")

    scripted_model = _ScriptedSdkModel(
        turns=[
            [
                ResponseFunctionToolCall(
                    id="fc-overdue",
                    call_id="call-overdue-1",
                    name="query_files",
                    arguments="{}",
                    type="function_call",
                    status="completed",
                )
            ],
            [
                ResponseOutputMessage(
                    id="msg-unreachable",
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[ResponseOutputText(type="output_text", text="不应进入第二次模型调用", annotations=[])],
                )
            ],
        ]
    )

    state: dict[str, Any] = {
        "user_message": "继续",
        "messages": [],
        "system_prompt": "synthetic system",
        "project_id": project.id,
        "user_id": user.id,
        "session_id": "sess-sdk-overdue",
        "request_context": req_ctx,
    }
    with (
        patch("agent.openai_agents.runner.build_failover_chat_model", return_value=scripted_model),
        patch("agent.openai_agents.runner._preferred_model_name", AsyncMock(return_value="synthetic-sdk-model")),
        patch("agent.openai_agents.usage_hooks.schedule_llm_usage_record"),
    ):
        events = [
            ev
            async for ev in runner_module.run_openai_agents_streaming_agent(
                state,
                agent_type="loop",
                system_prompt="synthetic system",
                request_context=req_ctx,
            )
        ]

    # _stop_run_after_tools stopped the SDK run right after the overdue refusal (never called model turn 2).
    assert len(scripted_model.calls) == 1
    # Never injects tool_choice="none" (preserves prompt-prefix cache stability).
    assert getattr(scripted_model.calls[0]["model_settings"], "tool_choice", None) != "none"

    tool_results = [ev for ev in events if ev.type == StreamEventType.TOOL_RESULT]
    assert len(tool_results) == 1
    payload = json.loads(tool_results[0].data["result"]["content"][0]["text"])
    assert payload["status"] == "error"
    assert payload["error_type"] == "file_body_pending"


# ---------------------------------------------------------------------------
# 2. Per-tool short-lived DB Session isolation across asyncio.to_thread
# ---------------------------------------------------------------------------


class _TrackingFakeSession:
    def __init__(self, idx: int) -> None:
        self.idx = idx
        self.thread_name = threading.current_thread().name
        self.closed = False
        self.rolled_back = False

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


async def test_session_scope_opens_and_closes_distinct_sessions_across_to_thread_workers():
    created: list[_TrackingFakeSession] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=5)

    def factory() -> _TrackingFakeSession:
        with lock:
            sess = _TrackingFakeSession(len(created))
            created.append(sess)
            return sess

    caller_session = _TrackingFakeSession(999)
    req_ctx = AgentRequestContext(
        session=caller_session,
        user_id="u1",
        project_id="p1",
        session_id="s1",
        create_session_func=factory,
    )

    def worker(should_fail: bool) -> tuple[int, str]:
        with req_ctx.session_scope() as db:
            barrier.wait()
            if should_fail:
                raise RuntimeError("boom inside worker")
            return db.idx, threading.current_thread().name

    res_ok, res_err = await asyncio.gather(
        asyncio.to_thread(worker, False),
        asyncio.to_thread(worker, True),
        return_exceptions=True,
    )

    assert isinstance(res_ok, tuple)
    assert isinstance(res_err, RuntimeError)
    # Two concurrent tool workers got two distinct short-lived sessions.
    assert len(created) == 2
    assert created[0] is not created[1]
    assert created[0] is not caller_session and created[1] is not caller_session
    # Both worker sessions were closed deterministically (even the failing one),
    # and the failing one was rolled back; caller_session was untouched.
    assert all(sess.closed for sess in created)
    assert sum(1 for sess in created if sess.rolled_back) == 1
    assert caller_session.closed is False
    assert caller_session.rolled_back is False


def test_session_scope_reuses_injected_session_without_closing_when_no_factory():
    caller_session = _TrackingFakeSession(42)
    req_ctx = AgentRequestContext(
        session=caller_session,
        user_id="u1",
        project_id="p1",
        session_id="s1",
        create_session_func=None,
    )

    with req_ctx.session_scope() as db:
        assert db is caller_session
    assert caller_session.closed is False
    assert caller_session.rolled_back is False

    with pytest.raises(ValueError, match="test failure"):
        with req_ctx.session_scope() as db:
            assert db is caller_session
            raise ValueError("test failure")

    assert caller_session.rolled_back is True
    assert caller_session.closed is False


# ---------------------------------------------------------------------------
# 3. Parallel tool_call_scope safety under concurrent edit_file calls
# ---------------------------------------------------------------------------


async def test_parallel_edit_file_calls_isolate_tool_ui_payloads_per_call_id(
    monkeypatch: pytest.MonkeyPatch,
):
    from agent.tools.file_ops.executor import FileToolExecutor

    barrier = threading.Barrier(2, timeout=5)
    lock = threading.Lock()
    created_sessions: list[_TrackingFakeSession] = []

    def session_factory() -> _TrackingFakeSession:
        with lock:
            s = _TrackingFakeSession(len(created_sessions))
            created_sessions.append(s)
            return s

    def concurrent_edit(self, *, id: str, edits: list[dict[str, Any]], **_kwargs: Any) -> dict[str, Any]:
        # Force both worker threads to execute simultaneously in asyncio.to_thread.
        barrier.wait()
        old_text = edits[0]["old"]
        new_text = edits[0]["new"]
        return {
            "id": id,
            "title": f"title-{id}",
            "edits_applied": 1,
            "mutation_applied": True,
            "new_word_count": len(new_text),
            "details": [
                {
                    "op": "replace",
                    "match_mode": "exact",
                    "old_preview": old_text,
                    "new_preview": new_text,
                }
            ],
            "undo": {
                "before_version_number": 1,
                "expected_after_updated_at": "2026-11-21T00:00:00Z",
                "expected_after_content_sha256": f"sha-{id}".ljust(64, "0"),
            },
        }

    monkeypatch.setattr(FileToolExecutor, "edit_file", concurrent_edit)

    req_ctx = AgentRequestContext(
        session=None,
        user_id="u-parallel",
        project_id="p-parallel",
        session_id="sess-parallel-ui",
        create_session_func=session_factory,
        read_snapshots={"file-A": "甲段原文。", "file-B": "乙段原文。"},
    )
    tools = {t.name: t for t in build_agent_function_tools()}
    edit_tool = tools["edit_file"]

    ctx_a = SimpleNamespace(context=req_ctx, tool_call_id="call-parallel-A")
    ctx_b = SimpleNamespace(context=req_ctx, tool_call_id="call-parallel-B")
    args_a = json.dumps(
        {"id": "file-A", "edits": [{"op": "replace", "old": "甲段原文。", "new": "甲段新文。"}]},
        ensure_ascii=False,
    )
    args_b = json.dumps(
        {"id": "file-B", "edits": [{"op": "replace", "old": "乙段原文。", "new": "乙段新文。"}]},
        ensure_ascii=False,
    )

    out_a, out_b = await asyncio.gather(
        edit_tool.on_invoke_tool(ctx_a, args_a),
        edit_tool.on_invoke_tool(ctx_b, args_b),
    )

    assert json.loads(out_a)["status"] == "success"
    assert json.loads(out_b)["status"] == "success"
    # Each parallel tool call received its own short-lived session and closed it cleanly.
    assert len(created_sessions) == 2
    assert all(s.closed for s in created_sessions)

    payload_a = req_ctx.pop_tool_ui_payload("call-parallel-A")
    payload_b = req_ctx.pop_tool_ui_payload("call-parallel-B")
    assert payload_a is not None and payload_b is not None
    assert payload_a["details"][0]["old_preview"] == "甲段原文。"
    assert payload_a["details"][0]["new_preview"] == "甲段新文。"
    assert payload_b["details"][0]["old_preview"] == "乙段原文。"
    assert payload_b["details"][0]["new_preview"] == "乙段新文。"
    assert payload_a["undo"]["expected_after_content_sha256"] != payload_b["undo"]["expected_after_content_sha256"]


# ---------------------------------------------------------------------------
# 4. Decoupled FailoverChatModel & isolated per-request overdue guard
# ---------------------------------------------------------------------------


async def test_failover_chat_model_is_decoupled_from_request_context_and_overdue_guard_is_isolated():
    # FailoverChatModel module must not reference AgentRequestContext or legacy ToolContext.
    source = inspect.getsource(model_module)
    assert "ToolContext" not in source
    assert "AgentRequestContext" not in source
    assert "_body_reply_settings" not in source

    # Two independent AgentRequestContext instances in the same event loop stay strictly isolated.
    ctx_overdue = AgentRequestContext(
        session=None, user_id="u1", project_id="p1", session_id="s-overdue"
    )
    ctx_overdue.set_pending_empty_file("file-1", "待写章节")
    ctx_overdue.begin_model_reply()  # reply_seq=1 > armed_at=0 -> overdue

    ctx_clean = AgentRequestContext(
        session=None, user_id="u2", project_id="p2", session_id="s-clean"
    )
    ctx_clean.begin_model_reply()

    tools = {t.name: t for t in build_agent_function_tools()}
    overdue_out = json.loads(
        await tools["query_files"].on_invoke_tool(
            SimpleNamespace(context=ctx_overdue, tool_call_id="call-ov"),
            "{}",
        )
    )
    assert overdue_out["status"] == "error"
    assert overdue_out["error_type"] == "file_body_pending"

    # Clean request is NOT blocked by ctx_overdue's pending file!
    mock_query = AsyncMock(
        return_value={"content": [{"type": "text", "text": '{"status":"success","data":[]}'}]}
    )
    with patch.dict(
        "agent.openai_agents.tools_adapter.TOOL_FUNCTIONS",
        {"query_files": mock_query},
    ):
        fresh_tools = {t.name: t for t in build_agent_function_tools()}
        clean_out = json.loads(
            await fresh_tools["query_files"].on_invoke_tool(
                SimpleNamespace(context=ctx_clean, tool_call_id="call-cl"),
                "{}",
            )
        )
    assert clean_out["status"] == "success"
    assert mock_query.await_args.kwargs["request_context"] is ctx_clean

    # _stop_run_after_tools checks ctx.context independently for each wrapper.
    stop_res = runner_module._stop_run_after_tools(
        RunContextWrapper(ctx_overdue),
        [SimpleNamespace(output=json.dumps(overdue_out, ensure_ascii=False))],
    )
    assert stop_res.is_final_output is True

    continue_res = runner_module._stop_run_after_tools(
        RunContextWrapper(ctx_clean),
        [SimpleNamespace(output=json.dumps(clean_out, ensure_ascii=False))],
    )
    assert continue_res.is_final_output is False
