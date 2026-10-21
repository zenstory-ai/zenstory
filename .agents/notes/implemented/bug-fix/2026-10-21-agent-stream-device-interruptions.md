# Agent Note: 生成中断显示可读提示，设备挂起造成的中断不再当作异常上报

相关：[停止与断线不继续生成](../architecture/2026-10-09-agent-graceful-stop-and-no-output-refund.md)、[平台 15 分钟上限与终止帧](2026-10-13-agent-stream-platform-deadline-and-terminal-guard.md)、[AI 结果事件](../feature/2026-10-05-activation-and-ai-outcome-events.md)。

Status: implemented

## Problem

PostHog 近 30 天 `AgentStreamError: STREAM_ERROR` 96 次 / 33 人，是错误追踪里最多的一类。它表示终止帧之前 `fetch()` 或 `reader.read()` 被浏览器拒绝。分布显示大量来自 Android / iOS，持续时间从几秒到 39,596 秒（设备休眠期间的墙钟时间）；900–901 秒的一组发生在 10-13 运行期限改为 780 秒之前，已由既有修复覆盖。客户端从不区分「标签页在后台被挂起或离线」与服务端故障，两者都作为异常上报。同时错误卡片正文直接显示浏览器原文（"network error"、"Load failed"、"Failed to fetch"），违背「不向作者展示原始错误」的约定；`STREAM_CLOSED` 已使用 `chat:stream.connectionInterrupted`。

## Decision

- `agentApi` 的传输类 `STREAM_ERROR`（非 UI 回调抛错）改用 `chat:stream.connectionInterrupted`（「网络中断，请重试」）作为错误文案；错误码、可重试判断、已显示的部分回答与重试入口不变。
- `agentStreamTelemetry` 在每轮生成期间累计页面隐藏时长，结果事件新增 `hidden_ms`，失败事件新增 `online`。传输类失败（`STREAM_ERROR` / `STREAM_CLOSED` / `NO_BODY`）若本轮页面曾隐藏或浏览器离线，只记 `ai_chat_failed`，不再 `captureException`；页面始终可见且在线的传输失败，以及任何 HTTP 5xx，仍照常上报。
- 不改变服务端断线即停止、计费、退款、会话租约与流水刷新行为。

## Alternatives considered

- **断线后服务端继续运行并提供状态/回放接口。** 最强理由：手机切后台回来能看到完整回答。未采用：会反转 10-09 的决定，牵涉断线计费与退款、会话租约、流水刷新和跨进程事件存储，需要单独的决策。
- **完全不上报传输类失败。** 最强理由：大部分是网络与设备原因。被否：发版重启、边缘故障等真实问题也表现为传输失败，可见在线标签页的失败仍需要进入错误追踪。
- **只改文案。** 最强理由：最小改动。被否：错误追踪继续被设备挂起淹没，真正的服务端或部署中断难以发现。

## Consequences

- 收益：作者看到可理解的中断提示；错误追踪中的 `AgentStreamError` 主要剩下可见、在线标签页的中断，更容易与发版时间和服务端日志对照。
- 代价：页面短暂切到后台又回来后发生的真实服务端中断也只体现在 `ai_chat_failed`（带 `hidden_ms`），需要从事件而非异常列表查看。发版期间进行中的生成仍会被中断（旧进程收到 SIGTERM 后最多等待 295 秒），本批未处理。
