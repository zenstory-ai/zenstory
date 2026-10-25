# Bug-fix Note: 写作 agent 按本轮已解析的会话 id 读历史

Status: implemented

关联：[复用会话时 SQLite 写锁](2026-10-03-agent-sqlite-write-lock-on-session-reuse.md)、[steering run 心跳与 busy 409](2026-10-05-steering-run-heartbeat-and-busy-409.md)。审计编号 F-04（B-BUG-007、A-S7 的 loader 部分）。

## Problem

`AgentService.process_stream` 先用 `_resolve_or_create_chat_session_id` 解析（必要时激活）本轮的会话 A，并用 A 领 generation slot、建 ToolContext、最后把历史存回 A。随后 `SessionLoader.load_chat_session` 却重新查询「该用户该项目最新的活跃会话」。

同一作者在两个分页对不同会话几乎同时发送时：A 解析完成后，B 的解析把 A 置为非活跃、激活 B；A 接着加载时读到的是 B 的历史和跨轮工作集，而存档、ledger、任务板仍写回 A。模型拿着另一段对话的上下文回答，A 的历史里出现与前文无关的回复。

## Decision

- `SessionLoader.load_chat_session`、`load_session_and_context`（含 PG 卸载到线程的同步路径）新增可选参数 `chat_session_id`。传入时按 id 读取该会话：先保留原有的项目存在 / 用户存在 / 项目归属检查（superuser 跨项目规则不变），再要求该会话的 `user_id`、`project_id` 与本 loader 一致，否则记 WARNING 并返回空的 `SessionData`。会话此后变成非活跃也照常读取，按 id 读不修改任何活跃状态。
- 不传 id 的旧调用保持原样：取最新活跃会话并修复多活跃。服务端解析步骤本身已在同一处修复多活跃，所以按 id 的路径不再重复修复。
- `process_stream` 传入解析出的 `session_id`（无 user 时不传）；加载结果的 `session_id` 与解析值不一致时记 WARNING，不改行为。
- 不加项目级整轮锁：不同会话之间本来就允许并发，单会话的串行仍由 generation slot 保证。

## Alternatives considered

- **项目级整轮锁，同一项目同一时刻只跑一轮**。最强理由：从根上消除同项目内任何交错，包括以后新增的读路径。未采用：会让两个分页、两个会话互相排队，违背「不同会话可并发」的现有设计，而这里的错误只是加载阶段重新挑了会话，按 id 读就能消除。
- **加载时发现与解析结果不一致就报错终止本轮**。最强理由：显式暴露竞态，不会悄悄用错上下文。未采用：作者的这一轮本来就该用 A 的上下文，按 id 读到的正是正确数据，终止只会白白失败一次并需要处理额度退还。
- **在 resolve 和 load 之间持有 `lock_active_chat_session` 的行锁**。最强理由：保留「读最新活跃会话」的语义同时消除窗口。未采用：SQLite 分支要求解析后立即提交以释放写锁（见 10-03 note），PG 分支解析在独立 session 中完成；把锁延长到加载结束会把写锁问题带回来。

## Consequences

- 收益：每一轮的历史、跨轮工作集、存档都绑定同一个会话；越权或不存在的 id 拿不到任何历史。
- 成本：`SessionLoader` 多一个参数，两条加载路径（按 id / 最新活跃）都要维护；无 id 路径只剩直接调用 loader 的测试与工具在用。
- 验证：`tests/test_agent/test_agent_service_session_id.py::TestProcessStreamLoadsResolvedSession` 用 Event 做确定性交错（A 解析后停住，B 完整跑完，再放行 A），修复前 A 读到 B 的历史和工作集；`tests/test_agent/test_session_loader.py` 覆盖非活跃会话按 id 读取、别人或别项目的 id 被拒绝。PG 并发测试 `test_chat_session_concurrency_postgres` 只在 CI 跑；staging 只做一次普通对话 smoke。
