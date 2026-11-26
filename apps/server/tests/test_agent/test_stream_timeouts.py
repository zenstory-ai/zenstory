"""Unit tests for two-tier LLM stream timeouts and non-blocking teardown of wedged streams."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from agent.core import llm_failover, llm_providers
from agent.openai_agents import model as sdk_model
from utils.request_context import bind_request_context, reset_request_context


def _ev(event_type: str, delta: str | None = None) -> SimpleNamespace:
    if delta is None:
        return SimpleNamespace(type=event_type)
    return SimpleNamespace(type=event_type, delta=delta)


# ---------------------------------------------------------------------------
# 1. ProviderKind & ModelProvider env parsing
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_provider_timeout_defaults_and_env_overrides(monkeypatch):
    for key in (
        "MINIMAX_FIRST_EVENT_TIMEOUT_S",
        "MINIMAX_CLIENT_TIMEOUT_S",
        "DEEPSEEK_FIRST_EVENT_TIMEOUT_S",
        "DEEPSEEK_CLIENT_TIMEOUT_S",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-minimax")
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")

    minimax, deepseek = llm_providers.configured_providers()
    assert minimax.first_event_timeout_s == 75.0
    assert minimax.stream_idle_timeout_s == 120.0
    assert minimax.kind.read_timeout_s() == 120.0
    assert deepseek.first_event_timeout_s == 45.0
    assert deepseek.stream_idle_timeout_s == 45.0
    assert deepseek.kind.read_timeout_s() == 45.0

    # Custom positive overrides
    monkeypatch.setenv("MINIMAX_FIRST_EVENT_TIMEOUT_S", "60.5")
    monkeypatch.setenv("MINIMAX_CLIENT_TIMEOUT_S", "150")
    monkeypatch.setenv("DEEPSEEK_FIRST_EVENT_TIMEOUT_S", "30")
    monkeypatch.setenv("DEEPSEEK_CLIENT_TIMEOUT_S", "40")
    minimax, deepseek = llm_providers.configured_providers()
    assert minimax.first_event_timeout_s == 60.5
    assert minimax.stream_idle_timeout_s == 150.0
    assert deepseek.first_event_timeout_s == 30.0
    assert deepseek.stream_idle_timeout_s == 40.0

    # 0 disables stream timeout tiers while keeping httpx read_timeout_s safely positive
    monkeypatch.setenv("MINIMAX_FIRST_EVENT_TIMEOUT_S", "0")
    monkeypatch.setenv("MINIMAX_CLIENT_TIMEOUT_S", "0")
    minimax, _ = llm_providers.configured_providers()
    assert minimax.first_event_timeout_s == 0.0
    assert minimax.stream_idle_timeout_s == 0.0
    assert minimax.kind.read_timeout_s() == 120.0

    # Invalid / negative / non-finite values fall back safely to provider defaults
    for invalid in ("-5", "abc", "nan", "inf", "-inf", ""):
        monkeypatch.setenv("MINIMAX_FIRST_EVENT_TIMEOUT_S", invalid)
        monkeypatch.setenv("MINIMAX_CLIENT_TIMEOUT_S", invalid)
        minimax, _ = llm_providers.configured_providers()
        assert minimax.first_event_timeout_s == 75.0
        assert minimax.stream_idle_timeout_s == 120.0


@pytest.mark.unit
def test_direct_model_provider_construction_resolves_default_timeouts(monkeypatch):
    monkeypatch.delenv("MINIMAX_FIRST_EVENT_TIMEOUT_S", raising=False)
    monkeypatch.delenv("MINIMAX_CLIENT_TIMEOUT_S", raising=False)

    provider = llm_providers.ModelProvider(
        slot="primary",
        kind=llm_providers.MINIMAX,
        model="MiniMax-M3.1-Flash",
        api_key="sk-test",
        base_url="https://api.minimaxi.com/v1",
        reasoning_effort="high",
    )
    assert provider.first_event_timeout_s == 75.0
    assert provider.stream_idle_timeout_s == 120.0

    zero_provider = llm_providers.ModelProvider(
        slot="primary",
        kind=llm_providers.MINIMAX,
        model="MiniMax-M3.1-Flash",
        api_key="sk-test",
        base_url="https://api.minimaxi.com/v1",
        reasoning_effort="high",
        first_event_timeout_s=0.0,
        stream_idle_timeout_s=0.0,
    )
    assert zero_provider.first_event_timeout_s == 0.0
    assert zero_provider.stream_idle_timeout_s == 0.0


# ---------------------------------------------------------------------------
# 2. _iter_with_stream_timeouts: Phase 1, Phase 2, handoff, disable (0)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_phase1_first_event_timeout_not_reset_by_lifecycle_or_empty_deltas(caplog):
    async def keepalive_only_stream():
        yield _ev("response.created")
        yield _ev("response.output_item.added")
        for _ in range(10):
            await asyncio.sleep(0.015)
            yield _ev("response.output_text.delta", "")

    with caplog.at_level("WARNING"), pytest.raises(TimeoutError, match="first_event"):
        async for _ in sdk_model._iter_with_stream_timeouts(
            keepalive_only_stream(),
            first_event_timeout_s=0.06,
            idle_timeout_s=0.5,
            provider_name="minimax",
        ):
            pass

    timeout_logs = [r.custom_fields for r in caplog.records if r.getMessage() == "LLM stream timeout"]
    assert timeout_logs and timeout_logs[-1]["timeout_phase"] == "first_event"
    assert timeout_logs[-1]["provider"] == "minimax"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_phase1_to_phase2_handoff_switches_budget_after_first_meaningful_delta():
    """First meaningful delta switches from short first_event timeout to longer idle timeout."""

    async def handoff_stream():
        yield _ev("response.created")
        await asyncio.sleep(0.02)
        yield _ev("response.reasoning_text.delta", "开始思考")
        # Gap of 0.10s exceeds first_event_timeout_s (0.06s), but stays within idle_timeout_s (0.25s).
        await asyncio.sleep(0.10)
        yield _ev("response.output_text.delta", "正文")
        yield _ev("response.completed")

    events = [
        ev
        async for ev in sdk_model._iter_with_stream_timeouts(
            handoff_stream(),
            first_event_timeout_s=0.06,
            idle_timeout_s=0.25,
            provider_name="minimax",
        )
    ]
    assert [ev.type for ev in events] == [
        "response.created",
        "response.reasoning_text.delta",
        "response.output_text.delta",
        "response.completed",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_phase2_idle_timeout_fires_distinctly_from_first_event(caplog):
    async def stall_after_first_delta():
        yield _ev("response.reasoning_text.delta", "先思考")
        for _ in range(10):
            await asyncio.sleep(0.015)
            yield _ev("response.output_text.delta", "")

    with caplog.at_level("WARNING"), pytest.raises(TimeoutError, match="idle timeout") as exc_info:
        async for _ in sdk_model._iter_with_stream_timeouts(
            stall_after_first_delta(),
            first_event_timeout_s=0.5,
            idle_timeout_s=0.05,
            provider_name="minimax",
        ):
            pass

    assert "first_event" not in str(exc_info.value)
    timeout_logs = [r.custom_fields for r in caplog.records if r.getMessage() == "LLM stream timeout"]
    assert timeout_logs and timeout_logs[-1]["timeout_phase"] == "idle"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_zero_disables_phase1_only():
    async def slow_ttft_then_midstream_stall():
        yield _ev("response.created")
        await asyncio.sleep(0.10)
        yield _ev("response.output_text.delta", "首字")
        await asyncio.sleep(0.25)

    seen: list[str] = []
    with pytest.raises(TimeoutError, match="idle timeout"):
        async for ev in sdk_model._iter_with_stream_timeouts(
            slow_ttft_then_midstream_stall(),
            first_event_timeout_s=0.0,
            idle_timeout_s=0.05,
            provider_name="minimax",
        ):
            seen.append(ev.type)

    assert seen == ["response.created", "response.output_text.delta"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_zero_disables_phase2_only():
    async def fast_ttft_then_long_gap():
        await asyncio.sleep(0.01)
        yield _ev("response.reasoning_text.delta", "首段")
        await asyncio.sleep(0.10)
        yield _ev("response.output_text.delta", "尾段")
        yield _ev("response.completed")

    events = [
        ev
        async for ev in sdk_model._iter_with_stream_timeouts(
            fast_ttft_then_long_gap(),
            first_event_timeout_s=0.05,
            idle_timeout_s=0.0,
            provider_name="minimax",
        )
    ]
    assert [ev.type for ev in events] == [
        "response.reasoning_text.delta",
        "response.output_text.delta",
        "response.completed",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_zero_disables_both_timers():
    async def slow_both_phases():
        await asyncio.sleep(0.06)
        yield _ev("response.output_text.delta", "a")
        await asyncio.sleep(0.06)
        yield _ev("response.completed")

    events = [
        ev
        async for ev in sdk_model._iter_with_stream_timeouts(
            slow_both_phases(),
            first_event_timeout_s=0.0,
            idle_timeout_s=0.0,
            provider_name="minimax",
        )
    ]
    assert len(events) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_iter_with_idle_timeout_compat_wrapper():
    async def stalled():
        yield _ev("response.created")
        await asyncio.sleep(0.3)

    with pytest.raises(TimeoutError, match="first_event"):
        async for _ in sdk_model._iter_with_idle_timeout(stalled(), 0.04, "minimax"):
            pass


# ---------------------------------------------------------------------------
# 3. Non-blocking teardown of wedged streams (releaseIterator pattern)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_timeout_does_not_block_on_hanging_aclose(monkeypatch):
    """When aiter.aclose() hangs for 10s, timeout must raise promptly (<0.5s) and bound cleanup."""
    monkeypatch.setattr(sdk_model, "_STREAM_TEARDOWN_TIMEOUT_S", 0.15)
    aclose_started = asyncio.Event()
    aclose_cancelled = asyncio.Event()

    class _WedgedCloseStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(10.0)

        async def aclose(self):
            aclose_started.set()
            try:
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                aclose_cancelled.set()
                raise

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="first_event"):
        async for _ in sdk_model._iter_with_stream_timeouts(
            _WedgedCloseStream(),
            first_event_timeout_s=0.05,
            idle_timeout_s=0.5,
            provider_name="minimax",
        ):
            pass
    elapsed = time.monotonic() - started
    assert elapsed < 0.5, f"timeout blocked on hanging aclose(): took {elapsed:.3f}s"

    # Background teardown runs, cancels the wedged aclose() at the bounded teardown timeout,
    # and removes itself from _pending_stream_teardowns.
    await asyncio.wait_for(aclose_started.wait(), timeout=0.5)
    await asyncio.wait_for(aclose_cancelled.wait(), timeout=0.5)
    await asyncio.sleep(0.02)
    assert not sdk_model._pending_stream_teardowns


@pytest.mark.unit
@pytest.mark.asyncio
async def test_timeout_after_keepalive_deadline_expiry_does_not_block_on_hanging_aclose(monkeypatch):
    """Even when timeout triggers at remaining <= 0 between keepalive frames, aclose() is detached."""
    monkeypatch.setattr(sdk_model, "_STREAM_TEARDOWN_TIMEOUT_S", 0.1)
    aclose_started = asyncio.Event()

    class _KeepaliveThenHangCloseStream:
        def __init__(self) -> None:
            self._count = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            self._count += 1
            if self._count == 1:
                await asyncio.sleep(0.06)
                return _ev("response.created")
            await asyncio.sleep(10.0)

        async def aclose(self):
            aclose_started.set()
            await asyncio.sleep(10.0)

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="first_event"):
        async for _ in sdk_model._iter_with_stream_timeouts(
            _KeepaliveThenHangCloseStream(),
            first_event_timeout_s=0.05,
            idle_timeout_s=0.5,
            provider_name="minimax",
        ):
            pass
    elapsed = time.monotonic() - started
    assert elapsed < 0.5
    await asyncio.wait_for(aclose_started.wait(), timeout=0.5)
    await asyncio.sleep(0.15)
    assert not sdk_model._pending_stream_teardowns


@pytest.mark.unit
@pytest.mark.asyncio
async def test_timeout_does_not_block_on_generator_finally_that_hangs(monkeypatch):
    """When idle timeout expires at remaining <= 0, a suspended generator's hanging finally in aclose() is detached."""
    monkeypatch.setattr(sdk_model, "_STREAM_TEARDOWN_TIMEOUT_S", 0.1)
    finally_started = asyncio.Event()

    async def _hung_finally_stream():
        try:
            yield _ev("response.output_text.delta", "partial")
            yield _ev("response.output_text.delta", "")
            await asyncio.sleep(10.0)
        finally:
            finally_started.set()
            await asyncio.sleep(10.0)

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="idle timeout"):
        async for ev in sdk_model._iter_with_stream_timeouts(
            _hung_finally_stream(),
            first_event_timeout_s=0.5,
            idle_timeout_s=0.05,
            provider_name="minimax",
        ):
            if ev.delta == "":
                # Push past idle_timeout_s while the upstream generator is suspended at yield.
                await asyncio.sleep(0.06)
    elapsed = time.monotonic() - started
    assert elapsed < 0.5
    await asyncio.wait_for(finally_started.wait(), timeout=0.5)
    await asyncio.sleep(0.15)
    assert not sdk_model._pending_stream_teardowns


