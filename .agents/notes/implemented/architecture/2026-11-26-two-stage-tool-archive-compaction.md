# Agent Note: 两阶段智能上下文压缩（Stage 1 零 LLM `tool_archive` + Stage 2 LLM 交接摘要）

## Problem

单循环 agent 的轮间历史压缩此前只有单一路径：当历史输入加上本轮 `<turn_context>` 超过 `AGENT_LOOP_COMPACT_AT_TOKENS`（300,000 token）时，直接向供应商发起一次整段历史的 LLM 交接摘要调用。在实际长篇创作会话中，这一路径有三个明显代价：

1. **大量超限会话本质上是“早期只读查询正文堆积”**：模型在前几轮用 `query_files` / `load_skill` / `read_skill_resource` 读过的整章旧稿与参考文档常占去 100k–250k token，但作者对话和写入脉络本身并不长。直接做 LLM 摘要既多付一次 300k+ 输入的模型调用费用与数十秒等待，又不得不将整段对话压缩成有损文本摘要。
2. **即便确实需要 LLM 摘要，直接送入未归档的大体积只读工具输出也会拖慢摘要生成并抬高成本**。
3. **不能破坏常态轮次的 KV 前缀缓存**：如果在每次普通轮次中滚动裁剪旧工具输出，会导致每次请求前缀变化、KV 缓存全线 miss（10× 输入成本）。

## Decision

在 `agent/loop/compaction.py`、`agent/loop/history.py`、`agent/service.py` 与 `config/agent_runtime.py` 中引入**两阶段轮间上下文压缩**：

1. **常态零前缀破坏**：Stage 1（`tool_archive`）仅在 `should_compact(...)` 已判定超限（`effective_history_provider_tokens(history) + turn_input_tokens > AGENT_LOOP_COMPACT_AT_TOKENS`）后运行；未超限的正常轮次绝不改写任何历史项，KV 前缀 100% 逐字节不变。
2. **Stage 1：零 LLM 确定性 `tool_archive` 压缩（`archive_old_tool_results`）**：
   - 运行时配置（均支持同名环境变量覆盖）：
     - `AGENT_LOOP_TOOL_ARCHIVE_ENABLED`（默认 `True`）
     - `AGENT_LOOP_TOOL_ARCHIVE_KEEP_RECENT_TURNS`（默认 `3`，保护最近 3 个真实用户轮次逐字节不动）
     - `AGENT_LOOP_TOOL_ARCHIVE_MIN_OUTPUT_CHARS`（默认 `2000`）
     - `AGENT_LOOP_TOOL_ARCHIVE_MIN_SAVINGS_TOKENS`（默认 `30000`，确保留出足够余量，避免下一轮立刻抖动重压）
   - 仅归档保护窗口之前、工具名属于 `ARCHIVABLE_READ_TOOLS`（`query_files` / `read_skill_resource` / `load_skill`）、输出长度 `>= 2000` 字符、且为成功 JSON 结果（`status="success"`、无 `error`/`error_type`、非已归档回执）的 `function_call_output`。绝不触碰任何写工具（`write_chapter` / `create_file` / `edit_file` / `delete_file` / `rename_file` / `update_project`）或错误结果。
   - 使用 `history.py` 单一纯函数 `build_tool_archive_receipt` 生成紧凑确定性 JSON 回执，保留 `status="success"`、`archived=true`、`tool_name`、提取的 `files`（`id`/`title`）或 `skill_id`/`skill_name`/`path`、`original_chars` 与简短重读提示；绝不放大原输出。
   - **准入条件**：用同一供应商同量纲估算器 `estimate_provider_items_tokens` 计量，仅当 `archived_count > 0`、`saved_tokens >= AGENT_LOOP_TOOL_ARCHIVE_MIN_SAVINGS_TOKENS` 且 `after_tokens + turn_input_tokens <= AGENT_LOOP_COMPACT_AT_TOKENS` 时直接放行（0 次模型调用、0 延迟、100% 无损保留对话与写入记录）。
3. **Stage 2 兜底与串联**：
   - 若 Stage 1 未达准入条件，自动回落到既有 Stage 2 LLM 交接摘要；若 Stage 1 已归档至少一条旧只读输出（`archived_count > 0`），则直接将更小的已归档历史送入 Stage 2，降低摘要调用体积与延迟。
