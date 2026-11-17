# Agent Note: 文件还在等正文时出错的一轮退还并移除空白文件

## Problem

[2026-10-09 停止产出定义](../architecture/2026-10-09-stop-output-definition-and-round-outcome.md) 规定「建了空章节之后出错 / 超时：退还」。实际代码里有一条路径与它不符：`create_file` 建好文件（或 `write_chapter` 定位了整章替换）之后，适配器把下一段回复扣在捕获缓冲里等 `<file>`；模型在这段引子还没写到 `<file>` 时流内出错，适配器在 error 帧之前才把引子作为 `content` 放出。`StreamBillingTracker` 看到终止帧时当前段落有文字，就记 `ended_on_reply`，`decide()` 返回 `error_after_output` 照常计费。作者没有读到任何回复，也没拿到正文，却被扣了一次额度。

另外，空白占位文件只在停止 / 断线被退还时移除；正常收尾（error 帧、失控停止）被退还的一轮不移除，空文件留在文件树里。

## Decision

- `stream_billing.py`：终止帧时只有「本轮开始写的文件没有还在等正文的」才记 `ended_on_reply`。等正文的状态用 tracker 已有的结构化状态判断：本轮新建、还没收到正文的空文件（`removable_placeholders()`），或定位了整章替换、还没收到落库结果的章节（`unconfirmed_replacement_ids()`）。不看文字内容和长度。墙钟时限路径用同一个 `ended_on_reply`，规则一致。正文已经写进文件之后的一句短回答再出错，仍按回复计费。
- 原来重复的终止帧条件合并进 `_is_terminal_event` 分支。
- `api/agent.py` 正常收尾被退还的分支和停止路径一样：等这一轮助手消息 id（最多 `STOP_MESSAGE_ID_WAIT_S`，代表残稿补存已落定）后移除这一轮的空白占位文件，`quota_refunded` 带 `removed_files`，终态行 `stop_outcome.removed_files` 同样记下。等不到 id 就保留文件，补存不会撞上已删除的文件；不带 `X-Client-Stop-Contract` 的旧标签页收不到 `removed_files`，同断线路径一样不移除。被计费的一轮照旧保留文件。前端已经处理 `removed_files`，无需改动。

## Alternatives considered

- **适配器出错时丢掉扣住的引子，不作为 `content` 放出。** 这样 tracker 看不到文字，自然不记 `ended_on_reply`，改动看起来更小。但时限路径同样会放出缓冲，需要再改一处；而且这改变了作者看到的内容和落库历史。计费问题应在计费判定里用状态解决，不应靠改变展示。
- **只修计费，不移除空白文件。** 改动最少，空文件对作者也基本无害。但作者没要这个文件、这一轮又不计入，留着一个空白设定或章节与停止路径的说法不一致，作者还得自己删。
- **按引子长度或是否像开场白判断。** 违反「不用长度 / 文本启发式判断完成」的规则，不采用。

## Consequences

- 收益：上游中途出错、什么都没写成的一轮不再扣额度，空白文件随退还一起移除，和停止 / 断线的规则一致。
- 代价：文件还在等正文时，若模型其实已给作者写了一句完整短回答再出错（协议上这一回复应以 `<file>` 开头，罕见），现在也退还；成本由每日成本兜底封住。
- 当场的 error 帧仍可能带 `unwritten_files`（出错时还不知道会不会退还），随后 `quota_refunded` 说明已移除。落库的终态行以移除后为准：`removed_files` 记下被移除的文件，`unwritten_files` 不再列它（`_finalize_stopped_round` 从当场回执里去掉已移除的 id），刷新后不会把已不在文件树里的文件说成「未确认保存」。
