# Agent Note: 备用供应商会因缺 reasoning_content 拒收时不切换

## Problem

[2026-10-26 的决定](../architecture/2026-10-26-failover-after-reasoning-and-in-stream-errors.md) 让 MiniMax 在只出了思考、或流中报错时改由 DeepSeek 接这次调用。线上 10-27 至 10-29 共 19 次流中切换，其中 4 次 DeepSeek 直接回 HTTP 400：`The reasoning_content in the thinking mode must be passed back to the API`，两边都失败，作者看到报错。

4 次失败都发生在同一个 run 里 MiniMax 先有 2–3 次完全没思考（思考 0 字、reasoning 0 token）的工具调用之后；成功的切换里没有这种调用。openai-agents 0.21.1 的 `Converter.items_to_messages` 只在思考文本非空时给 assistant 消息带 `reasoning_content`，所以这些工具调用轮回放时缺这个字段。MiniMax 接受这种消息；DeepSeek 思考模式要求最后一条用户消息之后每条带工具调用的 assistant 消息都回传它。

## Decision

- `ProviderKind.requires_tool_turn_reasoning` 标记会因此拒收的供应商（DeepSeek 为真）。
- 主供应商失败、准备换到备用供应商之前（流式与非流式两条路径），`model._rejects_missing_tool_turn_reasoning` 用 SDK 自己的转换器把这次输入转成要发的消息，检查最后一条用户消息之后有没有缺 `reasoning_content` 的工具调用消息。有就不切换，原样抛出主供应商的错误，并记一条 `LLM failover skipped: the other provider would reject this input`。转换本身出错时当作没有缺口，按原来的方式切换。
- 不改错误分类：MiniMax 的流中错误与超时仍是可重试错误，前端照常给重试按钮；计费规则不变。
- 流中错误已经把这个会话固定到备用供应商（不计熔断），作者重试时是新请求，输入从头重建，缺口不在最后一条用户消息之后，DeepSeek 能接。备用供应商没有被调用，也不记它的失败。

## Alternatives considered

- **照样切换，但这次及之后的 DeepSeek 调用关掉思考。** 最强理由是能把这一轮直接救回来，作者不用重试。被否：缺口会一直留在这个 run 的输入里，剩下的调用都得不思考，写作质量下降；这种情况约每天 2 次，作者手动重试即可，且新请求自然落到 DeepSeek。
- **给缺的消息补空字符串 `reasoning_content`。** 最强理由是改动最小、不影响思考。被否：没有确认 DeepSeek 是否接受空值，验证需要付费调用；猜错时仍是同样的 400。
- **维持现状。** 最强理由是不加代码。被否：每次都多发一个注定失败的请求，日志里的错误也指向了错误的供应商。

## Consequences

- 这种情况下作者看到的是 MiniMax 原本的可重试错误，不再白发一个 DeepSeek 请求；重试大概率由 DeepSeek 完成。
- 这一轮不会被救回，已经写进文件的内容照原规则保存和计费。
- 判断依赖 SDK 的转换规则；SDK 升级后若开始为空思考补字段，这个检查自然不再触发。
