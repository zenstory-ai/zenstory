# Agent Note: 重复读取无进展即停、上限前软着陆、失控停止不计费

Status: implemented

本 note 部分推翻了 `feature/2026-10-05-agent-stream-runtime-budget-and-heartbeat.md` 里「不直接调低 `AGENT_TOOL_CALL_MAX_ITERATIONS` / `AGENT_COLLABORATION_MAX_ITERATIONS`」的取舍，以及 `architecture/2026-10-05-agent-stream-error-and-refund-contract.md` 里「有产出就计费」在失控停止场景下的口径；两篇 note 的其余决定不变。

## Problem

一位付费用户的 agent 连续两次把同一批文件反复 `query_files` 到单 agent 100 轮上限（`AGENT_TOOL_CALL_MAX_ITERATIONS`）；用户回「继续」后，路由重新规划多 agent 流程，又从头读了一遍，最后只能人工退款。10-03 以来约三分之一的 agent 轮次里同一文件被读了 4 次以上，这些轮次花掉了 69% 的 agent 费用。

- **熔断器看不见。** `ToolFailureBreaker` 只数失败；30 次成功的同一读取在它眼里都是进展。
- **撞上限时什么也没留下。** `MaxTurnsExceeded` 时 runner 只发一张 `ITERATION_EXHAUSTED` 卡片，`iterations_used` 写死为上限值，`MESSAGE_END.stop_reason` 仍是 `end_turn`，模型没有机会总结做了什么、还差什么。
- **上限按「防死循环」设得很宽。** 100 轮 × 30 次协作 × 每轮重发整段上下文；请求级 200 次调用的预算也只扣 1 次额度。
- **writer ↔ 审稿人没有硬上限。** 只在第 3 轮起给审稿人一句「追更指数达到 5 分以上即可通过」——它与审稿人提示词的通过标准矛盾（见 `tests/test_agent/test_writing_guidance_contract.py`），也挡不住来回。
- **交接后从头再读。** 计划交接与自动质检的 `handoff_packet` 写死 `completed: []`、没有 `artifact_refs`；`normalize_messages_for_openai_agents` 回放时丢掉工具结果，下一个 agent 不知道本请求读过什么、改过什么。
- **原地打转照样扣费。** 打转时模型会边读边念叨，串流过文字就算「有产出」，`StreamBillingTracker` 按 `completed` / `error_after_output` 计费。

## Decision

**重复读取守卫（`agent/openai_agents/repeat_read_guard.RepeatReadGuard`）**

- 按一次请求记账：`writing_graph` 开头建一个放进 `state["repeat_read_guard"]`，runner 取同一个（没有时自建），交接不清零。键为 `(query_files, 文件 id, 归一化读取模式)`；模式是 `full`（`response_mode=full` 或 `include_content` 为真）或 `summary:<content_preview_chars>`。直接调用的 `query_files(id=…)` 与 `parallel_execute` 里 `type=="query_files"` 且 `params.id` 非空的子任务记在同一个键上。
- 只有真正拿到内容的读取才计数：结果 `status=success` 且 `data` 非空（文件不存在、报错、被截断成 overflow 引用都不算）；并行子任务结果被 `_bound_task_result` 截成预览时仍计数，但不记进「已读取全文」台账。
- 同一键第 2 次成功读取：照常返回，结果 JSON 附 `read_hint`「该文件全文本轮已读取过且之后未被修改，请直接使用上文内容。」（摘要读取用对应文案）与 `repeated_reads`；第 3 次：附以「警告」开头、说明下一次会被拒绝的提示；第 4 次起：不执行，返回 `error_type="repeated_read"` 的可恢复错误，点名文件《标题》(id) 并要求停止读取、直接产出或向用户说明缺什么。`parallel_execute` 里被拦下的子任务从 `tasks` 中剔除后执行其余子任务，结果附 `repeated_read_blocked`；整批都被拦下时整次不执行。同一批里重复的读取同样按次序计数。
- 写入清零：`create_file` / `edit_file` / `delete_file` 成功（`edit_file` 的 `all_failed` 不算），或 `parallel_execute` 的 `write_chapter` / `edit_file` / `delete_file` 子任务 `completed`，清零该文件 id 的全部读取计数，并记入「已修改」台账；`write_succeeded` 记录本请求是否有任何写入成功。
- 一次请求内被拦下的读取累计 `MAX_BLOCKED_READS_PER_REQUEST`（3）次即判定无进展（`trip`）。之后同一轮剩下的工具调用一律不执行（`error_type="no_progress_stop"`）。
- 接线与熔断器相同：`tools_adapter.build_agent_function_tools(read_guard=…)` 的 FunctionTool 回调里先 `plan()`（决定拦下 / 改写子任务）再执行再 `observe()`；`runner._stop_run_on_control_flow_tool` 在熔断检查之后检查 `read_guard.trip`，把本轮当作 final output，SDK 不再发起下一次模型调用；消费循环在 `tool_output` 处 `cancel(after_turn)` 兜底。熔断器对 `repeated_read` / `no_progress_stop` 不记账。
- 对外：runner 先发 `MESSAGE_END(stop_reason="no_progress")` 让 usage 入账，再发 ERROR：`code=ERR_AGENT_TOOL_FAILURE_LIMIT`（沿用前端已有文案）、`error_type="NoProgressStop"`、`reason="no_progress"`、`retryable=false`、`refundable` = 本请求没有任何写入成功、`blocked_reads`、`duplicate_reads`。日志：停止时 `Repeated reads without progress; stopping agent run`（`blocked_reads` / `duplicate_reads` / `files_read` / `files_written` / `write_succeeded`），每次 run 的完成日志带 `duplicate_reads`、`blocked_reads`、`stop_reason`；指标计数器 `agent.read.duplicate.total`、`agent.read.blocked.total`、`agent.no_progress.stop.total`。

