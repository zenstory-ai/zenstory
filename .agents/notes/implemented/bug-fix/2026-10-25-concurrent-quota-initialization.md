# Concurrent quota initialization is idempotent

Status: implemented

## Problem

尚无额度行的账号首次并发发起两个请求时，两个事务都可能先查到没有 `usage_quota`，再各自插入默认行。`user_id` 唯一索引只允许一个成功，另一个以 `duplicate key violates ix_usage_quota_user_id` 失败；实际表现是不同作品的首次 `/agent/stream` 之一在任何模型调用前返回 409。普通的查询后插入不能把唯一索引冲突当成产品级限流。

这类状态是既有兼容合同，不等于正常注册必然缺行：订阅系统迁移 `827c8c6578d3` 只创建表，没有回填迁移前的用户；`ensure_user_subscription_and_quota` 明确承担登录和管理操作中的 legacy backfill；注册与 OAuth bootstrap 又是 best-effort，现有 `test_auth_bootstrap_recovery.py` 锁定了真实 SQL 失败后用户（OAuth 还包括令牌）继续存在而 `usage_quota` 为空的恢复路径。

初始化还被月度功能预占等 `commit=False` 路径复用。这些调用方必须继续拥有外层事务，quota helper 不能为了处理竞争而提交或回滚调用方已经暂存的写入。已有 quota 的当日使用量也不能因并发初始化被默认值覆盖。

## Decision

`QuotaService.create_default_quota` 使用当前 PostgreSQL / SQLite 方言的 `INSERT ... ON CONFLICT (user_id) DO NOTHING`。插入或冲突等待结束后，在同一事务里读取唯一的 quota 行并返回：赢家得到自己插入的行，输家得到另一事务已提交的行。冲突路径不执行 update，所以不会把既有的当日、月度用量或素材试用状态重置成默认值。

`commit=True` 保持原调用合同；`commit=False` 只 flush，不 commit/rollback，外层调用方仍能把 quota 初始化与自己的业务写入一起提交或一起回滚。upsert 前的 flush 保留旧实现对同事务中新建 User 等对象的依赖顺序。北京自然日、免费每日 3 次、Pro 无限、预占与退款逻辑不变。

## Alternatives considered

- **捕获 `IntegrityError` 后整段 `session.rollback()` 再查询。** 最强理由是改动少，也能让普通独立请求恢复。未采用：rollback 会丢掉 `commit=False` 调用方在同一事务里已经暂存的业务写入，破坏原子性。
- **先锁用户行，再查询和插入 quota。** 最强理由是跨数据库容易理解，并能完整串行同一账号的首次请求。未采用：初始化只需要唯一键仲裁；额外持有用户行锁会扩大事务竞争面，而 upsert 已能在数据库内原子解决同一键竞争。
- **捕获冲突后重试完整 `/agent/stream`。** 最强理由是无需改 quota service。未采用：请求可能已经完成额度预占或其它副作用，自动重试整条生成 POST 不符合现有付费调用约束，也掩盖了初始化本身应当幂等的事实。

## Consequences

- 同一新账号的并发首次额度预占共享一条 quota 行；唯一键冲突不再冒泡成请求失败。
- 已有 quota 行完全保留，后续原子 `UPDATE` 正常累计用量。
- `commit=False` 初始化不会提交调用方事务；调用方 rollback 后，随事务新建的 quota 与其它暂存写入一起消失。
- PostgreSQL 双连接 Barrier 回归覆盖首次并发预占，另一个回归覆盖 `commit=False` 的事务所有权。
