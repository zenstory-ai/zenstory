# Agent Note: 作者只要建文件时，空文件不等正文

Status: implemented

关联：[两次回复写正文](2026-10-10-file-streaming-protocol-two-replies.md)、[正文待写守卫](2026-10-21-fast-mode-summary-and-file-body-turn.md)、[重复建挡路的空文件](2026-10-11-repeat-create-of-pending-empty-file.md)、[停止与产出定义](../architecture/2026-10-09-stop-output-definition-and-round-outcome.md)。

## Problem

2026-10-21 生产：作者说「建立第10章、第11章，不用写内容，建立标题就行」。路由判断正确（`write_content=false`，scope「建立第10、11章标题，不写内容」），但 create_file 建出的每个空文件都进入「等正文」：

- 空文件守卫不让再建第二个文件（`pending_empty_file_unwritten`）。
- 正文待写守卫拒绝下一次回复里的其他工具（`file_body_pending`）。

4 次被拒后触发熔断，这一轮报错并计费。作者要的就是两个空章节，系统却把它当成「建了文件忘了写正文」。

## Decision

- create_file 加可选参数 `empty`：作者明确只要建文件、这一轮不写内容时传 true。
- 只在路由明确判断作者本轮不要正文时生效（`ToolContext.author_wants_no_body()`，由 writing_graph 在路由后按 `routing_metadata.write_content` 设置）。作者要正文、或路由没给出判断时，传了也照常等正文，结果的 `next_step` 说明 empty 不生效。
- 生效的文件不进入待写状态：不占空文件守卫、不触发正文待写守卫、不触发空文件纠偏轮。结果带 `empty_by_request: true`。
- StreamAdapter 不为它进入 `<file>` 捕获，后续回复文字照常发出。计费的占位文件清理也不把它当空白占位，这一轮被退还时不移除。

## Alternatives considered

- **`write_content=false` 时所有空文件都不等正文**。最强理由：不用新参数，完全按路由判断。被否：要大纲、设定、人设的请求同样是 `write_content=false`，那些文件需要写内容。一律放开会让「建了大纲文件没写」的空文件问题回来。
- **从作者原话里识别「只建标题」「不写内容」**。最强理由：不依赖模型传参。被否：说法列举不完；`write_content` 已经是路由对同一件事的结构化判断，再叠一层关键词规则会互相打架。
- **只靠模型的 `empty` 参数，不看路由**。最强理由：简单。被否：模型可能用它跳过本该写的正文；有路由兜底，作者要正文时它不生效。

## Consequences

- 收益：「先建空章节/只建标题」这类请求一次完成，不再被守卫和熔断拦住，退还时也不会删掉作者要的空文件。
- 代价：create_file 多一个参数，工具清单前缀变一次。路由把「只建标题」判成 `write_content=true` 时，这个参数不生效，行为和以前一样。
