"""
Tests for telemetry metrics module.

Tests MetricsCollector functionality including counters, gauges, histograms, and timers.
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.core.metrics import (
    AGENT_CLARIFICATION_TOTAL,
    AGENT_REQUESTS_DURATION_MS,
    AGENT_REQUESTS_ERRORS,
    AGENT_REQUESTS_TOTAL,
    CONTEXT_ITEMS_COUNT,
    CONTEXT_TOKENS_TOTAL,
    LLM_CALLS_DURATION_MS,
    LLM_CALLS_ERRORS,
    LLM_CALLS_TOTAL,
    LLM_TOKENS_CACHED,
    LLM_TOKENS_INPUT,
    LLM_TOKENS_OUTPUT,
    SKILL_CACHE_HITS,
    SKILL_CACHE_MISSES,
    SKILL_MATCHES_TOTAL,
    STEERING_MESSAGES_PENDING,
    STEERING_MESSAGES_TOTAL,
    TOOL_CALLS_DURATION_MS,
    TOOL_CALLS_ERRORS,
    TOOL_CALLS_TOTAL,
    MetricsCollector,
    get_metrics_collector,
    reset_metrics_collector,
)


@pytest.fixture(autouse=True)
def _reset_global_metrics():
    """Reset global collector between tests to avoid cross-test pollution."""
    reset_metrics_collector()
    yield
    reset_metrics_collector()


@pytest.mark.unit
class TestMetricsCollector:
    """Test MetricsCollector basic functionality."""

    def test_create_collector(self):
        """Test creating a new collector."""
        collector = MetricsCollector()
        assert collector._counters == {}
        assert collector._gauges == {}
        assert collector._histograms == {}

    def test_counter_increment(self):
        """Test counter increment."""
        collector = MetricsCollector()
        collector.increment_counter("test.counter")
        assert collector._counters["test.counter"].value == 1

        collector.increment_counter("test.counter", 5)
        assert collector._counters["test.counter"].value == 6

    def test_counter_with_labels(self):
        """Test counter with labels."""
        collector = MetricsCollector()
        collector.increment_counter("test.counter", labels={"env": "test"})
        assert "test.counter{env=test}" in collector._counters

    def test_gauge_set(self):
        """Test gauge set."""
        collector = MetricsCollector()
        collector.set_gauge("test.gauge", 100)
        assert collector._gauges["test.gauge"].value == 100

        collector.set_gauge("test.gauge", 50)
        assert collector._gauges["test.gauge"].value == 50

    def test_histogram_record(self):
        """Test histogram record."""
        collector = MetricsCollector()
        collector.observe_histogram("test.latency", 100)
        collector.observe_histogram("test.latency", 200)
        collector.observe_histogram("test.latency", 300)

        assert len(collector._histograms["test.latency"].values) == 3
        assert collector._histograms["test.latency"].values == [100, 200, 300]


@pytest.mark.unit
class TestTimerContext:
    """Test timer context manager."""

    def test_timer_records_duration(self):
        """Test timer records duration."""
        collector = MetricsCollector()

        with collector.time_histogram("test.duration"):
            time.sleep(0.01)

        assert "test.duration" in collector._histograms
        duration = collector._histograms["test.duration"].values[0]
        assert duration >= 10  # At least 10ms

    def test_timer_on_exception(self):
        """Test timer records even when exception occurs."""
        collector = MetricsCollector()

        with pytest.raises(ValueError):
            with collector.time_histogram("test.error_duration"):
                raise ValueError("test error")

        # Timer should still record
        assert "test.error_duration" in collector._histograms

    def test_timer_with_labels(self):
        """Test timer with labels."""
        collector = MetricsCollector()

        with collector.time_histogram("test.tagged", labels={"operation": "test"}):
            pass

        assert "test.tagged{operation=test}" in collector._histograms


@pytest.mark.unit
class TestMetricNames:
    """Test metric name constants."""

    def test_agent_metrics_exist(self):
        """Test agent metric names."""
        assert AGENT_REQUESTS_TOTAL == "agent.requests.total"
        assert AGENT_REQUESTS_DURATION_MS == "agent.requests.duration_ms"
        assert AGENT_REQUESTS_ERRORS == "agent.requests.errors"
        assert AGENT_CLARIFICATION_TOTAL == "agent.clarification.total"

    def test_llm_metrics_exist(self):
        """Test LLM metric names."""
        assert LLM_CALLS_TOTAL == "llm.calls.total"
        assert LLM_CALLS_DURATION_MS == "llm.calls.duration_ms"
        assert LLM_CALLS_ERRORS == "llm.calls.errors"
        assert LLM_TOKENS_INPUT == "llm.tokens.input"
        assert LLM_TOKENS_OUTPUT == "llm.tokens.output"
        assert LLM_TOKENS_CACHED == "llm.tokens.cached"

    def test_tool_metrics_exist(self):
        """Test tool metric names."""
        assert TOOL_CALLS_TOTAL == "tool.calls.total"
        assert TOOL_CALLS_DURATION_MS == "tool.calls.duration_ms"
        assert TOOL_CALLS_ERRORS == "tool.calls.errors"

    def test_context_metrics_exist(self):
        """Test context metric names."""
        assert CONTEXT_TOKENS_TOTAL == "context.tokens.total"
        assert CONTEXT_ITEMS_COUNT == "context.items.count"

    def test_steering_metrics_exist(self):
        """Test steering metric names."""
        assert STEERING_MESSAGES_TOTAL == "steering.messages.total"
        assert STEERING_MESSAGES_PENDING == "steering.messages.pending"

    def test_skill_metrics_exist(self):
        """Test skill metric names."""
        assert SKILL_MATCHES_TOTAL == "skill.matches.total"
        assert SKILL_CACHE_HITS == "skill.cache.hits"
        assert SKILL_CACHE_MISSES == "skill.cache.misses"


@pytest.mark.unit
class TestGetMetricsCollector:
    """Test global collector singleton."""

    def test_returns_same_instance(self):
        """Test get_metrics_collector returns singleton."""
        collector1 = get_metrics_collector()
        collector2 = get_metrics_collector()
        assert collector1 is collector2

    def test_can_record_to_global(self):
        """Test can record to global collector."""
        collector = get_metrics_collector()
        collector.increment_counter("test.global_counter")
        assert "test.global_counter" in collector._counters

    def test_reset_metrics_collector(self):
        """Test reset_metrics_collector clears all metrics."""
        collector = get_metrics_collector()
        collector.increment_counter("test.counter")
        collector.set_gauge("test.gauge", 100)
        collector.observe_histogram("test.histogram", 50)

        reset_metrics_collector()

        assert len(collector._counters) == 0
        assert len(collector._gauges) == 0
        assert len(collector._histograms) == 0


@pytest.mark.unit
class TestHistogramSummary:
    """Test histogram summary statistics."""

    def test_empty_histogram(self):
        """Test empty histogram summary."""
        collector = MetricsCollector()
        collector.observe_histogram("test.empty", 10)
        collector._histograms["test.empty"].values.clear()

        summary = collector._histograms["test.empty"].summary()
        assert summary == {"count": 0, "sum": 0, "avg": 0, "min": 0, "max": 0}

    def test_histogram_summary(self):
        """Test histogram summary with values."""
        collector = MetricsCollector()
        collector.observe_histogram("test.values", 10)
        collector.observe_histogram("test.values", 20)
        collector.observe_histogram("test.values", 30)

        summary = collector._histograms["test.values"].summary()
        assert summary["count"] == 3
        assert summary["sum"] == 60
        assert summary["avg"] == 20
        assert summary["min"] == 10
        assert summary["max"] == 30


@pytest.mark.unit
class TestGetAllMetrics:
    """Test get_all_metrics method."""

    def test_get_all_metrics(self):
        """Test retrieving all metrics."""
        collector = MetricsCollector()
        collector.increment_counter("test.counter", 5)
        collector.set_gauge("test.gauge", 42.5)
        collector.observe_histogram("test.histogram", 100)

        all_metrics = collector.get_all_metrics()

        assert "counters" in all_metrics
        assert "gauges" in all_metrics
        assert "histograms" in all_metrics

        assert all_metrics["counters"]["test.counter"]["value"] == 5
        assert all_metrics["gauges"]["test.gauge"]["value"] == 42.5
        assert "summary" in all_metrics["histograms"]["test.histogram"]


@pytest.mark.unit
class TestAgentServiceMetrics:
    """Test metrics emitted by AgentService.process_stream."""

    @pytest.mark.asyncio
    async def test_process_stream_records_request_and_context_metrics(self):
        """Successful request should record request/context metrics."""
        from agent.core.events import content_event
        from agent.service import AgentService

        class DummySteeringQueue:
            async def get_pending(self):
                return []

        class DummyStreamAdapter:
            def __init__(self):
                self._metadata = {
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 12, "output_tokens": 8},
                }

            async def process_workflow_events(self, _events):
                yield content_event("ok")

            def get_last_message_metadata(self):
                return self._metadata

        session_data = SimpleNamespace(
            context_data=SimpleNamespace(
                context="ctx",
                items=[{"id": "1"}, {"id": "2"}],
                token_estimate=256,
            ),
            history_messages=[],
        )
        mock_loader = MagicMock()
        mock_loader.load_session_with_compaction = AsyncMock(return_value=session_data)
        mock_message_manager = MagicMock()
        mock_message_manager.build_system_prompt.return_value = "system"
        mock_message_manager.build_turn_context.return_value = ""
        mock_message_manager.save_messages = AsyncMock()
        mock_skill_injector = MagicMock()
        mock_skill_injector.build_skill_catalog.return_value = None

        with (
            patch("agent.service.SessionLoader", return_value=mock_loader),
            patch("agent.service.AgentService._resolve_or_create_chat_session_id", return_value="session-1"),
            patch("agent.service.MessageManager", return_value=mock_message_manager),
            patch("agent.service.get_skill_context_injector", return_value=mock_skill_injector),
            patch("agent.service.create_stream_adapter", return_value=DummyStreamAdapter()),
            patch("agent.service.create_steering_queue_async", new=AsyncMock(return_value=DummySteeringQueue())),
            patch("agent.service.cleanup_steering_queue_async", new=AsyncMock()),
            patch("agent.service.run_writing_workflow_streaming", return_value=object()),
        ):
            service = AgentService(context_assembler=MagicMock())
            events = [
                event
                async for event in service.process_stream(
                    project_id="project-1",
                    user_id="user-1",
                    message="hello",
                    session=MagicMock(),
                )
            ]

        assert events

        metrics = get_metrics_collector().get_all_metrics()
        counters = metrics["counters"]
        histograms = metrics["histograms"]

        assert counters[AGENT_REQUESTS_TOTAL]["value"] == 1
        assert counters[CONTEXT_TOKENS_TOTAL]["value"] == 256
        assert counters[CONTEXT_ITEMS_COUNT]["value"] == 2
        assert AGENT_REQUESTS_ERRORS not in counters
        assert histograms[AGENT_REQUESTS_DURATION_MS]["summary"]["count"] == 1

    @pytest.mark.asyncio
    async def test_process_stream_records_error_metrics_on_failure(self):
        """Failing request should increase error counter and still record duration."""
        from agent.service import AgentService

        class DummySteeringQueue:
            async def get_pending(self):
                return []

        mock_loader = MagicMock()
        mock_loader.load_session_with_compaction = AsyncMock(side_effect=RuntimeError("boom"))

        with (
            patch("agent.service.SessionLoader", return_value=mock_loader),
            patch("agent.service.AgentService._resolve_or_create_chat_session_id", return_value="session-1"),
            patch("agent.service.create_steering_queue_async", new=AsyncMock(return_value=DummySteeringQueue())),
            patch("agent.service.cleanup_steering_queue_async", new=AsyncMock()),
        ):
            service = AgentService(context_assembler=MagicMock())
            events = [
                event
                async for event in service.process_stream(
                    project_id="project-1",
                    user_id="user-1",
                    message="hello",
                    session=MagicMock(),
                )
            ]

        assert any("event: error" in event for event in events)

        metrics = get_metrics_collector().get_all_metrics()
        counters = metrics["counters"]
        histograms = metrics["histograms"]

        assert counters[AGENT_REQUESTS_TOTAL]["value"] == 1
        assert counters[AGENT_REQUESTS_ERRORS]["value"] == 1
        assert histograms[AGENT_REQUESTS_DURATION_MS]["summary"]["count"] == 1
