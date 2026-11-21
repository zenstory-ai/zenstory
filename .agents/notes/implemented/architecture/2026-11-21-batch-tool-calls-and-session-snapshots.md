# Architecture Note: 跨轮未变更会话快照结转、edit_file 批量合并指引与 query_files 批量 ids 读取

Status: implemented

关联：[作者改了什么给模型看](./2026-10-12-author-edits-shown-not-guessed.md)、[query_files 按章节或范围读取长文件](../feature/2026-10-19-query-files-range-reads.md)、[单循环 agent](./2026-11-16-single-loop-agent.md)。

## Problem

生产 24 小时工具调用与负载审计（`source='agent'`，MiniMax-M3.1-Flash）显示三类零质量损失的多步浪费：

1. **跨轮假阳性 `exact_only` 导致 `74 / 229`（32.3%）次 `edit_file` `anchor_not_found`**：`turn_context.py` 只把本轮 `<turn_context>` 全文渲染的文件放进 `read_snapshots["initial"]`。模型在前一轮已经读过或写过、且轮间完全未改动的文件，在下一轮被 `ToolContext.has_seen_file()` 判成没看过，触发 `exact_only=True` 关闭模糊/近似匹配，造成大量整批回滚和额外重读步数。
2. **提示词反向引导把同一文件的编辑拆成多次串行调用 + 对短文件滥用 `start_char` 翻页**：`prompts/base.py`、`tool_schemas.py`、`file_ops/edit.py` 与 `tool_failure_breaker.py` 存在「改动多时拆成几次调用」「每次只改少量几处」的旧指引，导致单轮内对同一文件连续串行 `edit_file`（`190` 对相邻成功调用、`119` 次文件轮次出现 `>=3` 步），且 `start_char` 未注明仅适用于超长文件，导致中位长度仅 `4,657` 字的常规文件被分段翻页 `2–10` 步。
3. **`query_files` 只支持单个 `id`**：连续按 id 读 2–5 个已知文件时，模型经常按单步串行调用（`105` 次可合并串行步骤），每多一步就要全量重放整个前缀缓存。

## Decision

1. **跨轮未变更会话文件快照结转（`core/session_loader.py`、`loop/turn_context.py`、`tools/tool_context.py`、`service.py`）**：
   - 新增 `SessionLoader.session_read_snapshots`（上限 `SESSION_SNAPSHOT_MAX_FILES=20`、最多扫 `SESSION_SNAPSHOT_MAX_PAGES=2` 页 assistant 行的 `tool_calls`/`created_at`/`message_metadata`），按单次批量 `SELECT File WHERE id IN (...) AND project_id = ... AND is_deleted = false` 核验后结转进 `LoopTurnContext.read_snapshots`（同时记录 `session_carried_snapshot_ids`）。
   - **严格安全不变量（绝不绕过 `StaleContentError`）**：只有文件当前库内状态与本会话最近一次工具触达时的精确凭证完全一致才结转——`query_files` 全文读 / `create_file` 直接写入核对 `content` 与（若有）`updated_at`；`edit_file` / `write_chapter(replaced)` 核对 `undo.expected_after_content_sha256` 与 `expected_after_updated_at`；`write_chapter(created)` / `create_file` 流式 `<file>` 落库核对 `file.updated_at <= assistant.created_at` 且单次批量查得的最新 `FileVersion` 属于 AI 且正文逐字一致。只要作者在轮间改过文件（哪怕未推进 `updated_at` 或未建版本），一律不结转，保持 `exact_only=True`；未变更文件结转后若作者在**本轮中途**修改，锁内快照比对仍照常抛出 `StaleContentError`。
   - 扫描遇到有效压缩点即止；若本轮开始前在 `service.py` 刚完成轮间压缩，调用 `ToolContext.drop_initial_read_snapshots(loop_turn.session_carried_snapshot_ids)` 撤销仅来自此前轮次的结转快照（保留 `<turn_context>` 本轮全文快照）。
