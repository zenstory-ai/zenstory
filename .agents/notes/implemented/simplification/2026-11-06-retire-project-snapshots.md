# Agent Note: 退役项目快照，只保留文件历史

## Problem

工作台同时有两套历史：编辑器里的文件历史（单文件列表、预览、diff、恢复）和顶栏的项目快照（整项目列表、对比、回滚，AI 每轮改完文件后由前端自动创建一份）。作者日常改稿靠文件历史和聊天卡片的撤销；项目快照的入口、面板、对比弹窗、服务和测试却要一直维护，两个历史入口也需要反复解释差别（见 [2026-10-09-file-restore-backs-up-current-content](../bug-fix/2026-10-09-file-restore-backs-up-current-content.md)、[2026-10-08-workspace-copy-terms-and-live-limits](../process/2026-10-08-workspace-copy-terms-and-live-limits.md)）。

## Decision

产品决定只留文件历史，整套退役项目快照。这是对上述笔记中项目快照入口、面板和正文 diff 决定的反转；那些笔记里恢复前备份、说明文字翻译等决定不受影响，继续有效。

实施范围：

1. 先补保留行为的回归，并在未退役的代码上跑通（单独提交）：缺口来自 PG 专用测试文件里的树门禁证明（素材/草稿上传、导入、工具建目录等创建入口等待强门禁；目录修复持门禁到正文提交；重复目录的 savepoint 恢复）、版本重放（`_get_contents_for_versions` 的基线、无基线旧链、多文件上界）、恢复前备份读到已提交正文、`FileVersion.snapshot_id` 钉住时不合并、一轮完成仍刷新文件。
2. 整删项目快照的前后端代码：`VersionHistoryPanel`、`SnapshotComparisonDialog`、`api/snapshots.py`、`snapshot_service.py`、`versionApi`、顶栏入口、AI 一轮后的自动快照、只被它们使用的类型/错误码/文案/日期函数。
3. 整删只测这些代码的测试，裁剪混合测试；把 PG 共用的 `pg_engine` 夹具从被删文件移到 `test_tree_writers_postgres.py`，同步 CI 固定清单。
4. 更新 README、工作台文档、`llms.txt` 和项目介绍里关于整项目恢复的说法。

`lock_project_for_files` 的 `rollback` 参数只有退役代码在用，随之删除，其余调用方的锁模式不变。

自动快照还顺带给没有版本的文件补过基线。退役后有两处依赖要由写入路径或保护逻辑自己承担，不新增后台写入：

- **空白文件的 AI 撤销**：`FileEditor.edit_file` 真要改一个没有任何版本的空白文件（含只有空白字符）时，在同一个锁和事务的 savepoint 里补一个 `edit/system`、摘要 `Before AI edit` 的基线，撤销锚点落在它上面，走原有的 rollback、token 冲突和卡片标记。基线不占用户额度；已有版本后不再补；没改动、改动失败不补；`create_initial_version` 和 `<file>` 流式写入不变，新建未改的文件仍然没有版本。基线写入失败只回滚 savepoint，AI 正文照存，这次不给撤销。
- **来源未知的破坏性操作**：非空且没有任何版本的文件来源未知。应用内聊天（`protect_author_content`）对它的删除、递归删除（含文件夹内的子文件）、整份覆盖和同名剧集复用，一律走现有的 `AuthorEditProtectionUnavailableError` 拒绝，内容、树和版本都不变。未知不算作者拥有（`latest_text_is_authors` 仍返回 False，也不写「作者手动保存」的文案），也不算可以破坏。这个错误的文案不再让模型「稍后重试」，因为来源未知不会自己消失。局部 `edit_file`、已确认 AI 来源的文件、空白文件、文件菜单、Agent API 和其他不传 `protect_author_content` 的入口不变。

`useChatStreaming.onComplete` 仍是 `async`、类型仍是 `void | Promise<void>`，`ChatPanel` 仍用 `void` 调用：去掉快照的 `await` 不改变调用方的异常语义，收尾抛错不会中断父级的后续步骤（建议芯片调度）。

不动：`Snapshot` 表和模型、`FileVersion.snapshot_id`、历史迁移、合并判断里的快照保护、两个 snapshot 索引、`versionSummary` 对旧版本说明的翻译、Agent API/CLI 的 `snapshot_created` 字段、`agent/context/turn_snapshot.py`、编辑工具的 read snapshots、`confirmedFileMutation`。不加迁移，不清数据。

## Alternatives considered

