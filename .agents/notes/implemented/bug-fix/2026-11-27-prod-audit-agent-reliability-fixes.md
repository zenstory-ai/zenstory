# 2026-11-27 线上日志与对话审计的五项 Agent 可靠性修复

## Problem

对 2026-11-26 至 2026-11-27 近一天线上 Railway 日志、PostgreSQL `agent_round_metric` 及真实用户对话排查发现 5 个在 `origin/main` 尚未修复的缺陷：

1. **并行 `file_body_pending` 抢跑击穿熔断器（P1）**：单条模型回复里并行发出 ≥10 个工具调用（例如刚调用 `write_chapter` 建立待写占位后，紧接着在同一回复里并行发 11 个 `delete_file`），`ToolFailureBreaker` 虽将 `file_body_pending` 列入 `_ORDERING_ERROR_TYPES` 跳过同参数连续计数，但仍逐个累加 `consecutive_failures / total_failures`，在第 10 个调用直接触发 `tool_failure_circuit_open`（已扣额度、阻断 `runner._stop_run_after_tools` 交回 `FILE_BODY_RESUME`、残留空文件）。
2. **流式复读检测器漏掉纯标点/分隔符循环（P1）**：`stream_processor.find_repetition_start` 要求末尾窗口内 `isalnum() >= min_alnum`，导致纯标点/破折号循环（线上实测 11,559 段连续 `\n\n　　——`、写爆 7.6 万字）`isalnum()` 恒为 0、100% 穿透检测并被自动落盘。
3. **`edit_file(continue_on_error=True)` 全失败缺少顶层 `error` 及 CRLF 空白替换死循环（P2）**：`all_failed=True` 时 `mcp_tools.edit_file` 仅写 `status="error"` 与 `error_type`、未写顶层 `error`，导致 `ToolFailureBreaker` 的 `last_error` 为空且错误指纹退化；同时 `zenstory-cli` 上传的 Windows CRLF（`\r\n`）文件中，模型以 `\n\n\n\n` 清理多余空行时，字面匹配失败、`_locate_normalized_exact_spans` 因无非空白行返回空，触发连续 `anchor_not_found` 死循环。
4. **断线轮次丢失 `agent_round_metric` 工具与停止原因遥测（P2）**：客户端中途断连触发 `api/agent.py` 的 `finish()` 时，`AgentService.process_stream` 的 `finally` 尚未执行；`#484` 仅实时同步了 `model` 与 `prepare_context_ms`，而 `tool_calls_count`、`first_tool_started_ms`、`tool_failures_count` 与 `stop_reason` 仍丢失或为 `0 / None`。
5. **墙钟软着陆「幻觉已改」与多文件写入空回复/多余空行（P2）**：只读检查轮次（0 次写操作）进入墙钟软着陆时模型在总结中宣称「已改/已统一」；多文件续跑时 `AgentTextShaper` 在续跑首段 `<file>` 前误补 `\n\n` 且 `service.py` 未吸收工具/文件边界前的纯空白，导致 `chat_message.content` 堆出 22 个连续 `\n`；多文件写入触及 12 轮协作上限（`max_runs`）停在 `</file>` 上时未发收尾文字导致 `chat_message.content=""`。

## Decision

1. **同回复批次 `file_body_pending` 最多计 1 次**：在 `AgentRequestContext` 暴露 `model_reply_seq()`（复用已有的 `on_llm_start` 单调递增 `_reply_seq`），`tools_adapter` 透传 `reply_seq` 给 `ToolFailureBreaker.observe`。对 `_ORDERING_ERROR_TYPES` 在同一 `(error_type, reply_seq)` 内最多计入 1 次失败；跨不同模型回复仍累计计数并受请求级上限兜底，不破坏既有单测。
2. **补充纯标点/分隔符复读检测**：在 `stream_processor.find_repetition_start` 中新增 `_find_symbol_line_repetition_start`（末尾连续 ≥20 行相同非空纯标点/分隔符行即截断回退到循环前，兼容流式末尾半行），并对 `len >= 2 * window` 的超长单行纯非空白符号洪泛放行 3 遍周期检测；短分隔线（如 5 行内）、普通 Markdown 表格分隔行与纯空白仍不误报。
3. **补齐 `all_failed` 顶层 `error` 与确定性 CRLF 换行适配**：
   - `mcp_tools.edit_file` 在 `continue_on_error=True` 且 `status="error"` 时从 `failed_edits` 回填顶层 `error`；`ToolFailureBreaker` 同步以 `_extract_error_message` 兜底读取 `data.failed_edits[0].error`。
   - `FileEditor`（`_apply_replace` / `_apply_delete` / `_apply_insert` 与 `batch_replace_files`）在非 `match_mode="exact"` 模式下，当文件含 `\r\n` 且模式仅含 `\n` 且字面匹配失败时，做确定性 `\n -> \r\n` 精确/引号等价回退，并将写入文本中的裸 `\n` 同步适配为 `\r\n` 保持文件行尾一致；显式 `match_mode="exact"` 保持字节严格匹配并在报错中附 CRLF 提示。
