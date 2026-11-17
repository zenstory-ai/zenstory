# Agent Note: 计费、续跑、任务板与历史都按确认落库的写入判断

Status: implemented

相关：[单循环 agent](../architecture/2026-11-16-single-loop-agent.md)（第 10 项的续跑条件随本笔记更新）；部分改变 [文件流续写与如实完成](2026-10-11-file-stream-continuation-and-truthful-completion.md) 里「正常写了总结的回合没有额外调用」这一后果；停止计费规则见 [停止产出定义与回合收尾](../architecture/2026-10-09-stop-output-definition-and-round-outcome.md)。同批的 edit_file 写前历史见 [2026-11-17-ai-edit-requires-pre-write-history.md](2026-11-17-ai-edit-requires-pre-write-history.md)。

## Problem

一份正文从解析到落库要经过几个阶段：解析器的结果、SSE 帧、ToolContext 的记录、ReadGuard 的台账、StreamAdapter 的保存回执。计费、续跑、任务板和历史各自挑了其中一份当作「已经写好」，几处选的并不是落库事实：

1. **完整正文在 yield 窗口丢失（H2）**：解析器收到 `</file>` 后立即 reset，完整正文只在一个局部 StreamResult 里。适配器先发最后的 `file_content` 帧，恢复迭代后才保存。SSE 队列满、作者恰好停止或断线时，生成器停在这个 yield 上被关闭，补存只能向解析器要正文，而解析器已经清空。新章留空，整章替换的新稿消失，这一轮却因为看到过 `file_content` 帧而计费。
2. **新建文件的补存回执不计费（M1）**：短正文还在解析器尾缓冲里（没有 `file_content` 帧）时停止，补存成功写进了文件，但 `note_saved_receipts` 只看整章替换的回执，API 也只为整章替换等回执，这一轮按「没有产出」退还。
3. **历史把「在等正文」回放成「已保存」（M2）**：新建写入的卡片只要 `awaiting_body` 为真就回放 `body_saved=true`，停在正文之前、保存失败的一轮也一样。
4. **续跑看 `</file>` 之后有没有文字（M3）**：保存后自查只在回复恰好停在 `</file>` 时触发；模型多写一句「第 2 集写好了」就跳过自查，多章请求可能写完一章就结束。这是按文本形态推断完成，违反 AGENTS.md 的语义决策规则。
5. **无操作的成功结果进了写入台账（M4）**：`write_chapter` 命中已有章节（`action=exists`）、同名 rename、整章替换的定位都是 `mutation_applied=false`，ReadGuard 仍记为写入，任务板据此把「写第 1 章正文」标成完成。

## Decision

只在 StreamAdapter 的保存点产生写入证据，下游读两份现有的记录：

- **唯一产生点**：`StreamAdapter._note_body_saved(file_id, words, *, title=None)`。正常保存、整章替换保存、取消补存、取消时在途的整章替换都经过它。它写每轮的保存回执 `_saved_file_word_counts`（流向 `run_outcome.saved_file_word_counts`）；`title` 不为 None（完整正文，不是截断补全）时同时记进本请求的 `ToolContext` 已保存正文（`_chapter_bodies_saved` 改为 `id -> 标题`，所有文件类型都记）。保存失败照旧 `setdefault(file_id, None)`。
- **H2**：`_emit_stream_result` 在第一个 yield 之前把 `file_complete` 的结果和当时的待写记录登记为 `_completed_unsaved`；`_complete_file_write` 一进入就清掉它（由正常路径接手）。`settle_aborted_capture` 先结算在途的整章替换，再处理 `_completed_unsaved`：空白正文不存；截断补全且目标原有正文时按同一覆盖保护不存；整章替换走 `_save_chapter_replacement`（保留版本核对）；新文件起一个补存任务登记为 `_abort_capture`。正常路径的新文件保存放进 `asyncio.shield` 的任务里，取消落在保存中途时登记为 `_abort_capture`；保存目标的类型与版本令牌（`_save_target`）在任务创建之前定下，`_save_file_content` 不再自己读待写记录。
- **M1**：`StreamBillingTracker.note_saved_receipts` 对本轮新建的空文件，回执是整数（确认提交）就记为已收到正文、算写入与产出；`None` 不算，仍可作为空白占位移除。`_file_body_pending` 改为公开的 `file_body_pending()`，API 截停时只要还有等正文的文件就先等回执（上限 `STOP_MESSAGE_ID_WAIT_S`；断线路径在后台等 `ROUND_OUTCOME_MESSAGE_ID_WAIT_S` 再决定是否退还）。
- **M2**：`service._stamp_body_receipts` 在三条落库路径（正常、出错补偿、取消补存）落库前，给等正文的 `create_file` / `write_chapter` 新建卡片写上 `body_saved`（回执里有字数才为 true）。只改落库记录，不发帧，计费不变。`loop/history.py` 回放这个值，false 时附「没有确认保存，需要时先 query_files」；修复前写下、没有这个键的行照旧当作已保存（新旧行靠键是否存在区分，不读文字）。
- **M3**：`loop/engine.py` 在本请求的已保存正文里有还没自查过的文件时就续跑一次自查（`self_checked_ids` 仍保证每个文件一次）；原来「停在 `</file>` 或新正文 EOF 上」的条件保留，用于整章替换没保存的说明与未闭合 EOF 的续写。续跑说明里的文件 = ReadGuard 的写入 + 还没保存的整章替换目标（ReadGuard 新增的 `files_targeted`） + 已保存正文。
- **M4**：ReadGuard 对 `mutation_applied` 明确为 false 的成功结果不记写入，只把目标记进 `files_targeted`；没有这个键的旧结果不变。任务板的写入 id = ReadGuard 的写入 + 本请求已保存正文，去重保序。

