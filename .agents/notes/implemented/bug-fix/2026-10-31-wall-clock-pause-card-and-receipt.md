# Bug Fix Note: 时限收尾与墙钟硬超时都以暂停卡和继续入口结束

Status: implemented

关联：[接近请求墙钟时限时收尾](2026-10-21-wall-clock-wrap-up.md)（本 note 补上它第 3 条缺的状态卡与继续入口）、[作者停止走服务端收尾](../architecture/2026-10-09-agent-graceful-stop-and-no-output-refund.md)、[时限终止帧先于取消收尾](2026-10-14-deadline-terminal-before-cancellation-cleanup.md)、[时限截停的 stop_reason 由路由层交给取消补存](2026-10-25-deadline-stop-reason-from-route.md)、[请求未完成事实](2026-10-26-request-incomplete-delivery.md)。

## Problem

10-21 的收尾承诺「以阶段总结和继续入口结束」，但有三条路径没有做到：

- **图层收尾**（`writing_graph` 循环顶部 `past_wrap_up`，日志「not starting another agent run」）只发一句固定文案就 `break`。每个 run 都是 `end_turn` 结束，这一轮于是记成 `stop_reason=end_turn`，也没有状态卡。带收尾文案的回复都是 `cards=[]`，前端不显示继续按钮，作者只能手打「继续」；路由的 `RESUMABLE_STOP_REASONS` 里没有 `end_turn`，作者的「继续」也接不上。
- **审稿后待返修**：审稿人交回 writer 时撞上收尾，返修 writer 不再开跑。审稿要点只在交接包里（工具结果不跨轮回放），作者看不到；下一轮的 writer 也拿不到。
- **墙钟硬超时**（`api/agent.py` 的 `SSEStreamPump` 时限）一律发通用超时错误卡，终态记 `error`。其中不少其实已经写进了文件，作者看到的是「出错了」，而不是「做到一半、已保存」。

另外，runner 把三种耗尽（墙钟收尾、请求级模型调用预算、真正的 max_turns）都记成「Agent run reached the tool-call turn cap」，326 条里 325 条其实是墙钟收尾，看不出真正撞上 max_turns 的有多少。

## Decision