**上限前软着陆（`runner._ModelCallCountingFilter`）**

- 过滤器在计请求级预算之外，也数本次 SDK run 的模型调用。第 `max_turns - SOFT_LANDING_TURNS`（3）次调用之后的每次调用，在内层过滤器（`IntraRunToolOutputTrimmer`）的结果 `input` 末尾追加一条 user 消息（`SOFT_LANDING_PROMPT`）：停止调用任何工具，写阶段总结——已完成、尚未完成、用到或改动过的文件（标题 + id）。不改 `instructions`，提醒只进本次调用、不写回 run 历史。
- 真正撞上 `max_turns`，或已进入收尾区间且本次 run 没有以交接 / 澄清结束（模型照提醒写了总结），都按轮数耗尽收尾：发 `ITERATION_EXHAUSTED(layer="tool_call")`，`iterations_used` 为本次 run 的实际模型调用数；再发 `MESSAGE_END(stop_reason="max_turns_exceeded")`（随 assistant 消息落库）。工作流照旧在 `tool_call_exhausted` 处结束，不再计划交接或自动送审。

**上限（`config/agent_runtime.py`，仍可用同名环境变量覆盖）**

- `AGENT_TOOL_CALL_MAX_ITERATIONS` 100 → 60；`AGENT_COLLABORATION_MAX_ITERATIONS` 30 → 12（full 工作流 3 个 agent + 2 轮审查与返修 + 空文件纠偏仍放得下）；`AGENT_RUN_MAX_MODEL_CALLS` 200 → 120。注释改为「这些上限就是成本控制」。删除只剩一处测试在用的 `AGENT_MAX_ITERATIONS` 别名（含 `agent/service.py` 的再导出）。

**审查轮数上限（`writing_graph.MAX_REVIEW_ROUNDS = 2`）**

- `review_round` 达到 2 后：自动质检门不再触发；writer 显式送审被丢弃；审稿人交回有写权限的 agent 返修时不再交接，而是追加一段 TEXT（「已完成 2 轮质量审查……审稿人仍建议修改：<交接 context + todo>」，随正文落库）并发 `WORKFLOW_COMPLETE(reason="review_round_limit", review_notes=…)` 结束。第 2 轮审稿人收到「这是最后一轮、之后不会再自动返修」的提示；删除第 3 轮起的「追更指数达到 5 分以上即可通过」，通过标准只由审稿人提示词决定。

**交接带上本请求的读写记录**

