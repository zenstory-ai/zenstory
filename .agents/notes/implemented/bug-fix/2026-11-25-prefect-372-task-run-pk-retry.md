# Agent Note: Prefect 升级至 3.7.2 消除并发 task-run 主键冲突日志

Status: implemented

相关前序决策：[Prefect 最小升级修复任务事件自然键冲突](2026-10-24-prefect-task-run-natural-key.md)。

## Problem

在 [2026-10-24 升级到 Prefect 3.6.29](2026-10-24-prefect-task-run-natural-key.md)（引入上游 `#21726`，将 `record_bulk_task_run_events` 改为按 `(flow_run_id, task_key, dynamic_key)` 做 `ON CONFLICT`）后，生产环境单副本 `prefect-server` 在 48 小时窗口内出现 12 次 `UniqueViolationError: pk_task_run` 错误日志（约占 1,111 次 task run 的 1.08%）。

根因已确认是 Prefect `3.6.29`（PR `#21726`）引入的上游并发竞态（[`PrefectHQ/prefect#21807`](https://github.com/PrefectHQ/prefect/issues/21807)）：

1. 客户端编排相隔 1–4ms 发出同一 `task_run.id` 与自然键的 `Pending` 与 `Running` 事件；
2. 单进程 `prefect-server` 内置内存消息消费者默认 `concurrency=2`、`batch_size=1`，两个协程并发进入 `flush()`；
3. 两笔事务同时通过预检查后，PostgreSQL 按索引 OID 顺序先校验主键 `pk_task_run`（而非 `ON CONFLICT` 仲裁索引 `uq_task_run__flow_run_id_task_key_dynamic_key`），败者抛出 `UniqueViolationError: pk_task_run` 并冒泡为 `ERROR` 与堆栈；虽然重试第 1 次即成功、零事件丢失，但持续制造误报噪音。

同时，`AGENTS.md` 与 `docs/ops/release-ci-cd.md` 中仍残留旧的 `3.6.28` 版本文字引用。

## Decision

- 将 API、worker、server 镜像、部署配置、CI 版本断言、契约测试与运维文档统一固定到 **`prefect==3.7.2`**，保持 `fastapi==0.123.7` 与 `openai-agents>=0.21.1,<0.22` 不变。
- 采用上游修复 [`PrefectHQ/prefect#21813`](https://github.com/PrefectHQ/prefect/pull/21813)（首次合入 `3.7.0`，无 `3.6.30`）：`record_bulk_task_run_events` 在捕获 `IntegrityError` 后于新 session 内部重试一次，以单行 `INFO` 处理而非向 `flush()` 冒泡 `ERROR`。
- 不升级至 `3.7.3+`：`3.7.3` 起声明 `starlette>=1.0.1`，与 `fastapi==0.123.7`（要求 `starlette>=0.40.0,<0.51.0`）直接冲突；`3.7.2` 是兼容 `fastapi==0.123.7` 与 `openai-agents<0.22` 的最新 `3.7.x` 版本。
- 接受 `3.7.2` 相比 `3.6.29`（head `09a9e091e578`）新增的 1 个 Prefect PostgreSQL migration：`7218aad17905_add_block_document_reference_self_check`（删除 `block_document_reference` 自引用行并加 `CHECK` 约束）。已核查生产 Prefect 元数据库 `block_document_reference` 当前为 **0 行（0 自引用）**，属于零行删除的安全 DDL。
- 同步更新 `AGENTS.md`、`docs/ops/release-ci-cd.md` 与 `apps/server/docs/PREFECT_RAILWAY_DEPLOY.md` 的版本表述为 `3.7.2`，完整保留所有工作池暂停、排空、确认新 worker 在线且旧部署 `REMOVED` 后恢复以及逐个具名登记 deployment 的发布门禁。

## Alternatives considered

- **仅调优 `prefect-server` 批处理环境变量（`BATCH_SIZE=20`、`FLUSH_INTERVAL=1`）**：运维改动最小，无需 worker 排空窗口或数据库迁移，可将绝大多数 `Pending`/`Running` 合并到同一批次中消除大部分报错。但该方案会引入最多 1 秒的 UI 状态延迟，且无法在根因上封闭并发刷盘窗口；更适合作为临时缓解，不如升级 `3.7.2` 从上游根治。
- **直接升级至 `3.7.3+` / `3.9.x`**：可一并获得后续 recorder 强化（如 `#22441`、`#22808`），但 `3.7.3+` 引入 `starlette>=1.0.1`、`3.7.8+` 进一步要求 `fastapi>=0.139.0`，必须连带升级 FastAPI 与 `openai-agents>=0.22` 并执行全栈回归与 E2E，超出本次最小修复范围，留待独立依赖升级批次评估。
- **保留 `3.6.29` 并过滤日志告警**：零发布风险，但日志过滤容易掩盖后续真实的 recorder 持久化异常，治标不治本，故不采用。

## Consequences

- **收益**：在无需升级 FastAPI / `openai-agents` 的前提下，彻底消除 `PrefectHQ/prefect#21807` 导致的 `pk_task_run` `ERROR` 噪音，并统一代码、镜像、CI、测试与文档的版本口径。
- **代价与发布门禁**：`3.7.2` 仍停留在 `starlette<0.51` 分支，后续升级 `openai-agents>=0.22` 仍需与 FastAPI/Prefect 协同升级。本批只提交代码、配置、测试与文档变更，不执行云端部署；后续经授权的 staging/production 发布必须先在 staging 验证，并严格遵守 `AGENTS.md` 门禁：暂停目标池（`zenstory-staging-pool` / `zenstory-pool`）→ 排空运行中流程与摄入任务 → 升级 Prefect server（执行 0 行影响的 PG migration `7218aad17905`）与 worker → 逐个具名登记 deployment → 确认新 worker 心跳正常**且旧部署状态为 `REMOVED`** 后恢复工作池。

## Verification

- 依赖干跑解析（`uv pip compile`，不修改本地 venv）：
  - `apps/server/requirements.txt`（`prefect==3.7.2` + `fastapi==0.123.7` + `openai-agents>=0.21.1,<0.22`）在 Python `3.11` 与 `3.13`（`x86_64-unknown-linux-gnu`）下均解析通过：`prefect==3.7.2`、`fastapi==0.123.7`、`starlette==0.50.0`、`openai==3.59.0`、`openai-agents==0.21.1`。
  - `Dockerfile.prefect-server` 依赖集在 Python `3.11` 下解析通过（`prefect==3.7.2`、`fastapi==0.123.7`、`starlette==0.50.0`）。
  - 反向验证 `prefect==3.7.3` + `fastapi==0.123.7` 在 `uv pip compile` 下明确报错不可满足（`starlette>=1.0.1` vs `starlette>=0.40.0,<0.51.0`）。
- 契约测试：运行 `apps/server/tests/test_prefect_worker_image.py`、`apps/server/tests/test_prefect_deployments_contract.py`、`apps/server/tests/test_scripts/test_prefect_worker_startup.py` 与 `scripts/ci/workflows.test.mjs` 核验全部通过。完整的 3.4.19 → 3.7.2 PostgreSQL migration 与 `prefect_contract.py` 运行时矩阵（Python 3.11/3.13）由 GitHub Actions `prefect-compatibility` 作业在合入前执行验证。
