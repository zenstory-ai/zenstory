# 版本历史：先比较，再有意识地恢复

ZenStory 有两种范围不同的历史：单个文件的版本，以及整个项目的快照。能通过比较找回段落时，不要先回滚。回滚会替换当前状态，不是取回一句话的最稳妥方法；两种历史也都不代表每次按键必然留下记录。

要沿用完整的失物招领示例，先读[用 ZenStory 写第一篇短篇：从创意到可修改的正文](https://zenstory.ai/docs/getting-started/first-project)；保存和差异审阅详见[在 ZenStory 中编辑正文与审阅修改](https://zenstory.ai/docs/user-guide/editor)。

## 先分清打开的是哪种历史

| 历史 | 从哪里打开 | 记录范围 | 比较结果 |
| --- | --- | --- | --- |
| **单文件版本** | 打开文件，在编辑器状态栏点击“历史” | 该文件已保留的若干内容状态 | 两个选定版本之间的文字差异 |
| **项目快照** | 点击项目顶部的历史图标 | 某一时点的项目文件元数据及文件版本引用 | 哪些文件新增、移除或换了版本号 |

项目快照范围更广，但它不是每次都重新复制所有文件正文的独立归档。当前实现保存文件元数据和文件版本引用。**已保存、但没有形成新文件版本的改动，未必能由项目快照恢复。** 它适合查看项目范围的变化，不能代替外部备份。

## 保存与留下历史不是同一个动作

停止输入约三秒后，编辑器会安排自动保存；手动保存可以更早提交。这会保存当前文件，却不表示每次输入都生成版本。

对用户手写修改，当前编辑器按内容长度相对上次保存基线的净差决定是否请求版本；这不是统计“改过多少字”。较小净差或等长替换可能保存成功，却跳过版本。单文件版本额度用满时，内容也可能保存成功，只是跳过历史快照。因此：

- “已保存”确认的是当前内容，不等于新增了历史版本；
- 重要节点要打开版本列表确认，而不是凭感觉假设；
- 可用历史受策略与额度约束，不应依赖无限版本或无限期保留。

项目快照遵循另一套规则。AI 一轮对话完成后，如果修改过文件或回复含有实质内容，浏览器会尝试自动创建项目快照；快照失败被视为非关键错误，所以对话完成不保证列表里一定多一条快照。可恢复哪些状态，应看实际存在的快照与对应文件版本，而不是只看对话完成提示。

## 先比较两个单文件版本

打开目标文件，点击“历史”。列表显示版本号、时间、修改类型、字数、行变化和已有摘要。选中恰好两个版本，再点击“比较”；右侧文字差异面板是当前已实现的旧文/新文对照路径。

版本行上的“查看内容”动作确实会向 API 请求该版本正文，但编辑器中负责单独显示预览的回调目前仍是 TODO。不要把这个图标当成完整预览保证；需要看正文时，选择两个版本并使用“比较”。

### 示例：保留现稿，只取回一句话

假设《关窗之前》的“正文 / 第1场 认领”已经改好了节奏和对话，但旧版本中有一句事实边界更准确：

> 林禾只知道沈敏把内袋物件说错了，至于原因，他没有证据。

按下面的清单操作：

1. 确认当前文件已保存，并把不能重写的现稿段落另存到独立笔记。
2. 打开这个正文文件的“历史”。
3. 选中一个旧版本和一个较新版本，点击“比较”。
4. 在文字差异中定位这句话，只复制需要的这一句。
5. 关闭历史，把句子粘贴回当前正文，手动调整上下文后保存。
6. 只有在需要确认本次保存是否也留下版本时，再打开历史查看。

这样既保住后续改稿，也取回了一句有用原文；同时不越过故事边界：林禾知道回答有误，却不知道沈敏是否撒谎，也不知道她答错的原因。

## 使用单文件回滚前，理解真实行为

点击单文件版本的回滚按钮并确认后，前端会调用回滚 API。请求成功时，服务端把选定版本的内容覆盖到当前文件；额度允许时，它可能再生成一条 `restore` 历史，但这条记录保存的是**恢复后的内容**。后端不会先为即将被覆盖的当前正文保证创建一个新版本。

所以，单文件回滚前必须独立保留当前段落。成功回滚后编辑器会重新加载页面，不能把未保存文字留在页面里并假设它会保住。只有确实想替换整份文件时才使用回滚；找回一句或一段，优先比较并复制。

## 把项目回滚当成大范围操作

在项目历史中，先选两个快照并比较，再考虑回滚。比较弹窗汇总新增、移除、修改的文件，以及旧、新版本号；它是项目文件清单级比较，不会逐字展开每个被修改文件。措辞重要时，应打开相应文件，用单文件版本比较。

一次成功的项目回滚会先创建“回滚前”快照，再恢复目标快照的范围。这份安全快照仍采用版本引用：如果当前正文保存时没有生成自己的单文件版本，就不能假设它精确保留了即将被覆盖的文字。对完整项目快照，恢复可能重建缺失文件、取消旧文件的删除状态、恢复标题、类型、父文件夹、排序和正文，还会软删除目标快照中不存在的当前文件。因此它可能同时改变项目的许多部分。“回滚前”快照同样引用文件版本，不保证它已经独立保存了每一处刚写下的内容。

项目回滚前逐项确认：

- 完成保存，或保留所有未保存的编辑器内容；
- 把无法重写的当前文件或段落保存到独立位置；
- 比较两个快照，列出受影响文件；
- 单独检查重要的修改文件；
- 只有目标确实是替换项目整体状态时，才执行回滚。

不要为了测试恢复而删除文件。先用只读比较理解范围，把回滚留给真实的恢复决定。

## 下一步

- [导出稿件](https://zenstory.ai/docs/user-guide/export)：保留当前正文副本，并了解 TXT 没有包含什么。
- [编辑器](https://zenstory.ai/docs/user-guide/editor)：把取回的段落有意识地放进当前稿件。

## 已核对源码

源码核对日期：2026-09-12。例句为原创教学材料；没有登录执行比较或回滚，也没有恢复真实用户内容。

- [编辑器保存时机，以及请求或跳过单文件版本的条件](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/SimpleEditor.tsx#L509-L589)
- [单版本预览回调尚未实现；回滚后关闭历史并重载](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/SimpleEditor.tsx#L681-L695)
- [单文件版本的加载、选择、比较、查看和回滚](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/FileVersionHistory.tsx#L68-L150)
- [已实现的双版本文字比较面板](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/FileVersionHistory.tsx#L321-L406)
- [单文件回滚替换正文，并按额度记录恢复后状态](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/file_version_service.py#L412-L499)
- [版本额度可跳过快照，但不把正文保存判为失败](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/api/files.py#L899-L927)
- [项目快照列表、描述、比较与回滚操作](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/VersionHistoryPanel.tsx#L80-L175)
- [项目比较展示新增、移除与修改文件](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/SnapshotComparisonDialog.tsx#L138-L285)
- [符合条件的 AI 对话结束后尝试自动创建项目快照](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/hooks/useChatStreaming.ts#L1483-L1538)
- [项目快照保存文件元数据和最新单文件版本引用](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/snapshot_service.py#L526-L571)
- [项目回滚在恢复前创建“回滚前”快照](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/snapshot_service.py#L180-L227)
- [完整项目恢复可重建、取消删除、恢复及隐藏文件](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/snapshot_service.py#L594-L753)
- [已有版本与缺失版本的快照处理](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/snapshot_service.py#L504-L537)
