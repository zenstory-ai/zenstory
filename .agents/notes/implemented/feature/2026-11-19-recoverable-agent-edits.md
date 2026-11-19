# 改稿按本轮意图执行，删除和撤销提供真实恢复

Status: implemented locally, pending release validation (2026-11-19). 部分小场景与接口细节由后续[收敛可恢复改稿与回收站](../../implemented/simplification/2026-11-19-simplify-recoverable-agent-edits.md)精简与修正。

## Problem

作者已经取消聊天逐段审阅，但整章替换仍按正文来源拒绝。作者明确要求重写自己保存过的一章时会被挡住；同样的修改拆成多个局部编辑却能执行。继续增加拒绝后的停轮状态不能解决这个产品矛盾。

正文历史不能独自覆盖完整恢复：现有版本不存标题，聊天撤销遇到后来手写就整体拒绝，软删除也没有作者可用的恢复入口。用户在产品与作者场景推演后明确要求将完整方案一起实现。推演是基于产品和代码的场景分析，并非作者访谈。

本方案将取代[直接改稿](../../implemented/feature/2026-11-18-direct-ai-edits.md)保留的来源硬拦截，以及[作者手改保护](../../implemented/bug-fix/2026-10-09-agent-respects-author-manual-edits.md)中相应的权限判断；保留作者改动上下文。它也扩展[逐次撤销](../../implemented/bug-fix/2026-11-16-undo-earlier-write-after-later-undo.md)的整份正文相等限制。未合并的 PR #402 拒绝后停轮补丁由本方案替代。

## Decision

### 作者体验

- 模型按本轮请求合理涵盖的作品范围执行：这一章、全书统一、两稿合并都不要求逐个文件 ID。存在多个合理目标且选错会造成实质改动时才问。程序校验结构化目标、项目权限与版本，不用关键词、独立分类器或模型自报的 force 判定授权。
- 新写、局部改、整章重写直接保存，显示简洁结果卡和撤销，不重新引入段落审阅。流式新正文、调用内直接正文和 `source_text` 原文复制都建立原子 empty-before/body-after `edit_file` operation；“撤销正文”只把该次正文恢复为空，保留同一个文件 ID、标题、父目录和排序，并保留后来不重叠的作者文字。作者来源只提供上下文，不否定当前明确请求。保留原稿历史硬锚点、草稿恢复和并发保护。
- 一次正文与标题修改作为一次动作撤销；独立改标题也可撤销。裸章号定位保留既有副标题。
- 撤销只移除该次 AI 改动。与后来手写不重叠的改动自动合并；真实重叠先不写，显示紧凑冲突选择，默认保留作者当前文字。处理期间有新保存则重新核对，不覆盖。
- 精确指定的少量普通文件可由聊天直接移入回收站；文件夹或大批量先展示一次准确范围确认。一次批次只有一次确认与一次恢复，不逐章确认。默认“少量”为至多 5 个叶文件，这是本次交互阈值而非自然语言判断。
- 文件树提供持久回收站，聊天删除卡提供同一个批次的撤销。恢复保留文件 ID、正文、标题、层级和排序；不自动恢复早于该批次删除的子项，不覆盖已有同名文件，旧批次不可撤销后一次删除。
- 停止不再启动后续生成或修改，允许在途原子提交和已有正文完成收尾；进入停止终态后数据不再变化。停止不等于撤销。
- 停止链路本轮只作为回归不变量验证；只有出现实际失败才修复，不顺势重构。
- 移除 `ChatPanel.handleUndo` 现有的普通撤销确认。真实冲突使用紧凑对话框，只展示重叠片段及当前/旧稿选择，复用既有 merge/diff 数据而非整章 `DiffReviewSplitView`；非冲突部分自动撤销。独立 rename 已被后续改名取代时先零写提示，绝不自动把标题改回。
- 文件树文件夹删除统一预览完整 live subtree 并按 recursive 整批确认；不能沿用现有只删文件夹自身的无 recursive 请求。

### 实施顺序与边界

1. 从已验证的 main `19e08e7b` 开始新分支，保留 PR #402 及旧 QA 原始证据。先确定服务端恢复记录与前端接口，独立架构/审查通过后实现。
2. 先写缺失的行为回归，再移除来源判断；不删除 `author_changes`、`whole_body_replace` 工具路由、历史备份失败保护、写锁、SDK 工具串行、停止/计费/空文件收尾。
3. 原子记录可撤销的标题和正文前后状态；聊天卡只携带小型不可变锚点，不携带整章双份正文。具体存储和接口见下。
4. 删除、批次恢复与回收站使用同一服务和现有项目树锁；确认绑定确切文件集合及版本，恢复绑定确切删除代次。普通编辑不能改写恢复记录。
5. 前端沿用现有 Modal、ConfirmDialog、结果卡、草稿合并与主题/i18n；增补手机、键盘、失败重试、刷新后可恢复状态。变更 `DESIGN.md` 和中英文帮助事实。
6. 后端与前端分工实现，集成后独立交叉 review；提交 PR 附前后截图。最终 main CI/E2E 全绿后部署 staging，并让交互式 agy 验证真实浏览器与模型。新证据单独绑定新 SHA，不覆盖旧 19e 测试记录。
7. 按已授权的发布流程上线；任何未通过的恢复/并发/停止验收阻止上线，先修复再验证。保留既有 production source 与 Prefect drain/source/health 门禁。

