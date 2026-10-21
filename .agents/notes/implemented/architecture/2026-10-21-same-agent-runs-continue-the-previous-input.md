# Agent Note: 同一 agent 紧接着再跑时接着上一轮的完整输入

Status: implemented

关联：[项目资料快照与缓存稳定的工具清单](2026-10-21-context-snapshot-and-cache-stable-tools.md)、[写完 </file> 就结束 run 时同一 agent 续写](../bug-fix/2026-10-11-file-stream-continuation-and-truthful-completion.md)。

## Problem

writer 写完一段 <file> 正文、回复里没再调用工具时，这次 SDK run 就结束了，图会让同一个 writer 再跑一轮续写（空文件纠偏、只写总结、插话追加轮、截断续写同理）。新一轮的输入从 `state["messages"]` 重建：上一轮的工具调用和结果被折叠成一段 assistant 文字，读到的文件内容不在里面，`edit_file` 的「读过」记录也按 run 清空。

2026-10-20/21 生产数据：一次「继续写」的请求里 writer 连续跑了 8 轮，重复读取从 1 次涨到 5 次，共 26 次 query_files，最后撞上 780 秒时限；10.7 小时里重复读取 49 次。真实请求抓取显示，续写重开时请求在「本轮用户消息」之后就和上一轮不同，本轮已写的正文与读过的内容都按未命中重新计价（命中约 75%，同一 run 内 90%+）；备用供应商的缓存只认请求末尾追加，重开时偶尔几乎全部未命中。

## Decision

1. **接着上一轮的完整输入**（`openai_agents/runner.py` `_continued_run` / `_continued_run_input`）：run 正常结束时把它完整的 SDK 输入（`result.to_input_list()`：原输入 + 模型输出 + 工具调用与结果）连同这次的运行指令、用户消息和折叠后 `state["messages"]` 的长度记进 `state["native_run_holder"]`。图在同一 agent 紧接着自己再跑时置 `continue_native_run`，runner 的输入改为：上一轮完整输入 + 之后追加的插话 + 新的交接 / 续写消息 + 运行指令（内容没变时不再附）。请求只在末尾追加，前缀缓存整段命中；读过的文件仍在上下文里，不用重读。
2. **只用于同一 agent 紧接着自己**：换了 agent（含审稿后返修的 writer）照旧从折叠后的历史重建，工具结果不跨 agent 回放，`ToolContext.begin_agent_run()` 照旧清空「读过」记录；同一 agent 接着跑时不清空，读取计数也不清零。出错、取消、轮数耗尽、无进展等非正常结束不记录，下一轮照旧重建。
3. `state["messages"]` 的折叠、历史保存、交给其他 agent 的内容都不变；接着跑的输入只进模型，不写回 state。
4. **少开续写轮**（`graph/nodes.py` `WRITER_CHAIN_FILES_DIRECTIVE`）：writer 的运行指令写明「写多个文件时，每个 </file> 之后在同一次回复里直接 create_file 建下一个，全部写完再写总结」。系统提示里本来就有这条，运行指令在输入末尾、离模型做决定的位置最近，也不影响前缀缓存。

## Alternatives considered

- **把上一轮读到的文件全文附进续写消息**。最强理由：改动小，不碰 SDK 输入。被否：附进去的全文在续写消息里是新内容，每轮都按未命中计价，还和快照、工具结果重复。
- **在一轮内折叠已写的正文来缩短上下文**。最强理由：输入 token 变少。被否：改写前面的内容会让之后所有调用的前缀全部未命中，命中的单价只有未命中的 1/50，保持只追加更省。
- **只靠提示让 writer 不结束 run**。最强理由：零结构改动。被否：系统提示里早就有这条，生产里仍然每章重开；提示只能减少重开次数，重开时的代价要靠接着输入来消除。

## Consequences

- 收益：续写轮的请求只在末尾追加，命中接近同一 run 内；不再重复读取本轮读过的文件；批量写作少了无谓的调用和等待。
- 代价：同一 agent 连续多轮时上下文持续增长，与一次长 run 相同，超出时由 run 内安全阀（`IntraRunToolOutputTrimmer`）折叠；上一轮的运行指令留在上下文中间（同一 agent，内容相同时不再重复附）；state 里多存一份上一轮的 SDK 输入。
