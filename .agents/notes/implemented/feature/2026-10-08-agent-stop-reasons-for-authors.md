# Agent Note: 停止原因、额度用完与工具失败卡片都写给作者看

Status: implemented

本 note 推翻了 `bug-fix/2026-10-03-agent-tool-failure-circuit-breaker.md` 中「熔断说明带工具名和最后一次错误摘要」的展示决定（熔断条件与计费不变）。计费契约本身见 `architecture/2026-10-05-agent-stream-error-and-refund-contract.md`，本 note 只加了「退还落库后告诉前端」这一帧。

## Problem

1. **服务端写好的停止说明被前端覆盖。** `agentApi` 的 error 分支只要 code 以 `ERR_` 开头就用错误码文案。重复读取守卫停下时服务端说的是「反复读取…本轮没有修改任何文件」，作者看到的却是 `ERR_AGENT_TOOL_FAILURE_LIMIT` 的「AI 多次操作失败…已完成的内容已保存」：既没有工具失败，也没有保存任何东西。
2. **退还了额度，作者不知道。** `api/agent.py` 在流结束后的 `finally` 里判定并退还，作者看不到，往往原样重发。
3. **额度用完的卡片泄露第二个额度。** 成本兜底（`ERR_QUOTA_AI_DAILY_COST_EXCEEDED`）有自己的标题「今日 AI 额度已用完」，违反 AGENTS.md「不展示第二个额度」；卡片里标题、`{error}` 原文、说明把同一件事说了三遍，标题里写死的「10 条」在兜底提前触发时也不对。
4. **工具失败卡片直接显示内部文本。** 写给模型的指令（「Edit 0: 锚点匹配到多个位置…occurrence=N」）、文件 id、`Malformed tool_result payload: ValueError`、并行子任务的 `str(exc)`，以及熔断说明里的工具名和数据库报错，都原样显示给作者；不认识的工具直接显示 `hybrid_search` 这类内部名字。
5. 正常的「这一轮步数用完」用红色错误样式，停止按钮写「取消」，文案里暴露内部步数。

## Decision

**退还说明帧（`agent/core/stream_billing.quota_refunded_frame`，`api/agent.py`）**

- 计费判定和退还的位置、规则都不变；`event_generator` 用 `_settle_billing()` 保证整个请求只结算一次。正常收尾、墙钟时限、未捕获异常三条路径在终止帧之后结算，`_refund_quota` 返回 True（退还真正落库）时再补发一帧 `event: quota_refunded`，data 为 `{"refunded": true, "kind": "no_progress" | "error"}`：`billing_reason=runaway_no_progress` 为 `no_progress`，其余（平台错误、超时且无产出）为 `error`。取消路径不发。
- 前端 `agentApi` 收到 `refunded: true` 才调用 `onQuotaRefunded(kind)`，经 `useAgentStream` 传给 ChatPanel；ChatPanel 在消息区下方显示一行灰字：`chat:panel.notCharged`「这一轮没有改动文件，不计入今日 AI 消息。」或 `chat:panel.notChargedError`「这次出错不计入今日 AI 消息。」。状态按项目隔离，下一轮开始、新建会话时清空，不落库。

**停止原因的文案选择（`agentApi.selectStreamErrorMessage`）**

- error 帧带 `reason`，或 code 为 `ERR_AGENT_TOOL_FAILURE_LIMIT` / `ERR_AGENT_NO_PROGRESS` 且 `message` 非空时，显示服务端的具体说明；这些说明只有中文，所以只在中文界面这样做，英文界面仍用错误码文案。其余错误照旧只按错误码显示，原始 message 不上屏。
- 前端 `errors.json` 新增 `ERR_AGENT_NO_PROGRESS`，并改写 `ERR_AGENT_TOOL_FAILURE_LIMIT` / `ERR_AGENT_MODEL_CALL_LIMIT` / `ERR_AGENT_RUN_TIMEOUT`；后端 `core/error_codes.py` 的兜底文案同步（不再写「调用工具」「模型调用次数」，可接着做的停止统一用「回复『继续』」）。

**熔断说明（`ToolFailureTrip.user_message` / `as_event_data`）**

- 说明只区分「这一轮用不了的功能」与其余失败两种，不带工具名、失败次数和错误原文；ERROR 事件不再带 `tool_name`、`last_error`。这些只写 runner 的熔断告警日志（`tool_name` / `failures` / `last_error`），trip 对象上仍保留供日志使用。

**额度用完**

- 每日条数和成本兜底共用 `chat:panel.quotaExceededTitle`「今天的免费 AI 消息用完了」与 `quotaExceededHint`；删除 `dailyCostExceededTitle/Hint`。额度错误时 ChatPanel 不再渲染原始 `{error}`。两个错误码的前端与后端兜底文案统一为同一句。

