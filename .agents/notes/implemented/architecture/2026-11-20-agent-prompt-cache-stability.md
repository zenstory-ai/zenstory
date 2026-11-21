# Agent Note: 消除 `tool_choice="none"` 前缀缓存断裂并引入活跃会话短 TTL wire-prefix 缓存

## Problem

2026-11-20 对生产 `llm_usage_event`（`source='agent'`，`MiniMax-M3.1-Flash`）与运行日志的只读根因审计显示：单循环 agent 整体加权缓存命中率为 91.51%（近 72h 为 92.55%），正常追加步骤命中率达 97.45%–97.89%，但仅 8.6%–9.4% 的调用（`hit% < 50%`）制造了约 63% 的全部 cache-miss token（MiniMax 缓存未命中与命中的价格比为 10×：¥1.0 vs ¥0.1 / 1M tokens）。可消除的未命中集中在两处代码原因和一处供应商路由行为：

1. **`tool_choice="none"` 破坏 MiniMax 工具前缀（占全部 miss 的 ~27%–31%）**：
   - [2026-11-17](../bug-fix/2026-11-17-loop-body-reply-without-tools.md) 为待写 `<file>` 正文步骤设置了 `tool_choice="none"`（当时尚未实测前缀缓存影响，列为发布后复查项），`compact_history` 也设置了 `tool_choice="none"`。
   - 生产实测证明：即使请求仍携带完全一致的 `tools` 列表，MiniMax 服务端只要看到 `tool_choice="none"` 就会在聊天模板中剥离系统提示词之后约 4,300 token 的工具段，导致每轮第一次 `<file>` 正文写入缓存骤降到约 9.8k–10.5k（仅剩系统提示词，单次 miss 60k–266k），并把整轮分裂成 `auto` / `none` 两棵缓存树反复乒乓（占总 miss 18.7%–20.1%）；轮前压缩调用同样被击穿（单次 miss 200k–309k，占总 miss 8.5%–11.1%）。
2. **跨轮 DB 回放与上一轮实时 wire 输入分叉（占 TTL 内全部 miss 的 ~18%–23%）**：
   - Turn N+1 从数据库重建历史时，Turn N 之前的轮次都能命中，但在 Turn N 的第一个 token 就发生分叉：`<turn_context>` 未落库、`call_id` 改写为 `h{row.id}_{i}`、工具输出折成前端卡片口径、`<file>` 正文与 `reasoning_content` 及轮内续跑提醒被剥离。5 分钟内连续提问时，`step_1` 命中率从单步轮后的 85.85% 跌至 11+ 步轮后的 53.58%。
3. **MiniMax 服务端副本路由抖动（占全部 miss 的 ~3%–5%）**：
   - 同一轮工具循环中偶发请求落到无缓存副本（`hit=0/128`），紧接着下一步又回到原副本命中 `step k-1`。

本记录反转 [2026-11-17](../bug-fix/2026-11-17-loop-body-reply-without-tools.md) 对待写正文步骤强设 `tool_choice="none"` 的决定（旧记录保留原文不改写）。

## Decision

1. **移除待写正文与历史压缩路径上的 `tool_choice="none"`**：
   - `apps/server/agent/openai_agents/model.py` 删除 `_body_reply_settings` 及其在 `FailoverChatModel.get_response` / `stream_response` 中的调用；清理仅服务于该开关的 `ToolContext` 死代码（`_steering_at_arm` / `steering_since_armed` / `steering_since_file_bodies_armed` / `author_steering_count`）。待写 `<file>` 正文请求保持与普通步骤完全一致的 `tools` 和默认 `tool_choice`（未设 / `"auto"`），两次回复协议继续由 `tools_adapter.py` 的同步 `file_body_pending` 守卫 + `runner.py` 的 `_stop_run_after_tools` + `loop/engine.py` 的 `FILE_BODY_RESUME` 纠偏强制执行。
   - `apps/server/agent/loop/compaction.py` 移除 `compact_history` 中的 `tool_choice="none"`，继续携带 `_chat_tools()`，依靠 `COMPACT_PROMPT` 与已有的 `_extract_valid_summary`（拒绝结构化 `tool_calls` / `function_call` 与正文泄漏标签并重试一次）约束纯文本摘要；同时在 `_request_messages` 中传入与主循环一致的 `should_replay_reasoning_content=_replay_reasoning_content`，保证压缩请求与上一轮实时消息逐字节对齐。
