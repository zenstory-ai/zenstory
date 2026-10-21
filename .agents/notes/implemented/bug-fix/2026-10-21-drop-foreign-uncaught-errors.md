# Agent Note: 错误追踪丢弃只来自第三方脚本的未捕获错误

相关：[PostHog 统一经 analytics 包装](../architecture/2026-04-29-posthog-behind-analytics-wrapper.md)。

Status: implemented

## Problem

PostHog 错误追踪里有一批与我们代码无关的未捕获错误：`Script error.`（跨源脚本，27 次 / 9 人）、`ResizeObserver loop completed…`、`LIDNotifyId is not defined`（匿名注入脚本）、`La`（报在 `/dashboard` 第 196 行，而应用 HTML 只有 36 行且没有内联脚本）、钱包与广告插件报错。它们占据问题列表，掩盖真正需要处理的错误。

## Decision

`sanitizeCaptureResult`（PostHog `before_send`）对 `$exception` 事件：当每个异常都是未处理的（`mechanism.handled === false`），并且要么有堆栈但没有一帧来自本站的脚本文件（同源且路径以 `.js` / `.mjs` / `.ts(x)` 等结尾），要么没有堆栈且消息属于固定的浏览器噪音清单（`Script error.`、两种 ResizeObserver 消息）时，丢弃该事件。我们通过 `captureException` 主动上报的错误、任何含本站脚本帧的错误、以及其他没有堆栈的错误（例如动态 import 失败）都保留。

## Alternatives considered

- **在 PostHog 项目设置里配置抑制规则。** 最强理由：不改代码，可随时调整。未采用：规则不在仓库里、不可审查，也不随环境复制到 staging；本地规则与现有 URL 清洗放在同一个出口更一致。
- **丢弃所有没有本站帧的未捕获错误，包括无堆栈的。** 最强理由：清理更彻底。被否：动态 import 失败、`fetch` 的 `TypeError` 等我方异步错误常常没有堆栈，会被一起丢掉。

## Consequences

- 收益：错误追踪聚焦于我们能修的问题。
- 代价：真正由第三方脚本导致、但影响我方功能的问题不再出现在错误追踪里；无堆栈的其他插件错误（如钱包、广告超时）仍会少量出现。
