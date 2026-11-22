"""
Centralized runtime configuration for agent orchestration.

Keeps key workflow limits in one place so prompts, the single loop agent and
service entrypoints use the same thresholds and iteration budgets.
"""

import os

from config.env import get_int_env

# Iteration budgets. These caps ARE cost controls: one request is charged one quota unit
# no matter how many model calls it makes, and every extra turn re-sends the whole growing
# context (2026-10 incident: agents re-read the same files until the 100-turn cap, twice in
# a row; those runs were ~69% of agent spend). The repeat-read guard and soft landing
# (agent/openai_agents/repeat_read_guard.py, runner.SOFT_LANDING_TURNS) stop most loops
# early; these caps bound whatever slips through. All remain env-overridable.

# How many times the single loop agent may run within one turn (re-runs for an
# empty-file correction, a file-stream continuation or mid-turn steering). The
# variable name predates the loop agent and stays unchanged for deployed overrides.
AGENT_COLLABORATION_MAX_ITERATIONS = get_int_env(
    "AGENT_COLLABORATION_MAX_ITERATIONS",
    12,
)

# Single-agent tool-calling loop budget (SDK max_turns per agent run). The last
# runner.SOFT_LANDING_TURNS calls carry a "stop calling tools and summarize" reminder.
AGENT_TOOL_CALL_MAX_ITERATIONS = get_int_env(
    "AGENT_TOOL_CALL_MAX_ITERATIONS",
    60,
)

# Max output tokens for the OpenAI Agents SDK Chat Completions calls.
# This is intentionally independent from the tool-turn budget above.
AGENT_OPENAI_AGENTS_MAX_OUTPUT_TOKENS = get_int_env(
    "AGENT_OPENAI_AGENTS_MAX_OUTPUT_TOKENS",
    64000,
)

# 写作 agent 的思考强度（#278）。默认不设：沿用模型默认（high）。只为对照评估而留的开关，
# 实测没有证明降档不伤质量之前，生产不设置它。取值须是 deepseek-flash 接受的档位，
# 其他值忽略（不能让一个拼写错误把请求变成 422）。
AGENT_REASONING_EFFORT_CHOICES = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"})
_raw_reasoning_effort = (os.getenv("AGENT_REASONING_EFFORT") or "").strip().lower()
AGENT_REASONING_EFFORT: str | None = (
    _raw_reasoning_effort if _raw_reasoning_effort in AGENT_REASONING_EFFORT_CHOICES else None
)

# 上下文预算按 deepseek-flash 的 1M 窗口 + 自动前缀缓存来定：预算过小导致的「上下文
# 被压缩 → 模型反复 query_files 重读」每次都会把整段前缀重新送一遍。缓存命中价
# 因供应商而异（services/usage/pricing.py）：DeepSeek 约为未命中的 1/50，MiniMax
# M3.1-Flash 是 1/10，后者每多带一段上下文的成本明显更高。

# 每次请求组装的项目上下文（焦点文件 / 附加文件 / 角色 / 设定 / 文件清单）token 预算
AGENT_CONTEXT_TOKEN_BUDGET = get_int_env(
    "AGENT_CONTEXT_TOKEN_BUDGET",
    32000,
)

# Request-level budget for one POST /agent/stream (cost / runaway safety net).
# The per-agent and collaboration caps above multiply (60 × 12), so a single
# request could otherwise make hundreds of model calls for one quota unit.
# Exceeding either ends the run with an error frame
# (ERR_AGENT_MODEL_CALL_LIMIT / ERR_AGENT_RUN_TIMEOUT).

# Total provider calls per request (SDK turns across all agent runs plus the
# history-compaction summary calls before the first run): two full per-agent
# runs, enough for a long multi-chapter turn with review rounds.
AGENT_RUN_MAX_MODEL_CALLS = get_int_env("AGENT_RUN_MAX_MODEL_CALLS", 120)

# Wall-clock budget for the whole streaming request, in seconds. Railway closes a
# public HTTP connection after 15 minutes even while bytes (heartbeats) flow, so
# the run must end well before that: the remaining margin covers cancelling the
# producer, the timeout frame, refund settlement and the trailing quota frame.
RAILWAY_HTTP_CONNECTION_LIMIT_S = 900
AGENT_RUN_WALL_CLOCK_TIMEOUT_S = get_int_env("AGENT_RUN_WALL_CLOCK_TIMEOUT_S", 780)
# From here on the agent is told to wrap up and the workflow starts no new agent runs,
# so a long request ends with a phase summary instead of being cut at the deadline.
AGENT_RUN_WRAP_UP_AFTER_S = get_int_env(
    "AGENT_RUN_WRAP_UP_AFTER_S", max(60, AGENT_RUN_WALL_CLOCK_TIMEOUT_S - 180)
)

