# Agent Note: 纯会话粘性 Key 路由与软着陆 `wire_prefix_cache` 写入/失效

Status: implemented

反转 [MiniMax 双 Key 的会话粘性与单 Key 冷却](2026-11-07-minimax-key-sticky-routing-and-cooldown.md) 第 1 条的时间窗轮换（保留原文不改写），并补充 [前缀缓存稳定性与 wire-prefix 缓存](2026-11-20-agent-prompt-cache-stability.md) 的写入与失效规则。

## Problem

`prod-v2026.11.20.2` 发布后的 ~9.5 小时生产观测（覆盖北京时间早高峰，404 次 agent MiniMax 调用）验证了 PR #430 的两项修复在热路径上效果显著（0 次 `tool_choice="none"` 缓存断崖、所有 `wire_prefix_cache` 命中均达 73.4%–98.6%），但也暴露了两个仍造成大额 cache miss 的代码问题：

1. **按整点时间窗轮换 Key 在活跃会话中途击穿热缓存**：生产开启 `MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT=20` 后，`llm_providers._route_for_run` 对 `f"{session_key}:{int(time.time() // LLM_KEY_ROUTING_WINDOW_S)}"` 分桶，导致重度活跃会话在跨 UTC 整点（00:00Z / 02:00Z）时在 5–8 分钟相邻轮次之间从 `secondary` 翻转到 `primary`，单会话当场损失 **532.5k 热缓存 token**（轮前压缩命中率骤降至 `0.04%` / `0.1%`）。
2. **墙钟软着陆的一轮跳过写入 `wire_prefix_cache` 且未清理旧键**：约 22%（10/46）的计费轮次因接近墙钟收尾时间而软着陆，但由于 `runner.py` 在 `turns_exhausted=True` 时清空了 `completed_items`，且 `service.py` 仅允许 `assistant_stop_reason == "end_turn"`、无任何 `status_cards` 且 `not run_meter.past_wrap_up` 时写入，导致软着陆轮次既不写新缓存也不删旧键，6 分钟内的「继续」轮次被迫回退 DB 回放（命中率跌至 0.1%–45.1%）。

## Decision

1. **纯会话粘性双 Key 路由（`apps/server/agent/core/llm_providers.py`）**：
   - 移除 `LLM_KEY_ROUTING_WINDOW_S` 与时间窗分桶；`_route_for_run(run_id, session_key)` 直接使用 `session_key if session_key else run_id`，保持 HMAC-SHA256（以主 Key 为密钥）与 `MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT` 按会话稳定分流。
   - 保留 `other_key()` + `LLM_KEY_COOLDOWN_S` 的单 Key 限流/账号错自动冷却与转移兜底；单次 run 仍全程记住首次选定的路由（`_key_routes` LRU）。
2. **干净软着陆写入 `wire_prefix_cache` 并在跳过时主动失效旧键**：
   - `apps/server/agent/openai_agents/runner.py`：仅在真正触发 `MaxTurnsExceeded` 硬异常（`max_turns_exhausted=True`）时跳过 `result.to_input_list()`；正常结束与所有软着陆（`run_deadline_exceeded` / `max_turns_exceeded` / `model_call_budget_exhausted`）均把完整 `completed_items` 写入 `native_run_holder`。
   - `apps/server/agent/loop/wire_prefix_cache.py`：新增 `is_well_formed_wire_prefix(items)` 校验，要求非空 dict 列表、每个 `function_call` 均有非空 `call_id` 并在后续非工具消息或列表结束前被匹配的 `function_call_output` 应答、无孤立 `function_call_output`、且尾部不为悬空 `function_call` 或 `reasoning`。写入与读取双端校验，写入被拒或序列化失败时主动失效旧键。
   - `apps/server/agent/service.py`：允许 `assistant_stop_reason in {"end_turn", "run_deadline_exceeded", "max_turns_exceeded"}` 且仅含干净软着陆暂停卡（`type == "iteration_exhausted"`、`layer in {"tool_call", "collaboration"}`、`limit in {"wall_clock", "max_turns"}` 且无 `unfinishedFiles` / `rolledBackFiles`）、无错误/取消/`stop_kind`/保存前迟到插话/待写文件或未保存替换、且 `completed_wire_items` 通过 `is_well_formed_wire_prefix` 时写入缓存。
   - 凡一轮在正常保存路径、取消补存路径（`user_stopped` / `client_disconnected` / 硬超时）或异常兜底补存路径中落库了新消息却未写入新缓存时，统一异步 fail-open 调用 `ainvalidate_wire_prefix(session_id)` 删除 Redis 旧键。

## Alternatives considered

- **把 `LLM_KEY_ROUTING_WINDOW_S` 调大到 24 小时或改为基于空闲超时重置**。最强理由：仍可周期性把长期重度会话重新抽签，减少单把次 Key 长期承压。未采用：固定 24 小时窗口仍会在跨 UTC 零点（北京时间早 8 点早高峰）集中击穿活跃会话缓存；而空闲超时需要在 Redis 维护额外会话粘性状态。线上已有 `LLM_KEY_COOLDOWN_S` 300s 单 Key 冷却兜底，单把 Key 额度紧张时会自动平滑转移到另一把 Key，纯 `session_key` 零额外状态且彻底消除跨界翻转。
- **只在跳过写入时删除旧键，不为软着陆轮次写入 `wire_prefix_cache`**。最强理由：保守，只修 stale key 问题。未采用：软着陆占线上计费轮次的 ~22%，而且几乎所有软着陆后用户都会在几分钟内点击「继续」；放弃缓存会让这 22% 的高频热续写白白退回 DB 回放并损失整轮前缀。通过 `is_well_formed_wire_prefix` 校验与阻塞状态卡白名单隔离空文件回滚/异常态，可在保证安全的前提下完整收回这部分缓存命中。

## Consequences

- **收益**：
  - 活跃会话跨 UTC 整点不再无故切换 MiniMax Key，消除跨整点 500k+ 级热缓存清空。
  - 干净软着陆后的「继续」轮次（含轮前压缩）可直接命中上一轮完整 wire 前缀；任何不安全/跳过/取消/错误轮次均立即清理旧键，不再留下 `last_message_id` 失配的僵尸键。
- **代价与风险**：
  - 按会话固定 Key 后，单个超长生命周期会话会一直留在同一把 Key 上，短期分摊比例的方差会略高于按小时重抽签；若次 Key 额度触发 429/402，继续由既有 `LLM_KEY_COOLDOWN_S` 单 Key 冷却自动转移兜底。
  - 软着陆轮次最后一次调用曾附带瞬态收尾提醒（不写入 `to_input_list()`），因此 Turn N+1 会命中收尾前全部前缀（通常占 99%+），仅最后一段阶段总结与新用户消息为增量未命中，显著优于回退 DB 回放。
