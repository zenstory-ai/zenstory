# 单循环 Agent 收敛单一运行历史、墙钟策略解耦与用量归一化

## Problem

多 Agent 工作流图删除后，`apps/server/agent/` 仍保留了三处遗留复杂度：

1. **双轨运行历史**：`openai_agents/runner.py` 每次 run 仍同时维护已删图时代的 Anthropic 格式 `state["messages"]`（含 `tool_use` / `tool_result` / `thinking` 块拼装与还原）和 SDK 原生 `native_run_holder`（含 `agent_type` 匹配、`messages_len` 切片与 `continue_native_run` 标志）。单循环 agent 只有一个角色，跨轮历史由 `state["history_items"]` 提供，同轮续跑只需要只追加的 `result.to_input_list()`；双轨同步徒增维护成本并迫使 `service.py` 靠 `len(messages) > messages_len` 间接推断是否存在未消费插话。
2. **双层墙钟软着陆拦截改写**：在保存后自查（`in_self_check`）run 中，`runner.py` 不了解外层收尾策略，先发 `ITERATION_EXHAUSTED(limit="wall_clock")` 和 `MESSAGE_END(stop_reason="run_deadline_exceeded")`，`loop/engine.py` 再在事件流中拦截暂停卡并把 `MESSAGE_END` 的 `stop_reason` 改写回 `end_turn`，跨层强耦合且脆弱。
3. **用量归一化三处重复**：`loop/compaction.py`、`openai_agents/runner.py` 与 `stream_adapter.py` 各自实现了一套 `prompt_tokens`/`input_tokens`、`completion_tokens`/`output_tokens`、`cached_tokens` 扣除与合并逻辑，口径容易漂移。

## Decision

1. **收敛为单一 `run_items` + `pending_steering` 运行态**（`agent/core/writing_state.py`、`agent/openai_agents/runner.py`、`agent/loop/engine.py`、`agent/service.py`）：
   - 从 `WritingState` 移除 `messages`、`native_run_holder` 与 `continue_native_run`，改为：
     - `history_items`：跨轮历史（DB 重建或 Redis `wire_prefix_cache` 命中）。
     - `run_items`：本轮最近一次正常结束 run 的 `result.to_input_list()`。同轮续跑由 `runner.build_run_input` 直接在其末尾追加（严格只追加，保住 DeepSeek/MiniMax 前缀缓存）；发生 `MaxTurnsExceeded` 或异常/终态错误时清除 `run_items`，不遗留残缺前缀。
     - `pending_steering`：工具边界或 run 边界消费到、尚未喂进下一次 SDK run 的插话文本列表。下一次 run 启动时一次性取出并追加到输入末尾；若整轮在未消化插话时结束，`service.py` 直接以 `bool(writing_state.get("pending_steering"))` 判定跳过 `wire_prefix_cache` 并清理旧键。
   - 保留 `extract_text_from_message_content` 与 `normalize_messages_for_openai_agents` 纯函数，供 `loop/history.py:_legacy_row_items` 只读回放旧图会话行。
2. **用 `RunExecutionPolicy` 取代跨层事件改写**（`agent/openai_agents/runner.py`、`agent/loop/engine.py`）：
   - 新增 `RunExecutionPolicy(defer_wall_clock_soft_landing: bool = False, deferred_wall_clock_exhausted: dict | None = None)` 由 `engine.py` 按 `in_self_check` 显式传入 `run_openai_agents_streaming_agent`。
   - 墙钟软着陆且 `defer_wall_clock_soft_landing=True` 时，`runner.py` 将暂停卡载荷写入 `run_policy.deferred_wall_clock_exhausted`，不发 `ITERATION_EXHAUSTED`，直接发 `MESSAGE_END(stop_reason="end_turn")`。
   - `engine.py` 不再在事件循环里改写 `MESSAGE_END`；run 结束后按结构化完成事实判定：无未完待办则直接正常结束，否则补发该 `wall_clock` 暂停卡（`StreamAdapter` 照旧据此把 `stop_reason` 置为 `run_deadline_exceeded`）。外部 SSE 与落库契约 100% 不变。
3. **抽离统一用量归一化模块 `agent/core/usage.py`**：
   - 提供 `extract_response_usage`、`sum_response_usages`、`normalize_usage_keys`、`merge_usage_dicts`，兼容 Chat Completions 与 SDK Responses 两族键名、安全扣除并钳制 `[0, prompt]` 范围内的 `cached_tokens`、省略零值键以保持既有 `bool(usage)` 契约。
   - `loop/compaction.py`、`openai_agents/runner.py`、`stream_adapter.py` 统一委托给该模块；`services/usage/llm_usage_service.py` 的账本提取契约保持独立不变。

## Alternatives considered

- **直接复用 `services/usage/llm_usage_service.extract_usage_tokens` 替代 `agent/core/usage.py`**：
  - 最强论点：账本与流事件共用同一套底层函数，文件数更少。
  - 拒绝原因：`extract_usage_tokens` 返回 `UsageTokens(cache_hit, cache_miss, output)` 且在异常上游 `cached_tokens > prompt_tokens` 时保留 `cache_hit=cached`（已由 `test_llm_usage_service.py` 锁定）；而流事件与对话落库需要 `total_tokens`、别名字典合并、以及把 `cache_read_tokens` 钳制到 `prompt` 以内并省略零值键。保留 `agent/core/usage.py` 与账本层职责分离更清晰且零回归风险。
- **在 `engine.py` 中直接把 `state["run_items"]` 包进一个自定义 dataclass**：
  - 最强论点：比 `TypedDict` 字段更强类型。
  - 拒绝原因：`WritingState` 在本 PR 中仍与 `service.py` / `tests/` 共享字典协议，全面替换为强类型 `AgentRequestContext` 属于下一阶段（替换 `ToolContext`），本 PR 保持最小增量与低冲突面。

## Consequences

- **收益**：
  - 删除 `runner.py` 中 ~130 行已删多 Agent 图遗留的双轨历史拼接/回写逻辑，热路径不再为每轮额外拼装 Anthropic 格式块列表。
  - `engine.py` 与 `runner.py` 的墙钟软着陆契约变为显式数据结构，消除中流拦截和 `stop_reason` 覆写。
  - `service.py` 的 `wire_prefix_cache` 未消费插话判定从隐式 `messages_len` 差值变为显式 `pending_steering` 布尔判断。
  - 三处重复的 usage 归一代码收敛到 `agent/core/usage.py` 并有独立单测覆盖。
- **代价**：
  - 直接断言已删 `state["messages"]` / `state["native_run_holder"]` / `state["continue_native_run"]` 内部结构的单元测试需要同步更新到 `state["run_items"]` 与 `state["pending_steering"]`。