# Deploy handover (agent/core/server_drain.py): after SIGTERM, Uvicorn keeps open
# requests for --timeout-graceful-shutdown (295 s) and Railway kills the process at
# drainingSeconds (300 s). Generations still running this long after SIGTERM end
# with a "server restarting" terminal frame, leaving time for partial-history
# saving, billing settlement and the round outcome before the hard stop.
AGENT_DRAIN_STOP_AFTER_S = get_int_env("AGENT_DRAIN_STOP_AFTER_S", 240)
# Seconds after SIGTERM by which shutdown cleanup must be done (Railway kills at 300).
AGENT_DRAIN_SHUTDOWN_BUDGET_S = get_int_env("AGENT_DRAIN_SHUTDOWN_BUDGET_S", 290)

# Interval between SSE keep-alive comment frames (": ping") while streaming.
AGENT_SSE_HEARTBEAT_INTERVAL_S = get_int_env("AGENT_SSE_HEARTBEAT_INTERVAL_S", 15)

# Loop history compaction (agent/loop/compaction.py), between turns only. When the prompt of
# the last model call of the previous turn (measured, cached tokens included) exceeds this,
# the next turn first writes a handoff summary with the same model; the history loader then
# replays recent author messages + the summary instead of everything before it.
AGENT_LOOP_COMPACT_AT_TOKENS = get_int_env("AGENT_LOOP_COMPACT_AT_TOKENS", 300_000)
# Token budget for the most recent original author messages kept verbatim after compaction.
AGENT_LOOP_COMPACT_KEEP_USER_TOKENS = get_int_env("AGENT_LOOP_COMPACT_KEEP_USER_TOKENS", 20_000)
# Output cap of the summary call (reasoning tokens count against it).
AGENT_LOOP_COMPACT_MAX_OUTPUT_TOKENS = get_int_env("AGENT_LOOP_COMPACT_MAX_OUTPUT_TOKENS", 24_000)
# Upper bound of the history sent to the summary call: older rows are left out beyond it,
# leaving safe headroom below the 1M provider context window for system prompt, tool schemas,
# max_tokens, and tokenizer variance.
AGENT_LOOP_COMPACT_INPUT_MAX_TOKENS = get_int_env("AGENT_LOOP_COMPACT_INPUT_MAX_TOKENS", 700_000)
# After a skipped compaction due to unusable model output (e.g. empty/truncated summary),
# wait until the measured prompt has grown by this much before trying again.
# Transient provider errors do not back off and retry on the next turn.
AGENT_LOOP_COMPACT_RETRY_GROWTH_TOKENS = get_int_env("AGENT_LOOP_COMPACT_RETRY_GROWTH_TOKENS", 100_000)
# Hard bound on the replayed history of a loop run (provider tokens): when compaction keeps
# failing, the oldest items are left out so the session degrades instead of exceeding the
# provider's 1M context window mid-turn.
AGENT_LOOP_RUN_HISTORY_MAX_TOKENS = get_int_env("AGENT_LOOP_RUN_HISTORY_MAX_TOKENS", 750_000)
# Single-turn <turn_context> bound (provider tokens, ~185k Chinese chars): keeps ordinary
# multi-chapter attachments verbatim while preventing 500k-char uploaded drafts from blowing
# past the 1M provider window on turn 1.
AGENT_LOOP_TURN_CONTEXT_MAX_TOKENS = get_int_env("AGENT_LOOP_TURN_CONTEXT_MAX_TOKENS", 120_000)

# Short-TTL Redis cache of the exact SDK wire-format input items at the end of a clean loop
# turn (agent/loop/wire_prefix_cache.py). Matches MiniMax's ~15m KV-cache window so active
# multi-turn sessions reuse an identical wire prefix across turns (including pre-run compaction).
# Payloads are stored gzip+base64 compressed (``zsgz1:``) so ~600k-token sessions stay well
# below ``AGENT_WIRE_PREFIX_CACHE_MAX_BYTES`` (enforced against the stored compressed size).
# Set <= 0 to disable.
AGENT_WIRE_PREFIX_CACHE_TTL_SECONDS = get_int_env(
    "AGENT_WIRE_PREFIX_CACHE_TTL_SECONDS",
    900,
    minimum=None,
)
AGENT_WIRE_PREFIX_CACHE_MAX_BYTES = get_int_env(
    "AGENT_WIRE_PREFIX_CACHE_MAX_BYTES",
    2 * 1024 * 1024,
    minimum=1,
)
