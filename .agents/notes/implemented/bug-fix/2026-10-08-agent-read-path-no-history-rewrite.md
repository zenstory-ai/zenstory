# Agent Note: 读取路径不改写历史，消除重读循环

Status: implemented

## Problem

一位付费用户的 agent 陷入循环：每一步读 2 个文件，说「已有 A、B，还缺 C、D」，读 C、D 后又说缺 A、B，直到 100 轮上限。线上统计（10-03 起）：约 1/3 的 agent 轮次把同一文件读了 4 次以上，这些轮次占 agent 花费的 69%。同期花费构成：cache-hit 输入 3.9 元、cache-miss 输入 34.9 元、输出 43.2 元。DeepSeek（deepseek-flash，1M token 上下文）有自动前缀缓存，cache-hit 只收 cache-miss 的 1/50——重发历史很便宜，改写历史（造成 cache miss）和重读才贵。

读取链路上有几处叠加的原因：

1. `IntraRunToolOutputTrimmer`（`keep_recent=2`、`max_output_chars=2000`、`preview_chars=400`）在每次模型调用前，把除最近 2 个以外的读取结果折叠成 400 字预览，并折叠旧的成功写入回执，另有 96k 字符预算。输入再小也会改写历史中段：前缀缓存从改写点起全部失效，模型也「看不到」更早读过的 A、B，于是重读。
2. `parallel_execute` 的 `handle_query_files` 只挑了 id/query/file_type 等几个键转发，`response_mode`、`content_preview_chars`、`include_content`、`metadata_filter` 被丢掉，并行读「全文」拿到的是 200 字预览；每个任务结果又被固定截到 4000 字，而且截法是把 `json.dumps` 后的字符串切一刀放进 `preview`，模型拿到的是转义 JSON 碎片。
3. `query_files(id=…)` 默认 summary，只给 200 字 `content_preview`，模型分不清「预览」和「短文件」，往往紧接着再读一次 full。
4. 生产关闭了 `AGENT_TOOL_HYBRID_SEARCH_ENABLED`，但 `hybrid_search` 仍在工具清单和 `parallel_execute` 任务枚举里，调用返回 success + 空结果，模型换关键词反复重试。
5. 工具结果超过 `TOOL_RESULT_MAX_CHARS`（默认 200k）时，整个 `data` 被换成 `{"truncated": true}` 占位，连读到了哪些文件都丢了。

## Decision

