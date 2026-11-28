"""模型在一次调用里把一大段原样再写一遍时，截停这一轮，正文只留重复之前的部分。

生产 10-26（MiniMax，要求「8000 字左右的短篇」）：前一万二千字正常推进，之后一段约 5,012 字
几乎逐字重复了九遍（相邻两遍相似度 99.9%），一直写到 780 秒的单轮时限才被截断，
文件存下 61,430 个字符，作者只能要求删掉重复。
"""

import random
from unittest.mock import AsyncMock

import pytest

from agent.core.events import EventType
from agent.core.stream_errors import REPETITION_LOOP_STOP_REASON
from agent.core.stream_processor import find_repetition_start
from agent.core.workflow_events import StreamEvent as WorkflowStreamEvent
from agent.core.workflow_events import StreamEventType

_CHARS = "天地玄黄宇宙洪荒日月盈昃辰宿列张寒来暑往秋收冬藏闰余成岁律吕调阳云腾致雨露结为霜金生丽水玉出昆冈"


def _prose(length: int, seed: int) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice(_CHARS) for _ in range(length))


@pytest.mark.unit
def test_a_repeated_passage_is_found_where_the_repeat_starts():
    opening, cycle = _prose(3000, 1), _prose(5000, 2)
    # 连续第三遍才算循环；检查晚一点也一样截在第二遍开头，只留一遍
    for written in (800, 3000, 12000):
        assert find_repetition_start(opening + cycle * 2 + (cycle * 3)[:written]) == len(opening + cycle)
    assert find_repetition_start(opening + cycle) is None
    assert find_repetition_start(opening + cycle + cycle[:4999]) is None
    # 「修改前 / 修改后」对照里原样出现两遍的大段文字不算
    middle = _prose(1200, 9)
    assert find_repetition_start("修改前：甲" + middle + "乙\n修改后：丙" + middle + "丁") is None
    # 短分隔线、空行不算循环；但连续大量重复纯标点/破折号行属于退化死循环（生产 11-27：11559 段 ——）
    assert find_repetition_start("——\n\n" * 5) is None
    assert find_repetition_start("\n\n" * 1000) is None
    assert find_repetition_start("——\n\n" * 3000) == 0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_adapter_saves_only_the_text_before_the_repeat():
    from agent.stream_adapter import create_stream_adapter
    from agent.tools.tool_context import AgentRequestContext

    opening, cycle = _prose(3000, 3), _prose(2000, 4)
    req_ctx = AgentRequestContext(project_id="p", user_id="u")
    adapter = create_stream_adapter(
        project_id="p", user_id="u", process_file_markers=True, request_context=req_ctx
    )
    adapter._save_file_content = AsyncMock(return_value=True)
    req_ctx.set_pending_empty_file("story", "订婚宴前的账本")
    adapter.set_pending_file_write("story", "draft", "订婚宴前的账本")

    async def events():
        yield WorkflowStreamEvent(type=StreamEventType.TEXT, data={"text": "<file>\n" + opening + cycle})
        yield WorkflowStreamEvent(type=StreamEventType.TEXT, data={"text": cycle + cycle[:900]})
        yield WorkflowStreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": REPETITION_LOOP_STOP_REASON})
        yield WorkflowStreamEvent(
            type=StreamEventType.ERROR,
            data={"code": "ERR_AGENT_REPETITION_LOOP", "retryable": False, "refundable": True,
                  "reason": REPETITION_LOOP_STOP_REASON},
        )

    out = [e async for e in adapter.process_workflow_events(events())]

    adapter._save_file_content.assert_awaited_once()
    assert adapter._save_file_content.await_args.args[:2] == ("story", opening + cycle)
    error = next(e for e in out if e.type == EventType.ERROR)
    assert error.data["code"] == "ERR_AGENT_REPETITION_LOOP"
    assert error.data["reason"] == REPETITION_LOOP_STOP_REASON


