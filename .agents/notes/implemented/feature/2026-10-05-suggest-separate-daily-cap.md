# Agent Note: 下一步建议不占 AI 对话额度，改用独立日上限；前端只在必要时自动请求

Status: implemented

## Problem

`POST /agent/suggest` 只要 `SuggestService.llm` 存在就预扣一次 `ai_conversations`（与 `/agent/stream` 共用每日额度，免费版 20 次），只有结果恰好等于兜底文案才退款。而前端 `ChatPanel` 会在用户没有任何操作时自动触发它：打开/切换项目时（即使命中本地缓存也会在后台再刷新一次）、每轮流式完成后 1.5 秒、以及闲置 10 秒的计时器（每轮消息数变化都会重新武装）。结果是每送一条消息实际扣 2～3 次额度，免费用户约 6 轮就被 402 挡住；额度只剩 1 次时，后台建议可能先把它用掉，用户真正要发的消息反而被拒。

## Decision

- 后端 `api/agent.py` 的 `/suggest` 不再检查、预扣或退还 AI 对话额度（删除 `_refund_ai_conversation` 与 `_is_pure_fallback_suggestion`）。成本由两个按用户的限流依赖兜住：既有的小时限流 `agent_suggest`（30 次 / 3600 秒）和新增的每日上限 `agent_suggest_daily`（`SUGGEST_DAILY_MAX_REQUESTS=100` / 86400 秒）。两者都走 `middleware.rate_limit.require_user_rate_limit`（Redis 优先、失败退回内存），超限返回 429。
- 路由层守卫用例改为：每个 LLM 端点必须有小时级按用户限流，`/suggest` 另需恰好一个每日上限。模块 docstring 的约定改为「鉴权 + 项目权限 + 成本上限 + 按用户限流」，并写明三个端点各自的成本上限是什么。
- 前端 `fetchSuggestions` 收到 429 时静默返回空数组（不记错误日志、不提示），界面退回本地兜底建议。
- 前端 `ChatPanel` 只保留两个自动触发点：项目打开时本地没有有效缓存（10 分钟 TTL）才请求一次，有缓存就直接用、不再后台刷新；每轮对话完成后 1.5 秒请求一次，并按「项目 + 消息数 + 末条消息 id」去重，同一位置不重复请求。闲置 10 秒的自动刷新整体移除。手动「刷新建议」按钮保持不变。

## Alternatives considered

- **只在自动触发时免费，手动刷新仍扣额度**。最强理由：保留「有意识的 LLM 调用要付费」的一致口径。被否：建议是 15 字级别的短输出，单次成本远低于一轮写作；为手动刷新单独维护「扣/不扣」两条路径，前端还要在额度见底时处理 402，复杂度与收益不成比例。日上限已经把最坏成本封顶。
- **剩余额度 ≤1 时前端跳过自动建议**。最强理由：不改后端计费即可避免「建议吃掉最后一次额度」。被否：每轮仍会多扣 1～2 次，只是把问题推迟到最后一次；前端也拿不到实时剩余额度，需要额外请求。
- **保留闲置刷新但拉长到 60 秒**。最强理由：用户长时间不动时建议仍能跟上最新上下文。被否：建议只依赖最近消息，消息没变时刷新只会得到同样的内容；每轮完成后的那一次已经覆盖了上下文变化。

## Consequences

- 收益：用户看到的「每日 20 次对话」与实际可发送的次数一致；打开项目、闲置都不会再消耗任何额度；每轮最多一次自动建议请求。
- 代价：`/suggest` 的厂商成本不再计入用户额度，只受每用户每日 100 次、每小时 30 次约束；多账号仍可线性放大这部分成本。本地缓存 10 分钟内重新打开项目会显示旧建议，直到下一轮对话完成才刷新。
- 每日上限是固定窗口（Redis `INCR + EXPIRE`）或滑动窗口（内存后端），两种后端的边界行为略有差异。

## Verification

`cd apps/server && venv/bin/pytest tests/test_api/test_round3_agent_api.py -q`（额度耗尽仍可用、不调用 `consume_ai_conversation`、每日上限 429、守卫用例）；`cd apps/web && pnpm exec vitest run src/components/__tests__/ChatPanel.mount.test.tsx src/lib/__tests__/agentApi.test.ts`（有缓存不请求、闲置 30 秒不再请求、每轮完成只请求一次、429 静默）。还原 `ChatPanel.tsx` 后三个前端用例全部失败。
