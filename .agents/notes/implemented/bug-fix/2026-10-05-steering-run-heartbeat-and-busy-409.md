# Agent Note: 生成槽周期心跳、僵尸判定缩到 90 秒；会话忙返回 409 ERR_SESSION_BUSY

Status: implemented

## Problem

`service.process_stream` 每次都以 `create_steering_queue_async(..., exclusive_run=True)` 抢占聊天会话唯一的生成槽，槽位只在 `process_stream` 的 finally（或取消时的后台任务）释放。部署或崩溃杀掉进行中的生成时 finally 不会执行，持有记录留在 Redis 的 runs ZSET 里；僵尸判定 `_RUN_HEARTBEAT_TTL_S` 等于键级 TTL（3600 秒），而续期只发生在 run 自己轮询 steering 时。于是重新部署后，用户刷新页面再发消息，后端解析到同一个活跃会话，抢槽失败——`SteeringSessionBusyError` 在 SSE 已经开始后才抛出，被当成泛用的「生成回复时发生错误，请重试」，重试多少次都一样，最长持续 1 小时。带 `session_id` 时则收到错误码为 `ERR_RESOURCE_CONFLICT`、不可重试的 409。用户按停止后立即重发，后台清理还没跑完时也会短暂撞上同样的错误。

## Decision

- `agent/core/steering.py`：`_RUN_HEARTBEAT_TTL_S` 改为 90 秒，新增 `_RUN_HEARTBEAT_INTERVAL_S = 20` 与 `heartbeat_steering_run_async(session_id, run_id)`。Redis 后端以 `ZADD xx` 只续期本 run 的成员（不会复活已释放的 run）并续期 runs 键 TTL；内存后端只更新仍在册的 run 的时间戳。轮询即心跳的既有语义保持不变。
- `service.process_stream` 抢槽成功后立即启动独立的心跳 task，每 20 秒续期一次；正常路径在释放持有之后、取消路径在后台清理完成之后停止它。进程被杀时心跳随之停止，残留持有最多 90 秒后失效。
- `api/agent.py` 在返回 `StreamingResponse` 之前先取出 `process_stream` 的第一个事件（`session_started`，它之前就是会话解析与抢槽）。抢槽抛出 `SteeringSessionBusyError` 时退还刚预扣的额度，返回 409，错误码 `ERR_SESSION_BUSY`（`core/error_codes.ErrorCode.SESSION_BUSY`）；带 `session_id` 的预检 `has_active_runs_async` 也改用同一个错误码。其余预启动异常保持原行为（SSE 终止帧 + 退款）。
- 前端 `agentApi` 收到 409 `ERR_SESSION_BUSY` 时标记为可重试，ChatPanel 显示 i18n 文案（「上一轮回复仍在生成或收尾中，请稍候片刻再发送，或开启新对话」）和重试按钮。
- 2026-10-25 补充（审计 F-28）：`POST /agent/steer` 原先先查 owner、再 RPUSH，两步不原子。本轮最后一次 drain 之后、释放之前到达的引导，接口回 200 queued，随后被释放脚本连同 msgs 一起删除；释放之后的 RPUSH 会重建 msgs（TTL 1 小时），注入同一会话的下一轮。现在：
  - `enqueue_steering_for_user_async` 把 owner 校验、「存在未超时且未封口的 run」与入队放进一个原子单元（Redis Lua `_ENQUEUE_FOR_LIVE_RUN_SCRIPT`；内存后端在 manager 锁内判定）。没有这样的 run 时回 404 `ERR_CHAT_SESSION_NOT_FOUND`，owner 不符仍是 403。从未登记过 run 的占位队列也不再接收引导。
  - `process_stream` 释放前的最后一次 drain（正常路径 finally 里的兜底 drain、取消路径后台补存里的 drain）改用 `drain_and_seal_steering_async`：取出消息的同时把本 run 记入封口集合（Redis `steering:sealed:{session}`，内存 `SteeringQueueEntry.sealed_runs`）。封口不释放独占槽位，新一轮在释放前仍收到 busy 409；释放与僵尸回收会一并清掉封口记录。所以已确认 queued 的引导要么被这次 drain 取走、随本轮历史落库，要么根本不会被确认。
  - 多持有者遗留路径的交还（`requeue_if_other_active`）不再把已封口的 run 算作「其它活跃 run」，避免把消息交给不会再读它的 run。
  - 前端 `sendSteeringRequest` 抛出带 HTTP 状态的 `ApiError`。ChatPanel 收到 404 时不报错，提示「这一轮已收尾，这句会在它结束后作为新消息发出」，本轮结束后用 `handleSendMessage` 发一次；发不出去（新对话未就绪、当日额度用完等）就把这句话放回输入框。其他失败可能已经入队，仍保留输入并报错，不自动重发，所以同一句话不会扣两次额度。旧前端遇到 404 仍是原来的「补充没有发出，请重试」，新前端对旧后端的 404 也能退回发送，两个方向都兼容。

