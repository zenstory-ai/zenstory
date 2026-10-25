# Agent Note: 时限截停的 stop_reason 由路由层交给取消补存

相关：[跨轮路由延续](2026-10-08-agent-routing-continuity.md)（本 note 取代其中「按已用时长区分时限截停」一条）、[发布交接收尾](../architecture/2026-10-21-deploy-drain-ends-running-generations.md)、[时限终止帧先于取消收尾](2026-10-14-deadline-terminal-before-cancellation-cleanup.md)。

Status: implemented

## Problem

10-08 起，取消路径补存部分历史时按 `elapsed >= AGENT_RUN_WALL_CLOCK_TIMEOUT_S`（780 秒）区分时限截停与作者停止。10-21 的发布交接会把进行中流的时限提前到 240 秒之后：这时已用时长远小于 780 秒，补存记成 `cancelled`，随后时限分支写「已中断」终态时 `cancelled` 属于可覆盖原因，又被改成 `error`。两者都不在 `RESUMABLE_STOP_REASONS` 里。终止帧告诉作者回复「继续」，下一轮却拿不到可恢复的状态，只能按新请求理解。

## Decision

- `RunOutcome` 新增 `deadline_stop_reason`。`api/agent.py` 的时限回调（`SSEStreamPump` 的 `on_deadline`）先把它设为 `run_deadline_exceeded`。泵在同步调用 `cancel()` 和这个回调之后才第一次 await，所以取消送达 `process_stream` 时原因已经在 `RunOutcome` 上。
- `process_stream` 的取消补存按 `stop_kind`（作者停止 / 断线）→ `deadline_stop_reason` → `cancelled` 的顺序取原因，删除 `partial_save_stop_reason` 的时长推断。作者停止、断线的优先级不变，ledger flush 与结算顺序也不变。
- `run_deadline_exceeded` 不属于可覆盖原因，「已中断」终态只写 `stop_outcome`，`stop_reason` 保留，「继续」可以结构化恢复。发布交接与墙钟超时都记这个原因。计费标签仍然分开（`server_restarting` / `run_deadline_exceeded`）。

## Alternatives considered

- **保留时长推断，只把阈值换成泵的实际时限。** 最强理由：改动最小，`process_stream` 不必知道路由层。没采用：泵的时限只有路由层知道，传进来还是一个路由层到 service 的通道。而且时长比较在边界上本来就有歧义，直接传原因更简单。
- **新增 `server_restarting` 作为可恢复原因。** 最强理由：落库原因与计费标签一致。没采用：要改 router 的恢复集合与前端展示，对作者来说两种情况都是「回复继续接着写」。
- **`task.cancel(msg)` 携带原因。** 10-08 已经否决过：GeneratorExit 路径拿不到取消消息。

## Consequences

- 收益：发布交接截停的一轮可以用「继续」接着写。原因来自真实事件，不再靠计时比较，也不会把时限前一刻的作者停止误记成时限截停。
- 代价：`RunOutcome` 多一个字段。正确性依赖泵「先同步调用 cancel 与 on_deadline，再 await」的顺序，改 `sse_pump._stop_for_deadline` 时要保持这个顺序。回归测试：`test_deadline_hands_the_resumable_stop_reason_to_the_cancelled_run`、`test_cancelled_partial_save_persists_stop_reason_and_routing`。
