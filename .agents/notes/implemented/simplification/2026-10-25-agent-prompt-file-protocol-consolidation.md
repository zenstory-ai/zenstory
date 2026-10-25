# Simplification Note: 共享提示词里的 `<file>` 协议合并为一段，过程说明只留一条规则

Status: implemented（真实模型复跑待做）

关联：[2026-10-10-file-streaming-protocol-two-replies](../bug-fix/2026-10-10-file-streaming-protocol-two-replies.md)、[2026-10-11-file-stream-continuation-and-truthful-completion](../bug-fix/2026-10-11-file-stream-continuation-and-truthful-completion.md)、[2026-10-19-agent-rename-file-tool](../feature/2026-10-19-agent-rename-file-tool.md)（F-13 是同类矛盾规则的实例）。

## Problem

`agent/prompts/base.py` 的 `TOOL_USAGE_PROTOCOL` 把新建文件的 `<file>` 流式协议分散写在「流式写入模式」「`<file>` 标记格式要求」「绝对禁止 - 创建空文件后不写内容」「关键限制：一次只能流式写入一个文件」「注意」「标题与正文分离」「交互规范 3」七处，措辞不一、反复强调同一件事（审计 A-S23）。过程说明也有三条：`AUTHOR_REPLY_PROTOCOL` 与「交互规范 1」都说「整轮最多一句，在第一次调用工具前」，「交互规范 3」却说「调用 create_file 前禁止说话」；第一次调用的就是 create_file 时两条无法同时满足。2026-10-10 的 note 已证实这类「做不到的指令组合」会让模型在开始输出前反复纠结。

## Decision

owner 决定 O-4：只合并、只消解矛盾，不加新规则。

- 七处 `<file>` 说明合并为一段「新建文件：create_file + `<file>` 流式写入」：6 条编号规则加一个三次回复的示例。第 1、2 条的「两次回复」措辞保留 2026-10-10 实验 D 组的原句（含「不传 content 参数」），只在第 1 条末尾加「这次回复除整轮那一句过程说明外不说别的」。保留的规则：两次回复、`<file>` 独占一行起头、纯文本、`</file>` 独占一行、未闭合时系统自动结束、`</file>` 后同一次回复接着建下一个或写总结、一次只写一个文件且不留空文件、标记的精确格式与五种错误写法、标题与正文分开存。
- 「交互规范与静默原则」改为「过程说明与思考」：过程说明一句话引用「给作者的回复」的规则；`AUTHOR_REPLY_PROTOCOL` 写明第一次调用的是 create_file 时同样适用。四条思考规则原样保留。删掉的只有重复：任务板只在开始和完成时更新（`UNIFIED_EXECUTION_PROTOCOL` 已有同一句）、「不要用技术性语言描述操作」（`AUTHOR_REPLY_PROTOCOL` 已禁止写工具名）、错误示例代码块（规则已覆盖）。
- 其他地方对协议的一句话引用（工具清单、edit_file 决策树、移动文件、角色资料、`subagents.py`）不动。
- 组装后的系统提示（测试配置下）从 11,767 字符降到约 10,300 字符；所有项目类型共享，前缀缓存在上线后第一次请求重建一次。
- 新增一条契约测试：格式说明只出现一次、两次回复原句只出现一次、不再出现「禁止说话 / 严格静默」。

## Alternatives considered

- **维持原样。** 最强理由：每段重复可能是某次事故后补的，删掉后模型行为只能靠 eval 验证；2026-10-10 的修复正是靠措辞改动生效。未采用：重复本身制造了矛盾（本轮 F-13、过程说明这条），而合并时保留了实验验证过的原句和每一条不同的规则，风险集中在措辞变化上，由后续真实模型复跑检验。
- **把「调用 create_file 前禁止说话」保留、改 AUTHOR_REPLY 为「第一次工具调用是 create_file 时不说」。** 未采用：等于新增一个例外，模型要先判断第一次调用的是哪个工具；统一成「整轮一句」规则更少。
- **连同工具 schema 里「正文必须放入 content」等描述一起改。** 未采用：不在 O-4 范围，改工具描述会改变工具列表的缓存前缀，单独处理。

## Consequences

- 系统提示少约 1.5k 字符（每次请求的缓存前缀，命中价），矛盾规则少一处。
- 模型行为变化未经真实模型验证：需要复跑 2026-10-10 的 writer 第 1 步重放（看是否仍 0 次撞上限、是否正常发出 create_file）和 2026-10-09 的标题命名 probe，再上线到生产。
- `create_file` schema 的 title 描述里「正文必须放入 content」与无 content 参数的事实不一致，仍待单独修。
