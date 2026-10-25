# Agent Note: ChatSession 的 active 唯一索引在模型里声明

## Problem

「每个 user + project 最多一个 active 会话」由 partial unique index `uq_chat_session_user_project_active` 保证。它只存在于两处 DDL：迁移 `20260307_230000_add_unique_active_chat_session_index`（PostgreSQL `WHERE is_active = true`，SQLite `WHERE is_active = 1`）和 `database.py` 启动时对 PostgreSQL 执行的 `CREATE UNIQUE INDEX IF NOT EXISTS`。`ChatSession` 模型没有声明它，所以 `SQLModel.metadata.create_all` 建的库（测试库、新建的 SQLite 开发库）没有这个索引：测试可以随手造出同一作品多个 active 会话，代码里为这种状态写了好几处「保留最新、其余停用」的修复循环（2026-10-25 审计 D-09），而 prod 根本不可能出现这种状态。几个测试为了贴近 prod，还各自手写了一遍 `CREATE UNIQUE INDEX`。

## Decision

- `ChatSession.__table_args__` 声明同名、同列、同条件的索引：`Index("uq_chat_session_user_project_active", "user_id", "project_id", unique=True, postgresql_where=text("is_active = true"), sqlite_where=text("is_active = 1"))`。
- 不加迁移：已有的 staging / prod 库由 20260307_230000 迁移建过这个索引（该迁移和 `database.py` 都用 `IF NOT EXISTS`）。`create_all` 只为新建的表建索引，已有的表不受影响。
- 回归（`tests/test_models/test_chat_session_active_index.py`）：
  - SQLite 上 `create_all` 建出的索引，与「`create_all` 后删掉索引、stamp 到前一版、用 alembic CLI 升级到 20260307_230000」建出的索引 DDL 相同；
  - 模型索引按 PostgreSQL 方言编译出的语句与迁移里的语句相同；
  - 同一作品的第二个 active 会话被拒绝（IntegrityError），停用的会话不受限。
  改动前三条都失败。
- 测试随之调整，只改造数方式、不放宽断言：
  - 不需要多个 active 会话的测试，多余的会话改成已归档（`is_active=False`），或像应用里新开对话一样先停用旧会话（scenarios 的 `create_chat_session`、首条消息提示、并发建会话 / 隔离、历史分页夹具、stream 中切换 active 会话改为先停用再激活）。
  - 手写 `CREATE UNIQUE INDEX` 的两条 SQLite 测试去掉手写语句，直接用 `create_all` 的索引；PostgreSQL 测试里的 `IF NOT EXISTS` 语句无害，保留。
  - 代码里仍保留的多 active 修复循环（`AgentService._resolve_or_create_chat_session_id`、`session_loader`、`suggest_service`），由新的 `legacy_db_without_active_chat_index` 夹具在测试期间删掉索引来模拟迁移前的旧库，测试结束后把多余的 active 停用并恢复索引。这些循环是否删除归 D-09（PR-17）处理，本次不动。

## Alternatives considered

- **维持现状，只在 prod 由迁移保证**：不碰模型元数据，autogenerate 不会因为 partial index 出现差异。但测试库和 prod 不一致，测试可以造出 prod 不存在的状态，代码继续为它写修复循环；每个想贴近 prod 的测试都要手写一遍 DDL。
- **加一个新迁移重建索引**：显式、可追溯。但索引已经存在，新迁移只是重复的 `IF NOT EXISTS`，还要占一次 prod 迁移窗口。
- **只在测试 conftest 里建索引**：不动模型。但新建的开发库仍然没有索引，DDL 也还是多处各写一份。

## Consequences

- 测试和新建的库与 prod 一致：同一作品不能有两个 active 会话。之后删除 D-09 的修复循环时，可以直接依赖这个约束。
- 以后在测试里造多个 active 会话会立刻失败；要测迁移前旧库的行为，显式用 `legacy_db_without_active_chat_index`。
- 模型元数据多了一个 partial index。名字、列、唯一性和 prod 相同，alembic autogenerate 正常不应报差异；`where` 条件不在 autogenerate 的比较范围内，本次没有在真实库上跑 autogenerate 核对。
- 不需要 prod migration，也不改变运行时行为。
