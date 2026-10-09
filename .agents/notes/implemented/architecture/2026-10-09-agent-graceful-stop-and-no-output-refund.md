# Agent Note: 作者停止走服务端收尾；停止或断线且没有产出时退还额度

Status: implemented

本 note 推翻了 `architecture/2026-10-05-agent-stream-error-and-refund-contract.md` 中「用户取消或断线 → 一律计费」这一条（其余计费规则不变）。

## Problem

10-03 至 10-09 的新用户对话里，45 次 AI 回复是空的（没有正文、没有文件写入），7 个用户最后看到的就是空回复，26 次用户把原话重发了一遍。逐条对上 Railway HTTP 日志后，空回复几乎都不是服务端出错，而是连接被前端或用户中断：

- 27 次是作者点了停止：取消后 1～3 秒内前端走完「停止」收尾（拉取聊天记录、再拉建议）。其中约 10 次发生在发送后 3 秒内，发送按钮原地变成停止按钮，手机上点两下就停掉了；约 15 次在 4～16 秒，屏幕上还看不到任何正文，作者以为卡住了。
- 10 次是生成中离开了聊天页（首页、定价页、技能页），聊天面板卸载即中断。
- 7 次之后再没有任何请求（关页、切应用、断网）；1 次整页刷新。

两个后果：

1. **服务端分不清「点了停止」和「断线」。** 前端停止就是 abort fetch，服务端只看到 `CancelledError` / `GeneratorExit`，日志里一律是 `user_cancelled`。
2. **没有任何产出也扣额度。** 计费契约把取消和断线都记为计费；免费用户一天 10 条，误触一次、等不及停掉一次、再重发一次，就是 2～3 条额度换来一个空气泡。

## Decision

**服务端收尾的停止（`POST /api/v1/agent/stop`）**

- 请求体 `{agent_run_id}`（`/stream` 响应头 `X-Agent-Run-ID`，32 位小写十六进制），按用户限流 240 次/小时，不触发 LLM，幂等，运行不存在或已结束时同样返回 `{stop_requested: true}`。
- 停止信号在 `agent/core/run_stop.py`：键为 `agent:stop:{user_id}:{run_id}`（TTL 1800 秒）。停止端点只写当前用户命名空间，运行方只读自己用户的键，所以别人拿到 run id 也停不了这次运行，不需要额外的归属校验。同一 worker 内用登记的 `asyncio.Event` 立即唤醒；配置了 Redis 时同时写 Redis，别的 worker 上的运行按 0.5 秒轮询看到（`RunStopWatch.wait`，Redis 健康检查沿用 steering 的 `_redis_available`）。
- `SSEStreamPump.request_stop()`：消费端优先于已排队的帧响应停止，取消后台 task（生成器按原有取消路径收尾：后台落库部分历史、补存文件正文、释放会话），然后抛 `StreamStoppedByUser`。
- `event_generator` 收到 `StreamStoppedByUser` 后在仍然打开的连接上补发 `workflow_stopped {reason: "user_stopped", message: "已停止生成。"}` 与 `done`，结算后若退还落库再发 `quota_refunded {kind: "stopped"}`，正常关闭连接。

**计费（`StreamBillingTracker.decide`）**

- 参数 `user_cancelled` 改名为 `client_disconnected`，新增 `user_stopped`。优先级第一条变为：作者停止或断线时，本轮已有实质产出（非空正文、非空文件正文、写文件工具成功）→ 计费；否则退还。`billing_reason` 分别为 `user_stopped` / `user_stopped_no_output` / `client_disconnected` / `client_disconnected_no_output`（原 `user_cancelled` 不再出现）。只想过（thinking）、只读过文件不算产出。
- 判断产出看「这次运行实际产出了什么」：`SSEStreamPump` 的 `on_item` 在后台 task 里对源生成器的每一帧先记账再入队，断线时队列里还没送到客户端的写入照样算产出，不会出现「文件已写入却退了额度」。
- 断线路径不能挂起：要退还时由 `_schedule_detached_refund` 在后台任务里调 `_refund_quota`（PostgreSQL 下照旧用独立 session），日志记 `refund_scheduled=true`；断线发生前作者已发出停止请求的，按 `user_stopped` 结算。
- `Agent stream billing evaluated` 日志新增 `user_stopped`、`client_disconnected`、`refund_scheduled`，以及 `first_output_ms`（第一次实质产出距请求开始的毫秒数，没有产出为 null），取代 `user_cancelled`。

