# ChatMessage 存储去重与压缩前历史工具结果归档

## Problem

生产 PostgreSQL 容量与费用审计显示，`chat_message` 表 TOAST 存储已达 **2.41 GB**（占整库 3.40 GB 的 71%），导致 Postgres 常驻内存、公网备份/查询流量和卷占用同步膨胀。按列拆解其原始文本体积（5.52 GB）主要由两类冗余造成：

1. **思考过程双写（`reasoning_content` 1.17 GB）**：单循环 agent 落库时把每一段 `thinking_content` 既按顺序写入 `message_metadata.display_events`，又拼接成完整字符串重复写入 `chat_message.reasoning_content`。审计整个后端（`apps/server/`）与前端（`apps/web/src/`）确认：`GET /api/v1/chat/session/{project_id}/recent` 的 `MessageResponse` 与 `load_only` 根本不读取 `ChatMessage.reasoning_content`，前端只消费 `message_metadata.display_events` 中的 `thinking_content`，历史回放（`loop/history.py`、`session_loader.py`）也不读取该列。
2. **历史只读查询全文永久滞留（`tool_calls` 2.82 GB）**：`query_files` 与旧版 `parallel_execute` 查询子任务把整章正文写进 `chat_message.tool_calls`；即便会话已经完成轮间压缩（`message_metadata.compaction`）或静置多日，压缩点之前早已不再回放、也不再参与 `SessionLoader.session_read_snapshots` 结转的历史查询正文仍永久滞留在 TOAST 中。

## Decision

在不改变表结构、聊天 UI 契约、冷启动 DB 历史回放、`SessionLoader.session_read_snapshots` 与修改/删除卡撤销恢复能力的前提下，分三步瘦身 `chat_message`：

1. **停止双写 `reasoning_content`**（`agent/service.py`、`agent/core/message_manager.py`、`agent/core/chat_storage.py`）：
   - 单循环路径以 `assistant_display_events` 作为 `thinking_content` 的单一真源，不再额外拼接并传入 `reasoning_content`；`MessageManager._save_messages_with_session` 也同步兜底：当规范化后的 `display_events` 已含非空 `thinking_content` 时，将 `chat_message.reasoning_content` 存为 `None`。
   - 仅输出思考、无正文的轮次仍通过 `has_substantive_display_events(assistant_display_events)` 正常落库并在前端原样渲染。
   - 不含 `thinking_content` 展示事件的旧行与旧调用方继续原样保留 `reasoning_content`。
2. **压缩后自动归档压缩边界之前的只读查询载荷**（`agent/core/chat_storage.py`、`agent/core/message_manager.py`、`agent/service.py`）：
   - 新增可复用纯函数 `archive_historical_tool_calls_json(raw_tool_calls)`：仅处理 `query_files` 及旧版 `parallel_execute` 里只读查询子任务（`query_files` / `hybrid_search`），仅剥离大体积载荷键（`content`、`content_preview`、`matches`、`author_edits`、`file_metadata`、`content_range`），保留 `id`、`title`、`file_type`、`status`、`error`、`error_type`、`user_message`、`count`、`match_count` 等轻量字段与原有列表/字典外形，并附加短说明 `archived_note`（`"历史查询正文已归档，需要时请重新用 query_files 读取"`）。绝不改动 `edit_file`、`write_chapter`、`delete_file`、`create_file`、`update_project` 或任意撤销/恢复卡元数据，且重复执行严格幂等。
   - 当含合法 `compaction` 记录的助手行或用户行主事务提交成功后，以 fail-open 方式调用 `archive_pre_compaction_tool_calls_safe`：严格按与 `loop/history.py` / `session_loader.py` 一致的 `(created_at, id)` 边界，仅归档同会话内 `<= covers_through_message_id` 的助手行；锚点缺失时直接跳过；单行更新使用 PostgreSQL `FOR UPDATE SKIP LOCKED`（SQLite 用进程内条带锁）并重读最新 `tool_calls`，不阻塞并发撤销/恢复写回，异常仅记日志回滚、绝不阻断主保存与 SSE。压缩点之后的活跃行保持 100% 完整。
3. **提供零锁分批历史回填脚本**（`apps/server/scripts/compact_historical_chat_messages.py`）：
   - 默认 `--dry-run`，需显式传 `--apply` 才写库；支持 `--batch-size 200`、`--sleep-ms 100`、`--idle-days 7`。
   - 对所有 `display_events` 已含非空 `thinking_content` 的助手行清空重复 `reasoning_content`；仅对落在会话最新合法压缩点 `<= covers_through_message_id` 边界内、或所属会话最新一条消息距今超过 `--idle-days` 的助手行归档 `tool_calls`。
   - 使用键集分页、短批次事务与 `SKIP LOCKED` 逐行更新，绝不执行 `VACUUM FULL`，仅输出纯数字进度与节省字节数（不打印任何正文或 PII）。

## Alternatives considered

1. **所有轮次入库时立即截断 `query_files` 正文，仅依赖 Redis `wire_prefix_cache` 维持跨轮上下文**：
   - 最强论点：写入体积降幅最大，连活跃会话的 `tool_calls` 也不存正文。
   - 拒绝原因：`wire_prefix_cache` 的 TTL 仅 15 分钟且 fail-open，Redis 重启、超时失效或跨供应商切换后必须回退到 `load_loop_history` 与 `SessionLoader.session_read_snapshots` 从数据库冷重建；若把活跃行正文也砍掉，模型会被迫反复重读刚刚读过的文件，且跨轮 `edit_file` 快照校验会退化。按压缩边界和静置天数归档，才能同时守住冷回放与快照零退化。
2. **直接对 `chat_message` 执行数据库迁移删除 `reasoning_content` 列，或归档脚本顺带跑 `VACUUM FULL`**：
   - 最强论点：删列彻底去掉 schema 历史包袱，`VACUUM FULL` 能一步把磁盘文件缩回最小。
   - 拒绝原因：历史旧会话仍存在没有 `display_events` 的行，删列需要额外数据搬迁与滚动发布协调；`VACUUM FULL` 会加 `ACCESS EXCLUSIVE` 全表锁直接打断在线写作服务。保持列兼容、只清重复行，并在生产依赖常规 autovacuum 或后续独立窗口下的在线 `pg_repack` 回收物理空间，风险可控。

## Consequences

- **收益**：
  - 新产生的单循环对话不再双写 `reasoning_content`，每次成功压缩后自动回收压缩前历史 `query_files` 正文，止住 `chat_message` TOAST 持续线性膨胀。
  - 运维可在任意时段先 `--dry-run` 预估收益，再分批 `--apply` 无锁清理存量历史行（配合常规 autovacuum 复用空闲页，或在受控维护流程中在线回收空间）。
  - 活跃会话冷回放、`session_read_snapshots`、前端历史时间线与修改/删除卡撤销恢复均保持零退化。
- **代价与注意**：
  - 超过 `--idle-days`（默认 7 天）被回填归档且无压缩点的沉睡会话若日后重新唤醒，模型在需要历史查询文件全文时会看到 `archived_note` 并重新调用一次 `query_files`。
  - 常规批量 `UPDATE` 依赖 PostgreSQL autovacuum 释放元组供后续复用，不会立即缩小磁盘文件物理大小；如需立即压缩磁盘占用，需另行在授权窗口使用在线工具（如 `pg_repack`）处理，严禁在线执行 `VACUUM FULL`。
