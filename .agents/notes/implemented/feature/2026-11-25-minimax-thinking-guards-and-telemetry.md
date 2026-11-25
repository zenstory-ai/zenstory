# MiniMax 思考/单调用时长护栏、裁剪器收尾防击穿、跨模式重读上限与失控遥测校准（Part 1）

Status: implemented

关联：[Tier 0 零质量风险 MiniMax 配额安全优化](../architecture/2026-11-22-tier0-minimax-quota-safety.md)、[墙钟收尾时间窗加宽与收尾期工具调用拦截](../bug-fix/2026-11-23-wall-clock-wrap-up-guard-and-margin.md)。

## Problem

生产 48 小时（2026-11-23 至 2026-11-25，1,067 轮单循环写作、7,817 次 MiniMax 调用）审计定位到五类结构性瓶颈与缺陷：

1. **单次 MiniMax 调用无思考/总时长上限**：`MINIMAX_CLIENT_TIMEOUT_S=120s` 仅检测流内空闲停滞，持续吐出 `reasoning_text.delta` 会无限重置计时器。生产中出现 542s 纯思考 64k 截断（0 字正文）及 4/4 次 780s 墙钟硬超时（单调用耗时 273–383s，击穿 240s 收尾余量）。
2. **`IntraRunToolOutputTrimmer` 收尾期击穿前缀缓存且污染 `last_prompt_tokens`**：5 次轮内裁剪全部发生在 `run_deadline_exceeded` 轮（其中 2 次发生在最后一次收尾总结调用），将 ~98% 缓存命中率击穿到 6%–17%，同时把 `message_metadata.prompt_tokens` 覆盖成裁剪后的 239k–291k，导致下一轮 `should_compact()` 漏判而 Redis `wire_prefix_cache` 仍保留 342k–425k 未裁剪前缀。
3. **229k–299k 亚阈值起跳缺口**：28/105 次 `run_deadline_exceeded` 轮次从 229k–299k 起跳并在单轮内增长 +81k 至 +293k 冲到 300k–466k。
4. **`RepeatReadGuard` 跨模式读取绕过**：按 `(tool, file_id, mode)` 计数允许模型交替使用 `start_char+max_chars`、`section`、`tail_chars`、`query` 对同一未修改文件循环读到 11 次（单轮膨胀 +293k token、0 写入）。
5. **`runaway_stop` 遥测误报与发布交接混记**：`api/agent.py` 将所有 `layer="tool_call"` 的 `iteration_exhausted` 卡标为 `runaway_stop=True`，导致 96/97 次失控告警其实是正常的 540s 墙钟软着陆或计划内 Railway 发布交接（`<540s` 触发 `is_draining()`）。

## Decision

在严守零自然语言关键词/正则规则、保住前缀缓存与计费/退还契约的前提下，先行落地零冲突核心护栏（Wave 2 将完成 `runner.py` / `service.py` 接线）：

1. **环境变量可配的单调用思考/时长护栏**（`config/agent_runtime.py`、`agent/openai_agents/model.py`、`agent/openai_agents/usage_hooks.py`）：
   - 新增可经环境变量覆盖（设为 `0` 关闭）的默认阈值：`AGENT_PRE_ANSWER_THINKING_MAX_S=180.0`、`AGENT_PRE_ANSWER_THINKING_MAX_CHARS=64_000`、`AGENT_MODEL_CALL_MAX_DURATION_S=300.0`（基于 7,747 次线上 MiniMax 调用反事实校准：健康 `end_turn` 调用误杀率仅 `0.20%`、活跃正文流误杀 `0.00%`，同时 `100%` 截住 542s 纯思考截断与 4/4 次 780s 硬超时）；经风险审计确认，**拒绝引入**收尾期 `max_output_tokens=16_384` 硬上限，避免思考吃光预算导致 0 总结或截断 `<file>`。`AGENT_LOOP_COMPACT_AT_TOKENS` 维持 `300_000` 默认值不变。
   - 在 `FailoverChatModel.stream_response` 与 `_iter_with_idle_timeout` 中执行结构化截停：未产出正文/工具参数前若思考耗时或字符数触顶，或单次调用总耗时触顶，立即经 `aclosing` 关闭上游流（保证 `_BudgetedAsyncStream` 触发取消态成本结算）、记录 `CALL_OUTCOME_TIMEOUT` 耗时与 `last_stream_cutoff_reason`、置 `last_response_output_capped=True`，**不触发备源 failover / 熔断 / 会话固定**，并合成 `ResponseCompletedEvent` 无缝衔接现有 `runner.py` 出口（未出正文时进入 `output_truncated`，已出正文时保留流过文字并带 `output_capped=True` 供断点续写）。
2. **`IntraRunToolOutputTrimmer` 收尾期禁裁与峰值 `max_prompt_tokens` / 排空辅助**（`agent/openai_agents/intra_run_trimmer.py`、`agent/core/run_meter.py`、`agent/openai_agents/usage_hooks.py`）：
   - `IntraRunToolOutputTrimmer` 新增可选 `run_meter` 参数；一旦 `run_meter.past_wrap_up` 或 `run_meter.wall_clock_landing_injected` 为真，严格跳过裁剪原样返回（兼容未传 `run_meter` 的旧调用方，Wave 2 在 `runner.py` 传入 `run_meter=run_meter`）。
   - `AgentRunMeter` 保留 `63257a86` 的 `on_served_model` 回调，新增 `max_prompt_tokens`、`record_prompt_tokens()`、`effective_prompt_tokens` 以及 `server_draining` / `is_draining_stop` 属性；`UsageMeteringMixin.on_llm_end` 经 `record_prompt_tokens()` 同时维护末次与峰值输入 token（Wave 2 在 `service.py` 落库 `effective_prompt_tokens`）。
