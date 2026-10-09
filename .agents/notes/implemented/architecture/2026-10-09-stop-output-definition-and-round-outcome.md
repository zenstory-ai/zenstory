# Agent Note: 停止 / 断线的「产出」口径、空白占位文件与一轮的终态

Status: implemented

本 note 是作者停止 / 断线计费与界面的最终契约：

- 修订 `architecture/2026-10-09-agent-graceful-stop-and-no-output-refund.md`（PR163）里「非空正文、非空文件正文、写文件工具成功即算实质产出」的口径（其余停止协议不变）。
- 取代 `feature/2026-10-08-agent-stop-reasons-for-authors.md`（#164）里「作者主动停止照常计费、`user_cancelled` 计费」与「停止说明里的『计入』按是否收到 `session_started` 推断」两条。PR163 已经推翻了「停止一律计费」，本 note 写明合并后的结果。
- 补充 `feature/2026-10-09-stop-guard-progress-and-leave-prompt.md`：浏览器后退也先确认、离开确认的文案与按钮层级、确认框打开时这一轮结束的处理、停止按钮准备期的反馈。

## Problem

2026-10-09 新用户审计（PR163 + #164 合并后）在停止 / 离开 / 重发链路上发现：

1. **一句过渡旁白、一个空章节就计费（P1-2）。** 「我先看一遍全书大纲。」这类工具调用前的旁白算产出；`create_file` 建好空章节（正文随后才流式写入）也算产出。浏览器复现：空章节停止计费 3/3，只有一句旁白就离开计费 1/1，还留下 `len=0` 的空章节，确认框「还没写出内容的话，这一轮不计入」的承诺落空。
2. **只有 `parallel_execute` 写入成功的一轮被判「无产出」并退还（P2-20）**，界面还说「这一轮还没有写出内容」。
3. **刷新、重进项目后停止 / 中断的一轮是无状态空气泡（P2-1）**；离开和断线路径从头到尾看不到是否计入。
4. **停止路径的 `done` 不带 `assistant_message_id`（P2-2）**，前端退回「最近一条未分配的助手消息」，把这一轮绑到上一轮停止的空消息上：时间戳错、点赞记错、「消息还在保存」永不消失。
5. **浏览器后退 / 手机系统返回不拦截（P2-19）**；离开后额度标签是旧值（P3-2）；无产出停止后建议芯片假设正文已写、还多调一次建议模型（P3-3）；已写出内容时离开确认仍只讲「不计入」，「离开」是蓝色主按钮（P3-4）；确认框打开时这一轮结束，作者的离开意图丢失（P3-5）。
6. **「重新发送」只带文字（P3-6）**，同一 tick 双击出两个用户气泡（P3-24）；停止按钮 1.5 秒准备期没有任何说明（P3-1）。
7. **生成失败后状态不同步（P2-3）**：空的「正在组装上下文…」气泡和错误卡片并存；每次重试服务端多存一条同样的用户消息，刷新后只剩 N 条重复用户消息、没有回复也没有重试入口。

## Decision

### 什么算「这一轮有产出」（`agent/core/stream_billing.py`）

作者停止或这一轮结束前断线时，本轮有实质产出（`produced_output`）则计费，否则退还。实质产出只有两种：

- **写入留下了正文（`write_succeeded`）**：流式文件正文非空；`create_file` 带非空正文新建（`reused_existing` 复用已有文件不算）；`edit_file` 真的改了文字（`mutation_applied`，旧格式结果没有该字段时看 `edits_applied`，都没有才按成功算）；`delete_file` 成功；`parallel_execute` 里有写入类子任务（write_chapter / edit_file / delete_file）完成——看整批的 `tool_result`，批次中途被停止 / 断线时看已完成子任务的 `parallel_task_end`（类型取自同一子任务的 `parallel_task_start`）；`update_project` 改了作品名（`project_name_updated`）或简介、文风、备注（`updated_fields` 含 summary / writing_style / notes），只更新任务板或 `current_phase`（进度标记，更新任务板时会被自动推进）不算。只建了空文件不算。失控停止规则里的「有没有写入」也改用这个口径。
- **真正的回复正文**：同一段正文（两次工具调用 / 交接 / agent 切换之间）累计到 `PROSE_MIN_CHARS = 60` 个可见字符。工具调用前那句 10～30 字的旁白到不了；分段相加不跨工具调用。

