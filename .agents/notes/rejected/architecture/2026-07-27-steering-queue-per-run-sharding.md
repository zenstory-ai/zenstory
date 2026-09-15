# Agent Note: steering 队列按 run 物理分片

Status: rejected — POST /agent/steer 请求体只有 session_id 没有 run_id，分片需要 API 与前端协议变更；当前以"只有仍在生成的 run 才能消费"闭合缺陷

## Problem

steering（"追加消息"）队列以 session 为键，同一 session 可能先后或并发存在多个生成 run。已结束的 run 在 drain 时会带走本该给新 run 的引导；并发 run 互删队列；Redis 里的僵尸 run 长期占位。

## Proposal

把队列键从 `session_id` 细化到 `run_id`，每个 run 只消费自己的引导消息；`api/agent.py` 的 `SteeringRequest` 增加 `run_id`，前端在发起 steering 时带上当前流的 run 标识。

## Alternatives considered

- **向 session 下所有活跃 run 扇出**。最强理由：不改协议就能让每个 run 都收到引导。被否：一条引导会在多个 run 的历史里重复出现。
- **消费侧守卫（采用）**：`agent/core/steering.py` 以 `active_runs` + 心跳 TTL 记录每个 run，`_reap_stale_runs` 回收僵尸；只有仍在生成的 run 才能 drain，已结束的 run never 带走消息。这是当前落地方案，ea85c83 的「有意保留」段记录了取舍。

## Risks

重提本提案的前提是 API 与前端一起改协议（`SteeringRequest` 加 `run_id`，`useAgentStream` 暴露 run 标识）。在此之前，同一 session 内两个真正并发的 run 仍共享一个队列，谁先 poll 谁拿到引导；这是已知未闭合的边界。若线上出现同一 session 并发双 run 且引导被错误 run 消费的实例，即为重启本提案的触发信号。

来源：ea85c83