4. **跨轮前缀稳定、冷库确定性回放与前后端兼容**：
   - Stage 1 成功后在 `message_metadata.compaction` 落库结构化记录：`{"mode": "tool_archive", "archived_through_message_id": ..., "archived_call_ids": [...], "archived_count": ..., "saved_tokens": ..., "before_tokens": ..., "after_tokens": ..., "created_at": ...}`。
   - 保持 `valid_compaction()` 仅识别 LLM 摘要记录，确保既有 `message_manager.py` 的 `archive_pre_compaction_tool_calls_safe`、`session_loader.py` 与旧 LLM 摘要记录 100% 向后兼容；新增 `valid_tool_archive_compaction()` 与 `is_tool_archive_compaction()`。
   - `load_loop_history()` 从新到旧扫描到最近一个 LLM 摘要压缩点为止，累积收集期间所有 `tool_archive` 记录的 `archived_call_ids`（同时匹配合成 id `h{row.id}_{index}` 与原始供应商 `call["id"]`），并用同一个 `build_tool_archive_receipt` 确定性回放，使冷库回放与实时 Stage 1 逐字节一致；遇到 `tool_archive` 边界同样停止向前继承压缩前的 `prompt_tokens` 与更早的 `compaction_skipped`。
   - `client_message_metadata()` 在发往浏览器前移除 `mode="tool_archive"` 的 `compaction` 键；`service.py` 在 Stage 1 成功时只发瞬态 `thinking` 提示，不发空摘要 `history_compacted` 帧也不写 `display_events`，且撤销被归档文件的跨轮 `initial_read_snapshots`（含后续轮次跨轮承接），防止 `edit_file` 误判旧章节仍在模型视野内。
   - 回合正常结束时 `wire_prefix_cache` 原样写入归档后的 `completed_items`，下一轮直接命中热前缀缓存。

## Alternatives considered

- **每轮按固定窗口滚动归档超过 3 轮的只读结果（无需等待 300k 阈值）**：最强理由是可将上下文始终保持在极低水位，单轮输入 token 更少。未采用：每次轮次推进都会改写历史中段，导致从被改写位置往后的整段历史 KV 缓存每轮都失效（MiniMax cache-miss 为 cache-hit 的 10 倍，DeepSeek 为 50 倍），反而大幅增加总成本与首字延迟。仅在跨越 300k 阈值时一次性归档才能兼顾长尾降本与常态 95%+ 缓存命中。
- **直接复用 `valid_compaction()` 与 `history_compacted` 卡片展示 Stage 1 归档**：最强理由是复用现有前端卡片和数据库清理路径，代码改动更少。未采用：第一，Stage 1 没有自然语言摘要，发出空 `summary` 的 `history_compacted` 会破坏前端折叠摘要体验；第二，`message_manager.py` 在命中 `valid_compaction()` 时会调用 `archive_pre_compaction_tool_calls_safe(covers_through_message_id=...)` 剥离边界内所有行的正文，若把 `tool_archive` 混入其中会错误剥离受保护的最近 3 轮只读正文或触发缺键异常。
- **仅靠 Redis `wire_prefix_cache` 维持归档状态，不落库 `tool_archive` 记录**：最强理由是无需扩展 `load_loop_history()` 回放逻辑。未采用：一旦超过 20 分钟缓存窗口、切 Key 或重启服务，`wire_prefix_cache` 失效退回冷库回放，已归档的旧查询正文会全部复活，导致上下文瞬间重新暴涨越过 300k 并被迫触发昂贵的 Stage 2 摘要。

## Consequences

- **收益**：
  - 因早期多章阅读膨胀触发 300k 阈值的长会话可在 <1ms 内以 0 LLM 费用完成压缩，保留 100% 无损对话与写入上下文，并立即在下一轮重建稳定 KV 缓存前缀。
  - 确实需要 Stage 2 摘要的会话可受益于更小的归档输入，降低摘要超时率与成本。
  - 热缓存（`wire_prefix_cache`）与冷库回放（`load_loop_history`）双路径一致，支持多轮累积归档与失败重试结转。
- **代价与风险**：
  - 归档后的旧章节正文不再处于模型直接视野中，若后续作者要求引用极早章节的逐字细节，模型需按回执中的 `id` 再次调用 `query_files(id=...)`；已通过撤销被归档文件的初始读快照防止 `edit_file` 基于过期视野误改。
  - Stage 1 生效当轮会产生一次预期的 KV 前缀重置（与 Stage 2 摘要一样），随后从新前缀起重新稳定命中缓存；30k 最低节省准入门槛避免了相邻轮次连续触发抖动。
