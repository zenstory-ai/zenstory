# update_project 的行锁自锁，和启动索引排在锁后面

## Problem

2026-10-20 发布 `prod-v2026.10.20.2` 时，生产 server 的新部署健康检查失败，Railway 保留了旧版本继续服务。新容器停在 `init_db` 的「Initializing database」，5 分钟后被停掉。

只读查看数据库，原因如下：

- **根锁**：server 的一个连接从 01:47:22 UTC 起一直处于 `idle in transaction`，持有某个项目行的 `SELECT … FOR UPDATE` 锁，持续了 78 分钟。
- **自锁**：同一秒开始的 `INSERT INTO agent_artifact_ledger` 一直在等这把锁。这个插入有指向 `project` 的外键，需要同一行的 key share 锁。
- **路径**：
  1. `update_project` 先对项目行加 `FOR UPDATE`。
  2. 在「什么都没改」的路径上直接返回，事务没有结束。典型情况是只带 `title`，而标题因作者起过名、名字未变或格式无效被跳过。
  3. 工具随后用**另一个 session** 写 artifact ledger，这个插入等的正是第 1 步的行锁。
  4. 第 1 步的 session 要等工具调用结束才关，于是请求自己锁死自己。
- **连带**：
  - 同一作品的其它请求（项目行 `FOR UPDATE`、版本写入）跟着排队，最长 77 分钟。
  - 新部署启动时的 `CREATE INDEX IF NOT EXISTS ix_file_…` 要拿 `file` 表的 SHARE 锁。它排在这条链后面，启动永远完不成。
  - Railway 停掉容器后，这条 DDL 的数据库连接还在锁队列里等。PostgreSQL 让之后所有对 `file` 的写入排在它后面，作者和 AI 的保存卡了 3–7 分钟。

处置：先结束失败部署遗留的 `CREATE INDEX` 连接，`file` 写入随即恢复；经操作者确认后，结束 78 分钟的 idle 事务；之后重新部署 server，成功。

排查时还发现一个相关回归：#285 让工作台给重名的默认作品名加编号（「我的小说 2」），但 `update_project` 只认模板里的原名，带编号的名字被当成作者起的名字。AI 永远不能给这类作品起名，而且每次带标题的调用都会走上面会锁死的「什么都没改」路径。

## Decision

- **`ProjectOperations.update_project_status` 每条路径都结束事务**：有改动照旧 commit + refresh；没有改动也 commit。这里不用 rollback，因为测试和 SQLite 内联路径可能共用请求级 session，rollback 会丢掉别的未提交改动。之后再写 ledger 时，项目行上已经没有锁。
- **启动时的可选索引带 `lock_timeout`**：`init_db` 在 `create_all` 之后、可选索引之前执行 `SET LOCAL lock_timeout = '3000ms'`（`STARTUP_INDEX_LOCK_TIMEOUT_MS`）。表被锁住时，这条索引照旧记 `Skipped creating performance index` 跳过，下次启动再建；启动不再无限等，也不会让后来的写入排在它后面。`create_all` 不受影响。
- **带编号的默认名仍算默认名**：`is_default_project_name`。模板原名，或原名加「空格+正整数」（「我的小说 2」、「My Novel 12」），都可以被 AI 起的作品名替换；作者起的名字照旧不动。

## Alternatives considered

- **ledger 改用工具自己的 session 写**
  - 最强论据：写在同一个事务里，不会等自己的锁。
  - 不选的原因：ledger 是「尽力而为、失败不影响工具结果」，独立 session 正是为此设计（`_record_artifact_ledger`）。合进主事务后，ledger 失败会连带回滚工具本身的写入。根因是持锁路径没收尾，修在那里更窄。
- **给应用的数据库角色设 `idle_in_transaction_session_timeout`**
  - 最强论据：任何泄漏的事务都会被数据库兜底断开。
  - 不选的原因：属于生产数据库配置变更，需要单独授权和评估。它会断开正常但慢的长事务，不能替代修复泄漏路径。作为后续单独提出。
- **启动索引改用 `CREATE INDEX CONCURRENTLY`**
  - 最强论据：只取 SHARE UPDATE EXCLUSIVE 锁，不阻塞写入。
  - 不选的原因：
    1. `CONCURRENTLY` 不能在事务里执行，要改掉现有的「每条索引一个 savepoint」结构。
    2. 它仍会等正在进行的事务结束，遇到这次 78 分钟的泄漏事务一样会卡住启动。
    3. `lock_timeout` 改动小，直接解决「不能无限等」的问题。

## Consequences

- **收益**：
  - 只带标题、什么都没改的 `update_project` 不再锁死请求。
  - 再有锁泄漏时，部署启动最多为每条被锁的索引等 3 秒，不会失败，也不会拖住写入。
  - 编号默认名的作品可以由 AI 起名。
- **代价**：
  - 表被锁住时，可选索引这次启动建不成，要到下次启动。这些索引只影响查询性能。
  - 用户自己起名为「我的小说 2」这样的名字也会被当成默认名，和「我的小说」本身的处理一致。
- **验证**：
  - PostgreSQL 回归（CI 的串行 PostgreSQL 车道）：
    - `tests/test_agent/test_update_project_lock_postgres.py`：作者起名、名字未变、标题无效三种只带标题的调用，都要写进 ledger。连接带 2 秒 `lock_timeout`，旧代码下预期因锁超时少写一条 ledger，不会挂住。这一点依据 PostgreSQL 的锁规则（`FOR UPDATE` 与外键需要的 key share 锁冲突），与生产事故里两个连接的表现一致；CI 没有对旧代码单独跑过这条用例。
    - `tests/test_core/test_database_startup.py::test_init_db_skips_an_index_whose_table_is_locked_instead_of_waiting`：另一个连接持有写事务时，`init_db` 15 秒内完成，被锁的索引跳过，另一张表的索引照建。
  - `tests/test_agent/test_update_project_title.py`：新增「我的小说 2」「My Novel 12」两例，改动前失败。
  - 本地没有 PostgreSQL，两条 PostgreSQL 用例只在 CI 跑。
