# 7 天 `chat_message_archive` 冷表存储与安全渐进式归档

## Problem

生产 PostgreSQL 的 `chat_message` 热表长期累积全量历史对话，虽然此前已对新建行去重 `reasoning_content` 并对压缩前 `query_files` 做局部瘦身，但 7 天以前大量历史会话的 `message_metadata`、`tool_calls` 与正文仍常驻热表，导致：

1. `chat_message` TOAST 持续膨胀至 2.4–3+ GB，远超生产实例的 `shared_buffers` 与 OS 页缓存预算，热会话读取与索引维护持续承受物理 I/O 压力。
2. 直接按时间删除热表旧行存在两个致命风险：
   - `chat_message_ux_summary.message_id` 原先建有 `ForeignKey("chat_message.id", ondelete="CASCADE")`，删除 7 天前的热表行会级联清空管理后台历史 UX 健康分析数据。
   - 用户查看超过 7 天的历史对话、对旧消息点踩/点赞、撤销或恢复旧修改/删除卡、以及 7 天后回访旧会话继续写作，都会因缺少冷表回退而断裂。

## Decision

1. **解除 `chat_message_ux_summary` 到 `chat_message` 的级联外键约束与迁移保护**：
   - 在 `models/chat_message_ux_summary.py` 与 Alembic 迁移 `20261126_000000_add_chat_message_archive.py`（接续 `20261124_180000`）中移除 `chat_message_ux_summary.message_id` 指向 `chat_message.id` 的 `ON DELETE CASCADE` 外键，保留 `message_id` 主键与现有索引。
   - 迁移同时兼容 PostgreSQL（`upgrade()`/`downgrade()` 开头设 `SET LOCAL lock_timeout = '5s'` 避免长时间锁阻塞热表读写，通过 `inspector.get_foreign_keys` 检索真实约束名再 `op.drop_constraint`）与 SQLite（通过 `batch_alter_table` + `naming_convention` 清理匿名内联外键）。`downgrade()` 检测到 `chat_message_archive` 非空时 `raise RuntimeError` 拒绝删表，防止误回滚导致不可逆冷数据丢失。
   - 会话清空（`DELETE /api/v1/chat/session/{project_id}`）按与归档一致的锁序**先删热表 `ChatMessage`、再删冷表 `ChatMessageArchive`、最后删 `ChatMessageUxSummary`**，避免并发归档导致已清空消息从冷表幽灵复活；同时补充 `ChatMessageArchive` 的 `after_update` 监听器，使对冷表消息的反馈与撤销/恢复写回同步更新 `chat_message_ux_summary`。
2. **新增冷表 `chat_message_archive`、部分索引与安全降级探测**：
   - 新增模型 `models/chat_message_archive.py`（`ChatMessageArchive`），包含与 `chat_message` 相同的列（`session_id` 带 `ForeignKey("chat_session.id", ondelete="CASCADE")`）及 `archived_at`，建立复合索引 `ix_chat_message_archive_session_created_at_id (session_id, created_at, id)` 与 `ix_chat_message_archive_archived_at (archived_at)`；在 PostgreSQL 迁移与 `database.py` 中补充与热表一致的反馈部分索引 `ix_chat_message_archive_assistant_feedback_created_at`（`ON chat_message_archive (created_at) WHERE role = 'assistant' AND message_metadata IS NOT NULL AND strpos(message_metadata, '"feedback"') > 0`），避免后台 `list_chat_feedback_votes` 全表扫描解压冷表 TOAST。
   - 导出 `has_chat_message_archive_table(connection)`（与 `has_chat_message_ux_summary_table` 同构，仅缓存存在状态），在迁移前滚动发布或精简测试库上自动降级不报错。
3. **零丢失、前置守卫 + 先拷贝校验再删除的渐进式归档服务与运维 CLI**：
   - `services/chat_message_archival.py` 提供 `archive_chat_messages_batch` 与 `run_gradual_archival`：
     - **Fail-closed 前置守卫**：每批开始前强校验 `chat_message_archive` 存在且 `sa.inspect(bind).get_foreign_keys("chat_message_ux_summary")` 无任何指向 `chat_message` 的外键（防止仅靠 `init_db()` `create_all()` 建表但漏跑迁移时级联清空 UX 汇总）。
     - 拷贝入冷表前，将 `tool_calls` 经 `archive_historical_tool_calls_json` 瘦身并转为紧凑 UTF-8 JSON（`ensure_ascii=False, separators=(",", ":")`），将 `message_metadata` 转为紧凑 UTF-8 JSON，并清理已在 `display_events` 中保存的重复 `reasoning_content`。
     - 以小批次短事务（默认 `batch_size=500`、PG 使用 `FOR UPDATE SKIP LOCKED`）执行 `INSERT ... ON CONFLICT (id) DO UPDATE`（若覆盖了预存冷行则记 warning 日志，确保热表最新修改不会被陈旧冷行静默吞掉），随后回查 `chat_message_archive` 校验整批每一条 `id` 均已落库，并在同事务内核对/补写缺失的 `assistant` `chat_message_ux_summary` 行后复验；任一校验缺失即回滚、绝不执行热表 `DELETE`，同时中止本轮渐进归档并由 CLI 返回非零退出码（`1`）。
     - 支持 `--dry-run`（零写零删）、`--older-than-days`（默认 7，CLI 强制 `>= 1` 防误归档进行中流式轮次）、`--batch-size`（默认 500）、`--max-batches`（默认 20）、`--sleep-ms`（默认 100）以及默认关闭的 `--vacuum-analyze`（仅 PG 非阻塞 `VACUUM ANALYZE chat_message` + `ANALYZE chat_message_archive`，永不执行 `VACUUM FULL`）。日志仅记录数值计数与字节数，不记录原文与 PII。
   - 提供独立 CLI `scripts/archive_old_chat_messages.py` 供分批运维执行。