@pytest.mark.asyncio
@pytest.mark.unit
async def test_sdk_run_stops_as_soon_as_the_model_repeats_a_long_passage(monkeypatch):
    from agent.openai_agents.model import reset_sdk_model_cache
    from tests.test_agent.test_tool_failure_breaker import _chunk, _run_against_local_model

    opening, cycle = _prose(1500, 5), _prose(1200, 6)
    pieces = [opening[i:i + 100] for i in range(0, len(opening), 100)]
    pieces += [cycle[i:i + 100] for i in range(0, len(cycle), 100)] * 6

    def script(_index: int) -> list[dict]:
        return [_chunk({"role": "assistant", "content": ""}), *[_chunk({"content": p}) for p in pieces],
                _chunk({}, "stop", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})]

    async def _unused_edit(_args):  # pragma: no cover - the script calls no tools
        raise AssertionError("edit_file should not be called")

    reset_sdk_model_cache()
    try:
        events, requests, _state = await _run_against_local_model(
            monkeypatch, script, _unused_edit, client_may_disconnect=True
        )
    finally:
        reset_sdk_model_cache()

    text = "".join(e.data["text"] for e in events if e.type == StreamEventType.TEXT)
    assert len(text) < len(opening) + len(cycle) * 3  # stopped during the third copy, not after six
    assert len(requests) == 1
    assert [e.type for e in events][-2:] == [StreamEventType.MESSAGE_END, StreamEventType.ERROR]
    assert events[-2].data["stop_reason"] == REPETITION_LOOP_STOP_REASON
    error = events[-1].data
    assert error["code"] == "ERR_AGENT_REPETITION_LOOP"
    assert error["reason"] == REPETITION_LOOP_STOP_REASON
    assert error["retryable"] is False


@pytest.mark.unit
def test_a_repetition_loop_is_charged_after_model_start_whether_or_not_it_wrote():
    from agent.core.stream_errors import repetition_loop_error
    from tests.test_agent.test_repeat_read_guard import _decide, _sse

    assert repetition_loop_error().refundable is False
    error = _sse("error", {"message": "x", "code": "ERR_AGENT_REPETITION_LOOP", "refundable": False,
                           "reason": REPETITION_LOOP_STOP_REASON})
    chat_only = [_sse("content", {"text": _prose(1600, 7)}), error]
    assert _decide(chat_only, provider_attempted=True) == ("non_model_error", False)

    saved = [_sse("file_content", {"file_id": "story", "chunk": "正文"}),
             _sse("file_content_end", {"file_id": "story"}), error]
    assert _decide(saved, provider_attempted=True) == ("non_model_error", False)