思考过程、读文件、只建空文件、工具调用前一句旁白都不算。

**终止帧之后才断开不算中断。** `done` / `workflow_complete` / 终止性 `workflow_stopped` / `error` 已经产生（服务端按入队记账，作者没收到也算）之后，`process_stream` 还在收尾时面板卸载、连接断开：按这一轮的结束方式结算（正常结束计费，出错按出错规则），不排「已中断」终态，也不把 `stop_kind` 交给取消路径。作者主动停止不受影响，仍按停止规则。

**墙钟时限与出错路径（与 PR163 之前的契约比较）。** 这两条路径计费看 `produced_output` 或 `ended_on_reply`：这一轮结束（终止帧 / error 帧）时，模型最后一段正文之后没有再调工具，也就是作者已经读到一段回复、随后才出错或超时。于是：
- 一句不到 60 字的完整回答之后出错 / 超时：计费，和之前（任何非空正文都算产出）一样。
- 只读了文件、想了、建了空章节、或旁白之后去调工具时出错 / 超时：退还。之前这类一轮因为那句旁白或空 `create_file` 计费；作者什么也没拿到，退还对作者是对的，成本由每日成本兜底封住。
- 旁白刚说完、工具调用还没发出就出错，和「短回答之后出错」分不出来，按后者计费（与之前相同）。
- 作者停止、这一轮结束前断线仍只看 `produced_output`（P1-2），不看 `ended_on_reply`。前端 `lib/agentRoundProgress.ts` 用同一套规则（`isWriteToolResult`、`isRealProse`，阈值同为 60），用于停止说明、离开确认和是否请求建议。

### 退还时移除这一轮的空白占位文件（`agent/core/round_outcome.remove_empty_placeholders`）

- 只在停止 / 断线的一轮**被退还**之后做。候选只来自本轮 `create_file` 成功新建、当时正文为空、不是文件夹、不是复用已有文件、之后也没收到非空流式正文的文件（`StreamBillingTracker.removable_placeholders`）。
- 在项目锁内逐个再查：仍属本项目、未删除、不是文件夹、正文为空、没有子节点，才软删除，并排队删除向量索引。已有正文的文件、之前就存在的文件一律不动；任何异常只记日志，不影响已完成的退还。
- 作者停止：退还帧 `quota_refunded` 带 `removed_files: [{id, title}]`，前端刷新文件树、若正打开该文件则取消选中，并在退还说明后加一句「这一轮新建的空白文件《…》已移除。」。
- 服务端只看已保存的正文。作者点「停止生成」时，前端先让打开着的编辑器把还没自动保存的字存下去（`flushOpenEditor`，最多等 1.5 秒），存完再发停止请求；作者在 AI 刚建的空章节里打了字，这一章因此有正文，不会被当成空白文件移除，文件树和编辑器里照常保留。等保存期间再点停止不会发第二次停止（第二次会直接断开连接）。这次保存没成功或 1.5 秒内没落定（例如 AI 正在改这份文件、版本历史开着、去AI味进行中，保存会被拒）时，停止请求带上打开着的文件 id（`/agent/stop` 的 `keep_file_ids`，最多 20 个），服务端记在这一轮的 `RunOutcome.keep_file_ids` 里，退还后移除空白文件时跳过它们（停止路径和停止后断线的后台收尾都一样），作者的字留在编辑器里、文件还在，之后照常保存。离开确认点「离开」时同样先保存编辑器再离开；保存没成功时先发带 `keep_file_ids` 的停止请求、等它返回再离开（这一轮于是按作者停止收尾，终态是「已停止」而不是「连接断开」），保存成功则照旧直接离开（断线）。断线：在后台退还落定后移除，作者回来时文件树已是最新，终态行里写明。

