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
- **停止时落定的整章替换回执**：API 停止分支在生成 done 帧之前先 `note_saved_receipts`（结算时再记一次，幂等），done 的 `produced_output` 与之后的计费一致。`_stamp_body_receipts` 也处理还没有 `body_saved` 的整章替换卡片：回执是整数时写 `body_saved` / `mutation_applied` 为 true 和 `new_word_count`，否则都为 false；整章替换的卡片只读按 `write_chapter` 的 tool_use_id 记的回执（StreamAdapter 的 `_replacement_receipts`：正常提交、被拒、停止时在途提交、停止时补存都记），不读文件级回执——文件级回执保留第一次保存，同一章在一轮里先替换成功、又定位替换一次没写成时，后一张卡不能借前一次的回执说成已保存；StreamAdapter 已补发结果（已有 `body_saved`）的卡片不改。卡片因此不再停在「替换中」，历史回放如实。
- **会被重试的整章替换不先说「未保存」**：截断补全的替换被拒绝时 StreamAdapter 只发 `file_content_end`；engine 真正作废定位（不能重试、重试的捕获没恢复、为另一章重试时作废其余定位）时才发固定说明。文字放在 `author_facing_text.rewrite_not_saved_notice`，两处共用。不会再重试的两条出口也要说：模型出错时适配器发出错帧之后不再处理事件，所以由 StreamAdapter 在出错帧之前、还有待写的整章替换时发这句；工具轮数耗尽时 engine 先作废还在等正文的整章替换并发这句，再做空文件核验（核验会因为原文还在就把它静默清掉）。
- **续跑输入总以这次的说明结尾**：`_continued_run_input` 只要 `state["user_message"]` 非空就追加，不再和上一轮的说明比较（两次纠偏说明相同的时候，第二次输入原本停在助手回复上）。engine 每个续跑分支都先写好 `user_message`。
- **CRLF 的独占一行文件标签**：`_LONE_FILE_TAG_LINE_PATTERN` / `_LONE_FILE_START_LINE_PATTERN` 允许行尾 `\r`。
- **没量到输入大小的停止行不挡住更早的读数**：`load_loop_history` 只在 `prompt_tokens` 为正时取用并停止往前找，并加上比它新的行（没量到的停止轮，例如贴了长材料就停止）的正文估算——读数里不含它们；遇到带压缩点的行就停（更早的读数是压缩前的大小），都没有时照旧用估算。
- **H3：写工具在停止之后才提交**：`edit_file`、`create_file`、`write_chapter`、`delete_file`、`rename_file` 改用共享的 `_offload_write_tool`：线程 future 先登记进 `RunOutcome.inflight_tool_writes`（ToolContext 拿着同一个 list，所以比 `clear_context()` 活得长），用 `asyncio.shield` 等它；正常返回或出错时移除，被取消时留着。service 的取消与出错收尾在交回消息 id 之前调用 `RunOutcome.settle_inflight_tool_writes(STREAM_FILE_SAVE_TIMEOUT_S)`（取消收尾与 `settle_aborted_capture` 并发等，最长一个时限而不是两个相加），把完成的结构化结果按 tool_result 帧的格式记进 `committed_tool_results`；线程出错、超时、结果解析不了的不记。`StreamBillingTracker.note_committed_tool_results` 把它们交给与实时帧相同的规则（`_observe_effects`）：确认的修改、删除、改名算写入，新建的空文件成为占位候选，退还时照常移除。tracker 记下没有结果帧的写工具调用（`write_tool_unanswered()`），API 截停时有这类调用也先等消息 id；停止卡、结算和断线后台退还都在等到之后调用它。
- **M8：压缩调用计入请求级模型调用预算**：`compact_history(run_meter=...)` 每次摘要调用前 `record_model_call()`，次数仍由 `_ATTEMPT_EFFORTS` 限定；`AGENT_RUN_MAX_MODEL_CALLS` 的注释改为「请求内的模型调用（SDK 轮次加压缩）」。

### 后续修复：输出上限截断的新正文、流式写入时的自动保存

