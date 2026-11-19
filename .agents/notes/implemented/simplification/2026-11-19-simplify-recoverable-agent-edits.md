# 收敛可恢复改稿与回收站的冗余分支、重复代码与交叉缺陷

Status: implemented (2026-11-19)

关联：[可恢复改稿与删除](../../implemented/feature/2026-11-19-recoverable-agent-edits.md)、[直接改稿](../../implemented/feature/2026-11-18-direct-ai-edits.md)。

## Problem

PR #403 在移除 `author_edit_guard`（净减 1,022 行）的同时新增了 6,726 行代码与测试，合并后暴露出三类问题：

1. **低频假想小场景导致的协议与状态膨胀**：
   - 为新建文件（`create_file` / `source_text` / `write_chapter(created)`）额外设计“撤销正文变 0 字空壳文件 + 三方合并”：创建时先插空文件再写双份全量版本，并在 `StreamAdapter` / `AgentService` 增加 `_created_tool_cards` / `_saved_body_undos` 二次补发链路，而新建文件本身已可通过回收站一键删除与恢复。
   - 在 `FileVersionHistory` 顶部叠加第二套分页列表 `GET /files/{id}/edit-operations`（「最近 AI 操作」）并复制整套冲突弹窗，不仅与下方的版本列表概念重叠，还因挂载即额外发起未预期请求导致 `main` 上的 `editor-review-mocked.spec.ts` 与 `file-history-design-mocked.spec.ts` E2E 失败。
   - 把内部树排序整数 `order` 纳入 `metadata_conflict`，向作者弹出 `当前排序位置: 7 / 恢复排序位置: 3` 选择框。
   - 文件树侧栏手动删单文件也先弹 loading 禁用框请求 `POST /deletion-preview` 锁库，并把正文哈希拼进 `target_hash`（打开中的文件触发自动保存即报 `409`）；同时把「回收站」按钮插在搜索框与文件树之间，截断 `Search -> Tab -> 首个文件行` 键盘焦点链，导致 `main` 上的 `accessibility.spec.ts` E2E 失败。
   - `mcp_tools._delete_file_sync` 在已有「文件夹必确认 + 同轮累计 ≤5 个叶文件」硬规则外，又加了同轮粘滞惩罚位 `confirmation_required=True`，连作者插话缩小为删 1 个废稿也会误拦。
2. **零调用死代码、前端整块复制粘贴与过度防御**：
   - `FileCRUD.delete_files`、`chat_tool_cards.hydrate_edit_operation_states` 在生产路径零调用；前端 `fileApi.delete` 被绕开闲置。
   - `ChatPanel` 与 `FileVersionHistory`、`FileTreePane` 与 `MobileFileTree` 分别整段复制了冲突状态机/弹窗与回收站状态机/弹窗（约 350+ 行重复代码）。
   - 纯读接口 `GET /projects/{id}/files/trash` 先抢 Project `FOR NO KEY UPDATE` 行锁再手动 `rollback()`；`stage_recoverable_file_mutation` 对只改 1 字的 `edit_file` 乃至不改正文的 `rename_file` 一律强制 `force_base=True` 写全量快照。
3. **交叉路径上的真实缺陷**：
   - `hydrate_recovery_card_states` 用 `order_by(created_at.desc())` 配字典推导式，旧批次覆盖新批次，重删后状态回退显示「已恢复」。
   - `POST /delete-batch` 硬编码 `source="agent_confirmed"` 且被侧栏手动删除共用，既污染来源记录，又会按 `target_hash` 误把聊天里未确认的预览卡改成「已删除」。
   - 确认删除只做聊天读取时内存覆盖、不回写 `ChatMessage.tool_calls`，导致 `loop/history.py` 下一轮向模型回放 `confirmation_required=true`（声称尚未删除）。
   - `ChatPanel.handleUndo` 让 `operationId` 的并发 `409 stale_write` 落入旧版 rollback 分支，错误提示去历史恢复并用 `staleUndoEdits` 锁死按钮；`aiWriteCards.undoBlockedByLaterWrite` 又把带 `operationId` 的早先修改卡按旧规则置灰禁用，与独立合并撤销自相矛盾。
   - 新增机器版本摘要（`Undid AI edit_file`、`Before undoing AI edit`、`AI 重命名文件` 等）未接入 `versionSummary.ts`；`FileEditOperation.created_at` 使用已弃用的无时区 `datetime.utcnow`；REST `update_file` 改标题未按文档约定取 `exclusive=True` Project 锁，反而给纯 `metadata` 更新加了多余锁。

## Decision

