# Agent Note: 生产发布前先跑数据库迁移，写进发布文档；不加会让健康检查失败的启动检查

Status: implemented

## Problem

2026-10-09 新用户审计 P0-1：PR163 给 `usage_quota` 加了 `material_trial_used_at`（迁移 `20261009_120000`）。用 PR163 的后端连一个没执行该迁移的库，`GET /subscription/quota` 和 `POST /agent/stream` 都返回 500（`UndefinedColumn`），所有用户的 AI 消息、额度徽章、订阅页、素材页同时不可用。

两个环境的部署都不执行 Alembic：Railway API 没有 pre-deploy 命令，启动只跑 API；`init_db` 只用 `create_all` 建缺失的表，不加列。生产迁移一直是手动执行的，但 `docs/ops/release-ci-cd.md` 里没有这一步，只存在于个人记忆里。Vercel 前端合并后几分钟就上线，Railway 晚一些，时间差真实存在。

## Decision

- `docs/ops/release-ci-cd.md` 新增「Database migrations before promotion」：发布 diff 含 `apps/server/alembic/versions/` 时，先列出生产 `alembic_version` 之后的待执行修订；确认每个修订对仍在运行的旧代码兼容（只新增表、可空列或不重写大表的默认值列；删除、改名要单独的 expand/contract 计划）；用与服务器同主版本的 `pg_dump`（当前 17）备份并核对；对显式确认过的 Postgres URL 执行 `alembic upgrade head`（`DATABASE_URL` 为空时 Alembic 会静默落到本地 SQLite）；回读版本与新列、记录修订和备份凭据之后才打 `prod-v*` 标签。staging 没有 `alembic_version` 表，同样的增量 DDL 手工以 `ADD COLUMN IF NOT EXISTS` 执行。
- `20261009_120000` 本身已经是可提前执行的形态：单个可空 `DateTime` 列、没有默认值、不重写表，旧代码对新表照常工作。没有改动迁移。
- 不加启动时的 schema 检查。

## Alternatives considered

- **启动时检查 schema，缺列就让 `/health/ready` 失败。** 最强理由：漏跑迁移时新版本根本不会切流量，旧版本继续服务，问题在部署阶段就暴露。被否：Railway 上 API 只能单实例（共享卷），健康检查失败等于发布卡住或在重启间隙整体不可用；staging 没有 `alembic_version`，检查要么失效要么让 staging 一直不健康；它也挡不住新前端先上线配旧后端的组合。任务范围也明确不加。
- **部署时自动执行 `alembic upgrade head`（Railway pre-deploy）。** 最强理由：彻底消除人工步骤。被否：生产迁移前需要备份并确认兼容性，失败时需要人判断回滚；自动执行会让一次有问题的迁移直接作用于生产库。这是需要单独授权的平台配置变更。
- **代码对缺列做兼容（读取时捕获 `UndefinedColumn` 再降级）。** 最强理由：顺序错了也不会全站 500。被否：每加一列都要写一次兼容，ORM 查询整行加载时很难只对一列降级；把流程问题变成代码里散落的兜底。

## Consequences

- 收益：发布清单里有明确的「先迁移、再打标签」步骤和可核对的凭据，任何执行发布的人都能照做。
- 代价：仍依赖人执行；漏做时的后果与之前相同（新后端上线后相关接口 500），直到有人补跑迁移。

## Verification

- 文档改动，无运行时代码。`apps/server/alembic/versions/20261009_120000_add_material_trial_used_at.py` 复核：`nullable=True`、无 `server_default`，`down_revision` 为 `20261007_170000`，之后没有新的修订依赖它。没有对任何共享数据库执行迁移。
