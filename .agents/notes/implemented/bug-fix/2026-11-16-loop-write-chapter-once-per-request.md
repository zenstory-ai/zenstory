# 同一请求里不整章重写刚写好的章节

Status: implemented

关联：[单循环 agent](../architecture/2026-11-16-single-loop-agent.md)（第 4 条 write_chapter、第 10 条续跑与自查）。

## Problem

单循环 agent 写完一章（`</file>`）后，续跑说明要求按大纲、前一章、角色卡自查，用 edit_file 改正，不整章重写。这只是提示：模型在自查续跑里仍可能再调用 write_chapter 把刚写好的同一章整章重写。`_write_chapter_replace` 只拒绝「已定为整章替换、还在等正文」的那一章；正文落库、待写条目清掉之后，再次调用就被当成一次新的整章替换。AI 刚写的章节不是作者内容，作者内容保护也拦不住。结果是同一轮里多一次整章重写：多花时间和输出，作者看到的 diff 与卡片也多一层。

## Decision

- `_PendingEmptyFileState` 多一个请求级集合 `_chapter_bodies_saved`：本请求里 write_chapter 已写完并落库正文的文件 id。待写条目被 `discard` 时保留，只在 `clear_all`（以及每个请求新建状态）时清空。
- 记录点只在 StreamAdapter 确认保存之后、且不是截断补全（`auto_completed`）时：整章替换在 `_complete_chapter_replace` 的保存成功分支；新建章节在 `_complete_file_write` 的保存成功分支，待写文件是 draft / script（单循环里正文只经 write_chapter 新建，create_file 拒绝建正文）。被拒、失败、没写到 `</file>` 的都不记，既有的恢复捕获与重新定位照常可用。
- `_write_chapter_replace` 在「待写中的同一章」分支之后、作者内容保护之前核对这个集合：命中时返回 `chapter_written_this_turn` 错误，告诉模型这一章这一轮已经写好保存、不再整章重写、没有写入，要改用 edit_file，没有要改的就继续下一步或回复作者。模型可见的工具结果与其他 write_chapter 结果一样只用中文（系统提示是中文）。
- 判断只看请求里确认落库的结构化记录，不读模型或作者的文字。已有章节在本请求里的第一次整章替换、下一次请求里的整章替换都不受影响。

## Alternatives considered

- **只加强续跑提示。** 最强理由：不加任何状态，模型偶尔确有理由重写时不被卡住。被否：提示本来就写了不整章重写，问题正是模型没有照做。
- **本请求写过的文件一律拒绝再写（edit_file 也拒绝）。** 最强理由：规则最简单，彻底杜绝同一轮反复改。被否：自查本来就要用 edit_file 改正，这会把正当的修改也拦掉。
- **用待写守卫里的 `_replacement_saved` 判断。** 最强理由：已有字段，不用新增。被否：它只记整章替换、不记新建章节，并且重新定位时会被置回 False，语义是「最近一次定位后是否已保存」，不是「本请求写过正文」。

## Consequences

- 收益：一轮里同一章最多由 write_chapter 写一次正文，自查只能局部修改；拒绝不落库、不建版本，模型拿到明确的下一步。
- 代价：作者在同一条消息里要求「先写第 N 章，再整章重写它」时，第二次只能用 edit_file 完成或留到下一轮；这与「同一个文件只自查一次」的既有规则一致。前端卡片暂按通用失败提示显示这类拒绝。

## Verification

`apps/server/tests/test_agent/test_loop_write_chapter.py`（真实工具与 StreamAdapter + 脚本模型）：新建与整章替换两种情况下，续跑里再 write_chapter 同一章被拒（`chapter_written_this_turn`），正文与 AI 版本数不变、不多建文件；下一个请求里整章替换照常；`discard` 不清、`clear_all` 清空这个记录。去掉 StreamAdapter 的记录点时两条端到端用例失败。