2. **新增活跃会话短 TTL Redis wire-prefix 缓存（`apps/server/agent/loop/wire_prefix_cache.py`）**：
   - 键名 `agent:wire_prefix:{session_id}`；TTL 默认 `900s`（15 分钟，匹配 MiniMax KV-cache 窗口，环境变量 `AGENT_WIRE_PREFIX_CACHE_TTL_SECONDS`，`<=0` 关闭）；体积上限默认 `2 MiB`（`AGENT_WIRE_PREFIX_CACHE_MAX_BYTES`，超限跳过并清理旧键）。
   - 严格校验信封：`schema_version`、`project_id`、`user_id`、`last_assistant_message_id`、`system_prompt_sha256`、`provider_key`（`provider:model:key_slot`，由 `FailoverChatModel` 记入 `AgentRunMeter.last_served_provider_key`）与 JSON 化的 SDK `items`。
   - **读取（Turn N+1）**：先按既有路径跑 `load_loop_history` 取得权威数据库状态（`last_message_id`、`last_prompt_tokens`、`author_messages`、压缩退避与截断计数）；当无 `retry_carried_compaction` 且能解析当前预期 `provider_key` 时，异步（`asyncio.to_thread`）读取并逐项核对信封。任何字段不匹配、过期、未配 `REDIS_URL` 或 Redis 异常（30s 熔断冷却）均 fail-open 回退到 `load_loop_history`。命中时直接将 `loop_history.items` 替换为缓存的 SDK `items`，使轮前压缩与主循环首步均复用完整前缀。
   - **兼容 SDK wire 项估算**：更新 `agent/loop/history.py` 的 `estimate_provider_items_tokens`，同时统计列表结构的 `content`（如 `output_text`）与 `summary`（如 `reasoning` 的 `summary_text`），确保缓存命中的 wire 项在触发压缩和 `bound_history_for_provider` 时 token 估算准确。
   - **写入（Turn N 结束）**：仅当本轮正常落库且拿到 `assistant_message_id`、`assistant_stop_reason` 为 `"end_turn"` 或干净软着陆（`"run_deadline_exceeded"` / `"max_turns_exceeded"`）、无流错误/取消/停止/阻塞状态卡（仅允许无未完或回滚空文件的 `wall_clock` / `max_turns` 暂停卡）/迟到未见插话/待写文件或未保存替换、且 `native_run_holder["items"]` 通过配对完整性校验（无悬空未回答 `function_call` 或末尾悬空 `reasoning`）与 `last_served_provider_key` 均非空时写入；凡本轮落库新消息但跳过写入时均主动 fail-open 删除旧缓存键（见 [2026-11-21 后续调整](2026-11-21-cache-soft-land-and-session-key-sticky.md)）。严禁在日志中打印原始 `items`。
   - **失效**：清空会话（`api/chat.py:clear_session`）、新建会话停用旧会话（`api/chat.py:_deactivate_and_create_session`）、修改卡撤销/删除卡恢复原地改写 `ChatMessage.tool_calls`（`services/chat_tool_cards.py`）、重试接过压缩记录以及落库后追加迟到插话时，通过 `services/infra/redis_client.py` 共享的 `delete_agent_wire_prefix_cache`（避免 `services -> agent` 反向依赖）主动删除缓存键。