- 每次带交接信息进入下一个 agent（显式交接、计划交接、自动质检、steering 追加轮、纠偏轮）时，`user_message` 追加 `[本请求的读写记录]: 本请求已读取全文：《X》(id=…)、…；本请求已修改：《Y》(id=…)…`（`RepeatReadGuard.handoff_summary()`，只有标题与 id，不带正文）。计划交接与自动质检的 `handoff_packet.completed` 填「已修改《Y》(id=…)」，`artifact_refs` 填本请求写过的文件 id。

**失控停止不计费（`agent/core/stream_billing.StreamBillingTracker`）**

- 新增 `runaway_stop`（error 帧 `reason="no_progress"` 或 `code=ERR_AGENT_MODEL_CALL_LIMIT`，或 `iteration_exhausted` 帧 `layer="tool_call"`）与 `write_succeeded`（写工具 `tool_result` success、`parallel_execute` 的写子任务 completed、非空 `file_content`）。判定顺序在用户取消、墙钟时限之后：失控停止且没有写入 → `("runaway_no_progress", 退还)`，不看是否串流过文字。`ErrorEventData` 新增可选 `reason`，`StreamAdapter` 透传；计费日志增加 `write_succeeded`、`runaway_stop`。

**「继续」直达上一个 agent（`router.resume_route_after_exhaustion`）**

- 用户消息去掉首尾空白与句末标点后属于固定的「继续」集合（含前端轮数耗尽卡片「继续」按钮预填的中英文提示），且上一条 assistant 消息 `stop_reason` 属于 `RESUMABLE_STOP_REASONS`（`max_turns_exceeded` / `no_progress` / `model_call_budget_exhausted` / `run_deadline_exceeded`），或它的状态卡合成文本含 `[iteration_exhausted]` + `layer: tool_call`（旧数据）时，`writing_graph` 跳过 LLM 路由，直接以 quick 工作流交给上一轮的 agent（审稿人则为 review_only），`routing_metadata.reason="resume_after_exhaustion"`。上一轮的 agent 与 read_only / scope / write_content 从落库的路由里取，见 `2026-10-08-agent-routing-continuity.md`。

## Alternatives considered

- **扩展熔断器，让它也数「同一调用成功」**。最强理由：一个机制、一个出口，不需要新模块和第二个请求级对象。被否：熔断器的键是「工具名 + 完整参数」，并行子任务、`include_content` 与 `response_mode` 的等价写法、中间写入后应当清零的是「同一文件」而不是「同一调用」；熔断的停止语义是「工具失败、不退款」，和「模型原地打转、没改任何东西」的计费口径相反。
- **重复读取直接返回缓存内容，不拦**。最强理由：对模型透明，不会因为拦截误伤确实需要重看的场景。被否：成本来自每轮重发的输入 token，缓存只省数据库查询；模型拿到同样的内容会继续同样的循环，什么也挡不住。
- **软着陆提醒写进 `instructions`（系统提示）**。最强理由：这是 `ModelInputData` 给的正式字段，模型对系统提示更敏感。被否：DeepSeek 按前缀命中缓存，系统提示在最前面，改它会让最后几次调用整段缓存失效；追加在输入末尾的 user 提醒同样有效。
- **无进展停止发 `ITERATION_EXHAUSTED` 卡片而不是 ERROR**。最强理由：状态卡会落库、带「继续」按钮，也不用给 SSE error 帧加字段。被否：卡片文案写死为「工具调用轮数达到上限（N 轮）」，在第 6 次调用就停时是假的；而且无进展停止要与熔断走同一个同步出口、终止整条工作流，ERROR 正是这条路径。
- **为无进展新增错误码 `ERR_AGENT_NO_PROGRESS`**。最强理由：前端能显示准确文案。被否：要同时改 `core/error_codes.py` 与前端 `errors.json`，超出本次修复的范围；现有「AI 反复调用工具失败，已停止本轮生成」足够接近，计费所需的区分由 `reason` 字段承担。
- **只靠守卫，不调低上限**（`2026-10-05` 两篇 note 的立场）。最强理由：上限调低会误伤合法的长任务，守卫已经挡住主要的循环。被否：守卫只认得「同一文件同一模式」的重复，换着读不同文件、反复 `hybrid_search` 的打转仍会一路跑到上限；一次请求只扣一次额度，上限本身就是成本边界。软着陆让撞上限时留下总结和「继续」入口，调低的代价变小了。
- **失控停止仍按「有产出就计费」**（`2026-10-05` 计费 note 的口径）。最强理由：口径统一，不给用户「故意打转换退款」的空间。被否：打转时模型总会串流几句「我再看看第一章」，按现口径几乎不可能退款，付费用户为一个什么都没改的请求付费；可利用的空间受 60 / 120 次上限与「有任何写入就计费」约束。
- **审查轮数保留软提示、不设硬上限**。最强理由：让审稿人自己判断何时放行，质量更有保障。被否：软提示（还与审稿人提示词矛盾）挡不住来回，协作上限调到 12 后更容易被审查往返吃光；两轮之后把剩余意见交给用户决定更可控。