### 一轮的终态落库与 `done` 的消息 id（`api/agent.py`、`agent/service.py`）

- 路由层给 `process_stream` 传 `RunOutcome`。`POST /agent/stop` 找到运行时先写 `stop_kind = user_stopped` 再请求停止；断线路径在取消前写 `user_stopped`（已请求停止）或 `client_disconnected`。
- 新前端在每个 `/agent/stream` 请求上带 `X-Client-Stop-Contract: 1`（`lib/agentApi.ts`）。不带这个头的请求来自发布前打开的旧标签页（没有 `/agent/stop`，也不认识 `removed_files` 和终态）：它断线时照旧退还没有产出的一轮，但路由层不写 `client_disconnected`（service 按原来的取消原因补存，没有产出就不写助手消息）、不移除空白占位文件、不写 `stop_outcome`，文件树里不会留着已删除的章节。新前端的行为不变。`Agent stream billing evaluated` 日志带 `client_stop_contract`。
- 取消路径补存部分历史时，`stop_reason` 落库为这两个值之一（墙钟时限仍是 `run_deadline_exceeded`，没有路由层原因时仍是 `cancelled`），并且**即使这一轮什么都没产出也写一条助手消息**（`persist_empty_assistant`），落库后把助手消息 id 交回路由层。路由的「继续」直达不认这两个停止原因（与 `cancelled` 一样不在 `RESUMABLE_STOP_REASONS` 里）。
- 停止路径依次发：`workflow_stopped(user_stopped)` → 等这一轮助手消息落库（最多 `STOP_MESSAGE_ID_WAIT_S = 3` 秒，通常几百毫秒）→ `done`（带 `assistant_message_id`、`session_id`、`stop_reason: "user_stopped"`、`produced_output`）→ 结算 → 被退还时移除占位文件并发 `quota_refunded`。等不到 id 时 `done` 不带 id。
- 结算后（断线时在后台、等退还落定）把结果写回这条助手消息：`message_metadata.stop_outcome = {reason, charged, saved_output, removed_files}`（`record_round_outcome`，后台最多等 30 秒拿消息 id）。`message_metadata.stop_reason` 只在原值为空或是取消类（`cancelled` / `user_stopped` / `client_disconnected`）时改成停止原因；停止到达前这一轮已按别的原因落库（例如可「继续」的 `max_turns_exceeded`）时保留原值，「继续」入口不丢。
- 结算只做一次，退还不会丢：退还一律放进后台任务；连接还开着时用 `asyncio.shield` 等它，开始等之前先记下「已结算」。等待途中请求被取消（作者停止后又断开），`finally` 不会再退第二次，排队中的退还也不会被一起取消；终态收尾任务等这个退还的真实结果再写 `charged`、再移除空白占位文件。
- 停止路径等消息 id（`RunOutcome.wait_message_id`）只把超时当作「没有 id」；等待中请求被取消照常抛出，走断线路径。

### 前端

