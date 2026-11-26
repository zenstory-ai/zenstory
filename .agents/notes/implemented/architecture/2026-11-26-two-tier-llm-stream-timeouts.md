# Agent Note: 两阶段 LLM 流式超时与挂死流非阻塞释放

Status: implemented

关联：补充 [模型供应商主备切换](2026-10-20-llm-primary-backup-failover.md) 与 [MiniMax 双凭证路由与缓存稳定](2026-11-23-minimax-responses-api-prompt-cache-key.md)。

## Problem

在 `apps/server/agent/openai_agents/model.py` 中，单次流式模型调用此前仅使用单一超时阈值 `_iter_with_idle_timeout(..., provider.kind.read_timeout_s())`（MiniMax 默认 120s、DeepSeek 默认 45s），且在 `finally` 中同步 `await aiter.aclose()`。实际运行中暴露出两类可靠性短板：

1. **首包等待与流中空闲共用 120s 上限**：MiniMax 实测 320k 无缓存输入的最大 TTFT 约 49s，当上游节点静默挂死、迟迟不吐出首个有效推理/文本事件（或只发 `response.created` / 空 delta 保活）时，调用被迫白等 120s 才能失败并切换备源，明显拉长故障恢复时延，且日志里无法直接区分「首包前挂死（`first_event`）」还是「流中卡顿（`idle`）」。
2. **挂死 TCP 连接阻塞超时释放**：超时触发后，`_iter_with_idle_timeout` 的 `finally` 直接 `await aiter.aclose()`（以及 `asyncio.timeout` 取消 `__anext__()` 时等待底层生成器/socket `finally` 退出）。若底层连接或 `aclose()` 在半开 TCP 上卡死，超时恢复与主备切换会被同步拖住，甚至耗尽整轮 780s 墙钟预算。

## Decision

1. **供应商级两阶段流超时配置**（`apps/server/agent/core/llm_providers.py`）：
   - 在 `ProviderKind` 新增 `first_event_timeout_env`、`default_first_event_timeout_s`，以及 `first_event_timeout_s()` / `stream_idle_timeout_s()` 解析方法；在 `ModelProvider` 解析并持久化 `first_event_timeout_s: float` 与 `stream_idle_timeout_s: float`（默认值由 `kind` 兜底，兼容直接构造 `ModelProvider` 的测试）。
   - **MiniMax**：`MINIMAX_FIRST_EVENT_TIMEOUT_S` 默认 `75.0`s（高于实测 320k 无缓存最大 TTFT ~49s，留足裕量）；`MINIMAX_CLIENT_TIMEOUT_S` 默认 `120.0`s 作为 Phase-2 `stream_idle_timeout_s`。
   - **DeepSeek**：`DEEPSEEK_FIRST_EVENT_TIMEOUT_S` 默认 `45.0`s；`DEEPSEEK_CLIENT_TIMEOUT_S` 默认 `45.0`s 作为 Phase-2 `stream_idle_timeout_s`。
   - 两个流超时环境变量均支持设为 `0` 以显式关闭对应阶段超时；负数、非数字或 `nan`/`inf` 安全回退默认值。底层 HTTP 客户端的 `read_timeout_s()` 维持正数解析，避免把 `0` 传给 `httpx.Timeout` 引发立即断连。
2. **两阶段监控与诊断区分**（`apps/server/agent/openai_agents/model.py`）：
   - 将 `_iter_with_idle_timeout` 升级为 `_iter_with_stream_timeouts(stream, *, first_event_timeout_s, idle_timeout_s, provider_name)`，保留 `_iter_with_idle_timeout` 作为薄兼容包装。
   - **Phase 1（`first_event`）**：等待首个 `_is_meaningful_stream_progress(event)`（非空 text/reasoning/refusal/tool-arg delta 或终态事件 `response.completed` / `response.incomplete` / `response.failed` / `error`）。`response.created`、`output_item.added` 与 `delta=""` 永不重置 Phase-1 计时器。
   - **Phase 2（`idle`）**：收到首个有效进展事件后切换至 `idle_timeout_s`，并在后续每个有效进展事件上刷新；空 delta 与保活帧同样永不重置。
   - 超时异常文案明确区分 `first_event timeout` 与 `idle timeout`，并同步记录脱敏结构化 WARNING 日志（`timeout_phase="first_event" | "idle"`、`provider`、`timeout_s`），配合 `FailoverChatModel` 既有的 `phase="stream_start" | "stream"` 诊断字段快速定位节点首包挂死或流中停滞。
