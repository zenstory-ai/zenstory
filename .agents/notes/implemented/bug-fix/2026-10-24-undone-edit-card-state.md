# 撤销 AI 修改后，聊天里的修改卡显示「已撤销」

Status: implemented

关联：[AI 写入前备份未入史的正文](2026-10-09-ai-write-backs-up-unversioned-text.md)（撤销锚点 `undo.before_version_number` / `expected_after_updated_at`）、[恢复历史版本前备份当前正文](2026-10-09-file-restore-backs-up-current-content.md)。

## Problem

2026-10-24 短剧合成试用：作者点修改卡上的「撤销」，经确认框撤销这次局部修改，正文确实回到修改前的 910 字（撤销成功，没有丢稿）；聊天里那张卡却仍是「修改完成 / 撤销」，看不出已经撤销，刷新后也一样。

核对源码：撤销走 `POST /files/{id}/versions/{n}/rollback`，带修改后版本 token，作者或别的写入在这之后改过文件时返回 409，不会覆盖新稿；成功后前端只刷新文件树与编辑器，不更新卡片。卡片是从助手消息落库的 `tool_calls` 里那次 `edit_file` 结果重建的，没有任何字段记录撤销；恢复版本只写一行 `change_summary` 文字，额度满时甚至不写版本，不能拿来反推。重复点击会因 token 已过期得到 409（安全，但卡片仍在误导）。

## Decision

- 撤销请求（`RollbackRequest`）可选带上这张卡的 `chat_message_id` 与 `tool_use_id`。条件回滚成功后，`services/chat_tool_cards.record_edit_card_undone` 只在能证明来源时把那一张卡标成已撤销：消息是当前用户在同一项目里的助手消息，卡的工具调用 id 一致，撤销锚点（文件 id、修改前版本、修改后 token）与这次撤销完全相同。写在既有 `tool_calls` JSON 的 `result.undo.undone`，不改助手原话，不新建表或第二份变更账本。响应带 `undo_recorded`（旧服务端没有这个字段，前端按 false 处理；旧前端不带卡片 id，撤销照旧生效）。
- 前端：ToolResultCard 把自己的 tool_use_id 与已落库消息 id 放进撤销目标。撤销真实成功后，ChatPanel 在 `UndoneEditsContext`（按文件 + 修改前版本 + 修改后 token）里记下这次修改，卡片当场显示「已撤销」/「Undone」并去掉撤销按钮；刷新后读 `undo.undone`。409 冲突、作者之后改过、请求失败都不标、不显示已撤销，错误提示照旧。
- 同一条消息里的几张卡可能同时撤销：标记时先锁住这条助手消息（PostgreSQL `SELECT … FOR UPDATE`；SQLite 用文件写入同一套进程内条带锁），在锁下以 `populate_existing` 重读最新 `tool_calls` 再改那一张卡，后提交的不会覆盖先提交的标记。
- 撤销本身（条件回滚、恢复前备份、版本额度、编辑器刷新、计费）不变。

## Alternatives considered

- **只在前端内存 / localStorage 里记已撤销。** 最强理由：不改接口。被否：换设备或清缓存后仍显示可撤销，刷新后误报这次修改还在生效。
- **从版本历史反推。** 最强理由：版本历史已经在记恢复。被否：恢复版本只有文字摘要、不指向哪次修改，额度满时不写版本；每张卡都去查历史也太重。
- **撤销成功后在服务端按锚点搜索所有聊天消息。** 最强理由：前端不用传卡片 id。被否：要扫描整个项目的历史 JSON，按子串找 id，且可能误标；前端本来就知道卡片属于哪条消息。
- **新建「撤销记录」表。** 最强理由：查询清晰。被否：卡片状态已有落库位置，新表是第二份账本，还要迁移。

## Consequences

- 收益：撤销后卡片如实显示已撤销，重复点击的入口消失；刷新、换设备后一致。失败 / 冲突不会被说成成功，也不会覆盖新稿。
- 代价：撤销接口多两个可选字段与一个响应字段；还没落库消息 id 的卡（流式进行中）撤销后只在当前页面显示已撤销，刷新后靠服务端标记的那部分缺失（极少：撤销通常在这一轮结束之后）。标记写失败时撤销仍已生效，只记日志，刷新后卡片会再次出现撤销按钮，点击得到 409、不会覆盖。

## Verification

- `apps/server/tests/test_services/test_edit_undo_card_state.py`（真实回滚接口 + 临时 SQLite）：成功撤销只标自己那张卡、助手原话不变、重复撤销 409 且不再写；作者之后改过时 409、作者新稿保留、不标；指错工具调用 / 别人的消息 / 别的修改 token / 不带 id / 别的项目都不标。修改前 6 项失败（无 `undo_recorded`），作者后改冲突一例本来就通过；修改后与既有撤销测试共 34 项通过。
- 最终整批审查在本地 PostgreSQL 复现两张卡同时撤销后只剩一个标记（两次都返回 True，落库 [True, None]）。新增 `tests/test_services/test_edit_undo_card_postgres.py`（加入 CI 串行 PostgreSQL 测试列表），修改前失败、修改后通过；SQLite 下原有授权 / 锚点 / 冲突 / 重复撤销用例 34 项不变。
- 前端：ToolResultCard（撤销带卡片 id、落库已撤销 / 当场已撤销都显示「已撤销」且无按钮）、ChatPanel（撤销请求带卡片 id、成功后记入已撤销、409 不记）。在不含本修改的临时副本里运行这些用例失败（模块缺失、撤销未带卡片 id），修改后 ChatPanel / MessageList / ToolResultCard / RoundEnd 6 个测试文件 339 项通过。
