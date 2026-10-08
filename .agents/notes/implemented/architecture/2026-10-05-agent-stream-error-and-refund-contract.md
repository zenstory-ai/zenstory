# Agent Note: Agent 串流错误对外只给错误码；有产出或熔断不退额度

Status: implemented

本 note 推翻了 `bug-fix/2026-10-03-agent-tool-failure-circuit-breaker.md` 中「熔断后退还本次对话额度」的计费决定（熔断机制本身不变）。

## Problem

两个问题叠在同一条 error 帧上：

1. **退款可被利用。** `api/agent.py` 只要看到 SSE `error` 帧就按 `internal_error` 退还对话额度，不看本轮是否已经产出正文或写入文件。工具失败熔断（`ToolFailureCircuitOpen`）由模型的工具调用触发，而模型会照用户的指令做：「先把第 1～5 章写好存档，最后连续 3 次调用不存在的技能」就能拿到完整产出又不扣额度，每账号每小时可跑 60 次，是每日 20 次额度的约 72 倍。另外 `event_generator` 只把 `CancelledError` 记为用户取消，生成器以 `aclose()`（`GeneratorExit`）方式断线时会被判为内部错误而退款。
2. **原始异常直接上屏。** runner、`writing_graph`、`graph/nodes.py` 与 `service.py` 都把 `str(exc)` 当作 `error` 文案发给前端：DeepSeek 429/503 的英文 SDK 报错、`Connection error.`、数据库异常里的 SQL 语句与参数都会显示给用户；这些错误没有错误码、一律 `retryable=false`，前端 3 秒后自动清除，也没有重试按钮。

## Decision

**错误契约（`agent/core/stream_errors.py`）**

- `classify_stream_exception(exc)` 把运行期异常映射为错误码：状态码 429 或 `RateLimitError` → `ERR_AGENT_UPSTREAM_RATE_LIMITED`；5xx、`APITimeoutError` / `APIConnectionError` / httpx 超时与传输错误 / `TimeoutError` / `ConnectionError` → `ERR_AGENT_UPSTREAM_UNAVAILABLE`；400/413/422 且报错含上下文超长特征 → `ERR_AGENT_CONTEXT_TOO_LONG`；其余 → `ERR_AGENT_RUN_FAILED`。上游类与通用内部错误 `retryable=True`，上下文超长 `retryable=False`。
- 工作流 ERROR 事件的 data 统一为 `{error: 固定中文文案, code, retryable, refundable, error_type}`（`StreamErrorInfo.as_event_data`）。runner、`graph/nodes.py`、`writing_graph`、`service.process_stream` 的兜底异常都走它，完整异常经 `log_stream_exception` 写服务器日志（`exc_info` 带堆栈，自动带上 `request_id` / `agent_run_id`，runner 另带 `model`）。`utils.logger.log_with_context` 为此新增 `exc_info` 关键字参数。
- `StreamAdapter` 处理 ERROR 时用 `stream_error_from_event_data` 还原错误码（缺字段按 `ERR_AGENT_RUN_FAILED` 兜底），SSE `error` 帧的 `message` 一律是固定文案；唯一例外是熔断，它的文案本来就是给用户看的说明（不含工具名与错误原文）。`ErrorEventData` 新增可选字段 `refundable`。文件落库失败的错误码改为 `ERR_AGENT_FILE_SAVE_FAILED`。
- 新错误码登记在 `core/error_codes.py`（14xxx 段，含中英文案），前端 `public/locales/{zh,en}/errors.json` 有对应文案；`agentApi` 对 `ERR_` 前缀的 code 按 i18n 显示。`useAgentStream` 的流式错误不再 3 秒自动消失（下一次发送或 reset 时清除），`retryable` 为真时 ChatPanel 照常显示重试按钮。

**计费（`agent/core/stream_billing.StreamBillingTracker`，由 `api/agent.py` 的 `event_generator` 驱动）**