# ---------------------------------------------------------------------------
# Streaming reasoning repetition loop guard (#1 — official minimax-code port)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    "reasoning_event_type",
    ["response.reasoning_text.delta", "response.reasoning_summary_text.delta"],
)
async def test_runner_stops_streaming_reasoning_repetition_with_zero_text_and_logs_source(
    monkeypatch, caplog, reasoning_event_type: str
):
    import logging
    from types import SimpleNamespace
    from typing import Any
    from unittest.mock import patch

    from agents import Runner

    from agent.openai_agents.reasoning_loop_guard import LOOP_WINDOW
    from agent.openai_agents.runner import run_openai_agents_streaming_agent

    phrase = "Now. Let me check. OK. "
    # 3 units per delta -> 120 deltas = 360 units; should trip at unit 256 (~86th delta).
    deltas = [phrase for _ in range(120)]

    class _FakeStreamResult:
        def __init__(self) -> None:
            self.cancelled_modes: list[str] = []
            self.raw_responses: list[Any] = []
            self.is_complete = False

        def cancel(self, *, mode: str = "immediate") -> None:
            self.cancelled_modes.append(mode)

        def to_input_list(self) -> list[dict]:
            return [{"role": "user", "content": "继续写"}, {"type": "reasoning", "summary": []}]

        async def stream_events(self):
            yield SimpleNamespace(
                type="raw_response_event",
                data=SimpleNamespace(type="response.created"),
            )
            for piece in deltas:
                yield SimpleNamespace(
                    type="raw_response_event",
                    data=SimpleNamespace(type=reasoning_event_type, delta=piece),
                )

    fake_result = _FakeStreamResult()

    monkeypatch.setattr(
        "agent.openai_agents.runner._preferred_model_name",
        AsyncMock(return_value="MiniMax-M3.1-Flash"),
    )
    monkeypatch.setattr("agent.openai_agents.runner._build_agent", lambda *_a, **_kw: SimpleNamespace(model=None))

    state: dict = {
        "user_message": "继续写第一章",
        "project_id": "p-1",
        "user_id": "u-1",
        "session_id": "s-1",
        "language": "zh",
        "run_items": [{"role": "user", "content": "stale"}],
    }

    with (
        patch.object(Runner, "run_streamed", return_value=fake_result),
        caplog.at_level(logging.WARNING),
    ):
        events = [
            ev
            async for ev in run_openai_agents_streaming_agent(
                state=state,
                agent_type="loop",
                system_prompt="sys",
            )
        ]

    # Stopped early (at ~86 deltas instead of 120), zero TEXT events emitted, cancelled immediately.
    thinking_events = [e for e in events if e.type == StreamEventType.THINKING]
    text_events = [e for e in events if e.type == StreamEventType.TEXT]
    assert 0 < len(thinking_events) < len(deltas)
    assert text_events == []
    assert fake_result.cancelled_modes and fake_result.cancelled_modes[0] == "immediate"

    # Terminal events: MESSAGE_END(repetition_loop) + ERROR(ERR_AGENT_REPETITION_LOOP).
    assert [e.type for e in events][-2:] == [StreamEventType.MESSAGE_END, StreamEventType.ERROR]
    assert events[-2].data["stop_reason"] == REPETITION_LOOP_STOP_REASON
    err = events[-1].data
    assert err["code"] == "ERR_AGENT_REPETITION_LOOP"
    assert err["reason"] == REPETITION_LOOP_STOP_REASON
    assert err["retryable"] is False
    assert err["refundable"] is False

    # Stale run_items cleared so downstream can never cache a repetition-stopped run.
    assert "run_items" not in state

    # WARNING log clearly identifies source="reasoning" and official guard diagnostics.
    reasoning_warnings = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING
        and getattr(rec, "custom_fields", {}).get("source") == "reasoning"
    ]
    assert len(reasoning_warnings) == 1
    fields = reasoning_warnings[0].custom_fields
    assert fields["agent_type"] == "loop"
    assert fields["run_on"] is False
    assert fields["window"] == LOOP_WINDOW
    assert fields["distinct_units"] == 3
    assert set(fields["sample_units"]) == {"now", "let me check", "ok"}
    assert isinstance(fields["call_reasoning_length"], int) and fields["call_reasoning_length"] > 0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_runner_resets_reasoning_guard_on_response_created_and_catches_run_on_loop(monkeypatch, caplog):
    import logging
    from types import SimpleNamespace
    from typing import Any
    from unittest.mock import patch

    from agents import Runner

    from agent.openai_agents.reasoning_loop_guard import RUN_ON_UNITS, UNIT_MOST
    from agent.openai_agents.runner import run_openai_agents_streaming_agent

    # 1) 150 identical units across 3 separate model calls (each preceded by response.created)
    #    totals 450 units (> 256), but MUST NOT trip because response.created resets the guard.
    per_call_reasoning = "Now. " * 150

    class _MultiCallNormalResult:
        def __init__(self) -> None:
            self.cancelled_modes: list[str] = []
            self.raw_responses: list[Any] = []
            self.is_complete = True

        def cancel(self, *, mode: str = "immediate") -> None:
            self.cancelled_modes.append(mode)

        def to_input_list(self) -> list[dict]:
            return [{"role": "user", "content": "ok"}, {"role": "assistant", "content": "done"}]

        async def stream_events(self):
            for _ in range(3):
                yield SimpleNamespace(type="raw_response_event", data=SimpleNamespace(type="response.created"))
                yield SimpleNamespace(
                    type="raw_response_event",
                    data=SimpleNamespace(type="response.reasoning_text.delta", delta=per_call_reasoning),
                )
            yield SimpleNamespace(
                type="raw_response_event",
                data=SimpleNamespace(type="response.output_text.delta", delta="全部完成。"),
            )

    monkeypatch.setattr(
        "agent.openai_agents.runner._preferred_model_name",
        AsyncMock(return_value="MiniMax-M3.1-Flash"),
    )
    monkeypatch.setattr("agent.openai_agents.runner._build_agent", lambda *_a, **_kw: SimpleNamespace(model=None))

    normal_result = _MultiCallNormalResult()
    with patch.object(Runner, "run_streamed", return_value=normal_result):
        events = [
            ev
            async for ev in run_openai_agents_streaming_agent(
                state={"user_message": "写吧", "project_id": "p", "user_id": "u", "session_id": "s"},
                agent_type="loop",
                system_prompt="sys",
            )
        ]
    assert normal_result.cancelled_modes == []
    assert events[-1].type == StreamEventType.MESSAGE_END
    assert events[-1].data["stop_reason"] == "end_turn"

    # 2) Unbroken run-on reasoning loop (3 * 4096 chars without stops) triggers run_on=True.
    run_on_unit = ("wait check ok " * 400)[:UNIT_MOST]

    class _RunOnLoopResult:
        def __init__(self) -> None:
            self.cancelled_modes: list[str] = []
            self.raw_responses: list[Any] = []
            self.is_complete = False

        def cancel(self, *, mode: str = "immediate") -> None:
            self.cancelled_modes.append(mode)

        def to_input_list(self) -> list[dict]:
            return []

        async def stream_events(self):
            yield SimpleNamespace(type="raw_response_event", data=SimpleNamespace(type="response.created"))
            for _ in range(5):
                yield SimpleNamespace(
                    type="raw_response_event",
                    data=SimpleNamespace(type="response.reasoning_text.delta", delta=run_on_unit),
                )

    run_on_result = _RunOnLoopResult()
    caplog.clear()
    with (
        patch.object(Runner, "run_streamed", return_value=run_on_result),
        caplog.at_level(logging.WARNING),
    ):
        run_on_events = [
            ev
            async for ev in run_openai_agents_streaming_agent(
                state={"user_message": "写吧", "project_id": "p", "user_id": "u", "session_id": "s"},
                agent_type="loop",
                system_prompt="sys",
            )
        ]
    assert run_on_result.cancelled_modes and run_on_result.cancelled_modes[0] == "immediate"
    assert [e.type for e in run_on_events][-2:] == [StreamEventType.MESSAGE_END, StreamEventType.ERROR]
    assert run_on_events[-1].data["reason"] == REPETITION_LOOP_STOP_REASON
    run_on_warnings = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING
        and getattr(rec, "custom_fields", {}).get("source") == "reasoning"
    ]
    assert len(run_on_warnings) == 1
    assert run_on_warnings[0].custom_fields["run_on"] is True
    assert run_on_warnings[0].custom_fields["window"] == RUN_ON_UNITS * UNIT_MOST


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
async def test_reasoning_repetition_zero_text_downstream_clean_terminal_and_never_pollutes_wire_prefix(
    db_session, monkeypatch
):
    from unittest.mock import patch

    from agent.core.stream_billing import StreamBillingTracker
    from agent.core.stream_errors import repetition_loop_error
    from agent.loop import wire_prefix_cache
    from agent.service import AgentService
    from models import ChatSession, Project, User
    from services.core.auth_service import hash_password
    from tests.test_agent.test_wire_prefix_cache import _FakeRedis

    fake_redis = _FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://fake-rep/0")
    monkeypatch.setattr(wire_prefix_cache, "get_redis_client", lambda: fake_redis)
    wire_prefix_cache.reset_wire_prefix_cache_state()

    user = User(
        email="reason_rep_e2e@example.com",
        username="reasonrepe2e",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="思考循环测试", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    chat = ChatSession(user_id=user.id, project_id=project.id, title="对话")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    cache_key = wire_prefix_cache.wire_prefix_cache_key(chat.id)
    fake_redis.setex(cache_key, 900, '{"stale": true}')

    def fake_reasoning_loop_run(state, **_kwargs):
        state["run_meter"].last_served_provider_key = "minimax:MiniMax-M3.1-Flash:primary:responses"
        state.pop("run_items", None)

        async def _stream():
            yield WorkflowStreamEvent(
                type=StreamEventType.THINKING,
                data={"thinking": "Now. Let me check. OK. " * 25},
            )
            yield WorkflowStreamEvent(
                type=StreamEventType.MESSAGE_END,
                data={"stop_reason": REPETITION_LOOP_STOP_REASON, "usage": {"input_tokens": 100, "output_tokens": 200}},
            )
            yield WorkflowStreamEvent(
                type=StreamEventType.ERROR,
                data=repetition_loop_error().as_event_data(
                    reason=REPETITION_LOOP_STOP_REASON, agent_type="loop"
                ),
            )

        return _stream()

    tracker = StreamBillingTracker()
    frames: list[str] = []
    with patch("agent.service.run_loop_agent_streaming", side_effect=fake_reasoning_loop_run):
        async for frame in AgentService().process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="写第一章",
            session=db_session,
            session_id=chat.id,
        ):
            frames.append(frame)
            tracker.observe(frame)

    # Single clean terminal error SSE frame (no done frame, no content_end, user-visible message present).
    assert any("event: thinking_content" in f for f in frames)
    assert not any("event: done" in f for f in frames)
    assert not any("event: content_end" in f for f in frames)
    error_frames = [f for f in frames if "event: error" in f]
    assert len(error_frames) == 1
    assert "ERR_AGENT_REPETITION_LOOP" in error_frames[0]
    assert "repetition_loop" in error_frames[0]
    assert tracker.saw_terminal_event is True
    assert tracker.saw_error_event is True
    assert tracker.produced_output is False
    assert tracker.decide(
        client_disconnected=False,
        unexpected_exception=False,
        provider_attempted=True,
        terminal_model_failure=False,
    ) == ("non_model_error", False)

    # Wire-prefix cache was NEVER populated and any pre-existing entry was invalidated.
    assert cache_key not in fake_redis.store
    assert cache_key in fake_redis.deleted_keys
    wire_prefix_cache.reset_wire_prefix_cache_state()