**工具失败卡片**

- `StreamAdapter` 处理失败的 `tool_result` 时，从工具结果顶层或 `data` 里取 `user_message` / `error_type`，放进事件 `data`；有 `user_message` 时 `error` 字段也换成它。解析异常只进日志，卡片拿到 `error_type="malformed_tool_result"`。
- `parallel_executor` 的 `parallel_task_end` 进度帧只带子任务的 `user_message`（没有就是 null）；返回给模型的 `tasks[].error` 仍是原始错误，模型据此纠正。
- `ToolResultCard` 失败态改为中性灰色，正文按顺序取 `user_message`（顶层或 `data` 下），否则按 `error_type` 归类：找不到原文 / 多处匹配 / 文件不在 / 重复读取（有标题时点名）/ 其他，认不出来的一律用「这一步没做成，AI 会换个方式继续。」，永远不显示原始 `error`。并行子任务失败显示子任务的 `user_message` 或「没有完成」；进度行改为「✗ {{description}}：没有完成」。新增 `chat:tool.hybrid_search`，不认识的工具显示 `chat:tool.generic`「AI 操作」。

**其他**

- 「这一轮先做到这里」状态卡（`iteration_exhausted`）改为中性样式和暂停图标，文案不再带步数；`lowTurnWarning` 不再报剩余步数。停止按钮（桌面与手机）显示并朗读「停止生成」。

## Alternatives considered

- **把 `refunded: true` 直接写进终止帧（error / done）。** 最强理由：前端只需在已有的 `onError` / `onDone` 里读一个字段，不用新事件类型。被否：退还在 `event_generator` 的收尾阶段才判定并落库，终止帧发出时结果还不知道；要写进终止帧，只能在帧发出前就退还，而 SSE 源由 `SSEStreamPump` 在后台 task 里驱动，非 PostgreSQL 环境下 `_refund_quota` 会对共享的请求 session 做 `rollback()`，可能和仍在落库历史的 `process_stream` 撞在一起；还会让「终止帧之后断线」从计费变成退还。补发一帧只陈述已经发生的事。
- **英文界面也显示服务端的停止说明。** 最强理由：说明比错误码文案更具体。被否：这些说明是写死的中文，英文界面会突然冒出一段中文；错误码文案已经按 C4 / C5 写清楚了下一步。
- **工具失败卡片在没有 `user_message` 时退回显示原始 `error`。** 最强理由：排障时能直接看到原因。被否：原始错误是写给模型的指令或英文异常，作者看不懂，还会泄露内部 id；原文仍在会话历史的 tool_calls 和服务器日志里，`X-Agent-Run-ID` 能定位。

## Consequences

- 收益：作者看到的是停在哪、能不能接着来，以及这一轮是否计入；额度卡片不再暴露成本兜底；工具卡片不再出现内部指令、id 和英文异常；正常暂停不再像报错。
- 代价：`quota_refunded` 帧在终止帧之后到达，作者若在终止帧后立刻断开就看不到说明（额度照样已退）；说明不落库，刷新页面后消失。按 `error_type` 归类依赖各工具的命名，认不出的类型只能给通用说法。中文界面里重复读取守卫自己的说明若也提到退还额度，会和灰字说明重复一句，需由守卫一侧去掉。英文界面看不到服务端的具体停止说明。

## Verification

- 后端：`venv/bin/python -m pytest tests/test_api/test_agent_stream_hardening.py tests/test_agent/test_tool_failure_breaker.py tests/test_agent/test_stream_adapter.py tests/test_agent/test_parallel_steering_rework.py -q --no-cov`：退还后补发 `quota_refunded`（失控停止 `no_progress`、内部错误与超时 `error`，排在 error 帧之后）、该退但没退成时不发、熔断不发；熔断 ERROR 不含工具名与错误原文；失败卡片用 `user_message`、解析异常只给 `error_type`；并行进度帧只带 `user_message`。
- 前端：`npx vitest run src/lib/__tests__/agentApi.test.ts src/components/__tests__/ToolResultCard.test.tsx src/components/__tests__/ChatPanel.mount.test.tsx src/components/__tests__/MessageInput.test.tsx src/components/__tests__/MobileChatInput.test.tsx`：停止说明的选择规则（reason / code / 空 message / 英文界面 / 其他错误码）、`quota_refunded` 回调、额度卡片不再显示原始错误且兜底与条数共用标题、退还说明只在回调后出现并在下一轮清空、失败卡片不显示原始错误。