- **服务端退还回执优先**（合并提交 ae35f38 已在 ChatPanel 实现，保留）：收到 `quota_refunded(kind=stopped)` 时只显示退还说明（带「重新发送」），不再显示点击时推断的停止说明；点击时的说明也只在这一轮已有实质产出时才写「本条计入今日 AI 消息」，Pro 不提每日条数。
- **Pro 与额度未加载时不提每日条数**：`showDailyCount` 只在额度已加载且 `limit !== -1` 时为真，停止说明、退还说明、历史终态行、离开确认共用。Pro 的停止退还说明是「已停止，这一轮还没有写出内容。」（仍带「重新发送」和移除的空白文件）；`no_progress` / `error` 退还对 Pro 没有可说明的，不显示（有移除的空白文件时只说那一句）。
- **停止中保持「正在停止…」直到 `done`**：`workflow_stopped(user_stopped)` 不再结束流式状态，`done` 到达才结束，避免作者在等消息 id 的这段时间发新消息把这一轮截断。
- **只按 id 绑定**：停止、出错、断开（`partial` / `stoppedByAuthor`）的一轮只用 `done` 带回的 id 绑定后端消息，不再回退到「最近一条未分配的消息」；没有 id 就不显示反馈按钮（不再永远「消息还在保存」）。
- **刷新后的终态**：历史里带 `stop_outcome` 的助手消息末尾显示灰字 `RoundEndNote`：「已停止 · 未计入今日 AI 消息」/「已停止 · 已写入的内容已保存」/「已中断」/「已中断 · 未计入今日 AI 消息」，移除过空白文件时加「空白的《…》已移除」；Pro 不提每日条数。
- **无产出停止不请求建议**：`done.produced_output === false`（或断开且本地没有任何产出）时不调用 `/agent/suggest`，也不显示建议芯片。
- **失败后的状态**：提前结束的一轮如果只有「正在组装上下文…」这类过程行，不留空气泡；出错后「重试」不加新气泡，请求带 `metadata.retry_unanswered = true`，服务端在组装上下文前删掉会话末尾那条内容相同、之后没有任何消息（同一时间戳有别的消息时也不动）的用户消息，本轮结束再写回，刷新后只有一条；历史最后一条是没得到回复的用户消息时，显示「这条消息没有收到回复。」和「重新发送」（同样复用这条消息）。
- **重新发送**：用这一轮原始请求（技能、附件、素材、引用、当前文件）重发，`metadata.resent_after_stop = true`，新加一个用户气泡。所有发送、重发、重试都经 `startRound`，用 ref 锁挡住同一 tick 的第二次调用（锁在下一个任务释放，之后由 `isStreaming` 挡）。
- **离开确认**（`useLeaveWhileGenerating` + `LeaveWhileGeneratingDialog`）：生成期间用同 URL 的哨兵历史条目拦截浏览器后退 / 手机系统返回，弹与站内导航同一个确认框，「离开」才真正后退，「继续等」后重新放哨兵；这一轮结束时移除哨兵。生成期间同一页面内的跳转（只改 query / hash）在哨兵在顶时用 replace 顶替哨兵、再放一个新哨兵，哨兵始终在最上面，结束时移除的正是它，不会多留一格「按了没反应」的后退。确认框里「继续等」是主按钮，「离开」是次要按钮；已写出内容时文案为「已经写出的内容会保存，这一轮计入今日 AI 消息」，还没写出时为「还没写出内容的话，这一轮不计入今日 AI 消息」，Pro 不提每日条数。确认框打开期间这一轮结束：确认框保留，改为「这一轮已经结束 / 现在离开不会中断任何内容」，作者点「离开」照样离开。站内跳转确认离开时用 replace 顶替哨兵，不在历史里多留一格。
- **停止按钮准备期**：1.5 秒内按钮外观与禁用的发送按钮一致（灰底），`title` / 朗读为「正在开始，稍后可停止」，用 `aria-disabled` 而不是 `disabled`，点击时弹同样的提示；「正在停止…」期间仍禁用。
- **离开后的额度**：生成中卸载聊天面板（离开、后退、切项目）时，在 0.7、1.8 和 5 秒后让额度查询失效并重新拉取（含当前不活跃的查询），Dashboard 和项目页的额度标签不用整页刷新就能更新；首页每次挂载也重新读一次额度（`useAiMessageQuota` 的 `refetchOnMount: "always"`，只作用于首页这一个查询观察者）；额度标签自身 60 秒轮询兜底。

## Alternatives considered

