# Agent Note: 空闲滑动 TTL 会话粘性 Key 路由与软着陆 `wire_prefix_cache` 写入/失效

Status: implemented

反转 [MiniMax 双 Key 的会话粘性与单 Key 冷却](2026-11-07-minimax-key-sticky-routing-and-cooldown.md) 第 1 条的固定墙钟时间窗轮换（保留原文不改写），并补充 [前缀缓存稳定性与 wire-prefix 缓存](2026-11-20-agent-prompt-cache-stability.md) 的写入与失效规则。

## Problem

`prod-v2026.11.20.2` 发布后的 ~9.5 小时生产观测（覆盖北京时间早高峰，404 次 agent MiniMax 调用）验证了 PR #430 的两项修复在热路径上效果显著（0 次 `tool_choice="none"` 缓存断崖、所有 `wire_prefix_cache` 命中均达 73.4%–98.6%），但也暴露了两个仍造成大额 cache miss 的代码问题：

1. **按固定墙钟整点分桶在活跃会话中途击穿热缓存**：生产开启 `MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT=20` 后，`llm_providers._route_for_run` 对 `f"{session_key}:{int(time.time() // LLM_KEY_ROUTING_WINDOW_S)}"` 分桶，导致重度活跃会话在跨 UTC 整点（00:00Z / 02:00Z）时在 5–8 分钟相邻轮次之间从 `secondary` 翻转到 `primary`，单会话当场损失 **532.5k 热缓存 token**（轮前压缩命中率骤降至 `0.04%` / `0.1%`）。同时，若直接永久固定 `session_key` 永不重抽，跨天重度会话会长期压在额度较小的次 Key 上。
2. **墙钟软着陆的一轮跳过写入 `wire_prefix_cache` 且未清理旧键**：约 22%（10/46）的计费轮次因接近墙钟收尾时间而软着陆，但由于 `runner.py` 在 `turns_exhausted=True` 时清空了 `completed_items`，且 `service.py` 仅允许 `assistant_stop_reason == "end_turn"`、无任何 `status_cards` 且 `not run_meter.past_wrap_up` 时写入，导致软着陆轮次既不写新缓存也不删旧键，6 分钟内的「继续」轮次被迫回退 DB 回放（命中率跌至 0.1%–45.1%）。

## Decision

1. **Redis 空闲滑动 TTL 会话粘性双 Key 路由（`apps/server/agent/core/llm_providers.py`、`apps/server/agent/core/llm_failover.py`）**：
   - 保留 `LLM_KEY_ROUTING_WINDOW_S`（默认 `1800.0` 秒 = 30 分钟），将其语义从固定墙钟分桶改为 **Redis 空闲滑动 TTL**（键 `llm:key_route:{session_key}`，最小 TTL 60 秒）。
   - **护栏 1（`weight=0` 零延迟切回主 Key）**：在访问内存 run 路由或 Redis 之前先判 `if not secondary_key or weight == 0: return primary_key, KEY_SLOT_PRIMARY`，关闭次 Key 后即使 Redis 仍有未过期 `"secondary"` 键也立即 0 秒切回主 Key。
   - **护栏 2（单次 run 内存锁定）**：同一 `agent_run_id` 首次解析出槽位后写入进程内 `_key_routes[run_id]`（LRU 4096），本 run 后续所有模型调用（含轮前压缩与主循环）零额外 Redis IO 并严格锁定同一槽位。
   - **护栏 3（空闲滑动 TTL + `SET NX` 竞态安全）**：run 首次带 `session_key` 路由时读 `llm:key_route:{session_key}`；命中 `"primary"|"secondary"` 时执行 `EXPIRE key ttl` 顺延滑动窗口并沿用该槽位（活跃会话跨 UTC 整点永不翻转）；未命中（新会话或空闲超过窗口自然过期）时用主 Key 对 `f"{session_key}:{run_id or nonce}"` 做 HMAC-SHA256 按 `weight` 抽签，并以 `SET key slot NX EX ttl` 原子写入，并发竞态落败时 `GET` 采用胜者槽位。
   - **护栏 4（事件循环与 fail-open 安全）**：`llm_failover.aordered_providers` / `arecord_failure` / `apin_session_to_other` 整体经 `asyncio.to_thread` 执行（保留 `agent_run_id` ContextVar，永不阻塞事件循环）；`REDIS_URL` 未配置或 Redis 异常时（30 秒退避冷却）静默 fail-open 退回确定性 `session_key` HMAC。
   - **护栏 5（冷却优先级不覆盖粘性键）**：`llm_failover._apply_key_cooldowns()` 仍优先于粘性槽位在单 Key 401/402/403/429 冷却期内切至 `other_key()`，且绝不改写 `llm:key_route:{session_key}`；冷却到期后恢复原粘性槽位。
