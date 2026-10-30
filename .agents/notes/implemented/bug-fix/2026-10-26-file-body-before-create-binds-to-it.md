# Agent Note: 先写正文、再 create_file 时，正文落进它自己的文件

Status: implemented

关联：[两次回复协议](2026-10-10-file-streaming-protocol-two-replies.md)、[同一回复写完上一份再建下一个](2026-10-10-same-reply-file-body-unblocks-next-create.md)、[IDLE 下独占一行的标记不进对话](2026-10-11-author-reply-single-summary-and-real-word-counts.md)。

## Problem

生产 10-26 00:20（用户 16d0b070，会话在 MiniMax 超时后固定到备用 DeepSeek）：writer 连写第62到第76章，从第64章起每章文件里存的是下一章的正文，第76章与第75章重复，第64章原稿丢失。

日志（agent_run b72bfc97）证实了顺序：每条回复都是「`<file>`第N章正文`</file>` + create_file(第N章)」。

1. 回复开始时没有在等正文的文件，StreamProcessor 处于 IDLE，独占一行的 `<file>` / `</file>` 被当作孤立标记丢掉（`Dropping a lone <file>/</file> line`），第N章正文作为对话放出。
2. create_file(第N章) 建出空文件，StreamAdapter 开始等它的正文。
3. 下一条回复里的第N+1章正文被这个空文件接住，之后每章依次错位；最后一个空文件触发空文件纠偏，补写了一份重复稿。

两次回复协议里，「`<file>`正文`</file>` + create_file」的正文属于上一个还在等正文的文件。这里的区别是回复开始时没有这样的文件，正文只可能属于同一回复里新建的那个文件。

## Decision

- `StreamProcessor` 新增 `prebody_capture`（只由 StreamAdapter 和 `extract_reply_prebody` 开启）：IDLE 下独占一行的 `<file>` 开始一段待认领的正文（占位 id `PREBODY_FILE_ID`），而不是丢掉标记、把正文放进对话。独占一行的 `</file>`、行内提到、代码围栏里的标记保持原样处理。
- `on_llm_end`（`usage_hooks.PendingBodyStreamedMixin`，在 SDK 执行本回复工具之前）：回复里有这样一段闭合、非空的正文，且**这次回复开始时**没有在等正文的文件（`begin_reply` 时记下；不能在回复结束时再看，StreamAdapter 边收边存，写完 `</file>` 就清掉条目，往往早于 on_llm_end），`ToolContext.offer_reply_prebody()` 记下；回复开始时有这样的文件，照旧按两次回复协议标记它的正文已写出。两边用同一个判定函数 `extract_reply_prebody`，每条回复都从行首开始判定：StreamAdapter 在每个工具结果处结束这条回复的文字（`StreamProcessor.begin_reply_text`），上一条回复末尾没有换行也一样。
- `create_file`：同一回复里第一个建空文件、非复用的调用认领这段正文（`take_reply_prebody`，只认领一次），待写条目直接标为正文已写出，结果带 `body_in_reply: true` 和「正文已存进这个文件，不要再写一遍」的 next_step，不带 `awaiting_body`。
- `StreamAdapter`：闭合的待认领正文先扣着，之后的对话文字跟在它后面一起扣着，放出时保持原顺序。紧跟的 create_file 结果带 `body_in_reply` 时，用与 `</file>` 收尾相同的落库路径存进这个文件（覆盖保护、保存回执、清除待写守卫）；同一回复里排在前面的其他工具结果（如 update_project、建文件夹）之后仍扣着；没被认领的在下一条回复的文字开始、MESSAGE_END、ERROR 或流结束时按对话放出，与以前一致。未闭合的待认领正文同样按对话放出，不落库；流被取消时不为它补存。工具确认了、这边却没扣到正文（两边判定不一致）时，不让这个文件接住下一条回复的 `<file>`，文件保持空白，由图的边界核验补写或回滚，不会再错位。

回归：`tests/test_agent/test_reply_prebody.py` 重放生产顺序（第64、65章各自落库、对话里没有正文），并覆盖：两次回复协议的连写不受影响（包括 StreamAdapter 在 on_llm_end 之前已存好上一份）、上一条回复没换行时下一条开头的 `<file>`、工具没确认时正文回到对话、正文之后的文字顺序、两边判定不一致时不错位。独立审查发现的前两个竞态先有红例，再修。

## Alternatives considered

- **在提示词里禁止先写正文**。最强理由：不碰流式状态机。被否：备用模型在同样的指令下仍这样写（本次就是），提示词无法保证；错位一旦发生就是作品数据错乱。
- **按章节标题或正文首行判断正文属于哪个文件**。最强理由：能识别任何顺序。被否：正文不一定带标题，按内容猜归属会把错位换成另一种错位。
- **所有独占一行的 `<file>` 块都直接绑定下一个 create_file，不需要工具确认**。最强理由：实现更少。被否：模型也会在别的场景写出这种块（例如重复上一章），只有工具执行前看过同一条完整回复、且确认当时没有在等正文的文件时，归属才确定。

## Consequences

- 收益：先写正文、再建文件的连写，每章正文落进自己的文件，不再连锁错位；正文不再整段出现在对话里。
- 代价：待认领的正文在认领或放出之前不显示（流式预览要等 create_file 结果后一次性出现）；没被认领时，它在下一个边界才作为对话放出，可能排在同一回复里它后面的文字之后。
- 代价：一条回复里先写多段正文、再建多个文件时，只有第一段被认领，其余照旧作为对话放出，对应文件按原逻辑等正文。
- 代价：作者在正文扣着时按停止，这段正文既不落库也不进对话（以前它在对话里）；本轮新建的空占位文件按既有停止收尾回滚。
