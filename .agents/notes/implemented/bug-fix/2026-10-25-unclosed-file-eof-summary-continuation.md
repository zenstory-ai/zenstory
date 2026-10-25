# Agent Note: EOF 抢救保存的新正文继续同一 writer 收尾

Status: implemented

关联：[2026-10-11-file-stream-continuation-and-truthful-completion](2026-10-11-file-stream-continuation-and-truthful-completion.md)、[2026-06-21-unterminated-file-block-is-not-prose](2026-06-21-unterminated-file-block-is-not-prose.md)。

## Problem

真实短剧写作请求已经落盘核心大纲、分集大纲和第 1 集剧本，但最终 assistant 消息只剩“正在新建第1集剧本文件”。最后一轮模型在非空 `<file>` 正文后直接结束，没有输出 `</file>`；`StreamProcessor` 按既有规则在 EOF 抢救正文，`StreamAdapter` 成功保存文件并清除待写状态。随后 graph 的续跑条件只认显式闭合的 `</file>`，因此没有再运行同一个 writer 生成总结。

已有正文的复用文件有不同的数据安全规则：未闭合的整份覆盖会被 `StreamAdapter` 拒绝。待写状态随后同样会清除，所以不能只凭“没有 pending file”把所有未闭合块视为已保存。

## Decision

- `completion_evidence.ends_on_unclosed_file_block` 只识别流式文件协议的结构边界：最后一段是非空 `<file>` 正文，且到 EOF 没有 `</file>`。它不读取自然语言，不按词语、标点或长度判断任务是否完成。
- `writing_graph` 在合并前序轮次的 `carried_writer_body_writes` 之前，记录当前 run 是否存在 `full_body`、字数大于零且 `requires_closed_block=false` 的新正文写入。只有这个当前 run 证据与未闭合 EOF 边界同时成立，才把它和原有显式 `</file>` 情况一样续跑同一 agent。
- 已有全部续跑保护保持不变：正常结束、角色可写、非只读/非先补问、有剩余迭代、create/resume 成功、没有待补写空文件，且没有交接、错误或工具耗尽。
- 续跑提示改为“在文件正文结束时就停止”，同时适用于显式闭合和 EOF 抢救，不再声称模型一定输出过 `</file>`。

## Alternatives considered

- **让 `ends_on_file_block` 接受所有未闭合 `<file>`。** 最强理由是改动最少，闭合与 EOF 两条路径共用一个布尔条件。没有采用，因为复用已有非空文件时，适配器会拒绝截断覆盖但也清除 pending 状态；仅放宽文字边界会把未保存的残稿误当成成功产物并多跑一轮。
- **由 `StreamAdapter` 向 graph 写入完整的持久化 receipt。** 最强理由是可以直接证明每种文件类型在数据库中的保存结果，也能覆盖规划师输出的大纲。没有采用，因为 adapter 消费 graph 事件，新增反向 receipt 会扩大 ToolContext/流式合同；本次故障已有 `BodyWriteTracker` 对新正文与复用正文的结构化区分，可用更小改动修复剧本正文路径。
- **按回复字数或完成话术判断是否续跑。** 最强理由是无需连接写入台账。没有采用，因为它会猜测自然语言任务完成状态，并重现已删除的长度启发式；字数在本实现里只证明非空正文实际属于新文件写入记录，不用于判断内容是否写完。

## Consequences

- 新建 draft/script 的未闭合正文在 EOF 被既有流式保存逻辑抢救后，graph 会续跑同一 writer，让它继续剩余工作或输出作者可见总结。
- 复用已有非空正文的截断覆盖仍不续跑；前序轮次携带过来的正文记录也不能替代当前 run 的保存证据。
- 显式闭合 `</file>` 的原路径及既有迭代、权限、交接、错误保护不变。
- 这项最小修复只使用 `BodyWriteTracker` 已覆盖的 draft/script 正文。若未来要求所有 outline/settings 文件在 EOF 抢救后也获得同样续跑保证，应另行设计跨 StreamAdapter/graph 的结构化保存 receipt。
