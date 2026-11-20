# Agent Note: 已进入模型执行的对话轮次按轮计费，仅未进入模型前或未恢复的上游模型报错终止退还额度

Status: implemented

本 note 推翻以下历史决策中“按是否有 60 字回复或文件写入（`produced_output` / `write_succeeded` / `ended_on_reply`）决定停止、断线、墙钟超时、发布交接与失控护栏是否退还 AI 消息额度”的计费规则（服务端优雅停止协议、残稿补存、文件版本保护、退还轮空白占位清理与 `stop_outcome` 历史回执机制保持不变）：

- [2026-10-05-agent-stream-error-and-refund-contract.md](../architecture/2026-10-05-agent-stream-error-and-refund-contract.md)
- [2026-10-09-agent-graceful-stop-and-no-output-refund.md](../architecture/2026-10-09-agent-graceful-stop-and-no-output-refund.md)
- [2026-10-09-stop-output-definition-and-round-outcome.md](../architecture/2026-10-09-stop-output-definition-and-round-outcome.md)
- [2026-10-21-deploy-drain-ends-running-generations.md](../architecture/2026-10-21-deploy-drain-ends-running-generations.md)

## Problem

生产巡检与真实长篇会话记录暴露了旧“无实质产出即退还”规则的结构性漏洞与前后端口径偏差：

1. **只读核对、纯思考、主动停止与墙钟超时形成无成本重度消耗通道。** 长篇多章一致性核对（例如单轮十余次 `query_files`、多次模型调用累计近 700 秒推理并在 780 秒墙钟硬超时结束）因未改文件、回复不足 60 字而被判 `produced_output=false` 自动退还 AI 消息额度。同样，作者发起请求后在思考或历史压缩阶段主动点击停止、关闭标签页，或触发 `no_progress` / `repetition_loop` / 模型调用上限 / 输出截断护栏时，即使已消耗数十万上下文 token 与多次供应商调用，仍可反复退回每日或套餐消息条数。
2. **错误码与 `refundable` 标志混同了“上游模型服务故障”与“非模型侧停止”。** `stream_errors.py` 将 `repetition_loop`、`model_call_limit`、`output_truncated`、`run_timeout`、`server_restarting` 及无写入的 `no_progress` 默认标为 `refundable=True`，且 `classify_stream_exception` 会将工具或数据库层的 `TimeoutError` / `ConnectionError` 映射为上游类错误码；仅凭表层错误码或旧 `refundable` 字段无法区分真正的上游模型报错与平台时限/业务护栏。
3. **停止回执与前端文案仍在沿用“没写出内容就不计入”的旧承诺。** `StreamStoppedByUser` 在结算之前发 `done`、未携带真实 `charged`，`stopped_delivery` 还会省略无写入轮的新建空壳；`LeaveWhileGeneratingDialog`、`ChatPanel`（按 `charged && producedOutput` 估算）及多语言文档与新口径冲突。

## Decision

### 1. 消息额度结算边界（`cost_budget.py`、`model_call_guard.py`、`llm_failover.py`、`model.py`、`runner.py`、`stream_adapter.py`、`stream_billing.py`、`api/agent.py`）