- **只隐藏入口，后端和自动快照照旧**。最强理由：改动最小，可随时恢复入口。被否：快照行继续增长，每轮多一次项目级写入和门禁等待，代码和测试的维护成本一点没少，作者也再用不到这些数据。
- **保留只读的快照列表或后端 API**。最强理由：旧快照还在，留一条找回路径。被否：作者已接受失去整项目恢复；只读列表要继续维护重建逻辑和文案，真要找回旧数据可由运维按表处理，不需要产品入口。
- **同时删表并清空 `snapshot_id`**。最强理由：一次清干净，合并判断也能简化。被否：迁移不可逆，历史版本与快照的关联一旦清掉就无法还原；代码退役不需要绑定数据清理，留到单独评估。
- **在所有 AI 写入路径（`stage_pre_ai_write_backup`）或新建文件时给空白文件建基线**。最强理由：规则只写一处，流式写入的文件也有基线。被否：撤销卡片只来自 `edit_file`，放到共用入口会让每个「新建再流式写入」的文件多一条无人使用的历史。
- **把来源未知当作作者拥有**。最强理由：可以直接复用 `AuthorEditProtectedError` 和现有文案。被否：这是把未知说成已知，会告诉作者「你手动保存过」，而文件可能来自模板或写入失败。
- **所有手动保存前补一份免额度的系统版本，替代快照顺带的归档**。最强理由：历史写入失败的 AI 正文被手动保存覆盖前还能留底。被否：等于重建后台捕捉并绕过版本额度；要保留的是 AI 覆盖和恢复前的备份，不是每次手动保存前的归档。

## Consequences

- 收益：删除约两千行生产代码和对应测试；历史只有一个入口；AI 每轮结束少一次请求和项目级门禁占用。
- 代价：作者无法一键恢复整个作品的文件增删和目录结构；已删除的文件无法从历史恢复。
- 版本合并：不再产生新的 `Snapshot` 行后，AI 改了项目里别的文件也不会打断当前文件的手动保存合并。这个文件本身没被 AI 动过，不丢内容；AI 改过它时最新版本不是用户编辑，照旧不合并。
- 撤销：空白文件被 AI 首次 `edit_file` 后可以撤销回写入前的原样，这比快照时期更一致（此前最后一次快照之后新建的空文件没有撤销）。代价是这类文件的历史多一条系统版本。
- 来源未知：没有任何版本的非空文件（旧数据、版本写入失败、版本额度为 0）不再被快照补基线，这个状态可能长期存在。聊天里不能删除或整份覆盖它们，要由作者用文件菜单或编辑器处理，或者由模型局部编辑。局部编辑前的备份会给文件建立历史，之后最新版本归 AI，保护按现有「看最新一版」的规则判断，不再按未知处理；这与作者保存后又被 AI 局部编辑的现有行为一致。AI 自己写的正文如果版本写入失败，也按未知处理，这是偏保守的取舍。AI 写入前的备份和撤销锚点不受影响。
- 未入史正文（已退役后台写入的副作用，非阻断）：额度已满时的手动保存不再在一轮结束时被顺带存成系统版本；AI 覆盖和恢复前的备份仍然保护它们。已提交的 AI 正文如果历史写入失败，随后又被作者手动保存覆盖，这份 AI 文字不再被自动归档（只发生在失败路径）。不为此增加手动保存前备份、状态表或额度例外。
- 遗留：`Snapshot` 表只读不写，`ix_snapshot_project_file_created_at_desc` 不再有查询，两者留待日后与删表一起评估。`later_snapshot` 查询每次可合并的保存仍会执行，依赖 `ix_snapshot_project_created_at_desc`。`versionSummary` 里的快照翻译要保留到旧版本记录不存在为止。
- 发布间隙：旧前端标签页的自动快照请求会得到 404，已被其 try/catch 吞掉；旧面板显示加载失败。新前端先上线无影响。
- 工程：`project-page-{chromium,firefox}-linux.png` 在 Linux Playwright 官方镜像（`v1.58.0-noble`，装 `fonts-dejavu-core`，阈值不变）里重新生成。同环境渲染退役前后的代码，差异只在顶栏图标区域（各 547 像素）。一张基线要整体替换，所以新图同时带上了 10 月以后其他改动造成的聊天空状态和编辑器底栏差异。其余 8 张基线（dashboard、chat-empty、chat-with-messages、mobile）在退役前的代码上就已失败（`chat panel with messages` 的 `aria-label` 断言也一样），不属于这次退役，留给单独的批次；在那之前开启视觉回归的 `full`、`release` 仍会失败。