1. **图层收尾发与 runner 时限收尾同一张暂停卡。** 循环顶部的收尾分支照旧先发收尾说明（今日额度用完时不叫作者回复「继续」），然后发 `ITERATION_EXHAUSTED(layer="collaboration", limit="wall_clock", last_agent=<下一个本该运行的角色>)`。停在 writer 交给审稿人之前（自动质检、writer 自己送审或计划里的审稿阶段）时不发这张卡：正文已经交付，只是质量检查没跑；续进审稿人的回合没有质量检查任务包，「继续」会换来一份审稿报告而不是接着写，所以这种收尾照改动前一样按正常结束记录。这时的收尾说明也不叫作者回复「继续」，只说正文已经写好、这一轮没来得及做质量检查（`WRAP_UP_TEXT_REVIEW_SKIPPED` / `_EN`）。层用 `collaboration`，不用 `tool_call`：`tool_call` 属于计费的失控停止信号（`RUNAWAY_ITERATION_LAYERS`），图层收尾的计费仍按正常结束判定。`limit` 的取值抽成共享常量 `agent.core.events.WALL_CLOCK_LIMIT`，runner 的 `LANDING_LIMIT_WALL_CLOCK` 就用它。
2. **时限暂停卡决定这一轮的 stop_reason。** `StreamAdapter` 收到 `limit=wall_clock` 的 `ITERATION_EXHAUSTED` 时，把这一轮记成 `run_deadline_exceeded`（runner 的时限收尾本来就是这个原因，这里只补上图层收尾）。状态卡随历史落库，`session_loader` 读卡上的 `lastAgent`，路由的 `resume_interrupted_task` 据此回到卡上的角色。审稿后待返修时 `last_agent` 是 writer，「继续」回到返修，不会停在最后说话的审稿人。
3. **审稿要点写给作者。** 审稿人交回有写权限的角色、因收尾不再开跑时，用达到审查上限时同一个 `_review_notes_from_packet`（交接 context 与 todo），经 `author_facing_text` 清理后写进回复（「审稿人的修改意见（这一轮还没来得及改）：…」，英文界面用英文）。它随回复落库，下一轮「继续」的 writer 在历史里看得到。writer 被送审时丢掉的那份总结不补发：它本来就要被审稿人的回复取代，交付事实由下面的结构化回执给出。
4. **墙钟硬超时且本轮已经写进文件时，发暂停卡代替通用错误卡。** `on_deadline` 在 `tracker.file_written` 为真、且不是发布交接提前的时限时，返回「暂停卡 + done」两帧（`SSEStreamPump._stop_for_deadline` 接受一个帧或一组帧，仍在等收尾之前发出，10-14 的「终止帧先于收尾」不变）。暂停卡 `layer=collaboration`、`limit=wall_clock`、`last_agent` 为最近一次 `agent_selected` 的角色；done 带 `delivery_incomplete: true`，只列已确认写入的文件。这时残稿补存还没落定，没收到保存回执的流式正文既不说已保存，也不说未保存。残稿补存落定后，终态记 `incomplete`，`stop_outcome` 按最终保存回执列文件，`record_round_outcome(pause_card=...)` 把同一张卡追加进 `status_cards` 和已有的 `display_events` 末尾，刷新后作者看到同一张卡和继续按钮。没有任何写入的超时、发布交接截停都保持原来的错误卡。卡上的 `reason` 会经 `session_loader` 回放进下一轮模型历史，按界面语言（Accept-Language）选 `DEADLINE_PAUSE_REASON` 或 `DEADLINE_PAUSE_REASON_EN`，与图层收尾的中英文一对一致。暂停卡和 done 已经送到、收尾还在进行时前端断开（点卡上的「继续完成」或发下一条消息会中止旧连接，离开页面也一样），不算断线：不写 `client_disconnected` 的 stop_kind，残稿补存保留可续做的 `run_deadline_exceeded`，计费仍按墙钟超时判定；收尾落定后同样记 `incomplete` 并把暂停卡写进历史（收尾越过断开时在后台结算之后再写）。图层 / runner 的时限收尾卡已经流出、done 之前撞上硬超时时，只补 done，不发第二张卡，历史里也不追加，避免刷新后出现两组继续按钮。消费方（前端文案、`StreamAdapter` 的 stop_reason、继续路由）一律按 `limit=wall_clock` 认这张卡，不看 `layer`；`iterations_used` 是本轮已开始的 agent run 数（与图层收尾卡的协作轮次同一口径），`max_iterations` 是协作轮次上限，只作参考。
5. **交付回执不按字数判断。** 不按「总结少于约 120 字」决定要不要给回执（字数启发式不能判断是否完成）：凡是未完成的一轮列得出已保存的文件，前端 `RoundEndNote` 就列出文件名、字数和部分修改未生效标记（与停止 / 出错 / 正常完成的回执同一个 `formatRoundDelivery`），后面仍跟一项「本轮尚未完成，可以继续」（今日额度用完时只写「本轮尚未完成」，不说可以继续；作为 `·` 列表的一项不带句号）：空文件纠偏的状态卡在这类轮次里不显示，今日额度用完时也没有继续按钮，不说这一项就和正常完成的回执一样。这只在 `saved_output` 为真时成立；列出了文件却没有确认保存（或列不出文件）时，仍显示原来的「已保存本轮完成的修改，可以继续」/「本轮尚未完成，可以继续」，不会出现空白的灰色说明。今日额度用完时这两句也去掉「可以继续」，见[断线回执与额度用完的说明](2026-10-31-first-turn-summary-and-delivery-feedback.md)。
6. **观测**：runner 的三种耗尽分开记，分别是「Agent run soft-landed near the wall-clock limit」（带 `elapsed_s`、`server_draining`）、「Agent run soft-landed at the request model-call budget」和「Agent run reached the tool-call turn cap」。墙钟硬超时的告警带 `paused_with_saved_output`。
7. iteration_exhausted 落库成状态卡的结构抽成 `agent.core.events.iteration_exhausted_status_card`，`service.py` 与 `record_round_outcome` 共用。

## Alternatives considered

