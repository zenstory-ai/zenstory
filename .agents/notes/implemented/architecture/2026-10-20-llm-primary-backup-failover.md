# Agent Note: 模型供应商主备切换

Status: implemented

关联：推翻 `openai_agents/model.py` 原注释里「只支持一个供应商、回滚靠 git revert」的取舍；模型名由来见 [deepseek-flash 升级](../feature/2026-10-02-deepseek-flash-model.md)。

后续：第 2、3 条已调整（只出了思考就失败的调用当场切换、流内错误算供应商失败、路由被拒试另一边），见 [2026-10-26 调整](2026-10-26-failover-after-reasoning-and-in-stream-errors.md)。

## Problem

写作 Agent、路由、输入建议、润色和素材拆解只接了一个供应商，地址、Key、模型名写死。这个供应商宕机、限流或 Key 失效时，所有 AI 功能一起停，只能改代码重新部署。

## Decision

1. **配置**（`agent/core/llm_providers.py`）：已知两个 OpenAI 兼容供应商 DeepSeek 和 MiniMax。`LLM_PRIMARY_PROVIDER` / `LLM_PRIMARY_MODEL`、`LLM_BACKUP_PROVIDER` / `LLM_BACKUP_MODEL` 选主用、备用和各自的模型名，Key / 地址按供应商取（`DEEPSEEK_*`、`MINIMAX_*`）。不配备用时行为与之前完全一样：主用默认 DeepSeek `deepseek-flash`，不读写任何切换状态。主用缺 Key 报错；备用缺 Key 只记警告、按无备用运行。
2. **什么时候换**（`agent/core/llm_failover.py`）：连接失败、超时、429、5xx、401/402/403/404 算「供应商不可用」；400/413/422（上下文超长、请求格式）、我们自己的免费成本预算（`APIException`）和取消都不换。
3. **只在没有输出时当场换**：一次模型调用在收到任何输出之前失败，就在这次调用里改用另一个供应商。它什么都没产生，不会重复生成；这是「不自动重试生成请求」的唯一例外。已经开始输出后断流，照旧报可重试的错误，不在本轮重试。「输出」指第一个非空的输出 delta（正文、推理、拒答、工具参数）或 `response.completed`（openai-agents 0.17.6 会把带 role 的 `content: ""` chunk 变成一个空的 `output_text.delta`，空 delta 不算输出）：SDK 在第一个任意 chunk（可能只有 role）就发 `response.created`，`output_item.added` 等生命周期事件也不是输出。`FailoverChatModel.stream_response` 先把这些前导事件扣住，等到第一个输出或完成才一起交给 runner；只有前导事件就失败时丢掉它们换供应商，runner 只看到备用的一个 `response.created`；正常结束但没有任何内容的空回复仍算成功（2026-10-25 修正：原实现收到 `response.created` 就算已开始，role-only chunk 之后卡住的供应商不会切换。生产 `requirements.txt` 装 openai-agents 0.21.1，回归用 SDK 真实的 `ChatCmplStreamHandler` 在 0.21.1 与 0.17.6 上都确认过，role chunk 的 content 为 None 与空串两种都覆盖；空串那种在 0.17.6 上要靠「空 delta 不算输出」才会切换）。
4. **会话固定**：供应商不可用（不论是否已有输出）都把这个会话（`chat_session.id`）固定到另一个槽位 `LLM_SESSION_PIN_TTL_S`（默认 30 分钟），作者点重试和之后的轮次都用它，不来回切（保住前缀缓存）。成功调用的槽位与会话当前顺序不同时，会话改固定到实际服务的槽位。
5. **熔断**：一个槽位 60 秒内失败 3 次，打开 120 秒；打开期间新会话先试另一个槽位（阈值、窗口、时长可用 `LLM_CIRCUIT_*` 覆盖）。状态放 Redis（有 `REDIS_URL` 时，API 与 Prefect worker 共享），Redis 不可用时退回进程内存。
6. **Agent SDK**：每次 run 一个 `FailoverChatModel`，每次模型调用按上面的顺序选供应商。`reasoning_content` 在同一 run 的工具轮之间一律回传（MiniMax 要求完整回传，DeepSeek 接受）。账本、运行摘要、`MESSAGE_START` 记录实际服务的模型。
7. **推理开关差异**：MiniMax 不能关推理（`thinking.type=disabled` 返回 400），默认 effort 是 max；我们默认发 `MINIMAX_REASONING_EFFORT=high`。要求关推理的短调用（输入建议、润色）优先走能关推理的供应商；只能走 MiniMax 时用最低 effort，并给 `max_tokens` 加 2048 余量。这个偏好只在没有会话固定、熔断也没打开时决定先后（2026-10-25 修正：原实现在固定/熔断调整之后再排序，能关推理的供应商熔断打开时仍排第一，卡住型故障期间输入建议每次都在 25 秒超时前等不到切换）。
8. **计价**（`services/usage/pricing.py`）：按模型的 `pricing_version` 选价目表，账本行写入本模型的版本；MiniMax M3.1-Flash 全天同价（缓存命中 0.1 / 未命中 1 / 输出 4 元每百万）。免费成本预算对没有价目的模型仍然拒绝（fail closed），管理后台按每行的版本计价。
9. `LLMClient` 只保留生产在用的 `acomplete`（同步、流式、结构化方法没有生产调用方，删除而不是一并改造）；素材客户端改名 `MaterialLLMClient`。
10. **读超时按供应商**（2026-10-22 补充）：读超时是两次收到数据之间的最长间隔，包括等首字节的时间。DeepSeek `DEEPSEEK_CLIENT_TIMEOUT_S` 默认 45 秒；MiniMax `MINIMAX_CLIENT_TIMEOUT_S` 默认 120 秒。MiniMax 处理长输入时首字节慢：实测约 32 万字符、未命中缓存的输入要等约 49 秒。生产输入最长到 26 万 token，10-21/22 有 14 次 run 因 45 秒读超时直接报错。

## Alternatives considered

- **只靠会话自己发现主用不可用，不做全局熔断**。最强理由：实现最简单，正好是需求原话。被否：一次超时最多要等 45 秒 × 3 次重试，主用宕机时每个会话都要先白等一轮。
- **任何失败（含已开始输出）都在本轮换备用重来**。最强理由：作者少点一次重试。被否：已输出的正文和工具写入会重复，计费与「不自动重试生成请求」的约定冲突。
- **会话一旦切到备用就一直用到会话结束**。最强理由：前缀缓存最稳。被否：主用恢复后长期占用备用，备用通常更贵或额度更少；30 分钟过期后再按熔断状态重新选择。
- **用 LiteLLM 之类的路由库**。最强理由：现成的重试与回退。被否：新增依赖，且无法表达「只在无输出时换」、会话固定和我们自己的成本预算守卫。

## Consequences

- 收益：主用不可用时写作、路由、建议、润色、素材拆解自动改用备用，不用改代码重新部署；主备和模型名只是环境变量。
- 代价：失败那次调用的成本预留不退（免费预算按上限扣着，与之前对失败调用的处理一致），换备用时同一轮会多扣一次预留。
- 代价：会话固定和熔断是尽力而为的状态，Redis 故障时各进程各自判断；同一 run 中途换供应商时，回传的推理内容来自另一个模型。
- 代价：接入新模型要先加价目，否则免费用户调用被拒；管理后台顶部的价目说明仍只显示 DeepSeek 的价目表，每行费用按各自版本计算。