3. **挂死流非阻塞释放（`releaseIterator` 模式）**（`apps/server/agent/openai_agents/model.py`）：
   - 在调用方同一协程与 `contextvars.Context` 中通过 `asyncio.timeout(remaining)` 等待 `aiter.__anext__()`（保持 OpenAI Agents SDK `generation_span` / `response_span` 的 `ContextVar` 令牌有效，并兼容 `OpenAIResponsesModel.stream_response` 自身的 `CancelledError` 后台关流）。一旦超时，标记 `timed_out=True`，在 `finally` 中不再内联 `await aiter.aclose()`，而是通过 `_schedule_stream_teardown` 派发受强引用集合 `_pending_stream_teardowns` 保护的后台清理任务（总时长受 `_STREAM_TEARDOWN_TIMEOUT_S=2.0s` 限制，抑制清理异常并在完成后自动 `discard`），主协程立即抛出 `TimeoutError` 进入切换或终态上报。
   - 正常结束（`StopAsyncIteration`）、调用方取消（`CancelledError` / `GeneratorExit`）或上游直接抛出的异常仍维持同步内联 `aclose()`，确保正常释放与 `_BudgetedAsyncStream` 取消结算语义不变。
4. **保持既有不变量与单 Run 双凭证固定契约**：
   - **不引入思考时长合成截断**：不构造伪造的 `ResponseCompletedEvent` 强行截断长思考，避免破坏工具调用协议与计费语义。
   - **严格维持 MiniMax 单 Run Key 固定契约**：遵循 `apps/server/agent/AGENTS.md` 与 `llm_failover.py` 既定合同——同一 `agent_run_id` 内的全部 MiniMax 调用固定到同一凭证，双凭证同属一个 provider 槽位、不互相 failover。`first_event` 超时继续归类为 `is_provider_unavailable`（`TimeoutError`），在未产出正文前直接切换备源（若配置了备源如 DeepSeek），无备源时作为 `unrecovered_provider_error` 向上抛出并触发既有的上游错误退费路径，而不静默切换另一把 MiniMax Key。

## Alternatives considered

1. **在 `first_event` 超时后，单次调用内切到另一把 MiniMax 凭证重试**：
   - 最强论据：在未配置备源或想避免切换 DeepSeek 产生按量现金支出时，换一把 MiniMax Key 可能命中不同路由节点并在无正文输出前自愈。
   - 拒绝原因：违反已固化的单 `agent_run_id` 凭证固定契约（`test_minimax_key_routing.py` 明确要求双 Key 不形成第二个 failover provider）；节点级首包超时通常属于供应商侧负载/路由问题而非单 Key 429/401/402/403 账号限额，切 Key 会直接击穿热前缀缓存并污染同一 run 的归属追踪。因此保持既有契约，仅由跨供应商主备 failover 处理，后续若需同供应商重试应作为独立决策重新评估。
2. **每次 `__anext__()` 新建 `asyncio.Task` 并用 `asyncio.wait` 竞态**：
   - 最强论据：即便底层生成器在 `__anext__()` 的 `CancelledError` 展开里阻塞也能脱离。
   - 拒绝原因：OpenAI Agents SDK 的 `OpenAIChatCompletionsModel.stream_response` 与 `OpenAIResponsesModel.stream_response` 在 `yield` 外包裹了基于 `ContextVar` 的 `generation_span` / `response_span`；每次 `__anext__()` 切到新 Task 会复制不同 `contextvars.Context`，在流结束或异常退出时触发 `ValueError: Token was created in a different Context`。而且 `OpenAIResponsesModel.stream_response` 在 `CancelledError` 路径已自行后台关闭底层流，采用同上下文 `asyncio.timeout` + 超时后后台化 `aclose()` 既零每帧 Task 开销，又完全兼容 SDK 上下文管理。
3. **增加基于思考时长的合成 `ResponseCompletedEvent` 强停机制**：
   - 最强论据：可限制极端长思考耗尽墙钟。
   - 拒绝原因：已明确搁置——思考阶段活跃流出有效 `reasoning` delta 说明节点仍在工作，伪造完成事件会导致半截思路被当成正常回复、误触发计费或破坏工具循环。

## Consequences

- **收益**：
  - MiniMax 节点在首包前静默挂死时，检测与切换时间从 120s 缩短至 75s，且不会被挂死的 TCP `aclose()` 进一步拖延。
  - 日志与异常明确区分 `first_event` 与 `idle`，便于排查上游 TTFT 退化与流中卡顿。
  - 支持通过 `MINIMAX_FIRST_EVENT_TIMEOUT_S=0` / `MINIMAX_CLIENT_TIMEOUT_S=0` 按阶段灵活降级，无需改代码发版。
- **代价与权衡**：
  - 超时被剥离的底层连接最多在后台存活 `_STREAM_TEARDOWN_TIMEOUT_S`（2s）后被放弃，极端卡死场景下依赖操作系统 TCP keepalive/连接池回收；`_BudgetedAsyncStream` 的取消结算在正常取消展开中照常触发，若底层完全阻塞超过 2s 则由既有预留超时兜底（绝不产生免费漏记）。
  - 保留单 run Key 固定意味着无备源时 `first_event` 超时直接走可重试报错+退费，而非隐式跨 Key 重试。