@pytest.mark.unit
@pytest.mark.asyncio
async def test_teardown_external_cancellation_aborts_without_falling_through_to_close():
    """External cancellation (e.g. event-loop shutdown) must abort _teardown instead of calling close()."""
    aclose_started = asyncio.Event()
    close_called = False

    class _InnerIter:
        async def aclose(self):
            aclose_started.set()
            await asyncio.sleep(10.0)

    class _OuterStream:
        def __init__(self, inner: _InnerIter) -> None:
            self._inner = inner

        def __aiter__(self):
            return self._inner

        async def close(self):
            nonlocal close_called
            close_called = True

    inner = _InnerIter()
    outer = _OuterStream(inner)
    task = sdk_model._schedule_stream_teardown(inner, outer, timeout_s=5.0)
    assert task is not None
    await asyncio.wait_for(aclose_started.wait(), timeout=0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert close_called is False
    assert task not in sdk_model._pending_stream_teardowns


@pytest.mark.unit
@pytest.mark.asyncio
async def test_teardown_does_not_double_close_dual_method_stream():
    """When stream is its own iterator with both aclose() and close(), only aclose() is called."""
    calls: list[str] = []

    class _DualMethodStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(10.0)

        async def aclose(self):
            calls.append("aclose")

        async def close(self):
            calls.append("close")

    stream = _DualMethodStream()
    task = sdk_model._schedule_stream_teardown(stream, stream, timeout_s=1.0)
    assert task is not None
    await task
    assert calls == ["aclose"]


@pytest.mark.unit
def test_meaningful_stream_progress_handles_none_delta_and_response_error():
    assert sdk_model._is_meaningful_stream_progress(SimpleNamespace(type="response.output_text.delta", delta=None)) is False
    assert sdk_model._is_meaningful_stream_progress(SimpleNamespace(type="response.output_text.delta", delta="")) is False
    assert sdk_model._is_meaningful_stream_progress(SimpleNamespace(type="response.output_text.delta", delta="字")) is True
    assert sdk_model._is_meaningful_stream_progress(SimpleNamespace(type="response.output_text.delta")) is True
    assert sdk_model._is_meaningful_stream_progress(SimpleNamespace(type="response.error")) is True
    assert sdk_model._is_meaningful_stream_progress(SimpleNamespace(type="error")) is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_normal_exit_and_caller_cancellation_close_stream_inline():
    normal_closed = False
    cancelled_closed = False

    async def _normal_stream():
        nonlocal normal_closed
        try:
            yield _ev("response.output_text.delta", "ok")
            yield _ev("response.completed")
        finally:
            normal_closed = True

    events = [
        ev
        async for ev in sdk_model._iter_with_stream_timeouts(
            _normal_stream(),
            first_event_timeout_s=1.0,
            idle_timeout_s=1.0,
            provider_name="minimax",
        )
    ]
    assert len(events) == 2
    assert normal_closed is True

    async def _slow_stream():
        nonlocal cancelled_closed
        try:
            yield _ev("response.output_text.delta", "first")
            await asyncio.sleep(10.0)
        finally:
            cancelled_closed = True

    gen = sdk_model._iter_with_stream_timeouts(
        _slow_stream(),
        first_event_timeout_s=5.0,
        idle_timeout_s=5.0,
        provider_name="minimax",
    )
    first = await gen.__anext__()
    assert first.delta == "first"
    await gen.aclose()
    # P0-6: GeneratorExit schedules background teardown instead of awaiting inline in finally.
    await asyncio.sleep(0)
    assert cancelled_closed is True


# ---------------------------------------------------------------------------
# 4. FailoverChatModel integration & single-run key-pinning invariant
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_first_event_timeout_with_wedged_aclose_fails_over_promptly_to_backup(monkeypatch, caplog):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-mm")
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
    monkeypatch.setenv("MINIMAX_FIRST_EVENT_TIMEOUT_S", "0.05")
    monkeypatch.setenv("MINIMAX_CLIENT_TIMEOUT_S", "5.0")
    monkeypatch.setattr(sdk_model, "_STREAM_TEARDOWN_TIMEOUT_S", 0.1)
    llm_failover.reset_failover_state()
    llm_providers.reset_key_routing_state()

    class _WedgedPrimaryModel:
        def stream_response(self, *_args, **_kwargs):
            class _WedgedStream:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    await asyncio.sleep(10.0)

                async def aclose(self):
                    await asyncio.sleep(10.0)

            return _WedgedStream()

    class _FastBackupModel:
        async def stream_response(self, *_args, **_kwargs):
            yield _ev("response.output_text.delta", "备源秒回")
            yield _ev("response.completed")

    models = {"minimax": _WedgedPrimaryModel(), "deepseek": _FastBackupModel()}
    monkeypatch.setattr(sdk_model, "_sdk_model_for", lambda p: models[p.name])
    monkeypatch.setattr(sdk_model, "_settings_for", lambda _p, s: s)

    model = sdk_model.build_failover_chat_model("sess-first-event-fo")
    started = time.monotonic()
    with caplog.at_level("WARNING"):
        events = [
            ev
            async for ev in model.stream_response(
                "sys", [], SimpleNamespace(reasoning=None), [], None, [], None
            )
        ]
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert model.served_provider_name == "deepseek"
    assert model.unrecovered_provider_error is None
    assert any(getattr(ev, "delta", None) == "备源秒回" for ev in events)
    diagnostics = [r.custom_fields for r in caplog.records if r.getMessage() == "LLM provider attempt failed"]
    assert diagnostics and diagnostics[0]["phase"] == "stream_start"
    assert diagnostics[0]["failure_category"] == "timeout"
    await asyncio.sleep(0.15)
    llm_failover.reset_failover_state()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_first_event_timeout_preserves_single_run_minimax_key_pinning(monkeypatch):
    """Dual MiniMax keys belong to one provider slot and never fail over to each other mid-run."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-mm-primary")
    monkeypatch.setenv("MINIMAX_SECONDARY_API_KEY", "sk-mm-secondary")
    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "50")
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    monkeypatch.setenv("MINIMAX_FIRST_EVENT_TIMEOUT_S", "0.05")
    monkeypatch.setenv("MINIMAX_CLIENT_TIMEOUT_S", "5.0")
    llm_failover.reset_failover_state()
    llm_providers.reset_key_routing_state()

    attempted_slots: list[str] = []

    class _StalledModel:
        def __init__(self, key_slot: str) -> None:
            self._key_slot = key_slot

        async def stream_response(self, *_args, **_kwargs):
            attempted_slots.append(self._key_slot)
            yield _ev("response.created")
            await asyncio.sleep(1.0)

    monkeypatch.setattr(sdk_model, "_sdk_model_for", lambda p: _StalledModel(p.key_slot))
    monkeypatch.setattr(sdk_model, "_settings_for", lambda _p, s: s)

    tokens = bind_request_context(agent_run_id="pinned-run-timeout")
    try:
        model = sdk_model.build_failover_chat_model("sess-pinned-timeout")
        with pytest.raises(TimeoutError, match="first_event"):
            async for _ in model.stream_response(
                "sys", [], SimpleNamespace(reasoning=None), [], None, [], None
            ):
                pass
        assert len(attempted_slots) == 1
        assert isinstance(model.unrecovered_provider_error, TimeoutError)
        # Subsequent provider resolution in the same run stays pinned to the exact same key slot.
        assert llm_failover.ordered_providers("sess-pinned-timeout")[0].key_slot == attempted_slots[0]
    finally:
        reset_request_context(tokens)
        llm_failover.reset_failover_state()
        llm_providers.reset_key_routing_state()