- **上下文安全阀，而不是省钱裁剪器。** `agent/openai_agents/intra_run_trimmer.py` 的 `IntraRunToolOutputTrimmer`（类名与 runner 的无参构造不变）只在模型输入（items 的 JSON 长度 + `instructions` 长度）超过 `max_input_chars`（默认 600_000 字符，远低于 1M token）时才动手；不超过时原样返回同一个 `ModelInputData`，不做任何改写。
- **超预算时最旧优先、量化滞回。** 候选按输入位置从旧到新：已知读取工具（`query_files` / `hybrid_search`，以及只含已知任务类型的 `parallel_execute` 读取结果）的输出、确认成功的写入（`create_file` / `edit_file` / 成功的并行写入）的参数与回执。需折叠的量 = `ceil((总长 - max) / 台阶) × 台阶`，台阶 = `max - target`，`target = max × target_ratio`（默认 0.7）。输入在同一台阶内继续增长时折叠边界不动，两次越界之间仍是 append-only；结果对同一输入确定，随输入增长单调（已折叠的条目不会恢复）。只折叠超过 `min_compact_chars`（2000）的载荷。控制流工具、用户消息、未知工具、混入未知任务的并行调用、失败/部分失败的写入、没有结果的写入参数一律不碰。
- **折叠摘要告诉模型发生了什么。** 摘要 JSON 保留 `tool`、`original_chars`、trace（`_TRACE_KEYS` 新增 `entity_id`、`entity_type`、`line_start`）与 400 字开头预览，`_zenstory_context_summary` 为 `context_budget_compaction`，并带中文 `note`：读取结果写「该结果已在本轮更早时完整返回，因上下文接近上限被折叠。不要整篇重读；只在确实需要原文细节时，用 query_files(id=…, response_mode="full") 读取该文件一次。」，写入回执/参数各有对应说明（已落库、不要重复写入）。指标（`model.input_filter.chars.before/after`、`tool.output.trimmed.*`）照旧记录。
- **并行读取与独立调用同义。** `agent/tools/parallel_executor.py` 的 `handle_query_files` / `handle_hybrid_search` 原样转发子任务 params，只丢弃 `project_id`、`user_id`（`_scoped_task_params`），`query_files` 的 `project_id` 由 `ToolContext` 注入。每个任务结果的上限改为 `_task_result_budget(n) = max(4000, int(TOOL_RESULT_MAX_CHARS × 0.8) // n)`（默认 200k 时 5 个任务各 32k），截断由 `_bound_task_result` 在 content 等长文本字段内部进行（`mcp_tools.bound_payload_strings`），保持合法 JSON，补 `content_length`、`content_truncated: true` 与 `truncation_note`（需要全文时单独 `query_files(id=…, response_mode="full")`）。原有目的保留：聚合结果留 20% 余量，不顶破外层护栏，`any_failed` / `failed` / 逐任务 status+error 不会被吞。
- **按 id 读取默认全文。** `agent/tools/file_ops/serialization.resolve_query_files_response_mode`：`include_content=True` → full；显式 `response_mode` 原样使用（非法值仍在序列化时报错）；`include_content=False` 或显式给了 `content_preview_chars` → summary；给了 `id` → full；其余 summary。`FileCRUD.query_files` / `FileToolExecutor.query_files` 的 `response_mode`、`content_preview_chars` 默认改为 None（自动）。summary 条目新增 `content_length`（全文字符数）与 `content_truncated`；SQL 投影路径里长度用 `length()` 在库内计算（PostgreSQL/SQLite 都按字符计），SQLite 含 NUL 的旧行投影直接取完整值（预览为 0 时也一样），长度由序列化层计算。`mcp_tools._query_files_sync` 在不按 id、请求全文且未给 `limit` 时默认 `limit=10`（`QUERY_FILES_FULL_MODE_DEFAULT_LIMIT`），其余默认 50。工具 schema 描述同步为「按 id 读取默认返回全文」。
- **检索关闭就不暴露。** `agent/tools/registry.get_agent_tools` 在 `AGENT_TOOL_HYBRID_SEARCH_ENABLED` 关闭时（运行时读取）不返回 `hybrid_search`，并返回去掉 `hybrid_search` 的 `parallel_execute` schema 副本（枚举与描述）；共享 schema 对象不被修改。handler 仍注册，历史里或子任务里的调用拿到 `status="error"`、`error_type="tool_disabled"`、「检索功能未启用，请改用 query_files(query=...)，不要再调用 hybrid_search」，经熔断器记账。
- **超限结果保结构。** `mcp_tools._serialize_tool_payload` 对非 error 的超限结果先走 `_compact_oversized_payload`：保留顶层字段（含 `mutation_applied`、`overflow_ref`），加 `truncated` / `max_chars` / `original_length` / `note`，用 `bound_payload_strings` 把长文本按统一上限「注水」截短（受保护键 id/title/file_type/type/status/error/description 等不截），每个被截字段旁写 `<字段>_length` / `<字段>_truncated`（content 为 `content_length` / `content_truncated`）。先保留每段至少 200 字，放不下再降到 0；骨架都放不下才回到原来的最小占位。
- **交接与并行工具描述。** `handoff_to_agent.context` 描述要求写明「已读文件的 id 与关键结论/摘录、本轮修改的文件 id，供下一个 Agent 直接使用而不重读」。`parallel_execute` 的英文描述去掉「Writing multiple chapters at once」，改为：新章节默认用单次 `create_file` + `<file>` 流式写入，`write_chapter` 只用于已有完整正文可内联的情况；读取子任务参数与独立工具一致；本轮读过的文件仍在上下文里，不要重读。

## Alternatives considered