## Alternatives considered

- **成员带进程启动 ID，新进程在 lifespan 启动时回收不属于自己的成员、关闭时主动释放**。最强理由：部署后可以立即解锁，不用等 90 秒。被否：生产是多 worker / 滚动部署，新进程启动时旧进程可能仍在正常收尾自己的 run，按进程 ID 回收会误杀；周期心跳 + 短 TTL 不依赖进程生命周期，语义简单，最坏情况 90 秒。
- **保留 1 小时 TTL，只把错误转成 409**。最强理由：改动最小，不引入新的后台 task。被否：用户仍然要等最长 1 小时；轮询续期在长时间模型调用或工具执行期间不会发生，TTL 无法安全缩短。
- **F-28：最后一次 drain 直接释放槽位，不另设封口**。最强理由：不需要新的 sealed 键，释放后的入队自然 404。被否：释放之后到历史写完之前，新一轮可以抢到槽位并写入用户消息，晚到的引导就会排在新一轮之后，独占生成槽保护的历史顺序被破坏。
- **F-28：释放脚本把残留 msgs 返回给调用方，释放之后再追加进历史**。最强理由：不改变入队接口。被否：追加发生在释放之后，同样会和下一轮的写入交错；而且这时只能事后补救，接口在释放之前仍会对注定没人读的消息回 200。
- **提供「强制接管」端点，让前端在 409 时抢占**。最强理由：用户不必等待。被否：强制接管会让两个 run 同时写同一个会话的历史，正是独占生成槽要防止的情况；需要额外的「通知旧 run 停止」机制，超出本次范围。

## Consequences

- 收益：部署或崩溃后会话最多被锁 90 秒；会话忙时用户看到可理解的提示与重试按钮，而不是反复失败的泛用错误；被拒绝的请求不扣额度。
- F-28 的收益与代价：`/agent/steer` 回 200 时，消息一定会被某一轮读到或写进历史。代价是多一个 sealed 键和两段 Lua 脚本；封口到释放之间，作者的补充会变成下一条普通消息，并按普通消息计入额度（引导本身仍不计额度）。
- 代价：每个进行中的生成每 20 秒多一次 Redis 写。事件循环被阻塞超过 90 秒（远超正常）时，活着的 run 可能被误判为僵尸，另一个请求会抢到同一会话的生成槽。抢槽与会话解析改在路由里执行，这一步发生在 ToolContext 建立之前、请求日志上下文已绑定 `agent_run_id`。仍然没有在生成开始时先落库用户消息：崩溃时这一轮的用户消息依旧会丢失。

## Verification

`cd apps/server && venv/bin/pytest tests/test_agent/test_stream_launch_hardening.py tests/test_api/test_round3_agent_api.py tests/test_agent/test_service.py tests/test_agent/test_round3_steering.py -q`：心跳让老持有保持存活、停止心跳超过 TTL 后新 run 可接管、心跳不复活已释放的 run、TTL 与间隔的取值约束、`process_stream` 生成期间周期续期且结束后停止、未带 `session_id` 时解析到的忙会话返回 409 `ERR_SESSION_BUSY` 且额度退回、带 `session_id` 的预检同样返回 `ERR_SESSION_BUSY`。前端 `src/lib/__tests__/agentApi.test.ts` 覆盖 409 被标记为可重试。

F-28：`tests/test_agent/test_steering_live_run_enqueue.py` 在内存后端和真实 redis-server（CI 用 `ZENSTORY_TEST_REDIS_URL`，本机临时起 unix socket 实例）上各跑一遍。用例让 steer 落在最后一次 drain 与释放之间，断言它要么落库、要么不是 200；同时覆盖释放后 404 且不留任何键、封口后 busy 仍成立、已封口的持有者不接收交还。前端 `src/components/__tests__/round3_frontend_chatpanel.test.tsx` 覆盖：404 在本轮结束后只发一次；500 不退回发送；成功送达的引导不会重发。
