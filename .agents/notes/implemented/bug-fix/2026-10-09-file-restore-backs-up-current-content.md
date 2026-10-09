# Agent Note: 恢复历史版本前先备份当前正文；历史面板说清「当前内容」和「项目快照」

Status: implemented

## Problem

2026-10-08 新用户审计（#9、#10、#22、#24、#31）在历史版本上找到一串互相叠加的信任问题：

- 单文件恢复 `FileVersionService.rollback_to_version` 直接用旧版本覆盖 `File.content`，只在额度允许时追加一个 `restore` 版本。作者手动把「三秒」改成「两秒」，如果这次保存没进历史（旧编辑器对 ≤10 字的改动发 `skip_version`，或版本额度已满），点「恢复到此版本」后「两秒」就永久丢了。`2026-10-08-workspace-copy-terms-and-live-limits.md` 因此只能把确认文案写成「已有的历史版本都会保留」，不敢承诺能换回来。
- 文件历史列表第一行固定挂着「最新保存的版本」，哪怕正文里还有没进历史的改动。作者据此以为最新的已经存好，再点恢复就丢稿。
- 版本说明直接显示服务端写入的英文标记和 UUID（`Before rollback to snapshot 0f8f…`、`Snapshot baseline version`），快照说明全是「AI 对话完成 - 文件已修改」；行数写成「+」图标再跟「+35 行」，显示为「++35 行」。
- 顶栏的项目快照入口也叫「历史版本」，和文件里的「历史版本」同名；快照对比只列出哪些文件变了，看不到正文改了什么；快照面板是手写的遮罩，不响应 Esc、不圈焦点。恢复确认用原生 `confirm`，失败用原生 `alert`。

## Decision

