# Agent Note: 同一工具调用重复失败时熔断，终止本轮并退还额度

Status: implemented

熔断后的计费已改为不退还额度，见 `architecture/2026-10-05-agent-stream-error-and-refund-contract.md`；熔断机制本身不变。

## Problem

单个 Agent 的 SDK run 只有 `max_turns`（`AGENT_TOOL_CALL_MAX_ITERATIONS`，默认 100 轮模型调用）兜底；`AGENT_COLLABORATION_MAX_ITERATIONS`（30）数的是 Agent 交接次数，不是工具调用。SQLite 写锁不释放时，writer 每隔约 37 秒原样重发一次失败的 `edit_file`，持续十多分钟，每轮都在烧 token，用户只能看着转圈。任何「工具稳定失败 + 模型原样重试」的组合都会这样，与失败来源无关。

失败还会跨 agent 延续：writer 的写入全部失败后，自动质检门照样按字数把 quality_reviewer 拉进来，审稿人发现正文没落库又把 writer 叫回来重试同一个写入。真实强制复现里一次请求跑了 3 轮 writer、13 次失败的 `edit_file`、约 108 秒、41 万 token——如果熔断器每个 SDK run 各建一个，每次交接都会让计数归零。

## Decision

`agent/openai_agents/tool_failure_breaker.py` 的 `ToolFailureBreaker` 按**一次用户请求**记账：`writing_graph.run_writing_workflow_streaming` 开头建一个放进 `state["tool_failure_breaker"]`，`runner.run_openai_agents_streaming_agent` 从 state 取同一个（没有时自建，供单独调用 runner 的场景），writer → quality_reviewer → writer 的往返不会让计数归零。按工具结果本身记账：

- 失败判定：工具输出是 `{"status": "error", ...}` 的 JSON（项目工具统一的报错格式）；其余 status 与非 JSON 输出都不算失败。唯一例外是 `parallel_execute`：它无论子任务成败都返回 `status=success` 外壳（逐任务明细在 `data.tasks`），因此 `data.any_failed` 为真即算一次失败，错误指纹取各失败子任务「类型 + 错误」排序后的拼接；结果过大被截断成 overflow 引用时看不到明细，按成功处理。
- 同一调用 = 工具名 + 归一化参数（键排序的紧凑 JSON；解析失败用原文）。等价错误 = `error_type` + 抹掉 UUID、十六进制、数字并折叠空白后的错误文本（SQLite 报错里每次都变的时间戳参数不能让重试被当成「不同错误」）。
- 同一调用以等价错误连续失败 `MAX_IDENTICAL_TOOL_FAILURES`（3）次即熔断；同一调用换了错误则从 1 重新计数；同一调用成功则清零。穿插其他成功调用（例如重试之间 `query_files`）不清零。
- 兜底：本次请求累计失败 `MAX_TOOL_FAILURES_PER_REQUEST`（10）次即熔断，不要求参数/错误相同。
- 顺序性拒绝不按「同一调用连续失败」熔断：`error_type` 属于 `_ORDERING_ERROR_TYPES`（目前只有 `pending_empty_file_unwritten`）的失败只计入累计上限，不进连续计数、不附加通用提示。这类拒绝只要模型先完成错误里点名的前置动作就会成功；按连续规则熔断会让 ERROR 终止整条工作流，空文件纠偏轮也跑不到，只留下一个空文件（真实复现：planner 并行 `create_file` 两份人设，第二份被挡回后原样重试 3 次即熔断）。
- 第 1 次失败原样交还模型；同一调用第 2 次等价失败时，交给模型的原始输出 JSON 附带 `repeated_failures` 与 `retry_hint`（不要原样重试；系统侧故障请停下向用户说明；再失败几次将终止）。实测 deepseek-flash 看到提示后会自行停手，熔断只是兜底。

配套改动：

- `writing_graph` 的自动质检门只认「至少一次写工具成功」：writer 的 create_file / edit_file 调用数减去 TOOL_RESULT 为 `status=error` 的数目为 0 时，`writer_used_write_tools` 为假，不送审——稿子一个字都没落库，送审只会让审稿人把 writer 叫回来重试同一个失败写入。
- `agent/tools/mcp_tools.py` 的 `create_file` 空文件守卫：占坑失败时先落库核验挡路的待写入标记（`_clear_settled_pending_empty_files`），正文已写入（模型用 `edit_file(op=append)` 写完，标记不会被清）或文件已删就清掉标记再占一次；仍被挡回时返回 `error_type="pending_empty_file_unwritten"`、`pending_file_id`，错误信息点名解除办法（先用 `<file>…</file>` 或 `edit_file` 写完上一个文件，再创建新文件）。

