# 上一线上版本以来跨批次回归收口与冗余代码清理

Status: implemented locally, pending staging validation (2026-11-19). 补充收口 `prod-v2026.11.18.5`（`a33febfd`）至 `34d74203`（PR #397–#406、Issue #407）之间的跨改动边界问题与遗留防御/死代码。

## Problem

系统回审 `prod-v2026.11.18.5` 以来合并的全部后端与前端改动后，各独立特性（Token 校准、`turn_context` 120k 预算、`<author_edits>` 隐藏、编辑器脏稿保护、图编排下线、单卡软删除恢复、写后防刷屏与压缩摘要净化）核心目标均已落地，但多次重构叠加留下了两类问题：

1. **跨 PR 交互缺口与真实边界缺陷**：
   - **聊天删除恢复闭环不完整**：`MessageManager` 落库字段为 `arguments` 而 `record_delete_card_restored` 读取 `input`，导致旧布尔结果卡无法写回 `restored: true`，且未过滤失败卡或核对 `deleted_at`；固定根目录（`{project_id}-*-folder`）在恢复前被自愈复活时，其同批软删的子章节会失联；恢复后的删除卡仍在 `SessionLoader.session_written_file_ids` 中屏蔽 `<author_edits>`；前端点击“恢复”后未清除 `fileRemovalEvents` 删除围栏、未同步更新当前消息状态，流式中尚未落库的删除卡过早暴露“恢复”按钮，且恢复文件重新打开后 `Editor` 顶部的“已删除文件草稿”横幅无法消退。
   - **待写正文窗口与计费账本不一致**：`_delete_file_sync` 缺少对 `write_chapter` 整章替换待写 `<file>` 窗口的拦截，且 `StreamBillingTracker` 在 `delete_file` 成功后未同步清理 `replace_target_ids`；合并冲突曾回退 `CREATE_FILE_TOOL` 的 `empty` 契约说明；导入章节带 `书名 - 第N章 标题` 前缀时整章替换会丢失书名前缀；流式 EOF 降级重试漏传 `input_items`。
   - **前端 `<author_edits>` 与摘要纯语法清洗边界**：行内代码反引号提及 `` `<author_edits>` `` 会误吞后续全部回复；流式半截 `<author_` 在闭合为空串后残留在 `streamRenderItems`；仅含 `<author_edits>` 回显的消息仍渲染空头像壳；`formatCompactionSummary` 未覆盖单横线/无外竖线 GFM 表格、`UUID`/`project_id` 标签、局部加粗首列及表后紧接正文的懒续行。
2. **重构残余与冗余防御代码（AI slop）**：
   - 后端残留多智能体路由器与旧操作账本时代的死代码：`FileWriteRecoveryError` 抽象基类及 `_create_file_sync` 不可达捕获分支、休眠模型上的 `EDIT_OPERATION_KINDS`、`ToolContext.get_author_messages()`、`_write_recovery_payload`、`empty_requested` 路由旧注记、`_delete_file_sync` 锁外重复 `SELECT` 预探测，以及 `_FAILURE_CALL_SITES` 中已下线的 `"router"`。
   - 前端残留 `useAgentStream` 中仅用于更新普通数组 `ref` 的 `immer` `produce` 代理与过时注释、空实现的 `onSegmentEnd` 透传、16 个零引用的 `TreeNode` / `SSE*Data` 接口、`ContextItemsView` 未使用的 `tokenCount`/`label` 参数，以及 `chat.json` 中每语种 18 个零引用的历史叶子文案键。

## Decision

1. **补齐跨模块状态闭环与边界一致性**：
   - `record_delete_card_restored` 兼容解析 `arguments`（字典或 JSON 字符串）与 `input`，跳过失败/未生效卡并核对 `deleted_at`；`POST /api/v1/files/{file_id}/restore` 支持固定根目录自愈后按卡片 `deleted_at` 找回同批软删子项并返回恢复 `file_ids`；`SessionLoader` 忽略 `restored: true` 或未生效的删除卡。
   - 前端恢复成功后同步调用 `clearFileRemoved`（文件树刷新时亦对存活节点清理围栏）并即时标记消息卡 `restored: true`；流式未落库阶段隐藏恢复按钮；恢复后的文件在 `Editor` 重新加载、保存或丢弃草稿时自动将 `"file-deleted"` 快照收敛回常规草稿并移除已删文件横幅。
   - `_delete_file_sync` 补齐非新建或递归删除时的 `pending_empty_file_for` 拦截（保留新建空占位文件的非递归清理语义），`StreamBillingTracker` 在删除成功时同步清理替换目标集合；恢复 `CREATE_FILE_TOOL.empty` 说明；`write_chapter` 保留上传章节的 `书名 - ` 前缀；补齐流式降级 `input_items` 与取消收尾时的标题/撤销回执校准。
   - 收紧 `stripThinkTags`（排除反引号包裹、剥离流式末尾 `<author_` 半截标签）、`useChatStreaming.onSegmentUpdate`（流式闭合为空时清空内容）、`MessageList` 空历史/流式消息过滤与 `formatCompactionSummary` 表格/UUID 纯语法转换。
2. **直接删除死代码与过度包装，不加兼容垫片**：
   - 删除 `FileWriteRecoveryError`、`EDIT_OPERATION_KINDS`、`ToolContext.get_author_messages()`、`_write_recovery_payload`、`empty_requested` 旧注记与 `_delete_file_sync` 锁外预查询（改由 `FileCRUD.delete_file` 锁内记录 `_last_deleted_meta`）。
   - 移除 `useAgentStream` 对 `immer` 的依赖与默认导出、空 `onSegmentEnd` 消费端透传、`ContextItemsView` 冗余参数、16 个未引用类型接口及中英文 `chat.json` 中各 18 个孤儿叶子文案键。

## Alternatives considered

- **仅清理死代码，将跨 PR 边界问题留待线上反馈后再修**：最强理由是变更集更纯、改动面更小。但审查已定位出明确的跨模块断链（例如旧删除卡 `arguments` 无法恢复、恢复后编辑器删除围栏与横幅卡死、行内反引号吞正文），带病合入违背本次全面复盘初衷；在有界回归测试覆盖下一并修复成本最低。
- **在 `FileCRUD.delete_file` 中一刀切禁止非空目录 `recursive=False`**：最强理由是表面语义更严格。但既有契约测试与工具层明确依赖非级联软删除目录节点而不级联子项的行为，强行收紧会破坏既有兼容契约，因此保持原目录软删除契约不变。

## Consequences

- **收益**：消除了 `prod-v2026.11.18.5` 以来多次架构演进（单循环、去守卫、去回收站、隐藏内部块）交界处的状态断链，同时清理了跨前后端的残留抽象、无意义代理包装与孤儿文案。
- **代价**：同一批次同时包含有界缺陷修复与死代码清理，需完整跑通前后端单测、类型/文案门禁及独立代码审查后再合入 `main`。