4. **读/写/删透明冷热回退**：
   - `load_active_session_recent_messages()`（供 `GET /api/v1/chat/session/{project_id}/recent` 及后续 `/bootstrap` 共用）：按 `fetch_limit = limit + 1` 先查热表，再以 `(session_id, created_at, id)` 索引单行水位探测冷表；纯热会话及全部冷行均早于热表窗口最旧行时不读冷表全行（`hot == limit` 边界仅做 1 行 `id NOT IN` 索引探测判定 `has_more`）；仅当热表不足 `limit` 或存在冷热交错（冷水位晚于热窗口最旧行，如 `FOR UPDATE SKIP LOCKED` 短暂跳过旧热行）时补充拉取最多 `fetch_limit` 条冷行，按 `id` 去重（热表优先）并按 `(created_at, id)` 升序合并，以 `len(merged) > limit` 精确判定 `has_more` 后切片 `[-limit:]`，复用同一套 `client_tool_calls` / `client_message_metadata` 瘦身逻辑。
   - `POST /api/v1/chat/messages/{message_id}/feedback`（`services/chat_feedback_service.py`）与 `services/chat_tool_cards.py`（修改卡撤销 / 删除卡恢复与状态读取）：先通过 `with_for_update()` + `populate_existing=True` 锁查热表，未命中则回退锁查 `chat_message_archive` 执行同等权限校验与写回，防止并发归档引发 `StaleDataError` 500。
   - `DELETE /api/v1/chat/session/{project_id}`：按热表 → 冷表 → `chat_message_ux_summary` 顺序同步清空并去重合并返回清理条数。
   - `agent/loop/history.py`（`load_loop_history`）：通过冷热双路降序归并（`(created_at, id) DESC`，按 `id` 去重且热表优先）按页读取会话行，并以单行纯索引冷水位（`SELECT created_at, id ... LIMIT 1`）延迟冷表全行读取——纯热会话以及在热表压缩锚点处停止的活跃跨周会话执行 **0 次**冷表全行扫描与 TOAST 解压，仅当热表耗尽或遍历越过冷水位时才按页拉取冷行，同时保持并发 `SKIP LOCKED` 交错下的严格时序、准确 `last_message_id`/`last_prompt_tokens` 与压缩锚点匹配。
   - `agent/core/session_loader.py`（`session_written_file_ids`、`session_read_snapshots`）与 `agent/suggest_service.py`（`_get_recent_chat_history`）：沿用同一套索引冷水位懒回退策略，确保 7 天后回访的已归档会话仍保留 `<author_edits>` 追踪、跨轮未变更文件快照与上下文感知的下一步建议，同时不增加纯热会话的冷表全行扫描。
   - `services/usage/admin_ux_metrics_service.py`（`list_chat_feedback_votes`）与 `agent/service.py`（`_project_has_ai_reply`）：同步支持 `chat_message_archive` 回退（按 `message_id` 去重且热表优先），确保 14d/30d 管理后台点踩明细与 UX 看板一致，且全归档旧项目不会误判为首条消息。

## Alternatives considered

1. **PostgreSQL 原生按月/按周声明式分区（`PARTITION BY RANGE (created_at)`）**：
   - 最强理由：对查询路径透明，数据库引擎自动路由分区，无需应用层编写冷热回退逻辑，且 `DROP TABLE` 历史分区几乎零膨胀。
   - 拒绝原因：对已有 30k+ 行在线表在线改造为主键包含分区键的分区表需要停机重建与外键重绑；且本地 SQLite 测试与 `create_all` 环境不支持 PostgreSQL 分区语法，引入高环境分叉风险；此外分区本身不会对冷数据执行 `query_files` 瘦身与 UTF-8 紧凑重编码。
2. **保留 `chat_message_ux_summary` 外键，改为仅靠定时脚本原地覆盖 `chat_message` 列内容（不迁出冷表）**：
   - 最强理由：无冷表模型、无需改 `/recent`、`/feedback`、`chat_tool_cards` 和 `load_loop_history`。
   - 拒绝原因：热表行数与堆表/TOAST 物理体积仍随时间单调增长，无法将热工作集锁定在最近 7 天内，也无法通过常规 `VACUUM` 将冷数据从活跃缓存工作集里隔离出去。

## Consequences

- **收益**：热表 `chat_message` 稳态仅保留近 7 天活跃消息，显著降低 TOAST 体积与热页缓存竞争；冷表行经过紧凑 UTF-8 重编码与查询正文瘦身，存储体积大幅缩减；历史对话浏览、反馈、撤销/恢复卡片、回访续写与管理后台 UX 指标 100% 保持连续。
- **代价**：热读路径每次冷加载需增加一次 1 行纯索引水位探测（命中 `ix_chat_message_archive_session_created_at_id`，<1ms、不触发堆表与 TOAST 读取）；仅在纯热表不足 `limit`、热表耗尽（无热压缩锚点的回访会话）或冷热短暂交错时才读取冷表全行；归档需通过 `scripts/archive_old_chat_messages.py` 按批次执行（先 `--dry-run` 验证再分批归档，必要时加 `--vacuum-analyze` 回收热表空闲空间）。
