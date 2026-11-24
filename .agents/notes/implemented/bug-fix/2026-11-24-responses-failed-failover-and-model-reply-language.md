# `/v1/responses` 的 `response.failed` 归类为流内上游错误，且回复语言交由模型自主判断

## Problem

1. **MiniMax `/v1/responses` 上游流内失败绕过了 failover 与退费**：当 MiniMax Responses API 在 HTTP 200 SSE 流中发出 `response.failed`（例如 `error.code="internal_error"`）时，`openai-agents` SDK 的 `OpenAIResponsesModel.stream_response` 会抛出 `ModelBehaviorError` 而非 `openai.APIError`。这导致：
   - 尚未产出正文（含仅流出思考片段）时无法自动 failover 到备选供应商；
   - 未恢复终止后 `FailoverChatModel.unrecovered_provider_error` 为空，账单结算将其误归类为 `non_model_error`（扣除用户当日 AI 消息额度）而非 `upstream_model_error`（退还额度）。
2. **正则语言检测误将越南语等拉丁变音语种强行覆盖为英文**：旧多角色图时代为给审稿人透传语言而引入的 `message_manager.detect_author_language`（见 [2026-10-11-agent-identity-reply-language-rewrite-in-place.md](2026-10-11-agent-identity-reply-language-rewrite-in-place.md)）仅按汉字与 ASCII 字母串计数，导致越南语作者消息（无汉字、变音字符间含 ASCII 片段）被判为 `"en"`，并在 `<turn_context>` 强制注入 *"The author wrote this turn's message in English: reply to the author in English this turn."*，直接干扰模型回复语言。该规则本身也属于针对自然语言文本的正则启发式，且在单循环 agent（模型直接看到作者最新原话）下已无存在必要。

## Decision

1. **`response.failed` 统一转为流内 `openai.APIError`**：
   - 在 `agent/openai_agents/model.py` 的 `FailoverChatModel.stream_response` 中，拦截 `event.type == "response.failed"`，提取结构化 `response.error.code` 与 `message` 构造无 HTTP 状态码的 `openai.APIError`（附带 `exc.code`）并先于 SDK 抛出。
   - 复用既有 `llm_failover.is_in_stream_error`（未出正文时自动切备并固定本会话，不触发全局熔断）与 `llm_failover.is_upstream_provider_error`（未恢复时记录 `unrecovered_provider_error` → `error_source="model"` → `upstream_model_error` 退还额度）。
   - 在 `llm_failover.log_provider_failure` 与 `stream_errors.classify_stream_exception` 中对有界协议错误码（`internal_error`、`server_error`、`rate_limit_exceeded`、`invalid_prompt`）补齐结构化日志分类与对外错误码（`ERR_AGENT_UPSTREAM_UNAVAILABLE` / `ERR_AGENT_UPSTREAM_RATE_LIMITED`）。
2. **移除正则作者语言检测，交由模型直接判断回复语言**：
   - 删除 `agent/core/message_manager.py` 中的 `detect_author_language`、`_build_author_language_line` 及相关字符计数正则，并从 `agent/loop/turn_context.py` 移除本轮语言覆盖注入（反转 [2026-10-11-agent-identity-reply-language-rewrite-in-place.md](2026-10-11-agent-identity-reply-language-rewrite-in-place.md) 中通过正则向动态上下文写入本轮语言行的做法）。
   - 将 `MessageManager._build_language_section` 泛化为「严格使用作者最近一条消息的语言（不限中英，含越南语、日语等任意语言）；作者切换语言时立刻跟随；仅当消息无明确语言时沿用本对话语言或界面语言兜底」。

## Alternatives considered

- **仅在 `is_upstream_provider_error` 里直接放行 `ModelBehaviorError`**：实现更短，但 `ModelBehaviorError` 也覆盖 SDK 内部非法工具名、无效参数和 `content_filter` 等非服务故障场景，直接放行会破坏 `2026-11-20-round-billing-model-error-only-refund.md` 确立的计费边界，因此选择在流事件边界仅针对协议事件 `response.failed` 做精确转换。
- **保留 `detect_author_language` 并为越南语/拉丁变音字符打补丁**：可以临时修掉越南语误判，但仍需持续维护各类文字（法/德/西/印尼/罗马音/混写）的正则特例，违背仓库禁用自然语言正则启发式的原则；单循环 agent 直接看见作者最新原话，交由模型判断更简洁且支持任意语种。

## Consequences

- **收益**：
  - MiniMax `/v1/responses` 偶发 `internal_error` / `server_error` / `rate_limit_exceeded` 在正文开始前可自动切备，未恢复时对外返回明确上游错误并准确退还额度。
  - 彻底消除越南语及其他非中英语种被错误强制成英文回复的问题，同时精简了每轮 `<turn_context>` 并清除一处遗留正则启发式。
- **代价**：
  - 若未来模型在中英混排或极短消息下出现回复语言漂移，需要通过系统提示表述与真实对话评估调优，而非靠服务端正则硬编码。
