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

不动：`Snapshot` 表和模型、`FileVersion.snapshot_id`、历史迁移、合并判断里的快照保护、两个 snapshot 索引、`versionSummary` 对旧版本说明的翻译、Agent API/CLI 的 `snapshot_created` 字段、`agent/context/turn_snapshot.py`、编辑工具的 read snapshots、`confirmedFileMutation`。不加迁移，不清数据。

## Alternatives considered

- **只隐藏入口，后端和自动快照照旧**。最强理由：改动最小，可随时恢复入口。被否：快照行继续增长，每轮多一次项目级写入和门禁等待，代码和测试的维护成本一点没少，作者也再用不到这些数据。
- **保留只读的快照列表或后端 API**。最强理由：旧快照还在，留一条找回路径。被否：作者已接受失去整项目恢复；只读列表要继续维护重建逻辑和文案，真要找回旧数据可由运维按表处理，不需要产品入口。
- **同时删表并清空 `snapshot_id`**。最强理由：一次清干净，合并判断也能简化。被否：迁移不可逆，历史版本与快照的关联一旦清掉就无法还原；代码退役不需要绑定数据清理，留到单独评估。
- **用服务端钩子保留空文件基线，维持撤销卡片的现状**。最强理由：空文件如果在此前某次快照时已经存在，快照会给它补一个空基线，之后被 AI 首次写入就能撤销回空白。被否：这份基线本来就是快照的副产品（最后一次快照之后才新建的空文件，包括同一轮内新建再写入的，此前同样没有撤销），撤回空白不丢任何文字；在 AI 写入路径加版本行又会让每个新建再写入的文件多一条历史。

## Consequences

- 收益：删除约两千行生产代码和对应测试；历史只有一个入口；AI 每轮结束少一次请求和项目级门禁占用。
- 代价：作者无法一键恢复整个作品的文件增删和目录结构；已删除的文件无法从历史恢复。
- 版本合并：不再产生新的 `Snapshot` 行后，AI 改了项目里别的文件也不会打断当前文件的手动保存合并。这个文件本身没被 AI 动过，不丢内容；AI 改过它时最新版本不是用户编辑，照旧不合并。
- 撤销：没有任何版本的空文件（`create_initial_version` 对空正文不建版本）被 AI 首次写入时，修改卡不显示撤销。此前只有该空文件在之前某次快照时已经存在才显示；撤回目标是空白，不丢文字，新增回归钉住此行为。
- 作者归属：没有任何版本的非空文件（旧数据、版本写入失败、版本额度为 0）不再被快照补上基线版本，`latest_text_is_authors` 对它们保持「来源未知」。AI 写入前的备份和撤销锚点不受影响。
- 未入史正文：额度已满时的手动保存不再在一轮结束时被顺带存成系统版本；AI 覆盖和恢复前的备份仍然保护它们。AI 版本写入失败的正文，如果作者随后手动保存覆盖，这份 AI 文字不再被捕捉（只发生在失败路径）。
- 遗留：`Snapshot` 表只读不写，`ix_snapshot_project_file_created_at_desc` 不再有查询，两者留待日后与删表一起评估。`later_snapshot` 查询每次可合并的保存仍会执行，依赖 `ix_snapshot_project_created_at_desc`。`versionSummary` 里的快照翻译要保留到旧版本记录不存在为止。
- 发布间隙：旧前端标签页的自动快照请求会得到 404，已被其 try/catch 吞掉；旧面板显示加载失败。新前端先上线无影响。
- 回调：`useChatStreaming` 的 `onComplete` 去掉快照后没有 await，改成同步函数；函数体内若抛错，会中断 `ChatPanel` 收尾处理的后续步骤（建议芯片调度），此前是未处理的 rejection。
- 工程：`e2e/visual.spec.ts` 的项目页基线含顶栏历史图标，需要在 Linux Playwright 环境里重新生成并人工确认，不放宽阈值；基线更新前，开启视觉回归的手动 E2E（`full`、`release`）会失败。
