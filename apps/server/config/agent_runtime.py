"""
Centralized runtime configuration for agent orchestration.

Keeps key workflow limits in one place so prompts, graph logic, and service
entrypoints use the same thresholds and iteration budgets.
"""

import os


def _get_int_env(name: str, default: int, minimum: int = 1) -> int:
    """Read an integer environment variable with safe fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


def _get_bool_env(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable with safe fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_str_env(
    name: str,
    default: str,
    *,
    allowed: set[str] | None = None,
) -> str:
    """Read a string environment variable with normalization + allowlist."""
    raw = os.getenv(name)
    value = default if raw is None else raw.strip()

    normalized = value.lower()
    if allowed is not None and normalized not in allowed:
        return default
    return normalized


# Iteration budgets. These caps are NOT for cost control (DeepSeek is cheap) — they only
# exist as a runaway-loop safety net so a stuck tool-loop or handoff ping-pong can't hang a
# request forever. They are therefore set generously so they never constrain a real task,
# while still bounding pathological loops. All remain env-overridable.

# Total request-level iteration budget (legacy compatibility constant)
AGENT_MAX_ITERATIONS = _get_int_env("AGENT_MAX_ITERATIONS", 100)

# Multi-agent collaboration loop budget (writer/planner/reviewer handoffs)
AGENT_COLLABORATION_MAX_ITERATIONS = _get_int_env(
    "AGENT_COLLABORATION_MAX_ITERATIONS",
    30,
)

# Single-agent tool-calling loop budget (SDK max_turns per agent run)
AGENT_TOOL_CALL_MAX_ITERATIONS = _get_int_env(
    "AGENT_TOOL_CALL_MAX_ITERATIONS",
    100,
)

# Max output tokens for the OpenAI Agents SDK Chat Completions calls.
# This is intentionally independent from the tool-turn budget above.
AGENT_OPENAI_AGENTS_MAX_OUTPUT_TOKENS = _get_int_env(
    "AGENT_OPENAI_AGENTS_MAX_OUTPUT_TOKENS",
    64000,
)

# 上下文预算按 deepseek-flash 的 1M 窗口 + 自动前缀缓存来定：缓存命中的输入只要
# 未命中价的 1/50，而预算过小导致的「上下文被压缩 → 模型反复 query_files 重读」
# 每次都会把整段前缀重新送一遍，反而贵得多。

# Chat history loading budget (sliding window, newest-first)
AGENT_CHAT_HISTORY_TOKEN_BUDGET = _get_int_env(
    "AGENT_CHAT_HISTORY_TOKEN_BUDGET",
    32000,
)

# 每次请求组装的项目上下文（焦点文件 / 附加文件 / 角色 / 设定 / 文件清单）token 预算
AGENT_CONTEXT_TOKEN_BUDGET = _get_int_env(
    "AGENT_CONTEXT_TOKEN_BUDGET",
    32000,
)

# 跨轮工作集：上两轮读取/修改过的文件，按当前库内容整份注入本轮。
# 最多附全文的文件数与总字符数；超出的只列标题 + id。
AGENT_WORKING_SET_MAX_FILES = _get_int_env("AGENT_WORKING_SET_MAX_FILES", 8)
AGENT_WORKING_SET_MAX_CHARS = _get_int_env("AGENT_WORKING_SET_MAX_CHARS", 60000)

# Content length threshold for auto-handoff to quality reviewer
AGENT_AUTO_REVIEW_THRESHOLD_CHARS = _get_int_env(
    "AGENT_AUTO_REVIEW_THRESHOLD_CHARS",
    500,
)

# Router strategy used by writing graph:
# - llm: call router_node (extra LLM round-trip)
# - off: always start from writer (no routing)
AGENT_ROUTER_STRATEGY = _get_str_env(
    "AGENT_ROUTER_STRATEGY",
    "llm",
    allowed={"llm", "off"},
)

# Whether writing_graph.py should auto-trigger quality reviewer purely based on
# writer output length. Default is **True** for backward compatibility (the
# previous graph behavior always auto-triggered), but can be disabled to reduce
# extra agent round-trips when the prompts already handle explicit handoffs.
AGENT_ENABLE_GRAPH_AUTO_REVIEW = _get_bool_env(
    "AGENT_ENABLE_GRAPH_AUTO_REVIEW",
    True,
)

# Request-level budget for one POST /agent/stream (cost / runaway safety net).
# The per-agent and collaboration caps above multiply (100 × 30), so a single
# request could otherwise make thousands of model calls for one quota unit.
# Defaults are deliberately loose so a long multi-chapter writing turn with
# review rounds never hits them; exceeding either ends the run gracefully with
# an error frame (ERR_AGENT_MODEL_CALL_LIMIT / ERR_AGENT_RUN_TIMEOUT).

# Total model calls (SDK turns across all agent runs) per request.
AGENT_RUN_MAX_MODEL_CALLS = _get_int_env("AGENT_RUN_MAX_MODEL_CALLS", 200)

# Wall-clock budget for the whole streaming request, in seconds.
AGENT_RUN_WALL_CLOCK_TIMEOUT_S = _get_int_env("AGENT_RUN_WALL_CLOCK_TIMEOUT_S", 1200)

# Interval between SSE keep-alive comment frames (": ping") while streaming.
AGENT_SSE_HEARTBEAT_INTERVAL_S = _get_int_env("AGENT_SSE_HEARTBEAT_INTERVAL_S", 15)