2. **统一 `edit_file` 批量合并指引与 `start_char` 超长文件门槛（`prompts/base.py`、`tools/tool_schemas.py`、`tools/file_ops/edit.py`、`openai_agents/tool_failure_breaker.py`、`loop/engine.py`）**：
   - 移除所有「改动多时拆成几次调用 / 每次只改少量几处 / 不要堆很多条」的旧文案；明确指引把同一文件的多处互不依赖修改合并到一次 `edit_file(..., continue_on_error=true)` 调用中批量提交，仅在超过 15 处修改或后项依赖前项改后文本时拆分。
   - 在 `query_files` 工具描述、`start_char`/`max_chars` 参数说明与错误恢复提示中明确 `start_char + max_chars` 仅用于超过 `15,000` 字的超长文件，常规长度文件直接按 `id`/`ids` 读全文。
3. **`query_files` 增加批量 `ids: list[str]`（最多 5 个）（`tools/file_ops/serialization.py`、`tools/file_ops/crud.py`、`tools/mcp_tools.py`、`openai_agents/repeat_read_guard.py`、`core/session_loader.py`）**：
   - 保留单 `id` 100% 向后兼容；新增可选 `ids`（去重、上限 `QUERY_FILES_MAX_BATCH_IDS=5`，超限时截断至前 5 个并在结果附 `truncated_ids` 与 `note`）。
   - `FileCRUD.query_files` 在 `check_project_ownership` 之后用单条 `File.id.in_(target_ids)` 且限定 `project_id` 与 `is_deleted=False` 的 SQL 查询返回结果，按传入顺序重排并按文件附 `author_edits`。部分缺失时返回 `missing_ids`，全部缺失返回 `error_type="files_not_found"`；多 `id` 不允许混用范围参数（`section`/`tail_chars`/`start_char`）。
   - `_note_file_read` 逐行记录每个非截断非局部全文文件的 `_ReadSnapshots`；`RepeatReadGuard` 逐 ID 记账且与单 `id` 共享同一 `ReadKey`（无法通过交替 `id`/`ids` 绕过守卫），批量读取里只有部分 ID 触及上限时自动改写参数只查剩余允许的 ID 并附 `blocked_ids` 与 `read_hint`。

## Alternatives considered

- **作者轮间改过文件时，把旧版正文塞进 `read_snapshots["initial"]` 让下次 `edit_file` 直接抛 `StaleContentError`**：最强理由是能让模型在第一次 `edit_file` 就看到作者改动的行级 diff。未采用：`edit_file` 与流式 `write_chapter` 的持久化 `tool_calls` 并不存完整旧正文（只有 sha256 与时间戳），无法在不额外回放 `FileVersion` 的前提下构造旧快照；不结转已变更文件即可让 `exact_only=True` 拦住基于过时记忆的模糊改写，同时不额外增加 `prepare_context` 的数据库负载，也不误伤合法的逐字定位或 `append`。
- **`query_files(ids=[...])` 超过 5 个 ID 时直接整次报错拒绝**：最强理由是接口契约最严格。未采用：报错会白耗一整个 LLM 回合让模型重发前 5 个 ID；截断返回前 5 个并显式给出 `truncated_ids` 与 `note` 既保住上限又零浪费步数。
- **在 `edit_file` 服务端强行把 `continue_on_error` 默认值改为 `True`**：最强理由是不依赖模型显式传参。未采用：会静默改变既有客户端与全部依赖默认原子回滚语义的调用方及测试；通过提示词与 schema 引导模型在多处独立修改时显式传 `continue_on_error=true`，保持默认原子回滚向后兼容。

## Consequences

- **收益**：
  - 消除跨轮未变更文件的 `exact_only` 假阳性（目标覆盖 `74/229` 的 `anchor_not_found`），零质量损失且完全保留轮间与轮中作者手改保护。
  - 同一文件多处独立修改合并为单次 `edit_file(..., continue_on_error=true)`，读取 2–5 个已知文件合并为单次 `query_files(ids=[...])`（1 条 SQL），并减少常规长度文件的 `start_char` 翻页，直接削减多余步骤与前缀缓存重放费用。
- **代价与注意事项**：
  - `query_files` 与 `edit_file` 工具 schema 更新会在发布后产生一次性的系统前缀缓存刷新，之后保持稳定不变。
  - `prepare_context` 新增有界会话快照检查（至多 2 页轻量 `chat_message` 查询 + 1 条批量 `file` 查询 + 仅在存在流式新建文件时 1 条批量 `file_version` 查询），异常时降级为空字典不阻塞本轮。
