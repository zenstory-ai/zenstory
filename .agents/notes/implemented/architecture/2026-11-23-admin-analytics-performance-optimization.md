# 管理后台分析与列表接口性能优化

## Problem

管理后台多个页面（尤其是「体验健康」`/api/admin/dashboard/ux-health`、「仪表盘」和「用量与成本」）在生产数据增长后响应极慢甚至超时，根本原因包括：

1. **大字段全量拉回 Python 内存解析**：`admin_ux_metrics_service.py` 在当期和上期各拉取一次全部 `chat_message` 的 `message_metadata` 与 `tool_calls`（包含大体积 TOAST JSON），逐行 `json.loads`；同时全量拉取 `file_version` 与 `llm_usage_event` 明细行。
2. **重复扫表与串行查询过多**：`ux-health` 单次请求发出 18~20 条查询、`growth` 发出 18 条查询；`dashboard/stats` 在前端未展示积分/签到字段的情况下仍执行全表 `points_transaction` FIFO 重放；`usage/users` 的总数统计嵌套在完整成本聚合子查询中；`feedback` 在筛选 `has_screenshot` 时无 `LIMIT/OFFSET` 全表拉回 Python。
3. **关键过滤与排序列缺少索引**：`chat_message(role, created_at)`、`user(created_at, id)`、`payment_order(status, paid_at)`、`subscription_history(created_at)`、`file(file_type, created_at)`、`admin_audit_log(created_at, id)`、`points_transaction(user_id, created_at)` 等未建索引。
4. **缺少服务端缓存与超时保护**：所有管理后台统计接口每次均实时硬算且无 `statement_timeout`；前端 React Query 默认 `retry: 1` 在慢查询超时后会立刻重试放大 DB 压力。

## Decision

在保持既有 API 契约与 SQLite/PostgreSQL 双兼容的前提下，对管理后台做全链路优化：

1. **查询下推与扫表合并**：
   - `admin_ux_metrics_service.py`：合并当期与上期查询；轮次与反馈查询彻底剥离 `chat_message.tool_calls` 不再触发 TOAST 解压；工具卡撤销/恢复统计仅扫描 `tool_calls IS NOT NULL` 且包含目标工具的行，在 PostgreSQL 下经 `jsonb_array_elements` 聚合（含 `DataError` 回退）；`file_version` 章节/净增字数与 W1 写作者留存改为单条 SQL CTE 返回单行汇总；PostgreSQL 下 `llm_usage_event` 分位数（`percentile_disc`）与设备占比下推 SQL。
   - `admin_stage_latency_service.py`：24h AI 草稿存活率改写为单条 SQL CTE（不再拉回全部历史版本）；PostgreSQL 下用 `GROUPING SETS` 与 `percentile_disc` 一次 SQL 算出整体与分端分位数。
   - `admin_growth_service.py`：复用日趋势的可加指标并合并当期/上期扫描，将 18 条 SQL 压缩至 8 条。
   - `admin_usage_service.py`：`usage/users` 总数改为 `COUNT(DISTINCT user_id)`；`usage/summary`、`user/daily` 与 `usage/models` 消除冗余总计扫描，并在 PostgreSQL 下将模型分位数和 decode 速度下推 `GROUPING SETS`。
   - `dashboard/stats`、`points/stats`、`referrals/stats` 合并同表多条聚合查询；`points_service.get_available_points_stats` 改用轻量列投影并借助 `(user_id, created_at)` 索引消除全表排序。
   - `dashboard/stats`、`audit-logs`、`payment-orders` 增加向后兼容的可选参数（`include_commercialization`、`include_total`、`include_needs_attention_total`，默认 `true`），首页与用户详情页按需关闭未使用的重统计。
   - `feedback` 的 `has_screenshot` 筛选下推 SQL 过滤并仅校验含路径子集，避免全表物化；各列表页移除不必要的 `COUNT(*) FROM (SELECT * ...)` 子查询包装。
2. **补齐核心索引**：新增 Alembic 迁移 `20261123_000000_add_admin_analytics_indexes.py` 与对应 SQLModel/`database.py` 索引定义，包含 10 个复合/时间索引与 2 个 PostgreSQL 部分索引（assistant feedback / assistant tool_calls）。
3. **Redis 短 TTL 缓存与读超时守卫**：新增 `apps/server/services/infra/admin_analytics_cache.py`，对管理后台统计接口提供 fail-open 的 60s~300s Redis 缓存、进程内分段锁防击穿、显式 `refresh=true` 穿透刷新，以及 PostgreSQL `SET LOCAL statement_timeout` 保护。
4. **前端配合**：为管理后台重分析查询设置 `retry: false`，将「刷新」按钮接到 `refresh=true`，并在首页/用户详情页传入精简参数。

## Alternatives considered

1. **新建每日预聚合宽表 + 定时回填任务（物化视图 / Cron）**：
   - 最强理由：可将 30/90 天历史统计压缩到毫秒级点查，彻底解耦在线业务表。
   - 拒绝原因：需要引入新的后台调度任务、口径变更回填机制以及跨时区（北京时间）一致性维护成本。在当前数据规模下，剥离 `tool_calls` TOAST 读取 + SQL 聚合下推 + 核心索引 + Redis 短 TTL 缓存即可将耗时从数十秒压至百毫秒内（缓存命中 <5ms），且保持零数据冗余与实时口径一致；若后续规模继续增长，可在此基础上平滑演进按日快照。

2. **直接移除 `dashboard/stats` 中的积分/签到/邀请字段**：
   - 最强理由：彻底消除该接口的历史包袱，简化 schema。
   - 拒绝原因：属于破坏性接口契约变更，会影响既有测试和潜在调用方；通过 `include_commercialization=false` 可选参数（默认 `true`）既保持 100% 向后兼容，又让仪表盘前端立刻跳过全表 FIFO 重放。

## Consequences

- **收益**：体验健康、增长概览、阶段延迟、用量模型与列表分页的查询条数和传输体量大幅下降，冷查询耗时显著降低、热查询走 Redis 缓存秒开，且避免慢查询占用连接池或前端重试放大负载。
- **代价**：新增 10 个常规索引与 2 个 PostgreSQL 部分索引会略微增加写入开销与索引磁盘占用；开启 Redis 缓存后统计数据存在 1~5 分钟延迟（可通过页面「刷新」按钮带 `refresh=true` 实时穿透）；生产发布前需按标准流程执行 Alembic 迁移。