- **停止 / 断线只看 `write_succeeded`，正文一律不算。** 最强理由：规则最简单，和 runaway 规则同口径，旁白长短无从争议。被否：分析、问答这类不写文件的请求，作者已经读到一大段回复再停，这段回复本身就是产出，照退会被反复白看。
- **只按审计第一建议改 `produced_output or write_succeeded`，保留「任何非空正文」。** 最强理由：改动最小，只修 parallel_execute 误退。被否：P1-2 的主要来源就是旁白与空章节，这样修完作者照样「说了一句话就扣一条」。
- **按「正文之后紧跟 tool_call」识别旁白，不用长度阈值。** 最强理由：语义更准，长段落后接工具调用也能区分。被否：停止常落在旁白之后、tool_call 帧之前，此时无法判断；工具调用前的长段说明（先列出思路再读文件）确实是产出；长度阈值前后端能用同一个数字。
- **空占位文件不删，在文件树和气泡里标「空章节 · 已停止」。** 最强理由：不动作者的文件树。被否：要给文件加新状态字段并改文件树；作者仍得自己删；移除只针对本轮刚建、落库前再校验为空、没有子节点的文件，风险可控，且会明确告诉作者。
- **离开拦截迁移到 `createBrowserRouter` + `useBlocker`。** 最强理由：官方 API，能拦多级后退。被否：要把整个应用迁到数据路由，影响所有页面和测试，不在本批范围；哨兵条目覆盖了最常见的单步后退和手机返回手势。
- **全局开启挂载时重新拉取（改 QueryClient 默认的 `refetchOnMount`）。** 最强理由：任何页面回来都是最新值。被否：所有查询都会多打请求。改为只在首页的额度查询上开启（见上），加上离开后的三次定时失效。
- **退还时作者正打开、且编辑器里有没保存的字的空白文件，前端重建一份。** 最强理由：服务端已经移除后也能把字留下。被否：编辑器切换文件前会先保存，旧文件已删除时保存失败会把选中改回旧文件，要改编辑器的切换流程；停止前先保存已覆盖作者在那一章打字后点停止的主路径。

## Consequences

- 收益：「只想过、读过、只说了一句、只建了空章节就停」不再占作者一条额度，也不留空章节；并行写章的一轮按写入计费；刷新、重进后能看到这一轮停在哪、算没算；反馈和时间戳绑在正确的消息上；后退和手机返回不再悄悄中断生成；离开后额度标签自己更新；重发带上原来的技能和附件；失败后不再出现空气泡和重复的用户消息。
- 代价：停止路径的 `done` 最多晚 3 秒（通常几百毫秒），「正在停止…」可见时间变长；停下的空轮也多一条空助手消息（`message_count` +1、占历史窗口一格，模型回放时空正文被剔除）。
- 代价：60 字阈值下，一段不足 60 字、还没收尾的短回答被停止时会退还（对作者有利）。墙钟时限、出错路径上只读、旁白后调工具、只建空章节的一轮由计费改为退还，扩大了退还面（只受每日成本兜底约束）。`update_project` 只改 `current_phase` 不算产出，模型显式改进度的一轮被停止时会退还。前端 `agentRoundProgress` 还不认 `parallel_task_end`：并行批次中途断开时，前端本地判断可能比服务端保守（只影响断开后是否请求建议），计费以服务端为准。断线一轮的终态在后台写入，作者立刻回到项目时可能还看不到。历史里「未计入今日 AI 消息」隔天仍写「今日」。
- 代价：空占位只在被退还的一轮里移除；计费的停止轮（例如第 2 章已写好、第 3 章刚建好）里刚建的空章节仍保留。移除与后台补存 `<file>` 残稿之间理论上有极小的竞态：若残稿尚未发出任何 `file_content` 帧，它可能写进已移除的文件。
- 代价：停止前先保存编辑器，「正在停止…」最多晚 1.5 秒出现（没有未保存的字时几乎立即）。保存后到服务端再查之间（通常不到一秒）又打的字仍可能赶不上：那时文件已被移除，编辑器保存失败会提示并留在原文件上，字还在编辑器里，但文件不在文件树中。离开确认之外的断线（关标签页、刷新、网络断开）没有先保存这一步，编辑器的最后一次保存与服务端结算同时发生，同样可能赶不上（刷新/关页时草稿另存本地快照）。生成期间同一页面内的 replace 跳转之后，哨兵下面会多留一个同页条目（后退一次回到同一页的旧 query）。
- 代价：后退拦截依赖哨兵历史条目：长按后退一次跳多级、浏览器把没有用户激活时推入的条目当作可跳过（例如首页想法自动发送的第一轮）时拦不住；`beforeunload` 文案仍由浏览器决定。
- 代价：「这条消息没有收到回复」的重新发送只带文字（刷新后原始技能、附件不可得）；`retry_unanswered` 先删末尾用户消息、本轮结束再写回，进程在两者之间崩溃会丢这一条历史。

