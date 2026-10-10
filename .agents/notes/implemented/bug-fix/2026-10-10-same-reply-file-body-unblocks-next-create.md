# Agent Note: 同一条回复里写完上一份 `<file>` 正文，紧跟的 create_file 不再被空文件守卫误拒

Status: implemented

关联：[2026-10-03-agent-tool-failure-circuit-breaker](2026-10-03-agent-tool-failure-circuit-breaker.md)（空文件守卫、`pending_empty_file_unwritten`）、[2026-10-10-file-streaming-protocol-two-replies](2026-10-10-file-streaming-protocol-two-replies.md)（协议仍允许"写完 `</file>` 后在同一次回复里接着 create_file"）、[2026-10-06-llm-usage-ledger](../feature/2026-10-06-llm-usage-ledger.md)（复用同一个 `RunHooks.on_llm_end`）。

证据来源：`audit-runs/20261009-r4-zenstory-production/`（`logs/r4drama-stream-m*.txt` 是每 15 秒一次的 DOM 快照、`r4drama-chat-expanded.txt`、`export-body-final.txt`、`shots/r4drama-19-chat-raw-file-tag.png`）。本次没有原始 SSE 和服务端日志。

## Problem

r4 生产审计的短剧连写里，"6 个写新集的回合中 2 个回合出现、共 3 次"："已创建剧本 第N集"之后出现"创建文件失败 / 这一步没做成"，但文件其实已保存；其中一次模型随后把已保存的第 1 集整集正文连同 `<file>…</file>` 原样输出到对话气泡（直播和历史回放都有）。

**已证实（DOM 顺序 + 代码路径）**

1. 同一条模型回复可以是 `content=<file>A</file>` 加 `tool_calls=[create_file(B)]`（m1 第 4 步：第 2、3 集的"已创建"卡相邻，第 2 集正文已被捕获）。提示词的链式写法本身就会产生这种回复。
2. 守卫的检查方和清除方在两个不同步的执行体里：openai-agents 0.17.6 的 run loop 先 `await on_llm_end`，再执行本回复的工具，`create_file(B)` 立刻在线程里跑；而 StreamAdapter 在消费侧滞后处理到 `</file>`，`await _save_file_content` 落库后才按 file_id 清掉 A 的条目。SDK 事件经 `put_nowait` 发出，不等消费侧。
3. 于是 `try_reserve` 被 A 的条目挡住，落库探测（`_clear_settled_pending_empty_files`）看到 A 还没提交、判为仍空，返回 `pending_empty_file_unwritten`。前端 `classifyToolFailure` 不认识这个类型，显示 generic 失败卡。
4. 拒绝文案要求"先在回复里用 `<file>完整内容</file>` 写出 A"，模型照做；此时 adapter 已处于 IDLE（A 早已落库清标记），`<file>` 原样进入对话 content，并被存进历史。这就是标签外露（3b）的来源，外露文本与第 1 集落库正文逐字一致。

**推断边界**：被拒的 `error_type` 没有原始日志佐证，属强推断（generic 文案、模型随后核实"Ep2 616 words, Ep3 667 words"、未触发空文件补写等互相印证）。第 4 集回合"已创建 第4集"后紧跟的失败卡（3c）无法区分是同类竞态还是同一回复里重复 create_file 的真实拒绝，**未确认已修好**。

**修后本地复跑（n=1）**：用 r4drama 同一首条消息在本地真实模型跑完整流程（`audit-runs/20261009-180227-r5-fix-release-supervisor/full-flow/sse-drama1.txt`）：writer 三次都是同一条回复里「`<file>`上一份`</file>` + create_file(下一集)」，第 1/2/3 集 create_file 全部成功，server.log 里探针 6 次、`pending_empty_file_unwritten` 0 次，对话里没有 `<file>` 外露，各集正文都已落库。没有做修前对照，只说明修后路径正常；确定性竞态由单测覆盖。

## Decision

在负责的那一层（生产侧、工具执行之前）补上同步信号，不改前端、不加超时或重试：

