# MiniMax 写作循环与压缩迁移至 `/v1/responses` + `prompt_cache_key`

## Problem

生产排查发现，MiniMax（`MiniMax-M3.1-Flash`）在 `/v1/chat/completions` 上虽然支持前缀缓存，但缺乏会话级粘性路由键：即使请求体前缀逐字节一致，同一会话的连续步骤仍可能落到不同推理副本，出现 `cached_tokens <= 200` 的副本抖动断崖。实测 staging MiniMax `/v1/chat/completions` 对 `extra_body={"prompt_cache_key": ...}` 与任意未知字段静默忽略（返回 HTTP 200），无法依赖 Chat Completions 透传缓存路由键。

与此同时，2026-11-23 对 staging `MiniMax-M3.1-Flash` 的实测表明：
- `/v1/responses` 原生支持 `prompt_cache_key`、`store=False`、`reasoning={"effort": "high"}` 与流式 SSE，配合 `openai-agents` 的 `OpenAIResponsesModel` 和 ZenStory 全部 9 个工具 schema，连续 9 个热步（跨 5 轮 `Runner.run_streamed` 工具调用与 `<file>` 正文回复）命中率稳定在 96.4%–99.2%，零副本断崖，热步 TTFT 23–75 ms；
- `/v1/responses` 与 `/v1/chat/completions` 在 MiniMax 侧的 KV 缓存相互隔离：紧接 Responses 第 10 步后走 Chat Completions 压缩仅命中 0.8%（128/16,803），而同 `prompt_cache_key` 走 `/v1/responses` 压缩命中 96.4%（16,384/17,002）；
- 直接切到 `OpenAIResponsesModel` 存在五处工程缺口：成本兜底守卫仅包了 `chat.completions.create`、命中 `max_output_tokens` 时流式 `response.incomplete` 会触发 SDK `ModelBehaviorError` 崩溃、轮间压缩仍走 Chat Completions、MiniMax Responses 的 `reasoning` 项把思考放在 `content[].reasoning_text`（`summary=[]`）导致向 DeepSeek 备源切换时丢失 `reasoning_content`、以及滚动发布期间 Redis `wire_prefix_cache` 需隔离两套协议的输入项格式。

## Decision

将 MiniMax 主循环与轮间压缩迁移至 OpenAI Responses API（`/v1/responses` + `store=False` + 会话确定性 `prompt_cache_key`），保留环境变量即时回滚开关与全部既有计费、截断、备源切换保护：

1. **运行时接口切换与零发版回滚（`agent/core/llm_providers.py`）**：
   - 在 `ProviderKind` / `ModelProvider` 增加 `api_surface`（`responses` | `chat_completions`）与 `uses_responses_api`；`MINIMAX` 通过 `MINIMAX_API_SURFACE` 控制，默认 `responses`（非法值安全回退默认值），设为 `chat_completions` 即刻完整回滚；`DEEPSEEK` 严格保持 `chat_completions`。
   - 提供统一函数 `prompt_cache_key_for_session(session_id)`（`zs:{session_id}`，超过 64 字符时取确定性 SHA-256 截断），主循环与压缩共用同一路由键。
2. **成本兜底、输入清理与计费边界全覆盖（`services/usage/model_call_guard.py`、`services/usage/cost_budget.py`、`utils/sanitize.py`）**：
   - `install_async_cost_guard` 除 `chat.completions.create` 外，同时包裹 `client.responses.create` 与 `OpenAIResponsesModel` 流式实际调用的 `client.responses.with_streaming_response.create`（`install_sync_cost_guard` 同步覆盖 `client.responses.create`）。
   - `cost_budget.py` 适配 Responses 载荷（`input`、`instructions`、`max_output_tokens`、多模态项 fail-closed、1 元素材试用上限截断），并在进入真实传输前调用 `notify_model_transport_start()` 保持 `RunOutcome.provider_attempted` 唯一计费边界；流式包装器同时从 `chunk.usage` 与 `chunk.response.usage` 结算（覆盖 `response.completed` 与 `response.incomplete`）。
   - `sanitize_responses_text` / `sanitize_responses_input` 清理 `instructions` 与 `input` 中的非法 C0/DEL 控制字符，保留换行、制表符与不透明 `encrypted_content`。
3. **`response.incomplete(reason="max_output_tokens")` 优雅截断（`agent/openai_agents/model.py`、`agent/openai_agents/runner.py`）**：
   - `FailoverChatModel.stream_response` 拦截 `response.incomplete` 且 `incomplete_details.reason == "max_output_tokens"` 的终态事件：置 `last_response_output_capped=True`，将其转换为保留原始 `response`（含 `output` 与 `usage`）的 `ResponseCompletedEvent` 并提前结束内层流，避免 `OpenAIResponsesModel` / `run_loop` 抛出致命 `ModelBehaviorError`。非 token 上限原因（如 `content_filter`）仍原样上抛。
   - `runner.py` 综合 `last_response_output_capped` 与 `OUTPUT_CAP_RATIO` 判定输出截断，完整保留残稿落库、`OUTPUT_CAPPED_KEY`、`_BODY_CUT_CONTEXT` 续写与纯思考截断 `OUTPUT_TRUNCATED_STOP_REASON` 及 `on_llm_end` 用量入账。