## Alternatives considered

- **一个贯穿整个请求的写入证据对象**。最强的理由：所有下游读同一个对象，概念上最干净。没选的原因：取消收尾在 `ToolContext.clear_context()` 之后才跑，这个对象必须比 ToolContext 活得长，要另外穿过 service、API 和 runner；现有的两份记录（每轮回执、请求内的已保存正文）已经覆盖所有读者，只要产生点唯一就不会分叉。
- **给新建文件补发一次 `tool_result` 帧，让计费从帧上看到保存**。最强的理由：计费继续只看帧，和整章替换补发结果的做法对称。没选的原因：取消之后帧已经送不到客户端，tracker 也只在入队前记账；而且会改变新建卡片的帧语义，前端要多处适配。
- **保留按 `</file>` 后文字判断续跑，只把「完成标记」类短句加进允许列表**。最强的理由：不增加模型调用。没选的原因：这正是 AGENTS.md 禁止的按文本形态推断完成，换一种说法就会再漏；保存事实是结构化的，可以直接用。

## Consequences

- 收益：停止 / 断线落在 yield 或保存中途时，写完的正文不再丢；计费、停止回执、历史与任务板对同一份正文给出一致的保存结论；续跑不再受模型多写一句总结影响。
- 代价：
  - 最后一章正文之后模型已经写了总结时，会多一次自查调用（每个文件仍最多一次）。
  - 截停时只要还有等正文的文件，停止路径最多多等 `STOP_MESSAGE_ID_WAIT_S`；断线路径的退还在后台等回执后才决定。停止路径等不到回执时仍按「没有产出」退还，补存稍后落库，文件保留。
  - 停止后才补存的整章替换不发 diff 审阅事件，卡片也拿不到撤销锚点（与取消时在途的整章替换相同）。
  - 测试随行为更新：`test_loop_round_endings` 里「总结之后不续跑」改为「已保存正文后续跑自查」；几条纠偏用例多一次自查 run；一条停止 / 断线补存用例的断线分支改为断言撤回退还。

## Follow-up

- **H3：普通写工具被取消后线程照常提交**。`edit_file` 等在 `asyncio.to_thread` 里执行，停止 / 断线 / 时限取消了等待，线程仍可能提交，SSE 拿不到结果，计费按没有写入退还。修复需要一个在 `ToolContext.clear_context()` 之前建立的请求级在途登记：共享的 `to_thread` 包装在取消时 shield 并登记 `(tool_name, future)`，`settle_aborted_capture` 在 `STREAM_FILE_SAVE_TIMEOUT_S` 内等它们，把结构化结果（`mutation_applied`、删除成功、新建的空文件）翻译成回执与占位候选，API 的等待条件扩到「有写工具调用没有结果」。作者正文不会因此丢失，计费偏差对作者有利，所以单独排期。
- **M8**：压缩调用计入请求级模型调用预算，由负责 `compaction.py` 的分支处理。
