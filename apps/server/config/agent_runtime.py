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


# Iteration budgets. These caps ARE cost controls: one request is charged one quota unit
# no matter how many model calls it makes, and every extra turn re-sends the whole growing
# context (2026-10 incident: agents re-read the same files until the 100-turn cap, twice in
# a row; those runs were ~69% of agent spend). The repeat-read guard and soft landing
# (agent/openai_agents/repeat_read_guard.py, runner.SOFT_LANDING_TURNS) stop most loops
# early; these caps bound whatever slips through. All remain env-overridable.

# Multi-agent collaboration loop budget (writer/planner/reviewer handoffs).
# The longest planned workflow (full: planner → hook_designer → writer) plus two
# review rounds, rework and an empty-file correction fits in 12.
AGENT_COLLABORATION_MAX_ITERATIONS = _get_int_env(
    "AGENT_COLLABORATION_MAX_ITERATIONS",
    12,
)

# Single-agent tool-calling loop budget (SDK max_turns per agent run). The last
# runner.SOFT_LANDING_TURNS calls carry a "stop calling tools and summarize" reminder.
AGENT_TOOL_CALL_MAX_ITERATIONS = _get_int_env(
    "AGENT_TOOL_CALL_MAX_ITERATIONS",
    60,
)

# Max output tokens for the OpenAI Agents SDK Chat Completions calls.
# This is intentionally independent from the tool-turn budget above.
AGENT_OPENAI_AGENTS_MAX_OUTPUT_TOKENS = _get_int_env(
    "AGENT_OPENAI_AGENTS_MAX_OUTPUT_TOKENS",
    64000,
)

# 写作 agent 的思考强度（#278）。默认不设：沿用模型默认（high）。只为对照评估而留的开关，
# 实测没有证明降档不伤质量之前，生产不设置它。取值须是 deepseek-flash 接受的档位，
# 其他值忽略（不能让一个拼写错误把请求变成 422）。
AGENT_REASONING_EFFORT_CHOICES = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"})


def _get_reasoning_effort_env(name: str) -> str | None:
    raw = (os.getenv(name) or "").strip().lower()
    return raw if raw in AGENT_REASONING_EFFORT_CHOICES else None


AGENT_REASONING_EFFORT = _get_reasoning_effort_env("AGENT_REASONING_EFFORT")
# 按角色覆盖：审稿人（quality_reviewer）与审稿后交回的返修 writer。默认不设，沿用上面的
# 全局值；同样只接受合法档位。评估见 .agents/notes/.../2026-10-20-reviewer-rework-reasoning-effort.md。
AGENT_REVIEWER_REASONING_EFFORT = _get_reasoning_effort_env("AGENT_REVIEWER_REASONING_EFFORT")
AGENT_REWORK_REASONING_EFFORT = _get_reasoning_effort_env("AGENT_REWORK_REASONING_EFFORT")


def reasoning_effort_for(agent_type: str, *, rework: bool = False) -> str | None:
    """这次 agent run 发给模型的 reasoning_effort；None 表示不传（模型默认 high）。"""
    if agent_type == "quality_reviewer" and AGENT_REVIEWER_REASONING_EFFORT:
        return AGENT_REVIEWER_REASONING_EFFORT
    if rework and AGENT_REWORK_REASONING_EFFORT:
        return AGENT_REWORK_REASONING_EFFORT
    return AGENT_REASONING_EFFORT

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
# The per-agent and collaboration caps above multiply (60 × 12), so a single
# request could otherwise make hundreds of model calls for one quota unit.
# Exceeding either ends the run with an error frame
# (ERR_AGENT_MODEL_CALL_LIMIT / ERR_AGENT_RUN_TIMEOUT).

# Total model calls (SDK turns across all agent runs) per request: two full
# per-agent runs, enough for a long multi-chapter turn with review rounds.
AGENT_RUN_MAX_MODEL_CALLS = _get_int_env("AGENT_RUN_MAX_MODEL_CALLS", 120)

# Wall-clock budget for the whole streaming request, in seconds. Railway closes a
# public HTTP connection after 15 minutes even while bytes (heartbeats) flow, so
# the run must end well before that: the remaining margin covers cancelling the
# producer, the timeout frame, refund settlement and the trailing quota frame.
RAILWAY_HTTP_CONNECTION_LIMIT_S = 900
AGENT_RUN_WALL_CLOCK_TIMEOUT_S = _get_int_env("AGENT_RUN_WALL_CLOCK_TIMEOUT_S", 780)

# Interval between SSE keep-alive comment frames (": ping") while streaming.
AGENT_SSE_HEARTBEAT_INTERVAL_S = _get_int_env("AGENT_SSE_HEARTBEAT_INTERVAL_S", 15)
