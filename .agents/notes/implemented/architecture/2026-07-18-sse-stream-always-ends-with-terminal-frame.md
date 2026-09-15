# Agent Note: Agent SSE 流必须以终止帧结束，前端卸载必须中止在途流

Status: implemented

## Problem

`POST /agent/chat` 用 SSE 推送 Agent 输出。若 `process_stream` 之前或之中抛异常（聊天会话解析失败、Redis/DB 故障），连接直接断开而没有终止帧，前端流消费者永远等不到结束；补偿性配额退款又复用了已处于失败事务状态的 session，`PendingRollbackError` 被吞掉，用户被多扣费。反向也成立：用户中途离开页面时 fetch/reader 仍在跑，后端 Agent 继续消耗 token。

## Decision

`api/agent.py` 的 `event_generator` 跟踪 `saw_terminal_event`；任何逃逸异常在重新抛出前 must 先 `yield error_event(...)` 作为终止帧（仅当尚未发出过终止帧），客户端因此总能得到明确的流结束。SQLite 路径上的补偿退款 must 先 `session.rollback()` 再操作。`stream_adapter` 在 finally 中关闭上游 workflow/runner 生成器，pump task 的取消在致命错误退出时确定性执行，不等 GC。前端 `useAgentStream` 的卸载清理 must abort 在途 SSE 并递增 stream epoch；`ChatPanel.loadChatHistory` 在 await 之后重查 `currentProjectIdRef`，慢的旧项目请求 never 覆盖当前项目的消息。

来源：689ae3b (#14)

## Alternatives considered

- **异常时静默断开连接、由客户端自行判断结束**（修复所替换的行为）。最强理由：服务端零改动，前端可以按连接关闭事件收尾。被否：Agent 单轮可运行数分钟，连接关闭无法区分"完成"与"崩溃"，前端只能一直等或误判；review 将其判定为缺陷而非可选方案。

## Consequences

- 收益：前端流消费者的结束条件只有一个——收到终止帧；退款不会因失败事务被吞。
- 代价：新增任何在 `event_generator` 之外产生事件的路径都要接入同一个 `saw_terminal_event` 记账，否则可能发出两个终止帧或一个都没有。

## Verification

`cd apps/server && .venv/bin/pytest tests -k "agent_api or event_generator or refund" -q`；前端 `pnpm exec vitest run src/hooks/__tests__/useAgentStream.test.ts` 覆盖卸载中止。
