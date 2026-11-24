# `chat_message_ux_summary` 窄投影表与阶段延迟草稿存活单次扫描优化

## Problem

在 `prod-v2026.11.24.2`（`3ea2971d`）上线后，生产环境管理后台「体验健康」（`GET /api/admin/dashboard/ux-health?days=7`）仍然耗时 **35.4s 并返回 HTTP 500（`QueryCanceled: canceling statement due to statement timeout`）**，且「用量与成本」页调用的 `GET /api/admin/usage/stage-latency?days=30` 中 24h AI 草稿存活查询耗时 **14.8s**。

生产 PostgreSQL 只读 `EXPLAIN (ANALYZE, BUFFERS)` 实测揭示了根本原因：

1. **`chat_message` TOAST 体积远超内存预算**：`chat_message` 仅 31,868 行、堆表 17 MB，但 TOAST 达 **2,383 MB**（60 天窗口内 `message_metadata` 压缩后 691 MB、`tool_calls` 压缩后 830 MB，解压后合计约 5.5 GB），而生产 Postgres `shared_buffers = 128MB`。
   - 7d 窗口下 `_query_round_and_tool_metrics` 的 `message_metadata` 投影耗时 **12,174 ms**（每行将大 JSON 解压解析 4 次，读取 204,211 个页），`tool_calls::jsonb` 聚合直接超过 **20,000 ms** `statement_timeout` 报错 500，导致 Redis 缓存也从未被成功写入。
   - 30d 窗口下两条查询双双超时（>20s + >20s）。
   - 即使改为单次 `::json` 解析，7d 仍需 7,726 ms；而不触碰 TOAST 列扫描同样 60 天窗口仅需 **51.6 ms**。
2. **`_compute_ai_draft_survival` 对 `file_version` 做了 4 次关联扫描**：30d 窗口对 11,913 份候选草稿反复关联 `file_version`（min/max 边界、co-edit、first_fv、latest_fv），读出 42,071 个页耗时 **14,849 ms**。

## Decision

1. **新增窄投影表 `chat_message_ux_summary`（1:1 对应 `chat_message` 助手行）**：
   - 仅存储结构化枚举与整型计数（`message_id`, `session_id`, `created_at`, `round_category`, `abnormal_reason`, `feedback_vote`, `feedback_preset`, `ai_edit_cards`, `ai_edit_undone`, `ai_delete_cards`, `ai_delete_restored`），不含任何正文、Prompt 或 PII；全量 15,764 行约 ~1.5 MB，常驻 `shared_buffers`。
   - 通过 SQLAlchemy `ChatMessage` 的 `after_insert` / `after_update` 事件在同一数据库事务内原子 upsert（自动覆盖新消息落库、停止结果回写、对话点踩/点赞回写、编辑撤销与删除恢复卡回写；对 `load_only` 局部归档更新安全跳过；对尚未建表的环境/单元测试安全降级）。`clear_session` 批量清空时同步清理 SQLite/PG。
   - 新增 Alembic 迁移 `20261124_000000_add_chat_message_ux_summary.py`（接续 `20261123_000000`），建表建索引后以新到旧分批（500 行/批）执行 PostgreSQL 服务端单次 `::json` 回填（零 TOAST 网络 egress，含 `DataError` Python 回退），并提供独立幂等脚本 `scripts/backfill_chat_message_ux_summary.py`。
   - `admin_ux_metrics_service._query_round_and_tool_metrics` 直接在 `chat_message_ux_summary` 上做 SQL `GROUP BY` 聚合，仅通过轻量 `NOT EXISTS` 反连接检查极少数未回填历史行并在存在时才回退扫描，稳态下零 TOAST 访问。
2. **重写 `admin_stage_latency_service._compute_ai_draft_survival` 为单次窗口扫描**：
   - 使用单次 `ROW_NUMBER() OVER (PARTITION BY file_id ORDER BY version_number ASC/DESC)` 聚合替代 4 次 `file_version` 关联，保持 SQLite/PostgreSQL 双兼容与口径 100% 一致；生产实测 30d 执行时间从 **14,849 ms 降至 276 ms（54x 提速）**。

## Alternatives considered

1. **直接在 `chat_message` 表上新增 8 个窄列**：
   - 最强理由：查询少一次表关联，无外键维护成本。
   - 拒绝原因：staging 由 `create_all` 维护（不会自动 `ALTER TABLE`），且发布窗口内任何先于 `alembic upgrade head` 的代码都会让所有 `SELECT * FROM chat_message` 报 `UndefinedColumn` 500；此外对 15,764 行执行 `UPDATE chat_message` 会产生堆表死元组与热表锁竞争。独立新表由 `create_all` 自动兼容、零锁影响 `chat_message`，且回填完全不写主表。
2. **继续仅靠 SQL 表达式优化 `message_metadata` / `tool_calls`（不建投影表）**：
   - 最强理由：无新表、无回填、无写路径同步逻辑。
   - 拒绝原因：生产实测即使只做单次 `::json` 解析，7d 窗口仍需 7.7s、30d 仍超过 20s 超时；瓶颈在物理层 1.5 GB 压缩 TOAST 与 128 MB `shared_buffers` 的不匹配，纯查询重写无法突破物理 I/O 下界。

## Consequences

- **收益**：`ux-health` 稳态查询彻底脱离 2.38 GB `chat_message` TOAST，7d/30d 冷查询从 35s+/双超时 500 降至 ~1–2s 内并可稳定命中 Redis 缓存（<5ms）；`stage-latency` 30d 从 15s 降至 ~0.3s。
- **代价**：新增一张 ~1.5 MB 窄表与每次助手消息写入/状态回写时的一次同事务主键 upsert（<1ms）；生产发布前需按 `docs/ops/release-ci-cd.md` 执行 `alembic upgrade head`（自动完成分批服务端回填，预计 ~1.5 分钟，零网络 TOAST 流量、无长事务锁）。