3. **关于 MiniMax 粘性路由参数（`prompt_cache_key` / `user`）的调研结论：本次不注入**：
   - 核对 MiniMax 与 DeepSeek OpenAI 兼容文档及社区问题：MiniMax 仅在 `/v1/responses` 接口文档化支持 `prompt_cache_key`，其 `/v1/chat/completions` 接口对未识别参数执行严格校验，传入 `prompt_cache_key` 会返回业务错误 `2013`（`invalid parameters`）；DeepSeek `/chat/completions` 同样不支持 `prompt_cache_key`（且 `openai-agents` SDK 仅在官方 OpenAI `base_url` 下发送该字段）。
   - 标准 `user` 字段在 MiniMax 与 DeepSeek 文档中均未声明用于 KV-cache 副本亲和路由，且未经实测不能假设不会触发严格参数校验或引入意外限流行为。因此本次按「不猜测未文档化参数」原则跳过该字段，后续若 Fix #1 与 Fix #2 上线后 3%–5% 的副本抖动仍构成主要尾部成本，可另行授权做单次实测或评估迁移 MiniMax 至 `/v1/responses`。

## Alternatives considered

- **在 `tool_choice="none"` 时同时省略 `tools` 或切换 `tool_choice="auto"` + 提示词加强**。最强理由：省略 `tools` 与 MiniMax 服务端模板一致、可能避免部分校验差异。未采用：历史消息里已存在 `tool_calls` / `tool` 角色消息，省略 `tools` 同样无法命中带 `tools` 的前序缓存，还可能触发供应商校验错误；去掉 `tool_choice="none"` 直接让前后步骤共享同一棵缓存树，收益最大且改动最小。
- **把完整 wire-format `items`（含 `<turn_context>`、`reasoning_content`、`<file>` 正文）直接持久化到 PostgreSQL `chat_message`**。最强理由：不依赖 Redis，进程重启或 15 分钟之后依然能逐字节回放。未采用：MiniMax KV-cache 在 10–15 分钟后自然过期，超过 TTL 后再回放冗长的 `<turn_context>`、原始工具输出与 `<file>` 正文反而放大冷启动输入体积与数据库存储膨胀，且改动持久化契约与隐私保留周期；短 TTL Redis 缓存精准对齐 KV-cache 生命周期，过期自动退回紧凑 DB 历史，零表结构变更。
- **在 `/v1/chat/completions` 强行注入 `prompt_cache_key` 或 `user` 尝试提升副本亲和度**。最强理由：可能再收回 3%–5% 的副本抖动 miss。未采用：`prompt_cache_key` 已被确认会在 MiniMax `/v1/chat/completions` 触发 `2013` 参数拒绝且 DeepSeek 不支持；`user` 无缓存亲和文档依据且未经实测验证，不值得拿 3%–5% 尾部收益去冒 400 生产故障风险。

## Consequences

- **收益**：
  - 消除写作轮内因 `tool_choice="none"` 导致的工具段剥离与双缓存树乒乓，以及轮前压缩的整段前缀失效（预计消除 ~27%–31% 全部 cache-miss token）。
  - 15 分钟活跃会话内的连续提问与轮前压缩可复用上一轮完整 wire 前缀（含 `<turn_context>`、原始 `call_id`、工具结果、`<file>` 正文与 `reasoning_content`），回收 ~18%–23% 活跃跨轮 miss。
  - 所有 Redis 操作 fail-open，Redis 不可用或关闭开关时完全退回原有 DB 回放行为，不改变前端展示、计费/退还、并发锁或工具守卫语义。
- **代价与风险**：
  - 取消 `tool_choice="none"` 后，若模型在待写 `<file>` 正文回复中偶发违反提示词调用工具，会像 11-17 之前一样由 `file_body_pending` 守卫拦截并走一次 `FILE_BODY_RESUME` 纠偏；压缩若偶发调用工具则由 `_extract_valid_summary` 拒绝并重试一次。与每次写正文必然击穿 60k–266k 缓存相比，偶发纠偏的期望成本低一个数量级，但发布后仍需观察 `file_body_pending` 与压缩重试计数。
  - 15 分钟连续多轮命中 wire-prefix 缓存时，历轮 `<turn_context>` 会保留在缓存前缀中直到超过 300k token 触发压缩或超过 2 MiB 上限回退 DB；因命中价为未命中价的 1/10 且压缩可高命中收敛，总体输入成本显著下降。
  - Redis 内存会增加活跃会话的短时占用（单会话封顶 2 MiB，15 分钟自动过期）。
