# Agent Note: 发布交接时旧进程让进行中的生成在关机宽限内收尾

相关：[平台 15 分钟上限与终止帧](../bug-fix/2026-10-13-agent-stream-platform-deadline-and-terminal-guard.md)、[时限终止帧先于取消收尾](../bug-fix/2026-10-14-deadline-terminal-before-cancellation-cleanup.md)、[墙钟收尾提醒](../bug-fix/2026-10-21-wall-clock-wrap-up.md)、[停止与断线不继续生成](2026-10-09-agent-graceful-stop-and-no-output-refund.md)、[设备挂起的中断不当作异常](../bug-fix/2026-10-21-agent-stream-device-interruptions.md)。

Status: implemented

## Problem

生产每天发版 2–7 次。Railway 先让新实例接管新请求，再给旧进程发 SIGTERM；Uvicorn 停止接收新连接，进行中的请求最多再等 295 秒（`--timeout-graceful-shutdown`），之后直接取消，Railway 在 300 秒（`drainingSeconds`）杀进程。生成的墙钟时限是 780 秒，SIGTERM 时还要跑超过 295 秒的一轮会被硬切断：前端收到传输错误而不是终止帧，部分历史、计费结算与终态写回都在后台任务里，进程退出时可能来不及完成。

2026-10-15 至 10-21 的 11 次 server 部署日志里，旧进程两次在 SIGTERM 后等待进行中的生成（98 秒、99 秒），两次都在宽限内正常完成，新实例同期已在服务新请求；同期客户端的 `STREAM_ERROR` 与部署时间对不上（10-18 无部署却有 8 次）。所以发版目前不是中断的主要来源，风险集中在 SIGTERM 时剩余时间超过宽限的长生成。

## Decision

- `agent/core/server_drain.py`：lifespan 启动时把收尾挂在 Uvicorn 的 SIGTERM 处理器之前（只在主线程安装，原处理器照常执行），信号到达时把 `begin_drain` 排进事件循环。
- 收尾开始后 `AgentRunMeter.past_wrap_up` 立即为真：每次模型调用都按「时间快到了」提醒收尾，工作流不再开新的 agent run，多数生成会自然结束。
- `api/agent.py` 的收尾监听把所有进行中 SSE 泵的时限提前到 `AGENT_DRAIN_STOP_AFTER_S`（240 秒）之后（`SSEStreamPump.end_within`，只提前不延后）；收尾期间开始的流同样处理。到点走既有的时限收尾：先发终止帧，再按取消路径落库部分历史、补存进行中的文件正文并结算。终止帧换成 `ERR_AGENT_SERVER_RESTARTING`（「ZenStory 正在更新……回复『继续』就能接着写」，不可重试、可退还），与墙钟超时同一条计费规则，只把 `billing_reason` 记为 `server_restarting`。补存的助手消息记 `stop_reason=run_deadline_exceeded`，「继续」能结构化恢复；原因由路由层在取消送达之前写到 `RunOutcome.deadline_stop_reason`（2026-10-25 起，见 [时限截停的 stop_reason 由路由层交给取消补存](../bug-fix/2026-10-25-deadline-stop-reason-from-route.md)。此前按已用时长推断，截停的一轮会被记成 `cancelled`，之后又被改成 `error`，无法恢复）。
- lifespan 关闭时在 SIGTERM 后 290 秒的预算内（最多 30 秒）等后台的退还、终态写回与 `AgentService` 的取消清理任务落定，再排空用量记录。
- Railway 300 秒排空与 Uvicorn 295 秒宽限保持不变。

## Alternatives considered

- **断线后服务端继续运行、新进程接着推送。** 最强理由：作者看到完整回答。被否：反转 10-09 的决定，需要跨进程事件存储、续接协议，并改动断线计费、退款、会话租约与流水刷新。
- **延长 Railway 排空与 Uvicorn 宽限到超过 780 秒。** 最强理由：所有生成都能自然完成。被否：Railway 排空上限与发布时长都会受影响，旧进程长时间占用资源；仍需终止帧兜底。
- **收到 SIGTERM 立即停止所有生成。** 最强理由：最快释放旧进程。被否：大多数生成能在宽限内自然完成，立即停止会把本可完成的一轮变成中断。
- **为发版中断的一轮一律退还。** 最强理由：中断不是作者造成的。未采用：已有产出的一轮作者拿到了正文；沿用墙钟超时的规则（没有实质产出才退还）不引入新的计费政策，作者回复「继续」接着写。

## Consequences

- 收益：发版时长生成以明确的终止帧和「继续」入口结束，部分正文、计费和终态在进程退出前落定；短生成先收到收尾提醒，更多在宽限内自然完成。
- 代价：SIGTERM 后旧进程上的生成会提前收尾（阶段总结而非完整交付）；超过 240 秒仍未结束的一轮被截停。信号挂钩依赖 Uvicorn 在 lifespan 启动前安装处理器的顺序，升级 Uvicorn 时需确认。多进程（`WEB_CONCURRENCY` > 1）时每个 worker 各自处理。
