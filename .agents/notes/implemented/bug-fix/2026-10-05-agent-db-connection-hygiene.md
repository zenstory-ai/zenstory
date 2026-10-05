# Agent Note: 工作流边界查库用短生命周期 session 并移出事件循环；PG 连接池开启 pre-ping

Status: implemented

## Problem

`writing_graph.run_writing_workflow_streaming` 在 agent 交接、空文件核验与回滚三处同步查库：`ToolContext.refresh_file_inventory()`、`_probe_pending_file_body()`、`_rollback_unfinished_empty_files()`。它们直接跑在事件循环上，并通过 `ToolContext.get_session()` 懒建 session、放进 `_owned_session_var`；查完既不 commit 也不 close，要到 `service.py` 在流结束时调用 `ToolContext.clear_context()` 才关闭。PostgreSQL 上这条连接以 idle in transaction 挂满整轮 SSE（常见的 writer → quality_reviewer 交接就会触发），约 30 个并发串流就能耗尽 `pool_size=10 + max_overflow=20`；之后在事件循环上同步等 `pool_timeout`（30 秒）会把整个进程的所有 SSE 与 API 一起卡住。这与 `api/agent.py` 主动 rollback 请求级 session、避免 idle in transaction 的设计目标相反。

另外两个 PostgreSQL engine（同步 psycopg、异步 asyncpg）都没开 `pool_pre_ping`，PG 重启、故障切换或闲置 TCP 被清掉后，池里的死连接会依次让拿到它的请求 `OperationalError`。

## Decision

- `ToolContext.short_lived_session()`（`agent/tools/mcp_tools.py`）是一个上下文管理器：上下文里有显式共享 session（SQLite 测试 / 非 offload 路径）就直接复用、不关闭；否则用 `create_session_func` 新建一个，with 块结束即 `close()` 归还连接，不写入 `_owned_session_var`。
- `refresh_file_inventory`、`_probe_pending_file_body` 改用它；`_rollback_unfinished_empty_files` 在它上面调用 `_soft_delete_verified_empty_files`（有软删时 commit，异常时 rollback 并由外层记 warning、返回空集合，行为与原来一致）。
- `writing_graph` 的三处调用都改成 `await asyncio.to_thread(...)`。`to_thread` 复制 contextvars，工作线程能读到同一个 ToolContext；短生命周期 session 在线程内开、线程内关。
- `database.POSTGRES_POOL_OPTIONS` 统一两个 PG engine 的池参数，并加入 `pool_pre_ping=True`（其余参数不变：10 / 20 / 30 秒 / 1800 秒回收）。

## Alternatives considered

- **只把调用挪进 `asyncio.to_thread`，仍走 `get_session()`**。最强理由：改动最小，事件循环不再被同步查库阻塞。被否：`to_thread` 在复制出来的 context 里写 `_owned_session_var`，请求上下文看不到它，`clear_context()` 也就关不掉——连接会一直泄漏到 GC，比原来更糟。
- **在每次查询后对共享自有 session 调 `commit()`**。最强理由：保留现有懒建 session 的复用，连接不会停在事务里。被否：连接本身仍被这个 session 占着直到流结束（只是从 idle in transaction 变成 idle），并发高时池同样会被耗尽；而且同一 session 仍在事件循环线程与工具工作线程之间来回使用。
- **缩短 `pool_recycle` 代替 `pool_pre_ping`**。最强理由：没有每次借出时的探测往返。被否：回收只按连接年龄淘汰，挡不住 PG 重启后「年轻但已断开」的连接；pre-ping 的一次轻量往返相对 Agent 请求的耗时可以忽略。

## Consequences

- 收益：交接、核验、回滚都不再阻塞事件循环，也不再让一条 PG 连接挂满整轮 SSE；PG 重启后的第一批请求不会因死连接失败。
- 代价：每次交接多一次「借连接 → 查询 → 归还」，连接池有空闲连接时开销很小；`asyncio.to_thread` 占用默认 executor 一条线程片刻。`pool_pre_ping` 让每次借出连接多一次往返。
- 共享 session 分支（测试 / SQLite 非 offload）下，查询会在工作线程里使用请求级 session；调用方在 `await` 期间不会并发使用它。

## Verification

`cd apps/server && venv/bin/pytest tests/test_agent/test_graph_db_session_hygiene.py tests/test_core/test_database_pool.py -q`：覆盖三个函数在工作线程中执行后 session 已关闭（`in_transaction() is False`）且 `_owned_session_var` 为空、共享 session 不被关闭、交接时文件清单刷新发生在事件循环之外、两个 PG engine 都收到 `pool_pre_ping=True`。去掉本次改动后前者 4 个用例全部失败。