4. **轮间压缩同步走 `/v1/responses`（`agent/loop/compaction.py`、`agent/core/llm_failover.py`）**：
   - 新增 `llm_failover.acreate_response`（支持 Responses 主源失败时经 `chat_kwargs_builder` 无缝切换到 Chat Completions 备源）。
   - 主源使用 Responses 时，`compact_history` 发送与 `OpenAIResponsesModel` 逐字节一致的工具清单、`instructions=system_prompt`、`store=False` 及同一 `prompt_cache_key`；新增 `_extract_valid_response_summary` 校验输出项（拒绝结构化 `function_call`、`incomplete`/`failed` 状态、空文本与泄漏工具协议标签，保留 1 次带 `_COMPACT_RETRY_NOTE` 重试）。主源为 DeepSeek 或回滚 `MINIMAX_API_SURFACE=chat_completions` 时仍走既有 `acreate_chat_completion`。
5. **模型分发、参数隔离与 DeepSeek 备源思考桥接（`agent/openai_agents/model.py`、`agent/core/llm_failover.py`）**：
   - `_sdk_model_for` 按 `provider.uses_responses_api` 分发 `OpenAIResponsesModel` 或 `OpenAIChatCompletionsModel`（缓存键包含 `api_surface`）。
   - `_apply_surface_settings` 仅为 Responses 供应商注入 `store=False` 与 `extra_args["prompt_cache_key"]`，切回 Chat Completions 备源时彻底剔除，不向 DeepSeek 泄漏未知参数。
   - `bridge_responses_reasoning_for_chat_completions` 在不修改原历史的前提下，浅拷贝将 Responses `reasoning` 项中 `content[].reasoning_text` 映射进 `summary`，使 `_rejects_missing_tool_turn_reasoning` 与 `ChatConverter.items_to_messages` 保留向 DeepSeek 备源的 `reasoning_content` 回放能力。
   - `llm_failover.py` 扩展 `_FAILURE_PARAMETER_FIELDS` 覆盖 Responses 参数，并在 `is_provider_unavailable` 中纳入 `httpx2` 超时与传输异常。
6. **滚动发布缓存隔离（`agent/loop/wire_prefix_cache.py`）**：
   - `provider_cache_key` 对 Responses 供应商追加 `:responses` 后缀（如 `minimax:MiniMax-M3.1-Flash:primary:responses`），避免滚动发布或切换 `MINIMAX_API_SURFACE` 期间 Redis 中混用两套线格式历史。

## Alternatives considered

- **继续停留在 `/v1/chat/completions` 并通过 `extra_body` 传 `prompt_cache_key` / `user`**：改动最小，无需适配 `OpenAIResponsesModel` 和 `model_call_guard`。但 staging 实测证明 MiniMax `/v1/chat/completions` 对未知/错误类型的 `extra_body` 字段静默放行，无法证实路由粘性生效，也不能彻底消除生产副本抖动断崖。
- **仅迁移主循环到 `/v1/responses`，保留 `compact_history` 在 `/v1/chat/completions`**：实现范围略小，但实测证明两接口缓存完全隔离（0.8% vs 96.4%），300k+ token 长会话每次轮间压缩都会全量冷未命中，直接抵消缓存收益并拉长压缩首字延迟。
- **全量切换所有调用点（含 `suggest`、`polish`、素材库）到 `/v1/responses`**：短期单轮或结构化 JSON 调用（如 DeepSeek `json_object` 与 `thinking=disabled`）不依赖多步会话前缀粘性，无必要扩大改动面，继续保留在 `chat.completions` 上更稳妥。

## Consequences

- **收益**：
  - MiniMax 主循环与轮间压缩共享同一套 `/v1/responses` KV 缓存树和会话 `prompt_cache_key`，显著降低副本抖动导致的缓存断崖与热步 TTFT；
  - 成本兜底、1 元素材试用上限、计费边界、流式截断残稿续写、DeepSeek 备源切换均在 Responses 路径上保持等价安全语义；
  - 出现上游异常时可通过 `MINIMAX_API_SURFACE=chat_completions` 在数秒内无发版回滚，且 `wire_prefix_cache` 自动按后缀隔离不会污染旧会话。
- **代价与约束**：
  - `model_call_guard.py` 与 `compaction.py` 需同时维护 Chat Completions 与 Responses 两条线格式路径，后续升级 `openai` / `openai-agents` SDK 时需回归验证 `with_streaming_response.create` 与 `ResponseIncompleteEvent` 行为；
  - MiniMax Responses 返回的 `reasoning` 项附带不透明 `encrypted_content`（每思考步约数百字节），会小幅增加 Redis `wire_prefix_cache` 压缩后体积（受现有 2 MiB 上限与 fail-open 保护）。