## Verification

- 后端：`cd apps/server && venv/bin/python -m pytest tests/test_agent/test_stop_output_definition.py tests/test_agent/test_round_outcome.py tests/test_api/test_agent_stream_hardening.py tests/test_agent/test_service.py tests/test_agent/test_stream_launch_hardening.py tests/test_api/test_agent.py -q --no-cov -n0`：旁白、空 `create_file`、只读不算产出，真正正文、流式正文、改了文字的编辑、并行写章算产出（停止与断线两条路径）；停止后 `done` 带助手消息 id 与 `produced_output`；被退还时只移除本轮新建的空文件并在 `quota_refunded` 里列出，别的空文件不动；终态写回 `stop_outcome`；断线在后台结算并写回；不带 `X-Client-Stop-Contract` 的旧标签页断线照样退还，但不写 `client_disconnected`、不移除空章节、不写终态；空停止轮也落一条助手消息；`retry_unanswered` 只删末尾未回复的同样消息。`done` 之后才断开按完成结算、不写终态；停止后退还排队时请求被取消，退还照样完成、终态记未计入、空章节照样移除；被打断的并行批次里已完成的写章子任务、改了简介的 `update_project` 算产出；短回答之后出错 / 超时计费，只读后出错 / 超时退还。`tests/test_agent/test_round_outcome.py`：`remove_empty_placeholders` 的每一道校验（已有正文、别的项目、有子节点、文件夹、已删除）各一条；`record_round_outcome` 保留可「继续」的 `stop_reason`；`wait_message_id` 不吞取消。
- 前端：`pnpm --dir apps/web exec vitest run src/components/__tests__/ChatPanel.mount.test.tsx src/components/__tests__/ChatPanel.newSessionLifetime.test.tsx src/components/__tests__/RoundEnd.test.tsx src/hooks/__tests__/useLeaveWhileGenerating.test.tsx src/hooks/__tests__/useQuotaRefreshAfterLeave.test.tsx src/hooks/__tests__/useAiMessageQuota.test.tsx src/hooks/__tests__/useAgentStream.test.ts src/lib/__tests__/agentApi.test.ts src/lib/__tests__/agentRoundProgress.test.ts src/components/__tests__/MessageInput.test.tsx`：每个 `/agent/stream` 请求都带 `X-Client-Stop-Contract: 1`；重发带原技能与附件、同 tick 双击只开一轮、移除的空白文件写进说明并刷新文件树、无产出停止不请求建议、停止轮只按 id 绑定、失败不留空气泡、历史终态与未回复重发、浏览器后退先确认、这一轮结束时保留离开意图、离开后刷新额度、停止中直到 `done` 才结束、准备期按钮的说明与反馈；停止前先保存编辑器且保存期间不发第二次停止、Pro 的退还说明不提每日条数、额度未加载时终态行不提每日条数、同页跳转后后退不多一格、首页挂载时重新读额度；`update_project` 只在改了作品名、简介、文风或备注时算写入（与服务端同口径）。