- **恢复前备份。** `rollback_to_version` 在拿到文件写锁、读到目标版本内容之后、覆盖正文之前，调用 `_backup_unversioned_content_before_restore`。这个方法只是一层薄包装：备份规则用的是 `FileVersionService.backup_unversioned_content`，AI 覆盖前的备份（`2026-10-09-ai-write-backs-up-unversioned-text.md`）也用它，两边只有失败策略不同。当前正文非空（去掉空白后），且既不等于要恢复的内容（`unless_equal_to`）、也不等于最新版本的内容时，在同一事务里 `create_version(change_type=edit, change_source=system, change_summary="Before restoring version N", force_base=True, skip_quota=True, commit=False)`。比较只看最新版本：正文如果等于更早的某个非最新版本，同样会备份。例如 `POST /files/{id}/versions` 只写历史、不改正文，这之后再恢复，备份 N+1 就是正文，restore 版本是 N+2，响应里的 `new_version_number` 报告的是 N+2。系统来源不占用户的 `file_versions_per_file` 额度，所以额度满了也照样备份。备份过程中（包括读历史头）抛任何异常都 `session.rollback()` 并抛 `APIException(ERR_VERSION_RESTORE_FAILED, 500)`，正文和历史都不变——宁可这次恢复失败，也不丢稿。备份之后的 `restore` 版本仍按原规则受用户额度约束。网页的 `POST /files/{id}/versions/{n}/rollback`、聊天里 AI 修改卡片的「撤销」（同一接口带 `expected_updated_at`）和 Agent API 的 `POST /agent/files/{id}/versions/{n}/rollback`（`agent_api.py` 约 L1200）都走这条路径。
- **当前内容标记。** `GET /files/{id}/versions` 在 `offset == 0` 时多返回 `current_version_number`：列表里最新一条版本的内容（按需重放 diff）与 `File.content` 完全相同时为它的版本号，否则为 `null`；翻页请求恒为 `null`。前端 `FileVersionListResponse.current_version_number?: number | null`。`FileVersionHistory` 只在 `version_number === current_version_number` 的那一行显示 `versions:currentText`「当前内容」/ "Current text"；字段缺失（旧服务端）或为 `null` 时一行都不标。`versions:latestSaved` 已删除。
- **说明文字。** `apps/web/src/lib/versionSummary.ts` 的 `describeVersionSummary(summary, t)` 返回要显示的说明，返回 `null` 表示不显示；文件历史和快照面板都用它渲染说明。已知的系统标记翻译成界面语言：`Restored to version N` →「恢复到版本 N」；`Before restoring version N`、`Before rollback to snapshot <id>` →「恢复前自动备份」；`Restored from snapshot <id>` →「从项目快照恢复」；`Before AI edit` →「AI 修改前自动备份」；`AI edit (reviewed)` →「AI 修改（已审阅）」；`Snapshot baseline version`、`Snapshot synchronized live content` →「拍项目快照时自动保存」；`Created via Agent API` / `Updated via Agent API` →「通过 Agent API 创建/更新」；`AI 对话完成 - 文件已修改` 及英文同义说明（`AI Chat Finished - files modified` 等）→「AI 修改后自动存档」；agent edit_file 写的 `AI 编辑: 替换, 追加 等 N 处修改` →「AI 修改：替换、追加 等 N 处」（操作名逐个翻译，有认不出的操作名时不显示）。只是重复类型徽标的系统说明不显示：`File updated`、`Initial version`、`创建文件`、`AI 更新文件内容`、不带操作的 `AI 编辑`（`File updated` 也会出现在「自动保存」类型上，翻成「手动编辑」会和徽标矛盾）。其他说明原样显示，但其中的 UUID 换成「…」。key 在 `versions:summary.*`（这一套与 #159 的 `versions:summaries.*` 合并而来，同义 key 只保留一个），`t()` 的 `defaultValue` 与 zh 文案一致。服务端写入的标记保持不变，翻译只发生在显示层。未知的 `change_type` 按「编辑」显示，不露出内部取值。`versions:linesAdded/linesRemoved` 去掉 +/- 前缀，符号只由图标表达。
- **确认与报错。** `FileVersionHistory` 的原生 `confirm` 换成 `ui/ConfirmDialog`（`versions:rollbackConfirm`「恢复到版本 {{version}}？当前正文会先存成一个版本，再换成这个版本的内容。」，按钮 `versions:rollbackConfirmButton`「恢复」）；确认框叠在历史弹窗上时 Esc 只关确认框。`ConfirmDialog` 的遮罩 `.modal-overlay`（`index.css`）在 `z-[1050]`：高于 `ui/Modal` 的 `z-[1000]`，低于 Toast 的 `z-[1100]`。原来是 `z-50`，从历史弹窗、快照面板或设置弹窗（API 密钥面板）里打开的确认框会被外层 Modal 盖住，点不到按钮，恢复无法完成；嵌套时确认框的 portal 在 DOM 里还可能排在外层 Modal 之前，所以只能靠层级而不能靠先后顺序。
- **项目快照入口。** 顶栏入口和快照面板标题改叫「项目快照」/ "Project snapshots"（`editor:header.versionHistory`、`editor:versionHistory.title`）；空状态「还没有项目快照，AI 改完文件后会自动存一份在这里」；首行标记「最新快照」；新增 `versionHistory.intro` 一句话说明快照和单文件「历史版本」的区别。文件里的入口仍叫「历史版本」。`VersionHistoryPanel` 改用 `ui/Modal`（Esc 关闭、焦点圈定、关闭后焦点归还；恢复进行中 Esc、遮罩和关闭按钮都不生效），原生 `confirm` 换成 `ConfirmDialog`（`confirmRollbackTitle` / `confirmRollback` / `confirmRollbackButton`），原生 `alert` 换成 `toast.error`。快照说明也走 `describeVersionSummary`，返回 `null` 时显示「无描述」。正在编辑快照描述时按 Esc 只退出编辑，不关闭面板（编辑期间 Modal 自身的 Esc 关闭停用）。
- **快照正文 diff。** `SnapshotComparisonDialog` 对「修改」里新旧版本号都存在且不同的文件显示「查看改动」（`versionHistory.viewChanges`）。点开后才请求 `fileVersionApi.compare(file_id, old_version, new_version)`，用现有 `DiffViewer` 渲染，默认 inline 视图（为此给 `DiffViewer` 加了可选的 `defaultViewMode`，不传时仍是 unified）；再点收起（`hideChanges`），结果在弹窗打开期间缓存；失败显示 `diffLoadFailed` 和「重试」。只改了标题、版本号相同（只改名或移动）的文件不显示按钮。

本笔记推翻了 `2026-10-08-workspace-copy-terms-and-live-limits.md` 里的两条：项目快照不再和文件历史共用「历史版本」这个名字；单文件恢复现在会先存当前正文，确认文案据此改写。

## Alternatives considered

