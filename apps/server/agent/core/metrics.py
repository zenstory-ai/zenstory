"""
Metrics collection for agent operations.

Provides a centralized metrics collector for tracking agent performance,
tool usage, and other operational metrics.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Final

from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)


# ============================================================================
# Metric Name Constants
# ============================================================================

# Agent metrics
AGENT_REQUESTS_TOTAL: Final[str] = "agent.requests.total"
AGENT_REQUESTS_DURATION_MS: Final[str] = "agent.requests.duration_ms"
AGENT_REQUESTS_ERRORS: Final[str] = "agent.requests.errors"
AGENT_ITERATIONS: Final[str] = "agent.iterations"
AGENT_CLARIFICATION_TOTAL: Final[str] = "agent.clarification.total"

# LLM metrics
LLM_CALLS_TOTAL: Final[str] = "llm.calls.total"
LLM_CALLS_DURATION_MS: Final[str] = "llm.calls.duration_ms"
LLM_CALLS_ERRORS: Final[str] = "llm.calls.errors"
LLM_TOKENS_INPUT: Final[str] = "llm.tokens.input"
LLM_TOKENS_OUTPUT: Final[str] = "llm.tokens.output"
LLM_TOKENS_CACHED: Final[str] = "llm.tokens.cached"

# Tool metrics
TOOL_CALLS_TOTAL: Final[str] = "tool.calls.total"
TOOL_CALLS_DURATION_MS: Final[str] = "tool.calls.duration_ms"
TOOL_CALLS_ERRORS: Final[str] = "tool.calls.errors"

# Read-only co-call observability (gate metric for item 1.5 concurrency-cap relax).
# Counts model turns in which ≥2 read-only tools (query_files / hybrid_search) were
# requested before any of their outputs returned — the signal that would justify
# raising max_function_tool_concurrency above 1.  Pair with TOOL_READONLY_TURNS_TOTAL
# to compute the fraction of turns that exhibit this pattern.
TOOL_READONLY_COCALL_TOTAL: Final[str] = "tool.readonly.cocall.total"
TOOL_READONLY_TURNS_TOTAL: Final[str] = "tool.readonly.turns.total"

# Intra-run tool-output trimming (call_model_input_filter). Counts how many
# stale tool outputs were previewed and how many characters were reclaimed
# from the model input across the lifetime of the process.
TOOL_OUTPUT_TRIMMED_TOTAL: Final[str] = "tool.output.trimmed.total"
TOOL_OUTPUT_TRIMMED_CHARS: Final[str] = "tool.output.trimmed.chars"
MODEL_INPUT_FILTER_CHARS_BEFORE: Final[str] = "model.input_filter.chars.before"
MODEL_INPUT_FILTER_CHARS_AFTER: Final[str] = "model.input_filter.chars.after"

# Context metrics
CONTEXT_TOKENS_TOTAL: Final[str] = "context.tokens.total"
CONTEXT_ITEMS_COUNT: Final[str] = "context.items.count"

# Steering metrics
STEERING_MESSAGES_TOTAL: Final[str] = "steering.messages.total"
STEERING_MESSAGES_PENDING: Final[str] = "steering.messages.pending"

# Skill metrics
SKILL_MATCHES_TOTAL: Final[str] = "skill.matches.total"
SKILL_CACHE_HITS: Final[str] = "skill.cache.hits"
SKILL_CACHE_MISSES: Final[str] = "skill.cache.misses"


# ============================================================================
# Metric Types
# ============================================================================


@dataclass
class MetricValue:
    """A single metric measurement."""
    name: str
    value: float
    timestamp: float = field(default_factory=time.time)
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class Counter:
    """A counter that only increases."""
    name: str
    value: int = 0
    labels: dict[str, str] = field(default_factory=dict)

    def increment(self, amount: int = 1) -> None:
        self.value += amount


@dataclass
class Gauge:
    """A gauge that can increase or decrease."""
    name: str
    value: float = 0.0
    labels: dict[str, str] = field(default_factory=dict)

    def set(self, value: float) -> None:
        self.value = value

    def increment(self, amount: float = 1.0) -> None:
        self.value += amount

    def decrement(self, amount: float = 1.0) -> None:
        self.value -= amount


# Cap retained histogram samples so a long-lived process can't grow an
# unbounded list. Trimming is amortized O(1): we only slice when the list
# reaches twice the cap, keeping the most recent HISTOGRAM_MAX_VALUES samples.
HISTOGRAM_MAX_VALUES = 10000


@dataclass
class Histogram:
    """A histogram for tracking value distributions."""
    name: str
    values: list[float] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)

    def observe(self, value: float) -> None:
        self.values.append(value)
        if len(self.values) > HISTOGRAM_MAX_VALUES * 2:
            # Keep only the most recent samples (bounded memory over process life).
            del self.values[:-HISTOGRAM_MAX_VALUES]

    def summary(self) -> dict[str, float]:
        """Get histogram summary statistics."""
        if not self.values:
            return {"count": 0, "sum": 0, "avg": 0, "min": 0, "max": 0}

        return {
            "count": len(self.values),
            "sum": sum(self.values),
            "avg": sum(self.values) / len(self.values),
            "min": min(self.values),
            "max": max(self.values),
        }


# ============================================================================
# Metrics Collector
# ============================================================================


class MetricsCollector:
    """
    Thread-safe metrics collector for agent operations.

    Supports counters, gauges, and histograms.
    """

    def __init__(self):
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}
        self._lock = threading.Lock()

    def increment_counter(
        self,
        name: str,
        amount: int = 1,
        labels: dict[str, str] | None = None,
    ) -> None:
        """
        Increment a counter metric.

        Args:
            name: Metric name
            amount: Amount to increment
            labels: Optional labels for the metric
        """
        key = self._make_key(name, labels)
        with self._lock:
            if key not in self._counters:
                self._counters[key] = Counter(
                    name=name,
                    labels=labels or {},
                )
            self._counters[key].increment(amount)

    def set_gauge(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        """
        Set a gauge metric value.

        Args:
            name: Metric name
            value: Value to set
            labels: Optional labels for the metric
        """
        key = self._make_key(name, labels)
        with self._lock:
            if key not in self._gauges:
                self._gauges[key] = Gauge(
                    name=name,
                    labels=labels or {},
                )
            self._gauges[key].set(value)

    def observe_histogram(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        """
        Observe a value for a histogram metric.

        Args:
            name: Metric name
            value: Value to observe
            labels: Optional labels for the metric
        """
        key = self._make_key(name, labels)
        with self._lock:
            if key not in self._histograms:
                self._histograms[key] = Histogram(
                    name=name,
                    labels=labels or {},
                )
            self._histograms[key].observe(value)

    def time_histogram(
        self,
        name: str,
        labels: dict[str, str] | None = None,
    ) -> "TimerContext":
        """
        Create a timer context for measuring duration.

        Args:
            name: Metric name (will append _ms)
            labels: Optional labels for the metric

        Returns:
            TimerContext that records duration on exit
        """
        return TimerContext(self, name, labels)

    def get_all_metrics(self) -> dict[str, Any]:
        """
        Get all collected metrics.

        Returns:
            Dict with counters, gauges, and histograms
        """
        with self._lock:
            counters = {
                key: {"value": c.value, "labels": c.labels}
                for key, c in self._counters.items()
            }
            gauges = {
                key: {"value": g.value, "labels": g.labels}
                for key, g in self._gauges.items()
            }
            histograms = {
                key: {"summary": h.summary(), "labels": h.labels}
                for key, h in self._histograms.items()
            }

            return {
                "counters": counters,
                "gauges": gauges,
                "histograms": histograms,
            }

    def reset(self) -> None:
        """Clear all metrics."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()

    def _make_key(self, name: str, labels: dict[str, str] | None) -> str:
        """Create a unique key for a metric."""
        if not labels:
            return name

        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"


class TimerContext:
    """Context manager for timing operations."""

    def __init__(
        self,
        collector: MetricsCollector,
        name: str,
        labels: dict[str, str] | None = None,
    ):
        self._collector = collector
        self._name = name
        self._labels = labels
        self._start_time: float | None = None

    def __enter__(self) -> "TimerContext":
        self._start_time = time.time()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._start_time is not None:
            duration_ms = (time.time() - self._start_time) * 1000
            self._collector.observe_histogram(
                self._name,
                duration_ms,
                self._labels,
            )


# Global metrics collector instance
_metrics_collector: MetricsCollector | None = None
_metrics_lock = threading.Lock()


def get_metrics_collector() -> MetricsCollector:
    """
    Get the global metrics collector singleton.

    Returns:
        MetricsCollector instance
    """
    global _metrics_collector
    if _metrics_collector is None:
        with _metrics_lock:
            if _metrics_collector is None:
                _metrics_collector = MetricsCollector()
                log_with_context(
                    logger, 20, "Metrics collector initialized"
                )
    return _metrics_collector


def reset_metrics_collector() -> None:
    """Reset the global metrics collector."""
    global _metrics_collector
    with _metrics_lock:
        if _metrics_collector is not None:
            _metrics_collector.reset()