- **唯一“已进入模型执行”判据（`RunOutcome.provider_attempted`）**：在 `services/usage/cost_budget.py` 中提供请求级 `ContextVar` 回调 `on_model_transport_start` / `bind_model_transport_start`（`services/usage` 不反向 import `agent.*`）。`services/usage/model_call_guard.py:install_async_cost_guard.create` 在 `reserve_model_call` 成本预检通过、`bind_call_start` 与 `original_with_options(max_retries=0)` 客户端准备完成后、紧贴 `await transport(*args, **kwargs)` 之前触发 `notify_model_transport_start()`，将当前请求的 `RunOutcome.provider_attempted` 置为 `True`（同时覆盖免费与付费账号、轮前历史压缩与主循环 `FailoverChatModel`；首次调用前被成本兜底 402/503 拦截或本地克隆失败均不置位，而第 2 次及以后调用被成本兜底拦截时 `provider_attempted` 已为 `True`）。
- **唯一“因上游模型报错终止”判据（`RunOutcome.terminal_model_failure`）**：在 `agent/core/llm_failover.py` 统一提供 `is_upstream_provider_error(exc)`，覆盖 `openai.APIError`（含 4xx 如上下文超长 `BadRequestError`、429 限流、5xx 服务端错、连接/超时与流内错误）、`httpx`/`httpx2`/`httpcore` 的 `HTTPError` 及标准库 `TimeoutError`/`ConnectionError`，显式排除平台 `APIException` 与取消异常。`FailoverChatModel` 在主备切换耗尽或不允许切备而最终上抛的单一出口记录 `unrecovered_provider_error`（不做跨模块异常动态属性猴补丁，也不改写 `_BudgetedAsyncStream`）；`runner.py` 仅在捕获到的异常与该次调用的 `unrecovered_provider_error` 匹配时标记 `error_source="model"` 与 `refundable=True`，`StreamAdapter` 在该 `ERROR` 真正终止整轮时置 `run_outcome.terminal_model_failure = True`。主备切换成功或 `compaction.py` 捕获跳过的异常不留失败标记；工具/数据库层抛出的 `TimeoutError`/`ConnectionError` 及 SDK `ModelBehaviorError`/本地异常不经过 `FailoverChatModel` 终态出口，按非模型错误计费。
- **上游 4xx 错误（含上下文超长 `BadRequestError` / 413 / 422）退还口径**：上游供应商返回的 4xx（`openai.APIError` 子类）属于供应商拒绝该次模型调用、无法由切备恢复且未完成本轮生成；在 `is_upstream_provider_error` 中统一纳入 `openai.APIError`，若导致整轮终止则退还本轮 AI 消息额度（例如上下文超长时作者可点击重试触发历史压缩/裁剪，不因单次超窗被扣双倍消息）。
- **结算优先级（`StreamBillingTracker.decide`）**：
  1. 终止帧（`done` / 终态 `error`）已产生后的断线或墙钟到点不改写原结束方式。
  2. 正常完成（已看到非错误终止帧且无异常、非停止、非断线、非时限）：计费（`completed`）。
  3. `not provider_attempted`（进入任何供应商 transport 前停止、断线、到时、预检或首次调用成本预检拦截）：一律退还（`user_stopped_before_model` / `client_disconnected_before_model` / `run_deadline_exceeded` / `server_restarting` / `pre_model_error`）。
  4. `terminal_model_failure == True`（未恢复的上游模型服务/网络/流错误终止整轮，无论是否已流出部分正文）：退还（`upstream_model_error`）。
  5. 其余所有已进入模型执行的结束方式（作者停止、客户端断线、墙钟超时/发布交接、工具失败熔断、`no_progress`、`repetition_loop`、模型调用上限、输出截断、SDK/本地异常、第 2 次及以后调用撞成本兜底）：一律计 1 轮 AI 消息额度，不退还。
- **清理旧计费死代码与对齐 `refundable` 字段**：`stream_errors.py` 中 `repetition_loop_error`、`model_call_limit_error`、`output_truncated_error`、`run_timeout_error`、`server_restarting_error` 及 `repeat_read_guard.py` 的 `no_progress` 均改为 `refundable=False`；`stream_billing.py` 删除不再使用的 `ended_on_reply`、`runaway_stop`、`error_refundable` 及旧退还分支；`api/agent.py` 删除已不可能撤回退款的 `_refund_after_body_settles()` 与结算前的 `body_unconfirmed` 等待。
- **停止先结算后发 `done` 与空白占位如实回执**：`StreamStoppedByUser` 在连接仍开着时先等待 `assistant_message_id`、按增量游标 `committed_seen` 同步迟到写工具结果并完成 `await _settle_billing()`；仅当 `refund_applied == True` 且拿到 `assistant_message_id` 时尝试移除空白占位文件（捕获并记录异常，确保永不阻塞 `done`），再用真实 `removed_ids` 过滤 `stopped_delivery` 发出 `done(charged=not refund_applied, **delivery)`，并在 `refund_applied == True` 时补发 `quota_refunded`。未移除的新建空壳如实保留在 `done.unwritten_files` 与 `stop_outcome.unwritten_files`。账户消息额度与免费客户端 IP 共享网络额度仍通过同一 `agent_run_id` 保持单事务幂等扣减与至多一次退还；`llm_usage_event` 与成本兜底账本按真实供应商调用独立记账。