- **只改文案，告诉作者恢复前先手动存一个版本**（审计决策表最初的保守方案）。最强理由：不改写入路径，零风险。被否：作者没有「手动存版本」的入口，旧编辑器还会主动跳过小改动；丢稿发生在作者看不见的地方，文案挡不住。
- **备份占用户额度**。最强理由：防止反复「改一下—恢复」刷出无限条全文版本。被否：额度满恰恰是最需要备份的时候（这时保存已经不进历史）；备份只在当前正文和最新版本不同时才建，每条都对应一份真实存在、别处没有的正文。
- **备份失败时照常恢复，只记日志**（和 `restore` 版本失败时的处理一致）。最强理由：恢复永远可用，作者不会被卡住。被否：备份失败时继续恢复，正好就是原来那个丢稿 bug；恢复失败可以重试，丢掉的正文找不回来。
- **在前端比较「最新版本内容」和编辑器里的正文来决定标不标「当前内容」**。最强理由：不改接口。被否：前端拿到的列表只有元数据，要再拉一次全文；编辑器里的草稿还可能没保存。服务端在同一次查询里比对 `File.content` 才是准确的。
- **把 `File updated`、`Initial version`、`创建文件` 翻成「手动编辑」「初始版本」显示出来**（本分支合并 #159 之前的做法）。最强理由：每条版本下面都有一行说明，版式整齐，也点明了是作者自己改的。被否：类型徽标已经写了「编辑」「创建」，来源也由徽标颜色区分，再写一遍是噪音；而且 `File updated` 是 `api/files.py` 的默认说明，`auto_save` 类型的版本也会带它，写成「手动编辑」会和「自动保存」徽标矛盾。
- **快照对比直接内嵌所有文件的全文 diff**。最强理由：一眼看全。被否：一个快照可能有几十个文件，打开对比就要发几十个 compare 请求、渲染上万行；懒加载只为作者真正关心的文件付费。

## Consequences

- 收益：任何恢复操作之前，没进历史的正文都会留下一份可以再恢复回去的版本；「当前内容」只在属实时出现；历史和快照里不再出现英文标记、UUID 和「++」；两个历史入口名字分开；快照对比能看到具体改了哪几句；三个弹窗都能用 Esc 和键盘操作。
- 代价：每次恢复多一次「最新版本内容重放」和比较；正文与最新版本不同的恢复会多出一条系统版本（不占额度，但占存储）。版本列表第一页多一次全文重放。备份失败会让恢复返回 500，作者需要重试。`DiffViewer` 多了一个可选 prop；`test_versions.py`、`test_file_version_service.py` 里依赖「恢复前不备份」的两个断言、`Editor.history.test.tsx` 的原生 confirm stub 随之更新；e2e 的 `test_versions_contract_e2e.py`、`test_core_api_e2e.py` 现在断言先有系统备份 v4，再有 restore 版本 v5。`2026-09-27-agent-api-structure-and-versions.md` 描述的 Agent API 回滚语义现在也包含恢复前备份。

## Verification

- pytest `tests/test_services/test_file_version_rollback_backup.py`：额度满时把「三秒→两秒」那次没进历史的保存备份成 `Before restoring version 1`（系统来源、base 版本），并能用该备份恢复回「两秒」；没有改动时连续恢复两次不产生备份；备份失败返回 500 `ERR_VERSION_RESTORE_FAILED`，正文与版本数不变；带 `expected_updated_at` 的撤销路径同样先备份。`tests/test_api/test_versions_current_marker.py`：内容一致时返回版本号、翻页为 null、有未入历史改动时为 null。改动前这些用例 5 个失败。
- vitest `ui/__tests__/dialogStacking.test.tsx`：从源码读出三层 z-index，断言 Modal < 确认框 < Toast；Modal 内打开的确认框用 `.modal-overlay`、不带别的 `z-` 类，焦点落在确认框里。Playwright `editor-review-mocked.spec.ts`「file history restore confirmation opens above the history modal and completes the restore」：打开文件历史 → 点 v1 的恢复 → 确认按钮中心点的 `elementFromPoint` 就是该按钮 → 点击后发出 `POST /files/A/versions/1/rollback`、确认框关闭、正文换成恢复后的内容；在 `z-50` 下这条用例失败（命中的是历史列表）。
- vitest `versionSummary.test.ts`（全部规则、zh/en 解析、defaultValue 与 zh 一致、UUID 清洗、与徽标重复的说明返回 null、edit_file 操作名翻译、zh/en key 一致）、`FileVersionHistory.test.tsx`（只在 current 时显示「当前内容」、ConfirmDialog 取消与 Esc、行数没有「+35」）、`VersionHistoryPanel.test.tsx`（Esc 关闭、恢复中 Esc 无效、ConfirmDialog、`toast.error`、说明翻译）、`SnapshotComparisonDialog.test.tsx`（懒加载 compare、渲染 diff、收起后不重复请求、失败重试、同版本不显示按钮）。
