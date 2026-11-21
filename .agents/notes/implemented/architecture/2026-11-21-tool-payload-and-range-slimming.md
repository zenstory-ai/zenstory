# Architecture Note: 工具载荷瘦身与范围读取快照优化（Batch A）

Status: implemented

关联：[query_files 按章节或范围读取长文件](../feature/2026-10-19-query-files-range-reads.md)、[单循环 agent](2026-11-16-single-loop-agent.md)、[回归文件历史与单卡恢复](../simplification/2026-11-19-simplify-recoverable-agent-edits.md)。

## Problem

生产 24 小时工具调用与载荷审计显示，单循环 agent 存在四类纯后端、零写作质量影响（不改提示词与工具 schema）的 token 与步数浪费：

1. **范围读取后 `edit_file` 误触发 `exact_only=True`**：`_ReadSnapshots.seen()` 排除了 `_sources["range"]`，即使模型刚刚通过 `section` / `tail_chars` / `start_char` 读过目标段落并已建立全文陈旧快照，`edit_file` 仍强制逐字匹配，造成 22.3% 的 `anchor_not_found` 整批回滚与额外重试步数。
2. **`edit_file` / `update_project` LLM 返回载荷冗余与 JSON 空白**：模型工具结果与前端 SSE/DB 卡片共用同一份 JSON 字符串，导致模型上下文反复重放仅供 UI 撤销使用的 `undo`（含 64 位 sha256）、精确匹配时与调用参数逐字重复的 `old_preview` / `new_preview` / `text_preview`、空数组/假值样板字段（`failed_edits=[]`、`unchanged_edits=[]`、`warnings=[]`、`partial_success=false`、`all_failed=false`）、`update_project` 顶层重复的 `current_status`，以及默认 `separators=(", ", ": ")` 的 JSON 空白。
3. **`query_files` / `create_file` / `write_chapter` 死字段与 `id+query` 缺陷**：`serialize_file()` 直接 `model_dump()` 输出 5 个模型与前端都不读的数据库字段（`project_id`、`created_at`、`updated_at`、`is_deleted`、`deleted_at`）；`file_metadata` 里重复输出 `word_count` / `word_count_rev` 或 `null`；`search_summary` 在已有 `matches[]` 时仍附带 `content_preview`；`crud.query_files()` 在同时传入 `id` 与 `query` 时直接忽略 `query` 并返回整篇全文。
4. **短文件 `start_char` 切片分页死循环**：模型对 4k–10k 字的短文件使用 `start_char` + `max_chars` 逐块翻读，把原本一步就能读完的文件拆成 2–10 步并标记为 `content_partial=true`。

## Decision

1. **范围读取区间快照（`tool_context.py`、`mcp_tools.py`、`file_ops/edit.py`）**
   - `_ReadSnapshots` 在保留全文 `range` 基线快照（`StaleContentError` 100% 严格比对全文）的同时，记录并合并模型通过范围读取看过的字符区间 `_range_spans`（同一次回复内多段范围读取合并；本请求局部写入由 `FileEditor` 按每个具体编辑 hunk `(old_start, old_end, replacement_len)` 经 `shift_spans_for_hunks` 精确平移，并在提交时传入 `written_snapshot_for_commit`，绝不桥接多处编辑或 `replace_all` 之间的未读间隙；`begin_agent_run()` 时清空）。
   - `ToolContext.has_seen_file()` 语义不变（仅全文看过时为 True）；`edit_file` 在 `exact_only=True` 且存在 `seen_spans` 时，允许模糊/近似/段落匹配仅落在已读区间内（区间外仍强制逐字匹配；`replace_all=True` 不会在部分命中未读区间后退化为单处近似匹配，且当全文模糊匹配全部落在未读区间时不会退化到已读区间做近似/段落兜底，绝不误伤未读段落或已读相似句）。