4. **实时同步 `run_report` 遥测并补全 `client_disconnected` 停止原因**：`AgentRunMeter` 增加 `on_report_update` 实时回调（在 `record_model_call`、`record_agent_run`、`record_stage`、`record_tool_failure`、`mark_first_tool_started`、`mark_first_file_prose_chunk`、`mark_first_body_persisted`、`record_leaked_tool_call` 时即时写入 `run_report`）；`AgentService` 在记录 `tool_call` / `content` 时实时更新 `tool_calls_count` / `response_length`；`api/agent.py` 在 `observe_frame` 计数 `tool_call` 帧作为下界兜底，并在 `client_disconnected and not saw_terminal_event` 时将 `fallback_stop_reason` 设为 `client_disconnected`。
5. **注入结构化写入事实并清理多文件收尾文本**：
   - `runner._ModelCallCountingFilter` 接收 `read_guard` 与 `request_context`，在 `LOOP_TIME_LANDING_PROMPT` / `LOOP_SOFT_LANDING_PROMPT` 末尾附上基于执行台账的 `[本轮实际写入事实]`（0 写入时明确声明未修改任何文件、禁止宣称已改；有写入时只列真实落盘文件并区分待写空文件），仅追加在 `input` 末尾不破坏前缀缓存。
   - `AgentTextShaper._emit` 首个片段补齐与 `self._after_tool_call` 一致的 `not text.lstrip().startswith("<")` 守卫；`service.py` 将 `post_file_whitespace_pending` 推广到工具调用与 `<file>` 流边界，吸收协议换行同时保持 `thinking_content` 夹杂正文空白的逐字语义；`loop/engine.py` 在触及 `max_runs` 协作上限时发送 `run_support.collaboration_cap_text` 确定性收尾说明，避免空回复。

## Alternatives considered

1. **把 `file_body_pending` 直接放进 `_PARALLEL_BATCH_ORDERING_ERROR_TYPES` 完全免计**：
   - 最强论点：实现最简单，且 `FILE_BODY_RESUME` 本身已有最大纠偏次数（2 次）兜底。
   - 拒绝原因：若模型在同一 run 内跨多个回复反复违背 `file_body_pending`（或通过非冲突工具穿插绕过），完全免计会失去请求级总失败上限的交叉防线，且破坏既有契约测试 `test_ordering_rejection_counts_toward_cap_but_never_trips_identical_rule` 和 `test_resumed_reply_still_refuses_edit_file_before_the_body`。按 `reply_seq` 每回复计 1 次同时消除并行批次误杀并保留跨轮上界。
2. **直接在写入入库时把用户 CRLF 文件统一转为 LF**：
   - 最强论点：从源头消除 `\r\n` 差异，后续所有工具无需关心 CRLF。
   - 拒绝原因：会静默改写用户通过 `zenstory-cli` 同步的本地文件换行格式，引发大面积假 diff 与版本噪音。在编辑匹配层做确定性 CRLF 回退并保持原文件 `\r\n` 既无副作用又立刻生效。
3. **在客户端断线时等待 `pump.wait_stopped()` 完成后再写 `agent_round_metric`**：
   - 最强论点：等 `process_stream` `finally` 执行完再读 `run_report`，无需实时回调。
   - 拒绝原因：`GeneratorExit` 路径下不可挂起等待（会触发 `RuntimeError: async generator ignored GeneratorExit`），且客户端断线时不应阻塞响应收尾。延续 `#484` 已验证的实时写入 `run_report` 模式与 `observe_frame` 计数零阻塞、零竞态。
4. **对模型墙钟总结文本做正则检查，拦截「已修改」字样再重写**：
   - 最强论点：可以在输出端二次兜底。
   - 拒绝原因：违反 `AGENTS.md`「Agent semantic decisions」红线（禁止用关键词/正则从自然语言推断语义），且流式已推送给前端的文字无法撤回。在软着陆提示中注入结构化写入事实清单，从源头提供确定性事实依据。

## Consequences

- **收益**：
  - 单回复并行调用触发 `file_body_pending` 不再误熔断，正常交回 `FILE_BODY_RESUME` 补写正文；
  - 纯标点/破折号复读在约 20 行内即被流式截停并干净截断回退，不再写爆章节与版本 diff；
  - `edit_file(continue_on_error=True)` 全失败具备完整 `error` 与指纹，Windows CRLF 文件空行与多行编辑一次命中并保真行尾；
  - 断线轮次的 `agent_round_metric` 准确保留工具计数、首工具耗时、失败计数与 `client_disconnected` 停止原因；
  - 软着陆总结获得本轮真实写入事实约束，多文件续跑与 12 轮上限收尾不再产生 22 连换行或纯空回复。
- **代价与注意事项**：
  - `find_repetition_start` 调整后，连续 ≥20 行完全相同的纯标点分隔行会被判定为循环（正常文学作品与表格不会连续出现 20 行相同纯标点分隔行）；相关旧单测已更新为区分短分隔线（5 行）与退化循环（20+ 行）。
  - 协作 12 轮上限收尾会额外输出一条简短的中文/英文确定性收尾说明文字（复用 `wrap_up_text` 的今日额度查询逻辑）。