2. **干净软着陆写入 `wire_prefix_cache` 并在跳过时主动失效旧键**：
   - `apps/server/agent/openai_agents/runner.py`：仅在真正触发 `MaxTurnsExceeded` 硬异常（`max_turns_exhausted=True`）时跳过 `result.to_input_list()`；正常结束与所有软着陆（`run_deadline_exceeded` / `max_turns_exceeded` / `model_call_budget_exhausted`）均把完整 `completed_items` 写入 `native_run_holder`。
   - `apps/server/agent/loop/wire_prefix_cache.py`：新增 `is_well_formed_wire_prefix(items)` 校验，要求非空 dict 列表、每个 `function_call` 均有非空 `call_id` 并在后续非工具消息或列表结束前被匹配的 `function_call_output` 应答、无孤立 `function_call_output`、且尾部不为悬空 `function_call` 或 `reasoning`。写入与读取双端校验，写入被拒或序列化失败时主动失效旧键。
   - `apps/server/agent/service.py`：允许 `assistant_stop_reason in {"end_turn", "run_deadline_exceeded", "max_turns_exceeded"}` 且仅含干净软着陆暂停卡（`type == "iteration_exhausted"`、`layer in {"tool_call", "collaboration"}`、`limit in {"wall_clock", "max_turns"}` 且无 `unfinishedFiles` / `rolledBackFiles`）、无错误/取消/`stop_kind`/保存前迟到插话/待写文件或未保存替换、且 `completed_wire_items` 通过 `is_well_formed_wire_prefix` 时写入缓存。
   - 凡一轮在正常保存路径、取消补存路径（`user_stopped` / `client_disconnected` / 硬超时）或异常兜底补存路径中落库了新消息却未写入新缓存时，统一异步 fail-open 调用 `ainvalidate_wire_prefix(session_id)` 删除 Redis 旧键。

## Alternatives considered

- **永久按 `session_key` 哈希固定，不设空闲过期**。最强理由：零 Redis 状态、实现最简。未采用：单个跨天高频重度会话会永久停留在次 Key 上，长期偏离配额分摊比例；而空闲滑动 TTL 在会话活跃期（每次间隔 < 30 分钟）同样 0 翻转，且在会话真正冷掉（KV-cache 也早已在 15 分钟过期）之后自动重抽，兼得两全。
- **扩大固定墙钟时间窗（例如 24 小时）**。最强理由：仍无 Redis 状态。未采用：固定墙钟边界依然会在跨 UTC 零点（北京时间早 8 点）集中击穿活跃会话的热缓存。
- **只在跳过写入时删除旧键，不为软着陆轮次写入 `wire_prefix_cache`**。最强理由：保守，只修 stale key 问题。未采用：软着陆占线上计费轮次的 ~22%，且用户几乎总在数分钟内点击「继续」；放弃缓存会让这部分高频热续写白白退回 DB 回放并损失整轮前缀。通过 `is_well_formed_wire_prefix` 校验与阻塞状态卡白名单隔离空文件回滚/异常态，可安全收回这部分缓存命中。

## Consequences

- **收益**：
  - 活跃会话跨 UTC 整点不再切换 MiniMax Key，消除跨整点 500k+ 级热缓存清空；空闲超过 30 分钟（服务端 KV-cache 已自然过期）的会话自动重抽，长期维持配置分摊比例。
  - `weight=0` 零延迟切回主 Key，单 run 内零额外 Redis IO，Redis 故障静默 fail-open 且全部异步 off-loop 执行。
  - 干净软着陆后的「继续」轮次（含轮前压缩）可直接命中上一轮完整 wire 前缀；任何不安全/跳过/取消/错误轮次均立即清理旧键，不再留下 `last_message_id` 失配的僵尸键。
- **代价与风险**：
  - 每个新 run 的首次路由增加一次轻量 Redis `GET` + `EXPIRE`（或 `SET NX EX`），已通过 `asyncio.to_thread` 与 30s 熔断退避隔离延迟与故障风险。
  - 软着陆轮次最后一次调用曾附带瞬态收尾提醒（不写入 `to_input_list()`），因此 Turn N+1 会命中收尾前全部前缀（通常占 99%+），仅最后一段阶段总结与新用户消息为增量未命中，显著优于回退 DB 回放。
