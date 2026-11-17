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
- **H2**：`_emit_stream_result` 在第一个 yield 之前把 `file_complete` 的结果和当时的待写记录登记为 `_completed_unsaved`；`_complete_file_write` 一进入就清掉它（由正常路径接手）。`settle_aborted_capture` 先结算在途的整章替换，再处理 `_completed_unsaved`：空白正文不存；截断补全且目标原有正文时按同一覆盖保护不存；整章替换还没开始提交的不存（截停之后没有 diff 审阅和撤销卡，保留作者原文、这一轮按替换没落库结算，与还在捕获中的替换相同），已经开始提交的由在途结算等结果；新文件起一个补存任务登记为 `_abort_capture`。正常路径的新文件保存放进 `asyncio.shield` 的任务里，取消落在保存中途时登记为 `_abort_capture`；保存目标的类型与版本令牌（`_save_target`）在任务创建之前定下，`_save_file_content` 不再自己读待写记录。
- **M1**：`StreamBillingTracker.note_saved_receipts` 对本轮新建的空文件，回执是整数（确认提交）就记为已收到正文、算写入与产出；`None` 不算，仍可作为空白占位移除。`_file_body_pending` 改为公开的 `file_body_pending()`，API 截停时只要还有等正文的文件就先等回执（上限 `STOP_MESSAGE_ID_WAIT_S`；断线路径在后台等 `ROUND_OUTCOME_MESSAGE_ID_WAIT_S` 再决定是否退还）。
- **M2**：`service._stamp_body_receipts` 在三条落库路径（正常、出错补偿、取消补存）落库前，给等正文的 `create_file` / `write_chapter` 新建卡片写上 `body_saved`（回执里有字数才为 true）。只改落库记录，不发帧，计费不变。`loop/history.py` 回放这个值，false 时附「没有确认保存，需要时先 query_files」；修复前写下、没有这个键的行照旧当作已保存（新旧行靠键是否存在区分，不读文字）。
- **M3**：`loop/engine.py` 在本请求的已保存正文里有还没自查过的章节 / 分集正文时就续跑一次自查（`self_checked_ids` 仍保证每个文件一次）。保存点连同文件类型记下（`ToolContext.saved_content_body_ids()`，类型不明按正文算），大纲、人物等资料文件不因此续跑，与修复前 `mark_chapter_body_saved` 只记章节的范围一致。正文之后作者已经看到一段回复（这次运行没有停在 `</file>` 上，按文件块协议结构判断，不读回复的意思）时改用 `_BODY_SAVED_REPLIED_CONTEXT`：照样自查和接着写，但不重复那段总结，只说改了什么或新写了哪章；原来「停在 `</file>` 或新正文 EOF 上」的条件保留，用于整章替换没保存的说明与未闭合 EOF 的续写。续跑说明里的文件 = ReadGuard 的写入 + 还没保存的整章替换目标（ReadGuard 新增的 `files_targeted`） + 已保存正文。
- **M4**：ReadGuard 对 `mutation_applied` 明确为 false 的成功结果不记写入，只把目标记进 `files_targeted`；没有这个键的旧结果不变。任务板的写入 id = ReadGuard 的写入 + 本请求已保存正文，去重保序。

### 复核补充（同一套写入证据）

- **整章替换的待写条目不被「已有正文」清掉**：`_clear_settled_pending_empty_files` 跳过 `pending_chapter_replacement` 有值的条目（与 engine 的 `_settle_pending_empty_files` 同一判断）。同一回复里第二次 `write_chapter` 或 `create_file` 按 `pending_empty_file_unwritten` 拒绝，下一段 `<file>` 只落进先定位的那一章。
- **`write_chapter` 整章替换也认领本条回复里先写出的正文**：定位成功后 `take_reply_prebody()` 为真就 `mark_pending_body_streamed` 并带 `body_in_reply`；StreamAdapter 在 `_arm_chapter_replace` 之后调 `_bind_reply_prebody`，正文走同一条整章替换落库（版本核对、diff、撤销）。下一步说明与 `create_file` 共用 `_body_in_reply_next_step`。
- **停止时落定的整章替换回执**：API 停止分支在生成 done 帧之前先 `note_saved_receipts`（结算时再记一次，幂等），done 的 `produced_output` 与之后的计费一致。`_stamp_body_receipts` 也处理还没有 `body_saved` 的整章替换卡片：回执是整数时写 `body_saved` / `mutation_applied` 为 true 和 `new_word_count`，否则都为 false；StreamAdapter 已补发结果（已有 `body_saved`）的卡片不改。卡片因此不再停在「替换中」，历史回放如实。
- **会被重试的整章替换不先说「未保存」**：截断补全的替换被拒绝时 StreamAdapter 只发 `file_content_end`；engine 真正作废定位（不能重试、重试的捕获没恢复、为另一章重试时作废其余定位）时才发固定说明。文字放在 `author_facing_text.rewrite_not_saved_notice`，两处共用。
- **续跑输入总以这次的说明结尾**：`_continued_run_input` 只要 `state["user_message"]` 非空就追加，不再和上一轮的说明比较（两次纠偏说明相同的时候，第二次输入原本停在助手回复上）。engine 每个续跑分支都先写好 `user_message`。
- **CRLF 的独占一行文件标签**：`_LONE_FILE_TAG_LINE_PATTERN` / `_LONE_FILE_START_LINE_PATTERN` 允许行尾 `\r`。
- **没量到输入大小的停止行不挡住更早的读数**：`load_loop_history` 只在 `prompt_tokens` 为正时取用并停止往前找；遇到带压缩点的行就停（更早的读数是压缩前的大小），都没有时照旧用估算。
- **H3：写工具在停止之后才提交**：`edit_file`、`create_file`、`write_chapter`、`delete_file`、`rename_file` 改用共享的 `_offload_write_tool`：线程 future 先登记进 `RunOutcome.inflight_tool_writes`（ToolContext 拿着同一个 list，所以比 `clear_context()` 活得长），用 `asyncio.shield` 等它；正常返回或出错时移除，被取消时留着。service 的取消与出错收尾在交回消息 id 之前调用 `RunOutcome.settle_inflight_tool_writes(STREAM_FILE_SAVE_TIMEOUT_S)`，把完成的结构化结果按 tool_result 帧的格式记进 `committed_tool_results`；线程出错、超时、结果解析不了的不记。`StreamBillingTracker.note_committed_tool_results` 把它们交给与实时帧相同的规则（`_observe_effects`）：确认的修改、删除、改名算写入，新建的空文件成为占位候选，退还时照常移除。tracker 记下没有结果帧的写工具调用（`write_tool_unanswered()`），API 截停时有这类调用也先等消息 id；停止卡、结算和断线后台退还都在等到之后调用它。
- **M8：压缩调用计入请求级模型调用预算**：`compact_history(run_meter=...)` 每次摘要调用前 `record_model_call()`，次数仍由 `_ATTEMPT_EFFORTS` 限定；`AGENT_RUN_MAX_MODEL_CALLS` 的注释改为「请求内的模型调用（SDK 轮次加压缩）」。