**前端**

- `agentApi.streamAgentRequest` 在响应头到达时回调 `onRunStarted(agentRunId)`；`stopAgentRun(runId)` 调停止端点。`QuotaRefundKind` 增加 `stopped`。
- `useAgentStream.stop()`：已知 run id 时请求服务端停止并继续读流，`isStopping` 为真；停止请求失败、5 秒（`STOP_GRACE_MS`）内等不到收尾、或再按一次停止时，退回原来的 `cancel()` 直接断开（服务端按断线结算，同样遵守无产出退还）。卸载、切换项目仍用 `cancel()`。

## Alternatives considered

- **维持「取消一律计费」。** 最强理由：取消前模型已经花了 token（路由、思考、读文件），而思考过程对作者可见，有人可能反复「问一句、看思考、停掉」免费拿思路。被否：思考过程是半成品，作者真正要的是正文或文件；这条路的成本已经被免费用户每日 ¥15 成本兜底和按用户限流封住；而误触和等不及停掉占了空回复的大多数，照收额度直接损害留存。
- **前端停止时只在断开前用 `sendBeacon` 报一个原因，服务端仍按断线收尾。** 最强理由：改动最小，不需要服务端在连接上补帧。被否：beacon 与断线到达服务端的先后不确定，计费判定可能已经结束；前端也拿不到「是否退还」的结果，只能自己猜，说明文字可能不准确。
- **只对「发送后 N 秒内取消」退还。** 最强理由：直接针对误触，滥用空间更小。被否：等不及停掉的作者（4～16 秒）同样什么都没拿到；按时间划线让同样「没有产出」的两轮结果不同，作者无法理解。
- **断线后服务端继续把这一轮跑完、结果照常落库。** 最强理由：作者离开再回来能看到完整结果，额度花得值。被否：需要改动卸载即中止的约定和会话占用、历史落库的时序，改动面大；作者关掉页面多数是真的不要了，继续跑会白花成本。本批次先用离开提示（见 `feature/2026-10-09-stop-guard-progress-and-leave-prompt.md`）降低误离开。

## Consequences

- 收益：停止和断线在日志与统计里可以分开；作者停掉一轮没有产出时不扣额度，并且立刻在原连接上得到准确的说明；断线时没有产出也会在后台退还。`first_output_ms` 让「第一个字要等多久」第一次有了服务端口径。
- 代价：停止多了一次 HTTP 往返（前端最多等 5 秒再兜底断开）；跨 worker 的停止最多延迟 0.5 秒，且每个进行中的运行每 0.5 秒读一次 Redis。没有产出的停止和断线不再计费，理论上可被用来反复白看思考过程，靠成本兜底和限流约束。断线退还在后台进行，作者看不到说明。部分历史落库的 `stop_reason` 仍是 `cancelled`，刷新后看不出这一轮是作者停止还是断线。

## Verification

- 后端：`cd apps/server && .venv/bin/pytest tests/test_api/test_agent_stream_hardening.py tests/test_agent/test_stream_launch_hardening.py tests/test_agent/test_repeat_read_guard.py tests/test_agent/test_sse_pump_backpressure.py tests/test_api/test_round3_agent_api.py -q --no-cov`：作者停止后原连接收到停止卡片 + done，无产出时退还并发 `quota_refunded(stopped)`、有产出时计费；别的用户写入同一 run id 不会停掉运行；断线无产出在后台退还、有产出计费；`/stop` 登记在按用户限流的端点清单里。
- 前端：`pnpm exec vitest run src/hooks/__tests__/useAgentStream.test.ts src/lib/__tests__/agentApi.test.ts src/lib/__tests__/agentStreamTelemetry.test.ts`：停止时请求服务端并继续读流、请求失败或超时退回断开、run id 未知时直接断开；`onRunStarted` 拿到 `X-Agent-Run-ID`；`stopped` 退还被识别；遥测带 `first_output_ms`，作者停止记为 `ai_chat_stopped`。