### 存储与接口合同

- 新增 `FileEditOperation`：id、user_id、project_id、file_id、kind（edit_file/write_chapter/rename_file）、before_version_id、after_version_id、before_title/after_title、before_order/after_order、after_updated_at、after_content_sha256、created_at、undone_at、undo_version_id。正文仍存在 `FileVersion`，这里不重复存。聊天清空会物理删除消息，所以不能只靠卡片 JSON 作为撤销状态事实。流式写入、调用内直接写入及 `source_text` 创建出的非空正文统一记录为 `kind=edit_file`，精确前像为空正文、后像为本次正文；撤销不删除文件，也不改变未被该 operation 修改的 ID、标题、父目录和排序。
- 每次可撤销 AI 动作在同一文件锁/事务中建立精确 before、必需 after 版本和 operation；空正文也有精确锚点。任何锚点或 operation 写入失败整次回滚。rename 也创建正文相同的 AI after 版本。版本 coalesce 不得改写被 operation 引用的行。无变化不建立 operation。
- 新卡的 `undo` 保留旧 before_version_number/post token/hash，增加 `operation_id`；新客户端优先 operation API，旧卡仍走原条件 rollback。标题恢复不依赖客户端任意提供的前后标题。
- `GET /api/v1/files/{file_id}/edit-operations` 返回分页最近 AI 操作摘要及 undone 状态；文件历史提供对应入口和同一个撤销动作，聊天清空后仍能找到标题/正文操作。操作详情由 undo 首次调用按需返回，不让列表下载正文。
- `POST /api/v1/files/{file_id}/edit-operations/{operation_id}/undo`：空请求先尝试撤销。当前正文等于 after 且操作改过的标题/排序仍等于 after 时直接原子恢复。不同则零写入返回 `status=needs_merge`、`before/after/current`（各含 content/title/order，current 另有 updated_at），让前端计算 `rebaseLocalEdits(after.content,current.content,before.content)`。快照只在此 API 返回，不进入聊天。
- 原操作未改正文时，undo 永远保留当前正文；当前某维度已等于 before 时视为该维已经恢复，不误判冲突。成功 undo（即使只改标题）也创建 system/restore 版本并与 operation.undone_at 同事务提交；任何失败全回滚。
- 无重叠时前端直接二次 POST `{expected_updated_at,expected_after_content_sha256,resolved_content,metadata_resolution:"preserve"|"restore"}`；有真实冲突才进入现有比较并默认保留 current，最终提交相同合同。服务端在锁下验证当前 token、operation 状态，重新备份当前稿并原子保存正文+标题/排序。operation 没改标题/排序时永远保留后续值；改过但 current 已不同则必须在 UI 明确选保留当前或恢复旧值。恢复旧标题需重查同父重名。成功返回 `status=undone`、file_id、operation_id、updated_at、undo_recorded。重复撤销幂等返回成功，不再写。读取聊天历史时用 operation 状态校准卡片 `undone`，不依赖尽力而为的 JSON 标记。
- 新增 `FileDeletionBatch`：id、user_id、project_id、source、root_ids（JSON）、target_hash、items（JSON）、created_at、restored_at。每个 item 存 id/title/file_type/parent_id/order/deleted_updated_at/content_sha256；不复制正文或上传对象。只记录这次从 live 变 deleted 的确切集合。
- `POST /api/v1/projects/{project_id}/files/deletion-preview` 接收 `{ids,recursive}`，返回 `root_ids,recursive,targets[{id,title,file_type,parent_id,order,updated_at}],count,target_hash`。项目/目录锁下规范化和去重，hash覆盖完整目标集、正文/元数据状态。模型文件夹或请求内累计直接删除将超过 5 个叶文件时，仅得到 `confirmation_required=true` 和该预览；超额调用整批不部分删。
- Agent `delete_file` schema 为 `{id?:string, ids?:string[], recursive?:boolean}`，id/ids 恰一者提供；旧 id 保持兼容。根 ID 先规范化、去重且最多 100 个，全部必须属于同一项目；100 是输入根集合上界，目录展开后的精确目标集合可更大。少量叶文件在一个原子 batch 删除。任何 folder 或累计将超 5 都返回同一 `confirmation_required + preview` 卡；同一作者请求一旦触发确认，request-local sticky 标记会让后续缩小目标或拆分的 `delete_file` 仍必须经 UI 确认，下一条作者请求才重置。用户一次确认调用 delete-batch，不能通过重复单删绕过。
- `POST /api/v1/projects/{project_id}/files/delete-batch` 接收同一 `{ids,recursive,target_hash}`。这个已鉴权的 UI 专用确认入口不暴露为模型工具；服务端项目锁下重算目标集与 hash，变化则 409，不能只信客户端计数或 force。精确少量 agent 删除在同一服务内执行并返回批次。原单文件 DELETE 与外部 Agent API DELETE 保留响应兼容，内部也写批次；外部 API 保留 API-key scope/project 授权，来源记录为 `agent_api`。
- 删除成功返回 `batch_id,root_ids,targets,count,restored:false,mutation_applied:true`；聊天以文件名/数量呈现。`GET /api/v1/projects/{project_id}/files/trash` 返回未恢复批次（limit/offset/next_offset 分页）；`POST /api/v1/projects/{project_id}/files/trash/{batch_id}/restore` 恢复整批并返回 `batch_id,file_ids,restored:true`。列表分页；卡状态同样按 batch 的 restored_at 校准。重复恢复幂等，不重新影响后一次删除。
- 恢复先核对每行仍 deleted、deleted token 相等；批内父一起恢复，批外父必须是同项目 live folder。同父同名、父目录缺失或代次不符均整批零写。旧已删子项不入 batch，后续重删有新 token。旧的无批次删除不冒充可恢复，不做推断式历史回填。
- 涉及树/标题/删除/恢复统一锁序：Project gate → File rows / SQLite 按 ID 排序的 stripe locks → Operation/Batch row；所有结构 writer 都先取同一个 Project gate，创建与排序使用 shared gate，可改变父子图、标题唯一性、删除/恢复或撤销的路径使用 exclusive gate；纯正文沿用文件锁。两张表使用一次 additive Alembic migration，新增索引/FK，无新依赖。