在不改已合并 Alembic 迁移表结构（保留 `file_edit_operation` 与 `file_deletion_batch` 全部列兼容）的前提下，收敛并修复如下：

1. **移除 5 处过度设计的小场景与冗余端点**：
   - 移除新建文件的「撤销正文变空壳」机制与 `StreamAdapter` / `AgentService` 的 `created_body_undos` 补发状态：新建非空文件或空文件首次流式落库只记初始版本，不造伪空前像 `FileEditOperation`；`edit_file`、`write_chapter(replaced)`、`rename_file` 保持一键操作撤销。
   - 移除 `GET /api/v1/files/{file_id}/edit-operations` 及 `FileVersionHistory` 内的「最近 AI 操作」列表与重复冲突弹窗，恢复历史面板纯粹职责。
   - `metadata_conflict` 只核对标题（`before_title` / `after_title`），不再把内部 `order` 暴露成作者冲突项。
   - 文件树手动删除直接基于内存已加载的 `FileTreeNode` 子树展示确认范围并调用 `DELETE /api/v1/files/{file_id}`（`source="file_menu"`），移除 `POST /projects/{id}/files/deletion-preview` HTTP 端点；将回收站按钮移至文件树底部栏，恢复搜索框 `Tab` 直达首个树节点。
   - 移除 `mcp_tools._delete_file_sync` 的同轮粘滞惩罚位，保留「含文件夹或同轮累计叶文件 > 5 则需确认」。
2. **清理死代码、防御性锁与快照膨胀，抽取共用组件**：
   - 删除 `FileCRUD.delete_files`、`hydrate_edit_operation_states`；抽取 `FileTrashModal` 供桌面与移动端文件树共用。
   - 移除 `FileTrashService.list_batches` 纯读接口上的 Project 行锁；`stage_recoverable_file_mutation` 在正文未变（如 `rename_file`）时直接复用同一精确 `FileVersion` 作为前后锚点，正文变化时走 `create_version` 常规增量 diff 策略而非一律 `force_base=True`。
   - 对齐 `api/files.py`、`api/agent_api.py`、`crud.py`、`edit.py` 的 Project 锁：`parent_id` / `title` 变更取 `exclusive=True`，`order` 变更取 `exclusive=False`，纯 `metadata` 更新不再锁 Project。
3. **修复全部交叉逻辑缺陷**：
   - `POST /delete-batch` 专供聊天确认删除，支持传入 `chat_message_id` / `tool_use_id` 在确认成功后回写原卡片状态，使 `loop/history.py` 下一轮能读到真实已删除事实；同时修正 `hydrate_recovery_card_states` 按 `created_at.asc()` 归并最新批次。
   - `ChatPanel.handleUndo` 区分 `operationId` 与旧版 rollback 错误处理，不将并发 `stale_write` 标进 `staleUndoEdits`；`undoBlockedByLaterWrite` 仅阻塞无 `operationId` 的旧卡片。
   - 在 `versionSummary.ts` 与中英文 `versions.json` 补齐新增机器摘要映射；`FileEditOperation.created_at` 改用 `utcnow`。

## Alternatives considered

- **连同 `file_edit_operation` 表一起撤回，只用 `FileVersion.rollback_to_version`。** 最强理由是省去一张表的维护。未采用：已有 `20261119_040000` 迁移已合入 `main`，遵循仓库不发破坏性 `DROP TABLE` 迁移原则；且保留精简后的 `FileEditOperation`（约 250 行服务代码）确实能低成本支撑「改名撤销」与「作者后续补写非重叠段落后的一键合并撤销」。
- **保留 `FileVersionHistory` 里的「最近 AI 操作」但补齐所有 E2E mock。** 最强理由是不减少清空聊天后的操作级合并入口。未采用：清空聊天后历史面板本就有完整的逐版本查看、对比和整版恢复，在同一弹窗叠两套高度重合的历史列表会让日常阅读界面变杂，还会让每次打开历史多一次请求。

## Consequences

- 收益：大幅削减约 2,000+ 行冗余生产/测试代码与重复弹窗，消除每次小改/改名的全量版本膨胀与纯读锁库，修复 `main` 上失败的 3 组 E2E 用例以及聊天确认删除跨轮失忆、侧栏删除串改聊天卡、并发撤销误锁卡、版本摘要裸露英文等 7 处真实缺陷。
- 代价：新建文件卡片不再提供「撤成 0 字空文件」按钮（改由文件树或聊天移入回收站并可整批恢复）；清空聊天后不再从历史面板单独发起三方合并撤销（改用既有的版本对比与整版恢复）。