按优先级：用户取消或断线（`CancelledError` 与 `GeneratorExit` 同等对待）→ 计费；请求级墙钟时限到期 → 有产出计费、无产出退还；失控停止（error 帧 `reason="no_progress"`、error 帧 `code=ERR_AGENT_MODEL_CALL_LIMIT`，或 `iteration_exhausted` 帧 `layer="tool_call"`）且本轮没有任何写入落库（写工具 success、`parallel_execute` 的写子任务 completed、非空 `file_content`）→ 退还，`billing_reason=runaway_no_progress`（这一条不看是否串流过文字，见 `bug-fix/2026-10-08-agent-no-progress-guard-and-soft-cap.md`）；正常结束 → 计费；失败时 error 帧 `refundable=false` → 计费；本轮已有实质产出（非空 `content`、非空 `file_content`，或 `create_file` / `edit_file` / `delete_file` 的 `tool_result` 为 success）→ 计费；其余没有任何产出的失败 → 退还。熔断的 ERROR 带 `refundable=false`、`retryable=false`。`Agent stream billing evaluated` 日志新增 `billing_reason`（`completed` / `user_cancelled` / `run_deadline_exceeded` / `runaway_no_progress` / `non_refundable_error` / `error_after_output` / `internal_error` / `internal_error_no_terminal`）、`error_refundable`、`produced_output`、`write_succeeded`、`runaway_stop`。SSE `error` 帧（`ErrorEventData`）带可选的 `reason`，`StreamAdapter` 从工作流 ERROR 事件的 `data.reason` 透传。退还真正落库（`_refund_quota` 返回 True）后，`event_generator` 在终止帧之后补发 `quota_refunded` 帧告诉前端这一轮不计入（见 `feature/2026-10-08-agent-stop-reasons-for-authors.md`）。

## Alternatives considered

- **熔断照常退款，只在「有产出」时不退**。最强理由：熔断也可能源于平台侧故障（例如数据库被锁导致写工具连续失败），这类情况下用户什么也没拿到，扣费不公平。被否：熔断的触发条件完全可以由用户指令驱动，单靠「有无产出」判定，用户只需要让模型先读文件再连续调用不存在的工具就能免费消耗大量 token；平台故障导致的熔断只占少数，且已有 `ERR_AGENT_TOOL_FAILURE_LIMIT` 日志可追查个案。
- **保留原始异常文本，只在前面加一句通用说明**。最强理由：用户和客服能直接看到具体原因，排障更快。被否：SQL 语句、表名与参数值属于内部实现细节，会泄露数据结构；英文 SDK 报错对中文用户没有帮助。错误码 + `X-Agent-Run-ID` 已能在日志里定位完整异常。
- **按 usage 阈值判定是否退款**。最强理由：直接以真实成本为准，比「有没有正文」更精确。被否：错误路径上 usage 往往拿不到（上游中途断开时没有末尾 usage chunk），阈值也需要随模型价格调整；「是否产出了用户可见的内容」是用户能理解的口径。

## Consequences

- 收益：刻意触发熔断不再能免费拿到产出；已经看到正文的失败不再退款；断线无论以哪种方式到达都按用户取消计费。用户看到的是可翻译、可重试的错误提示，原始异常只在服务器日志里。
- 代价：平台侧故障若发生在串流了部分正文之后（例如 DeepSeek 在写到一半时 503），本次对话照常扣额度；只靠 `billing_reason=error_after_output` 日志事后补偿。熔断由平台故障引起时也不退款。错误码分类依赖异常类型与报错文本，未识别的上游错误会落到 `ERR_AGENT_RUN_FAILED`。旧客户端若仍按 `code` 不以 `ERR_` 开头的约定处理 `FILE_SAVE_FAILED`，会改为显示新错误码的翻译。

## Verification

`cd apps/server && venv/bin/pytest tests/test_agent/test_stream_launch_hardening.py tests/test_api/test_agent_stream_hardening.py tests/test_api/test_agent.py -q`：覆盖错误分类与文案脱敏（429 / 503 / 超时 / 上下文超长 / 含 SQL 的异常）、熔断 `refundable=false`、StreamAdapter 透传 code/flags、runner 异常脱敏、计费判定各分支、路由层熔断不退款、有产出不退款、无产出的内部错误照常退款、`aclose()` 断线按 `user_cancelled` 计费。前端 `pnpm exec vitest run src/lib/__tests__/agentApi.test.ts src/hooks/__tests__/useAgentStream.test.ts` 覆盖错误码翻译与错误不自动消失。
