# Agent Note: 降低 DeepSeek 现金支出（缩短故障钉死、流式轮间压缩与章节摘要切回 MiniMax）

Status: implemented

关联：调整 [模型供应商主备切换](2026-10-20-llm-primary-backup-failover.md) 的会话固定默认时长、补充 [MiniMax `/v1/responses` 迁移](2026-11-23-minimax-responses-api-prompt-cache-key.md) 的流式压缩与记账归属，并替代 [章节摘要 JSON 合同](../bug-fix/2026-10-24-material-summary-json-and-safe-errors.md) 中把章节摘要单点锁死在 DeepSeek 的策略。

## Problem

生产环境近 7 天（`2026-11-19` 至 `2026-11-25 18:12 BJ`）的 `llm_usage_event` 与 Railway 日志审计表明，主供应商 `MiniMax-M3.1-Flash` 可用率达 **99.89%**（24,890 次写作调用仅 24 错 + 4 超时），无 429 限流撞顶，但备源 `deepseek-flash` 仍产生 **¥28.67（高峰日 ¥6–7/天）** 的按量现金支出，加上未入账的压缩切换冷 miss，主要来自三处机制缺陷：

1. **故障后 30 分钟长尾钉死放大 29.3×**：`LLM_SESSION_PIN_TTL_S` 默认 `1800` 秒（30 分钟），28 次偶发 MiniMax 报错放大成 **821 次** DeepSeek 写作调用（¥18.56，占 DS 账单 64.7%），其中 **83.4%（¥15.48）** 发生在首次切换之后的 30 分钟持续钉死窗口内。
2. **非流式轮间压缩触发 120s 读超时并漏记账**：
   - `compact_history` 通过非流式 `responses.create` 请求 ~275k–300k token 历史 + `reasoning_effort=high`，成功耗时 p95 已达 86.6s、max 112.5s，频繁顶破 `MINIMAX_CLIENT_TIMEOUT_S=120s` 触发 `APITimeoutError`，切去 DeepSeek 吃冷 miss 并把会话钉死 30 分钟（11-23 上线 Responses API 后至少引发 4 起 ¥0.5–1.3 的长尾 burst）。
   - `compact_history` 调用 `agent_usage_attribution()` 时漏传 `request_context`，导致 `attribution.user_id=None`，使轮间压缩既无法写入 `llm_usage_event`，又经 `cost_budget.current_user_id()` 遮蔽了绑定的 `_context_user_id` 而绕过免费用户 ¥15 日成本兜底。
3. **付费素材拆书的章节摘要硬锁 DeepSeek**：`chapter_summary_tasks.py` 传入 `response_format={"type": "json_object"}`，被 `_DOCUMENTED_JSON_OBJECT_MODELS` 过滤为只走 DeepSeek，占近 7 天 DeepSeek 素材费用的 **96.0%（¥5.30 / ¥5.52）**（单日拆书高峰达 ¥2.79）；而同一条管线其余 4 个任务（情节点、角色提及、角色整合、剧情聚合）均不传 `response_format`，在 MiniMax 上依赖 `extract_json_from_response` + `json_repair` 与校验器稳定运行。

## Decision

1. **缩短故障会话固定默认时长**（`agent/core/llm_failover.py`、`.env.example`）：
   - 将 `LLM_SESSION_PIN_TTL_S` 默认值从 `1800` 秒降至 `300` 秒（5 分钟），仍保留环境变量覆盖能力。足以覆盖单轮恢复与作者立即重试，大幅削减单次瞬时故障后的 30 分钟备源现金外溢。
2. **Responses 轮间压缩改流式收集并修复记账与成本兜底归属**（`agent/core/llm_failover.py`、`agent/loop/compaction.py`、`agent/service.py`、`services/usage/cost_budget.py`）：
   - `llm_failover.acreate_response` 新增 `stream_collect: bool = False`。对 Responses 供应商开启后发送 `stream=True`（经既有 `model_call_guard._BudgetedAsyncStream` 预留并结算成本），按 `provider.kind.read_timeout_s()` 监控有意义 delta 空闲超时并收集终态 `response.completed` / `response.incomplete` 返回等价 `Response` 对象；`response.failed` 转为流内 `openai.APIError`，断流转为 `APIConnectionError`，严格保持“仅在首段回答输出前切换备源，已输出后不重复生成”语义并完整写入 `llm_usage_event`。
   - `compact_history` 接收可选 `request_context` 并传给 `agent_usage_attribution(request_context)`；`AgentService.process_stream` 显式透传 `request_context`。
   - `cost_budget.current_user_id()` 改为仅当 `explicit.user_id` 非空时覆盖 `_context_user_id`，彻底杜绝 `user_id=None` 的归属对象静默遮蔽免费日成本兜底。
