"""Targeted regressions for Arch Batch A (P0-5, P0-6, P0-7, P0-9, P1-3, P1-5, P1-6)."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import openai
import pytest
from starlette.requests import Request

import api.agent as agent_api
from agent.core import empty_files, llm_failover
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.loop import engine, run_support
from agent.tools.tool_context import AgentRequestContext, _PendingEmptyFileState
from models import File, Project, User
from services.core.auth_service import hash_password

_CHARGED_PERIOD = datetime(2026, 11, 26, 0, 0, tzinfo=UTC)


def _seed_user_project_file(db_session, *, content: str = "", file_type: str = "draft") -> tuple[User, Project, File]:
    suffix = uuid4().hex[:8]
    user = User(
        email=f"arch-a-{suffix}@example.com",
        username=f"arch_a_{suffix}",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    project = Project(name=f"Arch Batch A {suffix}", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    file = File(
        project_id=project.id,
        title="Chapter 1",
        file_type=file_type,
        content=content,
        is_deleted=False,
    )
    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)
    return user, project, file


class _DummySlot:
    def __init__(self) -> None:
        self.adopted = False
        self.release_calls = 0

    def adopt(self) -> None:
        self.adopted = True

    async def release(self) -> None:
        self.release_calls += 1


# ---------------------------------------------------------------------------
# P0-5: Pre-stream CancelledError / BaseException refunds quota once
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_p0_5_prestream_cancelled_error_refunds_quota_and_releases_slot(db_session):
    user, project, _ = _seed_user_project_file(db_session)
    slot = _DummySlot()

    class _CancellingService:
        async def process_stream(self, **_kwargs):
            raise asyncio.CancelledError("client disconnected during priming")
            yield ""  # pragma: no cover

    request = Request({"type": "http", "headers": []})
    body = agent_api.AgentRequest(project_id=project.id, message="Write chapter 1")

    with (
        patch("api.agent.verify_project_access", new=AsyncMock(return_value=project)),
        patch("api.agent.quota_service.check_ai_conversation_quota", return_value=(True, 0, 3)),
        patch("api.agent._acquire_account_slot", new=AsyncMock(return_value=slot)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as release_quota,
        patch("api.agent.get_agent_service", return_value=_CancellingService()),
        patch("api.agent._record_activation"),
        patch("services.first_work_service.observe_first_work_ai_request"),
    ):
        with pytest.raises(asyncio.CancelledError):
            await agent_api.stream_request(
                request=request,
                body=body,
                session=db_session,
                current_user=user,
                accept_language="en-US",
                client_stop_contract="1",
                _rate_limit=1,
            )

    release_quota.assert_called_once()
    assert release_quota.call_args.kwargs["period_start"] == _CHARGED_PERIOD
    assert isinstance(release_quota.call_args.kwargs["reservation_id"], str)
    assert len(release_quota.call_args.kwargs["reservation_id"]) == 32
    assert slot.release_calls == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_p0_5_session_busy_refunds_exactly_once_not_twice(db_session):
    from agent.core.steering import SteeringSessionBusyError
    from core.error_handler import APIException

    user, project, _ = _seed_user_project_file(db_session)
    slot = _DummySlot()

    class _BusyService:
        async def process_stream(self, **_kwargs):
            raise SteeringSessionBusyError("busy")
            yield ""  # pragma: no cover

    request = Request({"type": "http", "headers": []})
    body = agent_api.AgentRequest(project_id=project.id, message="Write chapter 1")

    with (
        patch("api.agent.verify_project_access", new=AsyncMock(return_value=project)),
        patch("api.agent.quota_service.check_ai_conversation_quota", return_value=(True, 0, 3)),
        patch("api.agent._acquire_account_slot", new=AsyncMock(return_value=slot)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as release_quota,
        patch("api.agent.get_agent_service", return_value=_BusyService()),
        patch("api.agent._record_activation"),
        patch("services.first_work_service.observe_first_work_ai_request"),
    ):
        with pytest.raises(APIException) as exc_info:
            await agent_api.stream_request(
                request=request,
                body=body,
                session=db_session,
                current_user=user,
                accept_language="zh-CN",
                client_stop_contract="1",
                _rate_limit=1,
            )

    assert exc_info.value.status_code == 409
    release_quota.assert_called_once()
    assert slot.release_calls >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_p0_5_normal_prestart_error_hands_off_without_double_refund(db_session):
    user, project, _ = _seed_user_project_file(db_session)
    slot = _DummySlot()

    class _PrestartFailingService:
        async def process_stream(self, **_kwargs):
            raise RuntimeError("db setup boom")
            yield ""  # pragma: no cover

    request = Request({"type": "http", "headers": []})
    body = agent_api.AgentRequest(project_id=project.id, message="Write chapter 1")

    with (
        patch("api.agent.verify_project_access", new=AsyncMock(return_value=project)),
        patch("api.agent.quota_service.check_ai_conversation_quota", return_value=(True, 0, 3)),
        patch("api.agent._acquire_account_slot", new=AsyncMock(return_value=slot)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as release_quota,
        patch("api.agent.get_agent_service", return_value=_PrestartFailingService()),
        patch("api.agent._record_activation"),
        patch("api.agent._persist_round_metric"),
        patch("services.first_work_service.observe_first_work_ai_request"),
    ):
        response = await agent_api.stream_request(
            request=request,
            body=body,
            session=db_session,
            current_user=user,
            accept_language="zh-CN",
            client_stop_contract="1",
            _rate_limit=1,
        )
        # Before the StreamingResponse is iterated, no pre-stream refund happened yet.
        release_quota.assert_not_called()
        chunks = []
        with pytest.raises(RuntimeError, match="db setup boom"):
            async for chunk in response.body_iterator:
                chunks.append(chunk)

    # Once SSEStreamPump / event_generator settles the prestart error, quota is refunded exactly once.
    release_quota.assert_called_once()


# ---------------------------------------------------------------------------
# P0-6: runner.py GeneratorExit does not await aclose() inside finally
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_p0_6_runner_generator_exit_does_not_raise_runtime_error(monkeypatch):
    from agent.openai_agents import runner

    aclose_finished = asyncio.Event()
    cancelled_modes: list[str] = []

    class _SuspendingSdkEvents:
        def __init__(self) -> None:
            self._step = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._step == 0:
                self._step += 1
                return SimpleNamespace(
                    type="raw_response_event",
                    data=SimpleNamespace(type="response.output_text.delta", delta="hello"),
                )
            await asyncio.sleep(10)
            raise StopAsyncIteration

        async def aclose(self) -> None:
            # A real coroutine suspension inside aclose() would trigger
            # `RuntimeError: async generator ignored GeneratorExit` if awaited inside finally.
            await asyncio.sleep(0)
            aclose_finished.set()

    fake_result = SimpleNamespace(
        stream_events=lambda: _SuspendingSdkEvents(),
        cancel=lambda *, mode="immediate": cancelled_modes.append(mode),
        raw_responses=[],
        to_input_list=lambda: [],
    )

    import agents

    monkeypatch.setattr(runner, "_preferred_model_name", AsyncMock(return_value="synthetic-model"))
    monkeypatch.setattr(runner, "_build_agent", lambda *_a, **_kw: MagicMock())
    monkeypatch.setattr(agents.Runner, "run_streamed", staticmethod(lambda *_a, **_kw: fake_result))

    state = {"user_message": "hi", "messages": [], "system_prompt": "sys"}
    gen = runner.run_openai_agents_streaming_agent(state=state, agent_type="loop", system_prompt="sys")
    first = await gen.__anext__()
    assert first.type == StreamEventType.MESSAGE_START
    second = await gen.__anext__()
    assert second.type == StreamEventType.TEXT

    # Closing the async generator mid-stream injects GeneratorExit; must NOT raise RuntimeError.
    await gen.aclose()
    await asyncio.wait_for(aclose_finished.wait(), timeout=2.0)
    assert "immediate" in cancelled_modes


# ---------------------------------------------------------------------------
# P0-7: Async threadpool offload for engine word counts, task board, llm_failover
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_p0_7_engine_word_count_notes_run_off_event_loop_thread(monkeypatch):
    loop_tid = threading.get_ident()
    observed_tids: list[int] = []

    def _fake_collect(file_ids, *, request_context=None):
        observed_tids.append(threading.get_ident())
        return [("Chapter 1", 1234)]

    monkeypatch.setattr(run_support, "collect_body_word_counts", _fake_collect)
    req_ctx = AgentRequestContext(project_id="p1", user_id="u1", session_id="s1")

    note = await engine._word_count_note(["f1"], req_ctx, english=True)
    cut_note = await engine._cut_word_count_note(["f1"], req_ctx, english=True)

    assert len(observed_tids) == 2
    assert all(tid != loop_tid for tid in observed_tids)
    assert '[Saved body word counts]' in note and '"Chapter 1": 1234 words' in note
    assert '[Saved word counts at cut]' in cut_note and '"Chapter 1": 1234 words' in cut_note


@pytest.mark.unit
@pytest.mark.asyncio
async def test_p0_7_llm_failover_async_helpers_offload_store_io(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "synthetic-minimax")
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-deepseek")
    llm_failover.reset_failover_state()

    loop_tid = threading.get_ident()
    store_tids: list[int] = []
    orig_with_store = llm_failover._with_store

    def _tracking_with_store(op):
        store_tids.append(threading.get_ident())
        return orig_with_store(op)

    monkeypatch.setattr(llm_failover, "_with_store", _tracking_with_store)
    try:
        ordered = await llm_failover.aordered_providers("sess-offload")
        await llm_failover.arecord_failure(
            ordered[0],
            "sess-offload",
            openai.InternalServerError(
                "503",
                response=httpx.Response(503, request=httpx.Request("POST", "https://llm.test")),
                body=None,
            ),
        )
        ordered_after = await llm_failover.aordered_providers("sess-offload")
        await llm_failover.arecord_success(ordered_after[0], "sess-offload", ordered_after)
    finally:
        llm_failover.reset_failover_state()

    assert len(store_tids) >= 4
    assert all(tid != loop_tid for tid in store_tids)


# ---------------------------------------------------------------------------
# P0-9: Failover pin TTL = 1200s and sliding TTL refresh on backup success
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_p0_9_pin_ttl_defaults_to_1200_and_refreshes_on_backup_success(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "synthetic-minimax")
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-deepseek")
    llm_failover.reset_failover_state()

    assert llm_failover.LLM_SESSION_PIN_TTL_S == 1200

    clock = [100.0]
    monkeypatch.setattr(llm_failover.time, "monotonic", lambda: clock[0])

    try:
        providers = llm_failover.ordered_providers("sess-slide")
        primary, backup = providers[0], providers[1]
        assert primary.slot == "primary" and backup.slot == "backup"

        # Failover pins session to backup until t = 100 + 1200 = 1300.
        llm_failover.record_failure(primary, "sess-slide", TimeoutError())
        ordered = await llm_failover.aordered_providers("sess-slide")
        assert ordered[0].slot == "backup"

        # At t = 900 (before original 1300 expiry), backup succeeds (ordered[0] == backup!).
        # Sliding TTL must refresh expiry to 900 + 1200 = 2100.
        clock[0] = 900.0
        await llm_failover.arecord_success(backup, "sess-slide", ordered)

        # At t = 1500 (past original 1300 expiry, before refreshed 2100 expiry), session stays pinned!
        clock[0] = 1500.0
        assert (await llm_failover.aordered_providers("sess-slide"))[0].slot == "backup"

        # At t = 2101 (past refreshed 2100 expiry), pin expires and returns to primary.
        clock[0] = 2101.0
        assert (await llm_failover.aordered_providers("sess-slide"))[0].slot == "primary"
    finally:
        llm_failover.reset_failover_state()


@pytest.mark.unit
def test_p0_9_thinking_off_backup_preference_does_not_create_spurious_pin(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "synthetic-minimax")
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-deepseek")
    llm_failover.reset_failover_state()

    try:
        ordered = llm_failover.ordered_providers("sess-think-off", thinking_enabled=False)
        assert ordered[0].slot == "backup"
        llm_failover.record_success(ordered[0], "sess-think-off", ordered, thinking_enabled=False)
        # Subsequent normal thinking_enabled=True call must STILL start on primary!
        assert llm_failover.ordered_providers("sess-think-off", thinking_enabled=True)[0].slot == "primary"
    finally:
        llm_failover.reset_failover_state()


@pytest.mark.unit
def test_p0_9_redis_store_set_if_exists_uses_xx_flag(monkeypatch):
    fake_client = MagicMock()
    fake_client.set.return_value = True
    store = llm_failover._RedisStore()
    monkeypatch.setattr(store, "_client", lambda: fake_client)

    assert store.set_if_exists("llm:session_slot:s1", "backup", 1200) is True
    fake_client.set.assert_called_once_with("llm:session_slot:s1", "backup", ex=1200, xx=True)


# ---------------------------------------------------------------------------
# P1-3: English i18n formatting & deduplicated concise self-check checklist
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_p1_3_english_word_count_formatting_has_no_chinese():
    en_saved = run_support.format_body_word_counts([("Chapter 1", 1500), ("", 400)], english=True)
    en_cut = run_support.format_cut_body_word_counts([("Chapter 2", 900)], english=True)

    assert '[Saved body word counts]' in en_saved
    assert '- "Chapter 1": 1500 words' in en_saved
    assert '- 400 words' in en_saved
    assert '[Saved word counts at cut]' in en_cut
    assert '- "Chapter 2": 900 words' in en_cut
    for bad in ("《", "》", "、", "字", "本轮正文字数", "截断处"):
        assert bad not in en_saved
        assert bad not in en_cut

    zh_saved = run_support.format_body_word_counts([("第一章", 1500)], english=False)
    assert "[本轮正文字数]" in zh_saved and "《第一章》1500 字" in zh_saved


@pytest.mark.unit
def test_p1_3_deduplicated_concise_self_check_shared_across_templates():
    assert engine._BODY_SAVED_SELF_CHECK in engine._BODY_SAVED_CONTEXT
    assert engine._BODY_SAVED_SELF_CHECK in engine._BODY_SAVED_REPLIED_CONTEXT
    assert engine._BODY_SAVED_SELF_CHECK_EN in engine._BODY_SAVED_CONTEXT_EN
    assert engine._BODY_SAVED_SELF_CHECK_EN in engine._BODY_SAVED_REPLIED_CONTEXT_EN
    # Concise checklist stays compact (well under the old ~450-char 12-item wall).
    assert len(engine._BODY_SAVED_SELF_CHECK) < 260


@pytest.mark.unit
@pytest.mark.asyncio
async def test_p1_3_english_loop_continuation_uses_english_titles_and_counts(monkeypatch):
    seen: list[str] = []
    req_ctx = AgentRequestContext(project_id="p1", user_id="u1", session_id="s1")

    monkeypatch.setattr(
        run_support,
        "collect_body_word_counts",
        lambda file_ids, *, request_context=None: [("Chapter 1", 1200)],
    )

    turn_idx = 0

    async def _fake_runner(*, state, **_kwargs):
        nonlocal turn_idx
        seen.append(state["user_message"])
        turn_idx += 1
        if turn_idx == 1:
            req_ctx.mark_chapter_body_saved("ch1", "Chapter 1", "draft")
            yield StreamEvent(
                type=StreamEventType.TOOL_USE,
                data={"id": "c1", "name": "write_chapter", "status": "complete", "input": {}},
            )
            yield StreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={
                    "tool_use_id": "c1",
                    "name": "write_chapter",
                    "result": {"content": [{"type": "text", "text": json.dumps({"status": "success"})}]},
                },
            )
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "<file>\nEnglish prose.\n</file>"})
        else:
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Chapter 1 is saved (1200 words)."})

    state = {
        "user_message": "Write chapter 1",
        "messages": [],
        "system_prompt": "sys",
        "language": "en-US",
        "project_id": "p1",
        "user_id": "u1",
        "session_id": "s1",
        "request_context": req_ctx,
    }
    with patch("agent.loop.engine.run_openai_agents_streaming_agent", _fake_runner):
        _ = [ev async for ev in engine.run_loop_agent_streaming(state, request_context=req_ctx)]

    assert len(seen) == 2
    followup = seen[1]
    assert '"Chapter 1"' in followup
    assert "[Saved body word counts]" in followup
    for bad in ("《", "》", "、", "（见下）", "本轮正文字数"):
        assert bad not in followup


# ---------------------------------------------------------------------------
# P1-5: _PendingEmptyFileState.clear_all resets _saved_body_types and _closed
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_p1_5_pending_empty_file_state_clear_all_resets_saved_body_types_and_closed():
    state = _PendingEmptyFileState()
    state.mark_chapter_body_saved("o1", "Outline", "outline")
    state.close()
    assert state.is_closed() is True
    assert state.saved_content_body_ids() == []

    state.clear_all()
    assert state.is_closed() is False
    # Re-marking the same file_id without a file_type must not inherit stale "outline" type.
    state.mark_chapter_body_saved("o1", "Chapter 1", "")
    assert state.saved_content_body_ids() == ["o1"]


# ---------------------------------------------------------------------------
# P1-6: empty_files soft-delete advances file.updated_at
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_p1_6_rollback_unfinished_empty_files_advances_updated_at(db_session):
    user, project, empty = _seed_user_project_file(db_session, content="", file_type="draft")
    before_updated_at = empty.updated_at
    assert before_updated_at is not None

    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-p1-6",
    )
    rolled_back = empty_files.rollback_unfinished_empty_files(
        [{"file_id": empty.id, "title": empty.title, "updated_at": before_updated_at.isoformat()}],
        request_context=req_ctx,
    )

    assert rolled_back == {empty.id}
    db_session.expire_all()
    refreshed = db_session.get(File, empty.id)
    assert refreshed is not None
    assert refreshed.is_deleted is True
    assert refreshed.deleted_at is not None
    assert refreshed.updated_at is not None
    assert refreshed.updated_at > before_updated_at
