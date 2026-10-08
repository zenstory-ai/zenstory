# Agent Note: /agent/stream 的请求级预算、SSE 心跳与运行摘要日志

Status: implemented

## Problem

- **没有请求级上限。** `AGENT_TOOL_CALL_MAX_ITERATIONS`（单个 agent run 100 轮）与 `AGENT_COLLABORATION_MAX_ITERATIONS`（30 次交接）相乘，一次 `/agent/stream` 理论上能发起上千次模型调用，却只扣 1 次额度；整个请求也没有墙钟时限，失控的循环只能等用户手动取消。
- **没有 SSE 心跳。** router 排队、`edit_file(op=append)` 生成长参数、DeepSeek 尖峰排队时，连接上可能一分钟以上没有任何字节，Cloudflare / Railway 边缘代理会把它当闲置连接切断，前端显示 `STREAM_CLOSED`。
- **看不到成本。** 主写作 Agent 的完成日志只有 `agent_type`、工具调用数与回复长度，没有模型名、token 用量、LLM 耗时，也没有和计费结果放在一起，上线后无法按请求核对成本。

## Decision

- **模型调用预算。** `agent/core/run_meter.AgentRunMeter` 按请求计数：`service.process_stream` 建一个放进 `WritingState["run_meter"]`（`writing_graph` 在缺失时自建），每个 agent run 共享。runner 用 `_ModelCallCountingFilter` 包住 `IntraRunToolOutputTrimmer` 作为 SDK 的 `call_model_input_filter`，每次模型调用前计数；`tool_use_behavior`（`_stop_run_on_control_flow_tool`，控制流工具优先）在预算用尽时把本轮当作 final output，SDK 不再发起下一次调用；没有 FunctionTool 结果的轮次由消费循环在 `tool_output` 处 `cancel(after_turn)` 兜底。被截停的 run 先发 `MESSAGE_END(stop_reason="model_call_budget_exhausted")` 让 usage 入账，再发 `ERR_AGENT_MODEL_CALL_LIMIT` 的 ERROR 结束整条工作流；下一个 agent run 开始时若预算已用尽，直接发同一个 ERROR，不调用模型。上限 `AGENT_RUN_MAX_MODEL_CALLS`，默认 120（两个满额的单 agent run；单 agent 上限与协作上限已分别调到 60 与 12，见 `bug-fix/2026-10-08-agent-no-progress-guard-and-soft-cap.md`）。`_ModelCallCountingFilter` 同时负责单个 run 接近 `max_turns` 时的软着陆提醒（同一 note）。
- **墙钟时限与心跳。** `agent/core/sse_pump.SSEStreamPump` 在后台 task 里驱动 `process_stream`，消费端每 `AGENT_SSE_HEARTBEAT_INTERVAL_S`（默认 15 秒）等不到事件就发一个 SSE 注释帧 `: ping\n\n`（前端 `parseSSEEvent` 对没有 `data:` 行的帧返回 null，原本就会忽略）。整个请求超过 `AGENT_RUN_WALL_CLOCK_TIMEOUT_S`（默认 1200 秒）时取消后台 task——`process_stream` 走与用户取消相同的收尾（后台补存部分历史与进行中的文件正文、释放会话持有）——随后 `api/agent.py` 发 `ERR_AGENT_RUN_TIMEOUT` 终止帧，本轮有产出则计费、没有则退还。三个值都在 `config/agent_runtime.py`，可用同名环境变量调整。
- **运行摘要。** `process_stream` 接受调用方传入的 `run_report` 字典，结束时写入 `model`、`input_tokens` / `output_tokens` / `cache_read_tokens`、`usage_reported`、`model_calls` / `max_model_calls`、`agent_runs`、`llm_duration_ms`（各 SDK run 的耗时之和）、`duration_ms`、`stop_reason` 等；`api/agent.py` 把它们以 `run_` 前缀并入每个请求唯一的 `Agent stream billing evaluated` 日志行，与 `billing_reason`、`charged`、`refunded` 同行。runner 的单次 run 完成日志也补上 `model`、`model_turns`、`duration_ms` 与 token 用量。

## Alternatives considered

- **按 token 累计设预算（例如免费版 200k 输入 + 32k 输出）**。最强理由：直接对应成本，比「调用次数」更精确。被否：错误或中断路径上 usage 常常拿不到，按 token 截停会在最需要兜底的时候失效；每轮输入里 system prompt 与历史占大头，调用次数已经与 token 成本近似成正比，而且能在发起调用前同步判定。按方案区分的预算需要先有成本数据，本次只设一个宽松的全局上限。
- **直接调低 `AGENT_TOOL_CALL_MAX_ITERATIONS` / `AGENT_COLLABORATION_MAX_ITERATIONS`**。最强理由：零新代码。被否：两者分别约束单个 agent 与交接次数，调到能挡住乘积的程度会误伤正常的长篇写作；请求级总量才是成本的真正边界。
- **在 `process_stream` 内部用 `asyncio.timeout` 包住工作流**。最强理由：时限逻辑和工作流在一起，不需要额外的泵。被否：`process_stream` 是被路由消费的异步生成器，超时的取消会落在消费端（Starlette 的 send）而不是生成器里；把生成器放进独立 task 后，心跳与时限都能在消费端干净地实现。
- **心跳发成 `event: heartbeat` 业务事件**。最强理由：前端可以显式感知心跳。被否：SSE 注释帧是协议级保活，浏览器 EventSource 与现有解析器都天然忽略，不需要前端改动，也不会被计入任何事件统计。

## Consequences

- 收益：单次请求的模型调用次数与时长都有上界，超限时有明确的错误提示且已产出的内容保留；长时间无输出的阶段不会再被代理切断；每个请求一行日志就能看到模型、用量、调用次数、耗时、结束原因与计费结果。
- 代价：预算与时限是全局常量，付费用户与免费用户相同；不是按方案区分的精细成本控制。时限到期走取消路径，部分历史与文件正文在后台落库，摘要日志里的 usage 等字段在这条路径上可能为空。`process_stream` 的迭代改在后台 task 里进行，调试时调用栈多一层。心跳帧每 15 秒多几个字节。

## Verification

`cd apps/server && venv/bin/pytest tests/test_agent/test_stream_launch_hardening.py tests/test_api/test_agent_stream_hardening.py tests/test_agent/test_service.py -q`：覆盖泵的心跳帧、时限取消源并抛出、contextvars 在各步间保持、源异常透传；`tool_use_behavior` 在预算用尽时截停、计数过滤器、预算已尽时 runner 不调用模型；路由层空闲时发出 `: ping`、时限到期发 `ERR_AGENT_RUN_TIMEOUT` 并退款、摘要日志带 `run_*` 字段；service 写入 `run_report`。前端 `src/lib/__tests__/agentApi.test.ts` 覆盖心跳帧被忽略。