接线：`tools_adapter.build_agent_function_tools(..., failure_breaker=)` 的 FunctionTool 回调在每次工具执行后同步调用 `observe()`；熔断后同一轮里剩下的调用不再执行（写工具不会在熔断后落库），直接返回 `tool_failure_circuit_open` 错误。只读请求下被拒绝的写工具调用（`error_type="read_only_request"`）走同一个回调，同样记账；同一调用连续被拒 3 次时熔断原因同样记为 `repeated_unavailable_tool_call`（调用并未执行，不套用"以相同参数连续失败、请稍后重试"的文案）。模型调用工具集里没有的工具时，runner 的 `RunConfig(tool_not_found_behavior="return_error_to_model", tool_error_formatter=runner._format_tool_error)` 把它变成交还模型的 `error_type="tool_not_found"` 结构化错误，formatter 里同样 `observe()`（参数记为空串，同名即同一调用；连续 3 次时熔断原因记为 `repeated_unavailable_tool_call`、文案说"连续调用本轮不可用的工具（调用均未执行）"，不套用"以相同参数连续失败"的文案）；这类调用没有 FunctionTool 结果，SDK 不会为它调 `tool_use_behavior`，熔断改由消费循环在 tool_output 处 `cancel(after_turn)` + 「新一轮即截断」护栏落地，最多多出一次模型请求。`runner._stop_run_on_control_flow_tool` 作为 `tool_use_behavior`（经 `functools.partial` 绑定本次 run 的熔断器）在 SDK turn 内同步检查，熔断即把本轮当作 final output，run-loop 不会再发起下一次模型调用；消费循环在 tool_output 处看到熔断后同样 `cancel(after_turn)` 并启用「新一轮即截断」护栏。

对外：runner 先发 `MESSAGE_END`（`stop_reason="tool_failure_circuit_open"`，带本 run 的真实 usage），再发 `StreamEventType.ERROR`，`data.error` 是给用户看的中文说明（工具名、失败次数、去掉 `[SQL: ...]` 细节后的最后一次错误摘要），另带 `error_type="ToolFailureCircuitOpen"`、`reason`（`repeated_identical_tool_failure` / `repeated_unavailable_tool_call` / `too_many_tool_failures`）、`tool_name`、`failures`、`threshold`。`StreamAdapter` 把 ERROR 当致命错误：中断并 `aclose` 整个工作流生成器（计划内交接、自动质检、空文件纠偏都不会再跑），以 `error` 帧作为 SSE 终止帧、不再发 `done`；`api/agent.py` 据此记 `internal_error` 并退还本次 AI 对话额度；`service.py` 照常落库部分历史（含失败的工具调用与 usage）。前端 `agentApi` 对非 `ERR_` 前缀的错误信息原样展示。

## Alternatives considered

- **发 `ITERATION_EXHAUSTED(layer="tool_call")`**。最强理由：writing_graph 已经把它当终止信号（`tool_call_exhausted` 后 break），前端有带「继续 / 拆分 / 手动」按钮的状态卡并会落库，零跨模块改动。被否：卡片标题与摘要写死为「工具调用轮数达到上限（N 轮）」，与「同一调用连续失败」是两回事，会误导用户去「继续」一个仍然失败的操作；且它按正常完成计费，不退额度，与其他运行失败（runner 异常 → ERROR → 退款）不一致。
- **发 `WORKFLOW_STOPPED` 并新增 reason**。最强理由：前端对非澄清 reason 已有通用的「工作流已停止」卡片，能展示 message 且会落库为状态卡。被否：writing_graph 只对 `clarification_needed` 停止循环，其他 reason 只是透传，之后仍会按计划交接、自动送审甚至重跑 writer；要可靠停下必须改 writing_graph，超出本次改动的模块边界。
- **每个 SDK run 各建一个熔断器**。最强理由：熔断器与 run 同生命周期，runner 自给自足，不需要 graph 传任何东西。被否：真实复现里每轮 writer 失败两三次就自行停手或被送审，交接后计数归零，整个请求的上限变成「30 次协作 × 每轮 3 次同样失败（最多 10 次失败）」，正是要堵的循环。
- **让顺序性拒绝也按连续规则熔断，只把错误信息写清楚**。最强理由：规则统一，模型连撞 3 次说明它没理解，停下也合理。被否：熔断走 ERROR，会把空文件纠偏轮一起终止，留下空文件；原来没有熔断器时模型多试一两次就能自行纠正，这条路径本身不烧写库，累计上限足以兜底。
- **只降低 `AGENT_TOOL_CALL_MAX_ITERATIONS`**。最强理由：一行配置，无新代码。被否：它限制的是所有任务的模型轮数，长篇多文件写作需要大量合法的工具轮；降到能挡住重试循环的程度会误伤正常任务，而且挡住之前仍会烧掉同样多的轮次。
- **用 SDK 的 `StopAtTools` / 在消费循环里 `cancel(after_turn)` 实现熔断**。最强理由：不需要碰 tool_use_behavior。被否：`StopAtTools` 只看工具名，无法表达失败计数；`cancel` 是异步落地的，标志位生效前 run-loop 已发起下一次模型调用（控制流工具踩过同一个坑，见 `_stop_run_on_control_flow_tool` 的说明）。