- **把 `end_turn` 加进 `RESUMABLE_STOP_REASONS`。** 最强理由：只改一行，所有收尾轮都能续做。没采用：正常完成的一轮也是 `end_turn`，作者下一句「继续写下一章」会被当成恢复中断任务、继承上一轮的范围；前端也照样没有暂停卡和按钮。
- **图层收尾发 `layer=tool_call`，与 runner 时限收尾完全同形。** 最强理由：卡片和统计口径完全一致。没采用：`tool_call` 层会触发计费的失控停止规则（没有写入就退还），改变图层收尾现有的计费判定；前端文案只看 `limit`，层不同不影响展示。
- **硬超时先发暂停卡，等残稿补存交回消息 id 后再发 done，并列出最终保存的文件。** 最强理由：实时回执与刷新后一致，不用等刷新。第一版实现就是这样，被 10-14 的回归测试拦下：收尾可能越过平台 900 秒断连，终止帧必须在等收尾之前送达。最终保存的文件改由刷新后的 `stop_outcome` 列出。
- **硬超时有写入时按正常完成结算（done 交给计费记账）。** 最强理由：卡片说「做到一半、已保存」，计费说「完成」，看上去更一致。没采用：会把计费原因从 `run_deadline_exceeded` 改成 `completed`，还会写一次「首个成功回复」激活事件，超出本批次不改计费的范围。这条 done 不交给 `StreamBillingTracker.observe`，只记「已送达终止帧」。
- **总结短于阈值时由服务端拼一段回执文字放进回复。** 最强理由：模型上下文和回复里都有交付事实。没采用：靠字数判断「总结缺失」属于被禁止的启发式；回执已经有结构化来源（`written_files` / `stop_outcome`），由前端统一渲染即可，不往模型回复里塞固定文本。

## Consequences

- 收益：图层收尾、runner 时限收尾和有写入的墙钟硬超时三条路径，作者看到的是同一张「这一轮先做到这里」卡和「继续完成」按钮，刷新后也一样；「继续」能结构化恢复，审稿后待返修会回到 writer，并且历史里留有审稿要点。已经写进文件的硬超时不再显示成失败。
- 代价：图层收尾的回复里既有「回复『继续』我接着做」这句话，又有暂停卡上的按钮，提示略有重复（保留这句话是为了今日额度用完时的不同说法，以及模型的历史上下文）。硬超时暂停卡上的 done 不等残稿补存，作者当场看到的回执可能少于刷新后（流式正文的保存回执还没到）。发布交接截停仍是「服务重启」错误卡。
- 计费与退款判定不变（记录，不改）：图层收尾按正常结束计费；硬超时仍按墙钟超时规则（`billing_reason=run_deadline_exceeded`，有写入即计费），只在已有写入时才换成暂停卡，所以暂停卡出现的轮次总是计费的，卡片与计费不冲突。runner 的时限收尾仍是 `layer=tool_call`，没有写入时按失控停止退还；图层收尾用 `collaboration` 层，没有写入也计费，两条时限路径的计费口径仍不一致，这是现状，留给后续的续做计费决策统一。
- 回归：`test_no_new_agent_run_starts_near_the_wall_clock_limit`、`test_wrap_up_after_review_resumes_the_writer_with_the_review_notes`、`test_graph_wrap_up_card_saves_a_resumable_stop_and_continue_returns_to_the_writer`、`test_wall_clock_pause_card_records_the_resumable_deadline_stop`、`test_sdk_run_soft_lands_before_the_wall_clock_limit`（告警分开记）、`test_deadline_round_records_an_interrupted_outcome_after_capture_settles`（有写入 → 暂停卡 + incomplete + 历史卡；无写入不变）、`test_deadline_terminal_precedes_edge_even_when_cleanup_is_pending`（暂停卡与 done 同样先于收尾）、`test_server_drain_deadline_keeps_the_restarting_error_even_after_writes`、`test_deadline_pause_follows_the_interface_language_and_never_doubles_the_card`（英文界面的 reason、已有时限卡时不补第二张）、`test_disconnect_during_deadline_pause_cleanup_keeps_the_resumable_pause`（暂停后收尾中断开仍可续做、历史有卡）；前端 `RoundEnd.test.tsx`、`MessageList.test.tsx`、`chatDisplayEvents.test.ts`。