- `mcp_tools._PendingEmptyFileState` 增加 `_streamed`：`mark_body_streamed(file_id)` / `mark_latest_body_streamed()` 只对已登记的条目生效；`try_reserve` 的条目阻挡只计尚未 streamed 的条目，拒绝时点名最近一个未写出的文件；`_reservations`（建库途中）照旧阻挡。`add`、`bind` 对同一 file_id 清掉 streamed（幂等复用命中同一文件、重新等正文），`discard`、`clear_all` 同步清理。条目本身不删，仍由 adapter 落库后按 file_id 清除；`has_pending` / `latest` / `snapshot` 语义不变，writing_graph 的边界核验、补写和回滚照旧兜底。
- `ToolContext.mark_latest_pending_body_streamed()`。
- `openai_agents/usage_hooks.py` 新增 `PendingBodyStreamedMixin`，与 `UsageMeteringMixin` 组合成 `AgentRunHooks`（`build_agent_run_hooks`，runner 传给 `Runner.run_streamed`）。`on_llm_end` 先 `super()` 到计量，检查放在 `finally` 并整体 try/except 只记日志（钩子抛错会让整个 SDK run 失败）。只在有待写条目时检查：取 `response.output` 中 message 的 `output_text`（不含 reasoning），用一个全新 `StreamProcessor` 整段 `process_content`，仅当 `file_complete` 且非 `auto_completed` 且正文非空才标记。不调用 `finalize_on_stream_end`（它会把未闭合的块自动补全，误判为已写）。

真实违规（本条回复里没有闭合的 `<file>` 块就再建，例如连发两个 create_file）照旧被拒、照旧显示失败卡。3b 随 3a 消除，不在 IDLE 状态额外剥离 `<file>`。

## Alternatives considered

- **adapter 落库后再放行（让工具等待消费侧）**：需要跨执行体的等待和超时，消费侧是按 HTTP 拉取推进的，可能把工具执行挂住；属于新的同步机制，范围大。
- **前端/展示层隐藏失败卡或剥离 IDLE 下的 `<file>` 块**：只掩盖症状，还会吞掉真实的协议错误反馈；没有其他已证实的触发路径。
- **同标题的 streamed 条目继续阻挡**：更保守，但会让模型再次重复输出已写的正文，重现标签外露，不采用。
- **create_file 失败后自动重试**：用户明确不接受用重试替代根因修复。

## Consequences

- 同一条回复里"`<file>A</file>` + create_file(B)"放行，B 正常置位、adapter 随后落库 A 并进入 B 的捕获；守卫对真实违规的拒绝不变。
- 判定一致性有极小风险：钩子只看本条回复，adapter 的 WAITING_START 缓冲可能带着之前回复的叙述，代码块/行内代码奇偶性可能不同。若钩子判已写而 adapter 没接住，处理 create_file(B) 结果时 `_flush_active_capture` 可能把 A 的块回吐到对话，A 留空后仍由 writing_graph 补写或回滚，不会静默留下空文件。
- 同一回复里 `<file>A</file>` 之后再 create_file(A)：现在放行并走剧本幂等复用，工具执行时 A 的正文尚未落库，`original_content_length` 记为 0，截断覆盖保护对这次失效。此外 adapter 随后落库 A 时会按 file_id 清掉 A 的守卫条目（`bind` 重新置位的那条也一起清掉），再按复用结果重新捕获 A；若这次重新捕获的正文被截断、自动补全，会覆盖已落库的完整 A，而 A 已不在守卫里，writing_graph 的边界核验也接不住。属于模型误操作（刚写完又建同一个文件）的罕见路径，本次接受；若线上出现，再改为同标题的已写条目阻挡 create_file。
- 钩子只按"最近置位的条目"标记，不核对 adapter 当前是否正在捕获它。某个 agent 的 MESSAGE_END 之后 adapter 处理器已重置而守卫条目保留（例如 writer 被重新唤起补写空文件），若这条回复写成 `<file>A</file>` + create_file(B)：A 被标记、B 正常创建，`<file>` 块和改动前一样留在对话里；A 仍为空且条目仍在守卫里，writing_graph 边界核验照旧补写或回滚，不会静默丢数据。补写提示要求用 `edit_file`，此路径概率低，与上一条一致性风险同属一类。
- 剩余（未改）：`parallel_executor` 用 `has_pending_empty_file` 做前置检查，同一回复里 `<file>` 块后紧跟 parallel_execute 也会同样误拒（本次未观察到）；已存进历史的外露标签不迁移；WAITING_START 把 `<file>` 判为字面量时的回吐路径无证据触发；3c 需线上抓 SSE（tool_call 参数、tool_result 的 error_type）再判断。
- 临时 StreamProcessor 会打三条 INFO 日志（"Stream processor started file write mode"、"Found <file> marker"、"Found </file> marker, completing file write"，最后一条看起来像真实落库），file_id 固定为 `on-llm-end-probe` 以便排查时区分。只在有待写条目的模型调用结束时出现；压掉它需要改 stream_processor 或临时调全局 logger 级别（多请求并发下不安全），本次不做。
- `add`/`bind` 对已存在的 file_id 不改变其在字典里的位置，"最近置位"按首次置位顺序计。现有流程里同时存在多个条目时，未标记的条目会先挡住 create_file，不会因此标错条目；为保持 `latest` / `snapshot` 对 writing_graph 的既有语义，本次不改顺序。