### 并行文件所有权

- backend undo：新 operation model/service/API、FileEditor 提交路径、version coalesce、chat undo 状态与其测试。
- backend trash：新 batch model/service/API、FileCRUD/REST 删除共用、对应恢复与并发测试。
- frontend：types/API、卡片和 ChatPanel、文件树回收站/确认、冲突交互、i18n 与前端测试。
- 根：两个模型的注册与 migration、mcp/tool schema/stream 协议胶合、来源规则移除与模型范围提示、跨层回归、设计/文档、集成验证和发布。共享文件先协调，不覆盖另一条分工的修改。

### 验收

- 作者手写章节整章替换成功，局部修改成功；历史锚点建立失败或定位后版本变化均零写入。
- 流式、调用内直接写入、`source_text` 三种新写正文均有空正文前像和真实正文后像；撤销后文件 ID、标题、父目录、排序不变，后来追加且不重叠的作者文字逐字保留。
- 整章正文+标题、仅标题改动均一次撤销；后续独立标题修改不被覆盖。逐级撤销、重复点击、刷新后的卡状态一致。
- 普通撤销一次点击、不出现确认弹窗；真实冲突仅展示重叠片段。文件树删除文件夹的预览、实际删除和恢复集合逐项一致，不留下 live 孤儿。
- AI 修改后追加 300 字、在不同句修改、同句不同区段修改均保留作者字节；真正重叠零写入并可处理；冲突处理中再次保存拒绝陈旧提交。
- 单文件、两章、文件夹、大批次的删除/确认/恢复均覆盖；确认后新增/改动目标使原确认失效。旧已删子项、重删代次、同名路径、缺失父目录、跨项目/账号均有明确测试。
- 删除时打开的脏稿仍可找回；恢复后文件树、编辑器、聊天卡与重新加载一致。历史卡和旧客户端保持兼容。
- 停止前无正文、半稿保存、原子提交在途三种时点保留；停止终态后正文、标题、版本无漂移。
- targeted backend/frontend tests、类型/lint/i18n/token/build、完整 CI/E2E；staging 真实模型验证合理语义范围、作者稿重写及恢复、聊天批量删除、回收站和非重叠撤销。桌面/手机截图附 PR。

## Alternatives considered

- **只修拒绝后停轮。** 可阻止同一轮换工具绕过，但作者明确要求仍不能执行，额外状态越积越多，故替代未合并补丁。
- **永久禁止聊天删除。** 复用人工确认的成本最低，但用户已明确选择完整可恢复删除，长期禁止会割裂聊天能力。
- **仅去掉来源拦截。** 正文已有备份，改动很小；但删除仍无用户恢复，标题和后续手写撤销缺口仍在，不满足本轮范围。
- **把每次改稿变成确认或大 diff。** 可让人逐项把关，但违背直接写作需求；仅真实冲突和批量删除范围需要交互。

## Consequences

作者按自然意图控制作品，可直接改稿并恢复。成本是删除恢复存储/接口与选择性撤销的明确合同，需要真实并发和浏览器验证；不新增依赖、通用意图框架、回收站自动过期或永久清空功能。
