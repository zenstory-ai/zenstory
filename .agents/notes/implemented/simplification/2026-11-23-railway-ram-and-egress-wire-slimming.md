# Railway 内存与出网流量快速止血（Batch 1）

## Problem

2026-11-23 生产环境 Railway 账单审计显示（19.5 天 $22.48，按月折算 ~$35.29/月，目标压回 <= $20/月），内存（57.0%）和公网出网流量（34.0%）占总成本 91%，其中三类高杠杆热点可以在不动数据库落库语义、模型历史回放、计费与前端交互的前提下直接消除：

1. **管理端 UX 健康度接口内存爆炸**：`services/usage/admin_ux_metrics_service.py` 在 `GET /api/admin/dashboard/ux-health` 中对当前周期与上一周期（最长合计 60 天）使用 `.all()` 一次性拉取全部 `chat_message.message_metadata`（均 96 KB、最高 976 KB）与 `chat_message.tool_calls`（均 185 KB、最高 8.79 MB）并在 Python 堆中逐条 `json.loads()`。并发请求曾把 `prod / server` 瞬时打到 **10.33 GB** RSS，并在 pymalloc/glibc 中滞留数小时。
2. **聊天历史与 SSE 线缆携带前端不展示的整章正文**：
   - `GET /api/v1/chat/session/{project_id}/recent` 直接返回原始 `msg.tool_calls`（均 820 KB、P95 6.72 MB、最高 8.70 MB），其中 `query_files` 与 `parallel_execute.tasks[*].result` 携带完整章节正文与预览，但前端 `ToolResultCard.tsx` 对 `query_files` 仅展示 `id` / `title` / `file_type`，对 `parallel_execute` 仅展示任务状态、描述、错误与 `author_edits_touched`；
   - `POST /api/v1/agent/stream` 走未压缩的 `text/event-stream`，`StreamAdapter._handle_workflow_tool_result` 仅对 `load_skill` / `read_skill_resource` 瘦身，仍将 `query_files` / `parallel_execute` 的完整正文推给浏览器。
3. **启动与运维卫生**：
   - `apps/server/database.py` 的 `async_engine` 仅在启动 `init_db()` 建表/建索引时使用，之后常驻空池占连接与内存；
   - Prefect 的 `Dockerfile.prefect-server` / `Dockerfile.prefect-worker` 缺少 `MALLOC_ARENA_MAX=2`；
   - 发布前通过 `DATABASE_PUBLIC_URL` 拉全量 `pg_dump` 会触发 4–7.9 GB/次公网 TCP 代理流量与 Postgres 页面缓存飙升。

## Decision

1. **UX 健康度与反馈列表改为有界流式 + PostgreSQL SQL 侧 JSON 投影**（`apps/server/services/usage/admin_ux_metrics_service.py`）：
   - 所有行扫描改用 `execution_options={"yield_per": ...}` 分批拉取并即时释放单行对象，彻底移除 `.all()` 整窗驻留；
   - PostgreSQL 下（兼容 PG 15/16/17，使用 `::json` 而非 `::jsonb` 以容忍历史 `\u0000` 并避免建树开销）：`message_metadata` 仅在 SQL 侧提取 `stop_reason`、`stop_outcome`、`runaway_stop`、`feedback` 四个轻量字段；`tool_calls` 仅在包含 `"edit_file"` / `"write_chapter"` / `"delete_file"` 时由 `json_array_elements` 筛出对应卡片元素，直接丢弃 `query_files` / `parallel_execute` 巨型正文。若遇坏 JSON 触发 `DataError`，自动回滚子事务并透明回退到便携式 `yield_per(50)` Python 路径（同时通过 `CASE` 跳过不含写/删卡的 `tool_calls`）；
   - SQLite（本地与集成测试）走同一套 `CASE` + `yield_per(50)` 路径，指标口径 100% 一致。
