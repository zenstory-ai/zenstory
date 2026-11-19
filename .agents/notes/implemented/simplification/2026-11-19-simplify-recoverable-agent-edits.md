# 回归文件历史与单卡恢复，移除独立回收站与操作账本

Status: implemented locally, pending staging validation (2026-11-19). 部分取代 [改稿按本轮意图执行，删除和撤销提供真实恢复](../feature/2026-11-19-recoverable-agent-edits.md) 中的 `FileEditOperation`、`FileDeletionBatch` 与回收站子系统，同时修复 staging 验收（#407）暴露的写后微调刷屏与压缩摘要可读性问题。

## Problem

PR #403 在移除 `author_edit_guard`（让 AI 按作者本轮意图直接修改、重写或删除手写稿）的同时，额外引入了两套新子系统：

1. **独立的 `FileEditOperation` 操作账本与三方合并冲突弹窗**：而工作台本来就已经具备成熟的 `FileVersion` 自动原稿备份、聊天修改卡条件回滚（`fileVersionApi.rollback`）以及文件历史面板（`FileVersionHistory` 查看/对比/整篇恢复）。维护第二套操作表、每次读取聊天消息时跨表校准状态、再为低频的“先 AI 改、后手写、再点旧卡撤销”单独做三方冲突 UI，显著增加了状态面与心智负担。
2. **独立的 `FileDeletionBatch` 批次表、数量阈值范围确认协议与侧栏回收站弹窗**：为防聊天误删文件，直接把单次 `delete_file` 软删除做成一个带“恢复”按钮的结果卡即可，不需要在侧栏常驻一套完整的批次回收站和二次确认状态机。
3. **Staging 实测暴露的两项体验问题（#407）**：
   - 模型在 `write_chapter` + `<file>` 落库收到编辑器精确字数回执后，为逼近“约 380 字”等近似篇幅在同一轮连续调用 9 次 `edit_file` 微调字数，导致聊天刷出满屏修改卡、原整章替换卡因后续版本推进无法一键回退。
   - `COMPACT_PROMPT` 要求模型原样抄写目录与文件完整 UUID，且 `HistoryCompactionMarker` 直接把含 6 个 UUID 与 813px 宽 Markdown 表格的交接摘要渲染给作者，在 390px 手机视口内造成横向滚表和内部技术标识噪音。

## Decision

1. **保留来源解禁与自然范围理解，改稿恢复完全复用既有 `FileVersion` 体系**：
   - 保持 `author_edit_guard` 移除不变：正文来源仅通过 `author_changes` 提供上下文，不阻断作者本轮明确要求的局部修改、整章重写或删除。
   - 移除运行时的 `services/file_edit_operations.py`、`api/file_edit_operations.py`、前端三方撤销冲突弹窗及 `rename_file` 操作账本；`FileEditor._commit_ai_content` 沿用既有的 `stage_pre_ai_write_backup` + `FileVersion` + `{before_version_number, expected_after_updated_at, expected_after_content_sha256}` 轻量锚点，聊天卡通过 `fileVersionApi.rollback` 回退，跨轮或后续再改后的旧稿统一由文件历史面板恢复。
2. **聊天删除精简为软删除 + 当前卡片一键恢复，移除侧栏回收站与批次确认协议**：
   - 移除运行时的 `services/file_trash.py`、`api/file_trash.py`、`FileTrashModal`、`DeletionTargetList` 与 `useProjectTrash`。
   - `delete_file(id, recursive=false)` 回到单目标软删除，递归删除目录时为当次存活子树打上同一 `deleted_at` 时间戳，卡片返回 `{id, title, file_type, restored: false, mutation_applied: true}`。
   - 新增单一恢复端点 `POST /api/v1/files/{file_id}/restore`：在项目排他锁与文件写锁下校验父目录存活与同级重名，按同一 `deleted_at` 恢复文件或当次一起软删的目录子树（不复活更早单独删除的子项），并通过 `record_delete_card_restored` 将 `restored: true` 写回 `ChatMessage.tool_calls`，刷新与后续模型历史回放保持一致。
   - 鉴于 `20261119_040000_add_recoverable_file_actions.py` 已合并至 `main` 并在 staging 执行，保留该 Alembic 迁移文件及休眠的 ORM 表定义以保证迁移链与元数据检查兼容，不在此次简化中执行破坏性 `DROP TABLE`，运行时代码不再读写这两张表。
3. **收敛写后自查与压缩摘要展示（修复 #407）**：
   - 在 `LOOP_WORKING_RULES`、`_BODY_SAVED_CONTEXT` / `_BODY_SAVED_REPLIED_CONTEXT`、`_BODY_WORD_COUNT_NOTE` 与 `_LOOP_TOOL_USAGE_FILE_WRITES` 中明确：写后自查只针对人名、设定、时间线与大纲情节等事实连贯性矛盾，严禁为凑近“约 X 字”等近似字数或润色措辞调用 `edit_file` 反复增删微调；确有事实矛盾时同一章至多调用一次 `edit_file` 集中修正即止。
   - `COMPACT_PROMPT` 改为输出分级小标题与简短要点列表，不使用 Markdown 表格、不抄写内部文件/目录 UUID、不保留拼写残片或中途自纠过程；前端新增纯语法展示格式化 `formatCompactionSummary`，在渲染前剥离历史摘要中的 UUID / 目录 ID 并将管道表格转为纵向要点行，兼顾存量历史摘要与新生成摘要的移动端可读性。

## Alternatives considered

- **保留侧栏回收站与 `FileEditOperation`，仅删减部分字段与弹窗（首版 PR #406 方案）**：最强理由是清空聊天会话后仍能在侧栏找回已删文件，也能在后写不重叠段落时直接合并撤销旧卡。但代价是工作台同时并存“文件版本历史 + 操作账本 + 回收站”三套恢复机制，代码量与交互分支仍然偏重；相比之下，正文安全已由 `FileVersion` 完整兜底，聊天误删在当轮卡片即可一键恢复，彻底收敛更符合轻量写作工作台定位。
- **通过破坏性 Alembic 迁移立即 `DROP TABLE` 删除两张新表**：最强理由是数据库 schema 与运行时代码完全零残留。但该迁移已在 staging 跑过，直接删表破坏向后滚动兼容；保持表休眠、代码零读写是零风险的演进方式。
- **仅改 `COMPACT_PROMPT` 而不做前端摘要格式化**：最强理由是前端零新增展示转换代码。但存量已落库的压缩摘要（以及模型偶发输出的表格或 ID）仍会在 390px 屏幕出现宽表和 UUID；有界纯语法展示格式化属于允许的 presentation formatting，可同时修复新旧摘要。

## Consequences

- **收益**：彻底消除了约三千行跨层防御性状态机（双新服务、双新路由组、回收站弹窗、范围确认卡片、三方撤销弹窗），改稿恢复回到单一清晰的文件版本历史心智；整章重写后不再因凑字数连刷 9 张微调卡，首张替换卡可直接一键回退；桌面与手机端压缩摘要不再暴露内部 UUID 和横向大表。
- **代价**：通过左侧文件树手动确认删除的文件不再提供侧栏回收站恢复（聊天 `delete_file` 卡片仍可一键恢复）；AI 改稿后作者又继续保存了新内容时，旧聊天卡按版本令牌拒绝直接覆盖，需从文件历史面板查看或恢复旧版本。