## Consequences

- 收益：同一文件最多真正读 3 次（写入后重新计数），连续打转在约 6 次调用内停下；撞上限前模型有 3 次机会写阶段总结，用户看到的是「做了什么 / 还差什么」和「继续」入口，「继续」不再重新规划、从头再读；审查往返最多 2 轮；下一个 agent 知道本请求读过 / 改过哪些文件；没有改动任何文件的失控请求退还额度。
- 代价：守卫说「内容就在上文」依赖 `IntraRunToolOutputTrimmer` 没把旧的读取结果压成预览——若压掉了，模型被拦下后只能向用户说明缺什么（trimmer 另行调整）。合法的「同一模式第 4 次读同一个没改过的文件」会被拒绝（摘要与全文分键，不互相占次数）。进入收尾区间后自然结束也按轮数耗尽处理：即使模型恰好在最后几轮完成了任务，也会显示耗尽卡片并停止送审。上限调低后，超长的多文件写作更可能在一轮内做不完、需要「继续」。失控停止退款带来可利用空间：只读分析类请求若跑到上限且没写文件会被退款（上限内的 token 成本由平台承担）。无进展的 ERROR 不像状态卡那样落库。「继续」识别是固定集合精确匹配，「继续写第四章」这类带新要求的消息仍走路由；上一轮的 agent 与范围依赖 `message_metadata.routing`，这个字段上线前的旧消息在有正文时仍取不到 agent，回落到 writer。耗尽卡片的 `max_iterations` 仍是上限值，`iterations_used` 才是实际调用数。
- `ErrorEventData` 多了 `reason` 字段，熔断的 `reason`（如 `repeated_identical_tool_failure`）也会出现在 SSE error 帧里。

## Verification

`cd apps/server && .venv/bin/python -m pytest -n 0 --no-cov tests/test_agent/test_repeat_read_guard.py`：守卫的提示 / 警告 / 拦截 / 无进展、读取模式分键、失败读取不计数、写入清零（含失败写入不清零）、并行子任务共享键与剔除、同批重复、并行写入清零、交接摘要只含标题与 id、熔断器不为拦截记账、FunctionTool 不执行被拦下的读取且判定后短路写工具、`tool_use_behavior` 截停、软着陆过滤器只在最后 3 次调用追加提醒且不改系统提示；用本地 OpenAI 兼容端点驱动真实 SDK run-loop：模型始终重读同一文件时只执行 3 次读取、6 次模型调用后以 `MESSAGE_END(no_progress)` + `ERROR(refundable=true)` 结束；`max_turns=5` 时第 5 次请求带收尾提醒、`iterations_used=5`、`stop_reason=max_turns_exceeded`；模型照提醒写总结时同样以耗尽收尾；计费的 `runaway_no_progress` 各分支（有写入照常计费、熔断与协作耗尽不受影响）、`StreamAdapter` 透传 `reason`；「继续」直达与不触发的情形；writing_graph 第 2 轮审查后不再返修并展示审稿意见、交接信息与 packet 带读写记录、「继续」跳过 LLM 路由。另跑了受影响的 `test_tool_failure_breaker.py`、`test_round3_runner.py`、`test_openai_agents_adapter.py`、`test_stream_launch_hardening.py`、`test_writing_graph.py`、`test_round3_graph.py`、`test_graph_router.py`、`test_writing_guidance_contract.py`、`test_parallel_steering_rework.py`、`test_stream_adapter.py`、`test_service.py`、`test_round3_history.py` 与 `tests/test_api/test_agent.py`、`test_agent_stream_hardening.py`、`test_round3_agent_api.py`、`test_round3_converge.py`（均以 `-n 0` 串行运行）。