- **保留按「本轮新旧」裁剪（旧实现：只留最近 2 个读取结果全文）。** 最强理由：每次模型调用都会重发整段历史，输入长度随轮次线性增长、总输入随轮次平方增长；不裁剪的话，长 run 的输入 token 总量没有上界，旧实现正是为了把这条平方曲线压平。被否：DeepSeek 的前缀缓存让「原样重发」只花 1/50，而裁剪恰恰在每次调用都改写历史中段，把本可命中缓存的前缀变成 cache miss；更糟的是被折叠的文件在模型眼里「没读过」，引发重读循环——事故数据里 cache-miss 输入（34.9 元）是 cache-hit（3.9 元）的 9 倍，重读轮次占 69% 花费。平方增长的真正上界由 `AGENT_RUN_MAX_MODEL_CALLS` 和 600k 安全阀兜住。
- **超预算时优先折叠最大的载荷（旧实现的预算段）。** 最强理由：每折叠一条省得最多，改写的条目最少。被否：最大的往往是刚读的全文，正是模型下一步要用的；按大小挑选的折叠点随新条目大小跳动，不单调，前缀也就不稳定。最旧优先 + 量化台阶让折叠点确定且只在跨台阶时前移。
- **并行读取沿用固定 4000 字上限，只修 response_mode 转发。** 最强理由：改动最小，且 4000 字足以保证聚合结果远离 200k 护栏。被否：5 个文件每个 4000 字的转义 JSON 碎片几乎没有可用正文，模型必然逐个重读；按护栏的 80% 均分已经同样保证不触发外层截断。

## Consequences

- 收益：输入在 600k 字符以内时整段历史 append-only，DeepSeek 前缀缓存可以连续命中；模型不会因为早先读过的文件被折叠而重读。并行读与独立读语义一致，截断有明确的 `content_truncated` / `content_length` 与下一步提示；按 id 读默认就是全文，少一次「先预览再全文」的往返。检索关闭时模型看不到也不会空转 `hybrid_search`。超限结果仍能看到每个文件的 id/标题/状态。
- 代价：长 run 的单次输入会比旧实现大，单次 cache-miss（例如首次调用或 `instructions` 变化）更贵；一旦越过 600k，单次调用会改写一段较大的历史（滞回台阶为预算的 30%）。按 id 读默认全文让偶尔只想看预览的调用多付正文 token（显式 `response_mode=summary` 可回到旧行为）。summary 条目多两个字段。`hybrid_search` 关闭时以 `tool_disabled` 报错，模型若无视提示原样重试 3 次会触发熔断。

## Verification

- `tests/test_agent/test_intra_run_trimmer.py`：事故回归（4 次 `query_files` 全文读取共 31k 字 + 1 次 `hybrid_search`，带 8k 字 instructions，返回同一对象、内容不变）；预算以内不折叠旧读取与旧写入回执；instructions 计入预算；超预算最旧优先、最新一条保持全文；摘要含中文 note 与 id/title/entity_id/entity_type/line_start trace；同一台阶内继续追加条目时已发送前缀不变；随 run 增长单调；控制流/用户消息/未知工具/失败写入/混入未知任务的并行调用不动；不修改原输入；指标。
- `tests/test_agent/test_parallel_executor.py`：`_task_result_budget` 份额与保底；截断发生在 content 字段内且为合法 JSON、保留 id/title/file_type、带 `content_length` / `content_truncated`；多个文件均分预算；`response_mode="full"` 等参数经真实 `query_files` 包装层转发到 executor（`project_id` 来自 ToolContext）；5 个 60k 字的并行读取不触发外层截断，每个任务都保留 status、文件 id，且正文份额远大于 4000 字。
- `tests/test_agent/test_mcp_tools.py`：`hybrid_search` 关闭返回 `tool_disabled` 且不触达检索后端；不按 id 的全文查询默认 `limit=10`；超限的 `query_files` 结果保留每条 id/title/file_type/status 并截 content；超限的并行结果保留 `any_failed` 与失败任务的 status/error。
- `tests/test_agent/test_tool_registry.py`：开关打开/关闭两种状态下的工具清单与 `parallel_execute` 任务枚举，关闭视图不修改共享 schema。
- `tests/test_agent/test_query_files_summary_projection.py`：按 id 未指定模式默认全文；显式 summary / `content_preview_chars` / `include_content=false` 仍为预览；summary 期望值含 `content_length` / `content_truncated`；投影预算（单条 File SELECT、不加载正文）与 SQLite NUL 回退保持。