3. **`RepeatReadGuard` 跨模式单文件总读取上限**（`agent/openai_agents/repeat_read_guard.py`）：
   - 按同一未修改 `file_id` 汇总全部模式（`full`/`section`/`range`/`tail`/`query`/`summary`）的读取次数，新增 `FILE_TOTAL_READ_WARN_AT=5` 与 `FILE_TOTAL_READ_BLOCK_AT=6`；与既有 `_read_counts` 共享单一数据源，因此文件被写入、`stale_content`、正文落库或新开 `agent run` 时自动按文件清零，且保留合法的 4 窗口范围读取零干扰。
4. **`runaway_stop` 遥测与正常墙钟软着陆解耦**（`api/agent.py`）：
   - `_AgentStreamSettlement.observe_frame` 对 `limit="wall_clock"` 且 `wall_clock_landing_exhausted` 不为真的正常软着陆以及 `limit="server_draining"` / `server_draining=True` 的发布交接不再置 `runaway_stop=True`；`runaway_stop=True` 严格保留给真实失控（`max_turns`、`model_call_budget`、`no_progress`、`repetition_loop`、`tool_failure_circuit_open`、`wall_clock_landing_exhausted`）。
5. **精简批量节奏与机械步骤简短思考规则**（`agent/loop/prompts.py`）：
   - 在 `LOOP_WORKING_RULES` 中追加一条固定规则：超过 3 份文件的多章批量任务每轮稳妥完成 2–3 份并交接剩余清单；机械性 `query_files` / `edit_file` 步骤保持思考简短，不在思考里预先起草整章正文。

## Alternatives considered

1. **在 `FailoverChatModel` 触发思考截停时直接抛出 `TimeoutError` 并 failover 到 DeepSeek**：
   - 最强论据：无需构造合成 `ResponseCompletedEvent`，复用现有超时异常链路。
   - 拒绝原因：过长思考不是上游宕机，走 `TimeoutError` 会打开 MiniMax 熔断器、将会话固定到 DeepSeek、击穿前缀缓存并让一次用户轮次被按 `upstream_model_error` 误退费。合成带 `last_response_output_capped=True` 的 `ResponseCompletedEvent` 既零改动复用 `runner.py` 现有 `output_truncated` / `output_capped` 链路，又保持计费与路由准确。
2. **使用更激进的 `(90s / 32_000 字符 / 210s)` 默认截停阈值或引入 `AGENT_WALL_CLOCK_WRAP_UP_MAX_OUTPUT_TOKENS=16_384`**：
   - 最强论据：更早截停长思考并压低收尾调用尾延迟。
   - 拒绝原因：7,747 次线上 MiniMax 调用的反事实评估证实，`(90s / 32k / 210s)` 会造成 `2.04%` 健康调用误杀（`11.17%` 健康轮次误杀）且 `210s` 会误杀 0 思考长正文流（如 `243.3s` 成稿写入）；而收尾期 `max_output_tokens=16_384` 存在思考耗尽预算导致 0 总结或中途截断待写 `<file>` 的高风险，已明确否决。校准至 `(180s / 64_000 字符 / 300s)` 可兼顾 `0.20%` 超低误杀与 `100%` 极端挂起拦截。
3. **单独维护 `_file_read_counts` 字典与 `_read_counts` 并行记账**：
   - 最强论据：读取查询为 O(1)。
   - 拒绝原因：单轮 `_read_counts` 条目极少，直接聚合 `self._read_counts` 可天然与 `_forget_reads`、`_record_write`、`begin_agent_run` 共享单一数据源，杜绝状态漂移。

## Consequences

- **收益**：消除单调用 542s 纯思考挂起与击穿 240s 收尾余量的硬超时隐患；堵住 `RepeatReadGuard` 跨模式循环绕过；`runaway_stop` 指标立即从 96/97 误报恢复为高信噪比真实失控信号；所有阈值均支持环境变量热调优或置 `0` 紧急关闭。
- **代价与后续接线**：
  - Wave 2 已完成全部跨文件接线：`runner.py` 传入 `IntraRunToolOutputTrimmer(run_meter=run_meter)`、墙钟软着陆透传 `wall_clock_landing_exhausted` 与 `server_draining`（`stream_adapter.py` / `chat_message_ux_summary.py` / `service.py` 同步识别 `server_draining`）；`service.py` 落库 `run_meter.effective_prompt_tokens`；`repeat_edit_guard.py` / `tools_adapter.py` / `runner.py` 新增 `>=3` 次 `edit_repeat_limit` 软跳过干净早停；`engine.py` 新增 5 分钟单元边界结构化检查点（`AGENT_UNIT_CHECKPOINT_AFTER_S=300`）。
  - 默认思考/时长截停阈值（`180s` / `64_000` 字符 / `300s`）与单元检查点阈值均支持环境变量热调优或置 `0` 紧急关闭。