### 2. 前端与用户指南同步（`agentApi.ts`、`useAgentStream.ts`、`ChatPanel.tsx`、`LeaveWhileGeneratingDialog.tsx`、`chat.json`、`docs/`）

- `done` 帧新增可选字段 `charged?: boolean`；`ChatPanel` 停止说明优先取 `completionMeta?.charged`，仅在兼容旧服务端缺少该字段时回退旧估算 `charged && producedOutput`。
- 移除 `LeaveWhileGeneratingDialog`、`chat.json` 与中英文用户指南中“没写出内容就不计入”的旧承诺，同步为“仅未进入模型处理或未恢复的模型服务报错终止且实际退还时不计入；已开始模型处理的只读核对、停止与超时均计入今日消息”。保留 `producedOutput` 供停止后跳过 `/agent/suggest` 等 UI 逻辑使用。

## Alternatives considered

- **保留停止/断线无产出退还，仅将 780s 墙钟超时改为计费。** 最强理由：误触发送后很快停止的作者不会损失免费条数。被否：一旦模型开始推理或历史压缩，作者在看到思考结论或完成多章只读核对后点击停止/关页，仍可零额度反复白用算力；未进入模型前的停止已保留退还。
- **将上游 4xx（如上下文超长 `BadRequestError`）也归为不退还。** 最强理由：4xx 往往是请求体超长或参数问题而非供应商宕机。被否：上下文超长多由历史累计与系统组装窗口逼近导致，作者点击重试会触发轮间压缩/裁剪恢复；与 5xx/429 一样用 `openai.APIError` 统一退还，既避免用户因平台上下文边界被重复扣费，又避免在 4xx 里按错误文案做脆弱字符串切分。
- **新增全局 `_run_outcome` ContextVar 并让 `services/usage/model_call_guard.py` 反向 import `agent.core`、同时重写 `_BudgetedAsyncStream` 给第三方异常打动态属性。** 最强理由：试图在 transport 最底层拦截所有流异常并防范假想的 SDK 本地伪造 `openai.APIError`。被否：破坏了 `services/usage -> agent` 单向分层、引入 5 层跨模块状态传递与异常猴补丁；复用 `cost_budget.py` 已有 ContextVar 提供轻量回调，并在 `FailoverChatModel` 单一终态出口记录 `unrecovered_provider_error`，同等准确且复杂度降低约 80%。
- **在 `StreamBillingTracker.decide` 中把 `not provider_attempted` 排在正常 `completed` 之前。** 最强理由：强制所有请求都必须经过 transport 钩子才算完成。被否：会导致所有正常 `done` 却未接入 `install_async_cost_guard` 的桩流/集成测试（如激活漏斗测试）被误判为 `pre_model_error` 并错误退款；先判正常 `completed` 再判 `not provider_attempted` 对生产零风险且保持接口直觉一致。

## Consequences

- **收益**：堵住通过纯读核对、看思考后停止/关页、触发失控护栏或拖满 780s 超时免费消耗模型算力的漏洞；未进入模型前结束与未恢复的上游模型报错（含 4xx/429/5xx/网络与流错误）仍如实退还；前后端文案、SSE `error.refundable`、实时 `done.charged` / `unwritten_files` 与刷新后 `stop_outcome` 四者完全一致；零跨层反向依赖与零异常猴补丁。
- **代价**：请求一旦通过成本预检进入供应商调用（即使尚未收到首 token 或仅完成思考/只读），作者主动停止、离开页面、撞上 780s 时限、发布交接或遭遇非模型侧内部错误都会消耗 1 轮 AI 消息额度；测试里若模拟“进入模型后停止/报错/超时”的桩服务，需显式触发 `notify_model_transport_start()` 或置 `run_outcome.provider_attempted = True`。