- **新正文写到输出上限**：openai-agents 的 Chat Completions 流式 handler 不给 finish_reason，判断仍用 usage：正常结束的 run 最后一次响应 output_tokens 达到 `AGENT_OPENAI_AGENTS_MAX_OUTPUT_TOKENS * OUTPUT_CAP_RATIO` 时，runner 在 MESSAGE_END 上带 `output_capped=True`（stop_reason 仍是 `end_turn`，落库与历史回放不变；只有思考就撞上限的原有 `output_truncated` 不变）。StreamAdapter 把按流结束补全保存成功的新正文记进 `ToolContext.mark_cut_body_saved`（不进已保存的完整正文）。engine 在 run 边界取走这些记录（`take_cut_bodies`），这次 run 又带 `output_capped` 时：正文照样保存，作者看到固定说明「《…》写到了单次回复的输出上限，正文已保存到中断处。」；模型收到 `_BODY_CUT_CONTEXT`（截断处之前已保存、这一章没写完、附落库正文末尾 200 字，用 edit_file `op=append` 从断点续写，不整章重写，写完再自查），不再收到「写完的正文已保存，请自查」。没有撞上限的未闭合正文（模型漏写 `</file>`）照旧按已保存续跑。判断只用 usage 与保存点的结构化记录，不看正文文字。
- **整章替换的新正文写到输出上限**：行为不变（原文不动，在纠偏次数内经 `FILE_BODY_RESUME` 重试，补不成才说「未保存」），只把重试说明换成 `_CHAPTER_REPLACE_CUT_RETRY_CONTEXT`：如实说新正文被截断没保存，要求思考尽量简短。
- **流式写入时暂停自动保存**：`SimpleEditor` 的 3 秒防抖自动保存在打开的文件正被 AI 流式写入（`isStreaming`）时不排期，与 `isAiEditing`、对比审阅同一处理。流式结束后 effect 重新排期：服务端没变（替换被拒或截断没保存）就照常用作者写字时的版本令牌保存；AI 的新稿已落库时，Editor 流式结束的回读（`loadData` → `syncOpenFileFromServer`，100 ms，早于 3 秒的自动保存）按 #381 打开对比审阅，自动保存随审阅暂停，两边的字都不丢。离开 / 切换文件时的最后一次保存（`onFlushReady`）和页面关闭时的本地草稿快照不受影响。

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
  - 作者停止时路由层只等 `STOP_MESSAGE_ID_WAIT_S`（3 秒），比 service 收尾的上限（`STREAM_FILE_SAVE_TIMEOUT_S` 加历史落库）短：3 秒之后才提交的写工具或补存，这一轮照样按没有产出退还，那时新建的空文件也不会被移除（留给作者自己删）。这是有意的取舍：不让停止按钮在少见的慢提交上多卡十几秒；方向偏向作者，不会重复扣费。断线路径在后台等 `ROUND_OUTCOME_MESSAGE_ID_WAIT_S`（30 秒），service 超过它时同样按退还处理。
  - 已解析、没开始提交就被截停的整章替换新稿作废，作者需要时重新生成；只有取消时已经在提交的整章替换会落库，它不发 diff 审阅事件，卡片也拿不到撤销锚点。
  - 网页上的撤销按钮只在卡片同时有已保存消息 id 和工具调用 id 时出现（`isPersistedUndoTarget`）：流式进行中的卡片要等这一轮结束、拿到消息 id 后才显示；没有工具调用 id 的旧记录、拿不到消息 id 的停止 / 失败轮不再显示，仍可从历史版本恢复。这样撤销总能记到卡片上，刷新后不会又显示成没撤销。
  - 新正文被输出上限截断时，续写占用本轮的 run 次数（`AGENT_COLLABORATION_MAX_ITERATIONS`）与模型调用；续写成功后作者仍会看到那句「写到了单次回复的输出上限」，它说的是当时的保存事实。没续写成（run 次数用完、模型没有 append）时回合回执仍按落库字数列出这一章，加上那句说明，作者不会被告知这一章已写完。
  - 流式写入期间作者在这之前打的字要等流式结束后才自动保存；这段时间关页面仍由本地草稿快照保护。
  - 测试随行为更新：`test_loop_round_endings` 里「总结之后不续跑」改为「已保存正文后续跑自查」；纠偏用例写的是资料文件（outline），续跑次数与修复前相同；一条停止 / 断线补存用例的断线分支改为断言撤回退还。

## Follow-up

- 停止时才提交的写工具（H3）和停止时才提交的整章替换都没有结果帧：卡片不更新，也没有撤销锚点；改动可从历史版本恢复。需要时再把在途结果补成卡片。
- `create_file` 复用已有空文件时不认领本条回复里先写出的正文，还没有复现到作者路径，暂不处理。
- 不是停止 / 断线 / 时限的取消（例如 SDK 取消并行的兄弟工具任务）留在 `inflight_tool_writes` 里的写工具，正常结束时不等它们的结果，那次提交不进计费与写入回执；还没有复现到作者路径，需要时在正常收尾也调用 `settle_inflight_tool_writes`。
- 作废一次从没写正文的整章替换时，作者会看到固定的「未保存」一句，接着模型按 `_REPLACE_UNSAVED_CONTEXT` 再用一两句话说同一件事。固定句是确定的事实，模型那段还要交代其他章节，暂时两者都留。