2. **前后端线缆投影瘦身，保持数据库与模型回放零改动**：
   - 在 `apps/server/agent/loop/history.py` 新增共享投影器 `slim_client_tool_result()` 与 `client_tool_calls()`（与既有 `client_message_metadata()` 对齐）：仅对 `query_files` 和 `parallel_execute` 剥离 `content` / `content_preview` / `matches` / `author_edits` / `file_metadata` / `content_range` 等大载荷，完整保留前端卡片与失败提示依赖的 `id`、`title`、`file_type`、`status`、`error`、`error_type`、`user_message`、`count`、`match_count`、`author_edits_touched` 等字段；其余工具（`edit_file`、`write_chapter`、`create_file`、`delete_file`、`update_project` 等）原样透传；
   - `apps/server/api/chat.py` 的 `/recent` 响应改用 `client_tool_calls(msg.tool_calls)`；
   - `apps/server/agent/stream_adapter.py` 在 `yield tool_result_event(...)` 前调用 `slim_client_tool_result()` 瘦身 SSE 帧，同时暂存瘦身前载荷供 `apps/server/agent/service.py` 通过 `pop_persisted_tool_result()` 完整落库，确保 `ChatMessage.tool_calls`、`loop/history.py` 回放及 `SessionLoader.session_read_snapshots` 完全不受影响。
3. **安全启动与运维卫生**：
   - `apps/server/database.py` 新增 `dispose_async_engine()`，在 `apps/server/main.py` lifespan 执行完 `await init_db()` 后立即释放启动专用 `async_engine` 连接池；
   - `apps/server/docker/Dockerfile.prefect-worker` 与 `apps/server/docker/Dockerfile.prefect-server` 增加 `ENV MALLOC_ARENA_MAX=2`；
   - `docs/ops/release-ci-cd.md` 在发版前备份步骤补充说明：优先使用 Railway Volume Snapshot 或在私有网络 `postgres.railway.internal` 内执行 `pg_dump -Fc -Z9` 并直传私有对象存储，不降低发版前备份与校验门禁。

## Alternatives considered

1. **直接把 `query_files` 瘦身结果写入 `ChatMessage.tool_calls`（像 `load_skill` 那样）**：
   - 优点：同时削减 Postgres `chat_message` TOAST 体积与 SSE/HTTP 流量，代码更少。
   - 驳回原因：`loop/history.py` 在 Redis `wire_prefix_cache` 失效后会从 `ChatMessage.tool_calls` 重建模型上下文，且 `SessionLoader.session_read_snapshots` 依赖库内 `query_files` 原文做跨轮陈旧校验；直接截断落库需要同步设计模型回放提示、快照语义与存量迁移，属于 Batch 2 架构改造，超出本次零风险快速止血边界。
2. **为 `admin_ux_metrics_service.py` 新增数据库列或物化汇总表**：
   - 优点：管理端查询可以变成纯索引聚合，完全不碰 TOAST。
   - 驳回原因：需要 Alembic 迁移与历史回填；而流式 `yield_per` + PostgreSQL JSON 投影无需任何 schema 变更即可立刻将单次查询 Python 峰值内存从数 GB 压至几 MB。
3. **在 `admin_ux_metrics_service.py` 使用 PostgreSQL 16+ 的 `pg_input_is_valid(..., 'jsonb')`**：
   - 优点：单条 SQL 内可容忍非法 JSON 字符串，无需 `DataError` 回退分支。
   - 驳回原因：CI 与 E2E 工作流当前使用 `postgres:15-alpine`，`pg_input_is_valid` 在 PG 15 不存在；采用 `::json` + savepoint `DataError` 回退可同时兼容 PG 15/16/17 并正确处理 `\u0000`。

## Consequences

- **收益**：
  - 消除 `ux-health` 接口的 10 GB 级 Python RSS 峰值与长尾卡顿；
  - 显著削减 `/recent` HTTP 与 `/agent/stream` 未压缩 SSE 响应体积（`query_files` / `parallel_execute` 载荷剔除多 KB/MB 正文）；
  - 释放 API 启动后闲置的 `asyncpg` 连接池，收敛 Prefect 容器 glibc arena 碎片，并为发版备份提供避开公网 TCP 代理计费的操作指引。
- **成本与边界**：
  - `ChatMessage.tool_calls` 与 `message_metadata` 在 Postgres 中的存量与新增 TOAST 体积仍需后续独立批次治理；
  - PostgreSQL SQL 侧 JSON 投影与便携式 Python 路径需要维护口径一致，已通过单元与集成回归测试覆盖。