## Consequences

- 收益：一次请求里同一调用（跨 agent 也算）最多以等价错误连续失败 3 次、累计最多失败 10 次就停，不会再出现十几分钟的原样重试，也不会再出现 writer ↔ 审稿人围着同一个失败写入来回跑；停止时整条工作流一起结束并退还额度，用户看到的是具体的工具名和错误原因；前两次失败仍交还模型，正常的换参数纠错不受影响。
- 代价：熔断走 ERROR，失败原因只出现在本次流的错误提示里，不像状态卡那样随消息落库，刷新页面后只能从失败的工具调用记录看出原因；本 run 已发生的 token 消耗仍按 usage 记账，但对话额度会退还。阈值是常量而非配置项；「等价错误」靠文本归一，两个只有措辞差异的不同错误不会被合并。
- 与其他 ERROR 一样，熔断不会回滚本轮已落库的改动；若 writer 已 `create_file` 但正文未写，空文件会留在文件树里（空文件纠偏轮随工作流一起被终止）。
- 参数归一化不做语义比较：模型只改了无关字段（例如多带一个 `occurrence`）就算不同调用，只能由累计失败上限兜住。
- 累计上限按请求而不是按 run：一次请求里多个 agent 各自的零星失败（片段不唯一、标题重复）会叠加，长链路的多文件写作比以前更早碰到 10 次上限。
- 「工具不存在」的熔断比普通工具晚一步：SDK 不为它调 `tool_use_behavior`，第 3 次之后可能已发起第 4 次模型请求，才被消费循环截断（端到端用例断言模型请求不超过 4 次）。
- `parallel_execute` 部分子任务失败也算一次失败（计入累计上限）；模型原样重发同一批任务时已成功的子任务会改变错误组合，一般不会凑成「等价错误」。

## Verification

`cd apps/server && venv/bin/pytest tests/test_agent/test_tool_failure_breaker.py tests/test_agent/test_round3_runner.py tests/test_agent/test_openai_agents_adapter.py tests/test_agent/test_round3_graph.py tests/test_agent/test_mcp_tools.py -q`。请求级共享（`TestRequestScopedFailureBreakerAndWriteSignals`、`test_runner_uses_request_breaker_and_read_only_flag_from_state`）、`parallel_execute` 失败计数、顺序性拒绝豁免、写入全失败不送审、陈旧标记自愈各有用例，去掉对应改动后均失败。其中端到端用例用本地 OpenAI 兼容端点驱动真实 SDK run-loop：模型始终原样重试失败的 `edit_file` 时只发生 3 次模型调用、事件以 `MESSAGE_END` + `ERROR` 结束；把 `tool_use_behavior` 的熔断器绑定去掉后该用例因出现第 4 次模型调用而变红。`test_sdk_unknown_tool_thrash_is_bounded_by_breaker` 覆盖模型反复调用不存在的工具时以 `repeated_unavailable_tool_call` 的 ERROR 结束，`test_unknown_tool_trip_has_its_own_reason_and_message` 覆盖它的文案不说"相同参数"。另用真实 DeepSeek（deepseek-flash）手动验证：模拟 `database is locked` 时模型看到 `retry_hint` 后自行停止；去掉提示强迫原样重试时，第 3 次失败后 0.1 秒内熔断结束。