## Alternatives considered

- **一个贯穿整个请求的写入证据对象**。最强的理由：所有下游读同一个对象，概念上最干净。没选的原因：取消收尾在 `ToolContext.clear_context()` 之后才跑，这个对象必须比 ToolContext 活得长，要另外穿过 service、API 和 runner；现有的两份记录（每轮回执、请求内的已保存正文）已经覆盖所有读者，只要产生点唯一就不会分叉。
- **给新建文件补发一次 `tool_result` 帧，让计费从帧上看到保存**。最强的理由：计费继续只看帧，和整章替换补发结果的做法对称。没选的原因：取消之后帧已经送不到客户端，tracker 也只在入队前记账；而且会改变新建卡片的帧语义，前端要多处适配。
- **保留按 `</file>` 后文字判断续跑，只把「完成标记」类短句加进允许列表**。最强的理由：不增加模型调用。没选的原因：这正是 AGENTS.md 禁止的按文本形态推断完成，换一种说法就会再漏；保存事实是结构化的，可以直接用。

## Consequences

- 收益：停止 / 断线落在 yield 或保存中途时，写完的正文不再丢；计费、停止回执、历史与任务板对同一份正文给出一致的保存结论；续跑不再受模型多写一句总结影响。
- 代价：
  - 最后一章正文之后模型已经写了总结时，会多一次自查调用（每个文件仍最多一次）；这次调用的说明要求不重复总结，作者最多多看到一句「自查过了」或改动说明。
  - 截停时只要还有等正文的文件，停止路径最多多等 `STOP_MESSAGE_ID_WAIT_S`（一个请求只等一次：停止卡已经等满没等到时，结算和移除占位不再各等一次）；断线路径的退还在后台等回执后才决定。停止路径等不到回执时仍按「没有产出」退还，补存稍后落库，文件保留。
  - 截停时有写工具调用还没有结果帧，停止路径同样最多多等 `STOP_MESSAGE_ID_WAIT_S`；service 收尾最多等在途写工具 `STREAM_FILE_SAVE_TIMEOUT_S`，等不到就按没有写入结算（偏向作者）。
  - 已解析、没开始提交就被截停的整章替换新稿作废，作者需要时重新生成；只有取消时已经在提交的整章替换会落库，它不发 diff 审阅事件，卡片也拿不到撤销锚点。
  - 网页上的撤销按钮只在卡片同时有已保存消息 id 和工具调用 id 时出现（`isPersistedUndoTarget`）：流式进行中的卡片要等这一轮结束、拿到消息 id 后才显示；没有工具调用 id 的旧记录、拿不到消息 id 的停止 / 失败轮不再显示，仍可从历史版本恢复。这样撤销总能记到卡片上，刷新后不会又显示成没撤销。
  - 测试随行为更新：`test_loop_round_endings` 里「总结之后不续跑」改为「已保存正文后续跑自查」；纠偏用例写的是资料文件（outline），续跑次数与修复前相同；一条停止 / 断线补存用例的断线分支改为断言撤回退还。

## Follow-up

- 停止时才提交的写工具（H3）和停止时才提交的整章替换都没有结果帧：卡片不更新，也没有撤销锚点；改动可从历史版本恢复。需要时再把在途结果补成卡片。
- 新章正文在一次回复里写满输出上限（约 62.7k 输出 token）被截断时，仍按未闭合 EOF 保存并说「已保存」；区分它需要 runner 在 MESSAGE_END 上带一个新的输出被截断标记，单独排期。
- 整章替换在流式写入时，作者编辑器的自动保存可能先改了这一章：现在作者的保存胜出、AI 的替换按版本冲突拒绝，代价是白写一次。要暂停自动保存就会走 #381 的冲突审阅路径，需要编辑器层的运行时验证，单独处理。
- `create_file` 复用已有空文件时不认领本条回复里先写出的正文，还没有复现到作者路径，暂不处理。