3. **章节摘要切回主供应商 MiniMax**（`flows/atomic_tasks/summaries/chapter_summary_tasks.py`）：
   - 移除 `generate_chapter_summary_task` 的 `response_format={"type": "json_object"}`，显式传 `reasoning_effort="low"`，走正常主备路由（主用 MiniMax、故障时可切换 DeepSeek），继续沿用既有 Prompt 约束、`extract_json_from_response`（含非截断 `json_repair`）、`validate_chapter_summary_data` 与隐私安全解析日志。
   - 保留 `llm_failover.py` 的 `_DOCUMENTED_JSON_OBJECT_MODELS` / `JSONResponseFormatUnavailableError` 机制供未来显式要求协议级 `json_object` 的调用方使用；保留免费素材试拆 `thinking_enabled=False`（7 天仅 ¥0.22，优先保住试拆输出上限不被思考截断）。

## Alternatives considered

1. **只通过 Railway 环境变量覆盖 `LLM_SESSION_PIN_TTL_S`，不改代码默认值**：
   - 最强论据：零代码改动、即刻生效。
   - 拒绝原因：环境漂移难以追踪，staging / 新环境仍沿用 1800 秒默认值；将默认值直接改为 300 秒同时保留 env 覆盖，更透明可控。
2. **把轮间压缩降级为 `reasoning_effort="low"` 或直接拉大 `MINIMAX_CLIENT_TIMEOUT_S` 到 240s**：
   - 最强论据：改动行数最少，无需流式收集逻辑。
   - 拒绝原因：降 `reasoning_effort` 可能损伤长会话交接摘要质量（[compaction.py](../../../../apps/server/agent/loop/compaction.py) 原设计明确保持默认思考强度）；单纯抬高超时到 240s 会让真实挂死连接白等 4 分钟。改走 `/v1/responses` + `stream=True` 与主循环完全一致，既不降思考质量，又让活跃输出永远不撞 120s 读超时，同时保留 120s 空闲卡死检测。
3. **把 MiniMax 加入 `_DOCUMENTED_JSON_OBJECT_MODELS` 继续发送 `response_format={"type": "json_object"}`**：
   - 最强论据：无需改动 `chapter_summary_tasks.py`。
   - 拒绝原因：MiniMax OpenAI 兼容端点不承诺严格执行 `json_object` 协议约束，将其写入白名单会产生虚假安全承诺；移除该参数并依赖与其余 4 个素材任务一致的 Prompt + `extract_json_from_response` + `json_repair` + `validate_chapter_summary_data` 校验更诚实，且恢复了主备双向 failover 能力。
4. **同步把免费素材试拆与 `/agent/suggest` 一并切走**：
   - 最强论据：可一步把 DeepSeek 降到接近零。
   - 拒绝原因：免费试拆 7 天仅 ¥0.22，但其 `cost_budget` 会在请求前把 `max_tokens` 钳回 `trial_limit`（不留 `THINKING_HEADROOM_TOKENS`），切给不能关思考的 MiniMax 有截断 JSON 风险；`/agent/suggest` 留待结合 MiniMax Coding Plan 请求次数余量单独评估（P2）。

## Consequences

- **收益**：
  - 写作助手单次 MiniMax 偶发报错不再把会话钉在 DeepSeek 30 分钟，预计削减 60%–75% 写作 failover 现金支出。
  - MiniMax 轮间压缩消除长响应非流式 120s 超时引发的连环 failover，并修复了压缩漏记账与免费用户压缩绕过 ¥15 成本兜底的隐患。
  - 付费拆书的章节摘要回到主供应商 MiniMax（`reasoning_effort="low"`），与同书其他拆解阶段共享粘性 Key 路由，且恢复故障时向 DeepSeek 备源切换的可用性。
- **代价与后续验证**：
  - MiniMax 偶发持续性故障超过 5 分钟时，会话每 5 分钟会重新试探一次主源（产生一次预输出切换尝试）；可通过 `LLM_SESSION_PIN_TTL_S` 环境变量按需调回。
  - 章节摘要去掉 `response_format={"type": "json_object"}` 后，需在合并 staging 后抽真实章节回归观察非 JSON 失败率与摘要质量，再决定是否推进生产发布。