2. **`edit_file` UI 侧通道 + LLM 载荷瘦身 + 紧凑 JSON（`tool_context.py`、`edit.py`、`mcp_tools.py`、`tools_adapter.py`、`stream_adapter.py`、`events.py`、`repeat_read_guard.py`、`history.py`）**
   - `ToolContext` 新增请求级线程安全容器 `_ToolUiPayloadStore`（按 `tool_call_id` 索引，未传 `tool_call_id` 的测试替身回退到 `(tool_name, file_id)` 队列，`ToolContext.clear_context()` 清空）；`tools_adapter.invoke_project_tool` 通过 `ToolContext.tool_call_scope(call_id)` 透传 SDK `tool_call_id`。
   - `edit_file` 返回给 LLM 的 `function_call_output` 经 `slim_edit_file_result_for_llm()` 省略 `undo`、精确匹配（`exact` / `exact_quote` 及 `append` / `prepend`）的回显预览键，以及空数组/假值样板字段；非精确匹配的 `matched_original`、`warning`、`similarity`、`layout_notes`、`author_edits_touched` 与非空 `failed_edits` / `warnings` 全部原样保留。
   - `StreamAdapter._handle_workflow_tool_result` 在发 SSE `tool_result`、写 `ChatMessage.tool_calls` 及发 `file_edit_applied` 前从侧通道合并回完整 `undo` 与 `details`，保证前端卡片、diff 预览与撤销/回滚行为 100% 不变。`loop/history.py` 从库回放历史 `edit_file` / `write_chapter` 卡片给 LLM 时同样瘦身，保持冷热前缀一致。
   - `update_project` 移除顶层重复的 `current_status`（保留 `project_status.current_status`、`project_id`、`updated_fields`）。
   - 工具结果与历史回放统一使用 `json.dumps(..., ensure_ascii=False, separators=(",", ":"))`。

3. **文件序列化瘦身与 `id+query` 修复（`serialization.py`、`crud.py`、`mcp_tools.py`、`repeat_read_guard.py`）**
   - `serialize_query_file` 排除 `project_id`、`created_at`、`updated_at`、`is_deleted`、`deleted_at` 5 个库字段；`create_file` / `write_chapter` 排除其余 4 个并保留 `updated_at`（供 `StreamAdapter` 用作 `baseline_updated_at`）。
   - `serialize_file` 剥离 `file_metadata` 中的 `word_count` / `word_count_rev`，为空或 `null` 时省略 `file_metadata` 键，其余元数据以紧凑 JSON 输出。
   - `_attach_query_matches` 在 summary 行 `match_count > 0` 时省略 `content_preview`（仅标题命中 `match_count == 0` 时保留）。
   - `query_files(id=X, query=...)` 在未显式传 `response_mode="full"` / `include_content=True` 且非范围读取时，默认走 summary 模式按 `query` 过滤并附上 `match_count` / `matches[]`；`RepeatReadGuard` 将其归为 `search:<query>` 键，不与全文读取冲突。

4. **短文件 `start_char` 防分页（`content_range.py`、`mcp_tools.py`）**
   - `SHORT_FILE_FULL_RETURN_MAX_CHARS = 12000`：当 `start_char` 作用于总长 `<= 12,000` 字符的文件时直接返回全文（`content_partial=False`、`content_range={0, total}`），使 `_note_file_read` 记为全文快照；当总长 `> 12,000` 但剩余 `total - start <= 12,000` 时一次性返回剩余尾部至文末，避免拆成多次小窗口读取。`section` 与 `tail_chars` 保持原有精确定位语义。

## Alternatives considered

- **直接将范围读取标记为 `has_seen_file=True`**：改动最小，只需把 `_sources["range"]` 加入 `_ReadSnapshots.seen()`。未采用：会让模型在未读过的段落上触发模糊/近似匹配，违背范围读取「未读段落不猜」的安全边界。
- **把 `undo` 与精确匹配 `details` 直接从前端卡片移除，不做侧通道**：无需在 `ToolContext` 与 `StreamAdapter` 之间增加状态。未采用：会直接破坏聊天修改卡的撤销按钮、刷新后撤销状态及 diff 展开详情；请求级侧通道按 `tool_call_id` 精确合并，零前端改动即可保住 100% UI 与撤销行为。
- **在 `serialize_file` 默认移除 `updated_at` 并由调用方重新查库**：可进一步统一所有文件序列化输出。未采用：`create_file` / `write_chapter` 的流式落库与空文件回滚依赖 `updated_at` 作为乐观并发令牌，重查库会引入额外 SQL 与 TOCTOU 窗口；区分 `QUERY_FILES_OMITTED_FIELDS`（5 字段）与 `WRITE_FILE_OMITTED_FIELDS`（4 字段、保留 `updated_at`）更简单安全。

## Consequences

- **收益**：消除范围读取后的 `exact_only` 误判与短文件 `start_char` 多步分页；显著缩减 `edit_file`、`update_project`、`query_files`、`create_file`、`write_chapter` 写入模型上下文的载荷体积与历史回放体积；完全不改动系统提示词与工具 schema，不造成前缀缓存失效，写作质量零退化。
- **成本与注意**：`StreamAdapter` 依赖 `ToolContext` 同请求上下文中的侧通道还原 `edit_file` 的 `undo` 与预览字段；直接调用 `mcp_tools.edit_file` 的单测若需验证 `undo` 需从 `ToolContext.pop_tool_ui_payload` 读取；`FileEditor.edit_file` 与 `serialize_file` 本身的内部契约保持向后兼容。
