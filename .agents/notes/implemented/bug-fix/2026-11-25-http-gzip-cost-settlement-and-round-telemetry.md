# API SSE 安全 GZip 压缩、取消流保守成本结算与轮次遥测归类修复

## Problem

近 48 小时生产审计暴露三类后端性能、计费与体验观测缺陷：

1. **API 缺少响应压缩且 CORS 预检缓存短**：大体积 JSON 响应（文件树、历史对话、后台报表等）未启用 GZip 传输压缩，且 `CORSMiddleware` 沿用默认 `max_age=600` 秒导致浏览器频繁重发 `OPTIONS` 预检；而直接启用 Starlette 默认 `GZipMiddleware` 会在 `http.response.start` 阶段滞留响应头并对非排除类型的流式响应缓冲分片，存在阻塞 SSE 实时推送的风险。
2. **被提前取消的流式调用永久锁死免费 ¥15 每日成本兜底预留**：近 48 小时内 `48/1,309` 条 `ai_cost_reservation`（合计 ¥22.97，单次最高 ¥1.54，其中 22 条发生在正常 `end_turn:completed` 轮次内）永久保持 `settled_units IS NULL`。根因是 MiniMax 泄漏工具调用早停（`leaked_tool_call_early_stop`）或客户端断线/停止提前关闭流时，上游不会发出 `response.completed` 终态用量事件；`88396620` 仅保证了流中非终态事件不提前误结算，但流关闭后仍无结算逻辑，导致完整 `max_output_tokens`（65,536）与峰值上界长期占用 `ai_cost_daily_budget.charged_units`。
3. **体验健康指标存在两处观测盲区**：
   - 全部 `25` 条 `client_disconnected` 轮次的 `agent_round_metric.primary_model` 为 `NULL`（尽管平均运行 106 秒）：客户端断线时 `api/agent.py` 同步取消 `SSEStreamPump` 后台任务并立即执行 `finish()`，此时 `AgentService.process_stream` 的 `finally` 尚未运行，`run_report` 仍为空字典。
   - `22` 条包含 `iteration_exhausted` 状态卡（18 条 `collaboration` 上限、4 条 `empty_file_correction`）的轮次因保留 `stop_reason='end_turn'` 被 `chat_message_ux_summary.classify_round` 误归类为 `round_category='healthy'`。

## Decision

1. **SSE 安全 GZip 中间件与 CORS `max_age=7200`**（`apps/server/main.py`）：
   - 实现 `SSESafeGZipMiddleware`（`minimum_size=500`, `compresslevel=6`），置于 `LoggingMiddleware` 与 `BodySizeLimitMiddleware` 之间：
     - 遇到 `text/event-stream`、已带 `Content-Encoding`、无正文状态码（`<200` / `204` / `304`）或已压缩二进制类型（图片/音视频/zip/pdf/docx/octet-stream）时，在 `http.response.start` 即刻透传响应头，零滞留、零压缩。
     - 遇到任意分片流式响应（首块 `more_body=True`）直接透传不缓冲；仅对完整非流式响应（`more_body=False` 且 `>=500` 字节）添加 `Vary: Accept-Encoding` 并在客户端允许 `gzip`（`q > 0`）时单次压缩。
   - `CORSMiddleware` 显式设置 `max_age=7200`（2 小时）。
2. **取消流保守上界结算**（`apps/server/services/usage/cost_budget.py`、`apps/server/services/usage/model_call_guard.py`）：
   - `Reservation` 记录预留时的 `input_upper_bound` 与 `max_output_tokens`，新增 `settle_cancelled_stream_call(reservation, streamed_output_bytes=...)`：
     - 输入保持严格 UTF-8 字节上界按 100% `cache_miss` 计价（不假设缓存命中）；
     - 输出取 `min(max_output_tokens, streamed_output_bytes + 2048)`（UTF-8 字节数恒 `>=` 真实已流出输出 token，叠加 2,048 token 覆盖上游/TCP 在途缓冲），并按调用开始时刻的真实北京时间价段（`price_band(started_at)`）结算且永不超过原 `reserved_units`。
     - 严格幂等（已由终态真实用量结算的行不动），且显式跳过 `charges_trial=True`（免费试拆仍禁流式并受终身 1 元硬兜底保护）；非流式调用在途取消仍保持 `settled_units IS NULL` 失败关闭语义。
   - `_BudgetedAsyncStream` / `_BudgetedSyncStream` 统计已流出的输出字节，在未收到终态用量就退出或关闭流时触发保守结算（异步生成器 `finally` 内非阻塞调度后台任务以兼容 `GeneratorExit` / `CancelledError`，`close()` 与 `lifespan` 关机通过 `drain_pending_cost_settlements()` 兜底落盘）。与 `88396620` 的终态用量过滤完全正交：流进行中仍只认终态事件用量。
3. **实时写入 `run_report` 与准确归类 `iteration_exhausted`**（`apps/server/agent/core/run_meter.py`、`apps/server/agent/service.py`、`apps/server/models/chat_message_ux_summary.py`、`apps/server/services/usage/admin_ux_metrics_service.py`、`apps/server/services/usage/chat_message_ux_summary_backfill.py`）：
   - `AgentService.process_stream` 在首次 `yield session_started` 之前即向共享 `run_report` 写入 `model` 与 `session_id`，在 `aordered_providers` 解析会话固定模型与 `AgentRunMeter.on_served_model` 触发（含主备切换）时实时刷新 `run_report["model"]`，并在 `prepare_context` 完成后同步阶段耗时。
   - `classify_round` 保持聊天消息 `stop_reason='end_turn'` 不变以兼容 `wire_prefix_cache` 与前端卡片渲染，但检查 `message_metadata.status_cards` 中的 `iteration_exhausted` 卡（按 `wall_clock` → `unexecuted_tool_text` → `empty_file_correction` → `tool_call` → `collaboration`/其他 优先级映射为 `run_deadline_exceeded` / `unexecuted_tool_text` / `empty_file_correction` / `max_turns_exceeded` / `iteration_exhausted`）及 `stop_outcome.reason == "incomplete"`，将其归类为 `round_category='abnormal'`。同步更新 PostgreSQL 投影回退（`_PG_PROJECTED_METADATA_SQL`）与服务端批量回填 SQL（`_PG_INSERT_BATCH_SQL`）保持 100% 口径一致；`run_report["stop_reason"]` 同步反映该耗尽原因供 `agent_round_metric` 统计。

## Alternatives considered

1. **直接使用 Starlette 内置 `GZipMiddleware`**：
   - 最强理由：零自定义中间件代码，Starlette 0.50 已将 `text/event-stream` 列入默认排除类型。
   - 拒绝原因：Starlette `IdentityResponder` 在 `http.response.start` 时仍滞留响应头直到首块正文到达、内容类型判断区分大小写、且对其他流式响应（`more_body=True`）及已压缩二进制导出未做跳过，默认 `compresslevel=9` 也增加低配容器 CPU 开销。
2. **取消流时直接把 `ai_cost_reservation` 清零（全额退还）或用 `tiktoken` 重算输入并假设缓存命中**：
   - 最强理由：可最大化释放免费用户的 ¥15 日成本配额。
   - 拒绝原因：已流式建连并输出正文或思考的调用在供应商侧真实消耗了 prefill 与部分输出 token，清零或假设缓存命中会让恶意账号通过主动断连绕过 ¥15 成本兜底；保留严格输入字节上界 + 已流出输出字节 + 2,048 在途余量既清零了永久未结算悬空行、释放掉绝大部分未用的 65k 输出预留，又保持了不可绕过的成本安全边界。
3. **把 `iteration_exhausted(layer="collaboration")` 轮次的 `chat_message.message_metadata.stop_reason` 改写为非 `end_turn`**：
   - 最强理由：与 `run_deadline_exceeded` 一致，无需在 `classify_round` 中解析 `status_cards`。
   - 拒绝原因：`wire_prefix_cache` 与既有契约测试依赖干净协作软着陆轮次保持 `stop_reason='end_turn'`，且历史库中已有记录同样需要基于 `status_cards` 正确归类；在 `classify_round` 与回填 SQL 统一识别 `status_cards` 零破坏前端与缓存契约。

## Consequences

- **收益**：大 JSON 接口带宽显著下降且 SSE 流零缓冲；浏览器 CORS 预检频次降低 12×；被早停/取消的流式调用不再产生永久 `settled_units IS NULL` 预留并释放未用输出预算；断线轮次的 `agent_round_metric.primary_model` 完整可观测；`iteration_exhausted` 与 `empty_file_correction` 轮次在体验健康面板不再虚高健康率。
- **代价**：取消流结算仍按严格输入字节上界与 2,048 输出余量保守计价，单次泄漏取消仍略高于真实成本（但大幅低于原 65k 输出峰值上界且不再悬空）；生产已有历史 `chat_message_ux_summary` 中早先误判为 `healthy` 的旧行需按需重新回填才会刷新历史窗口数字，新写入与更新行即时生效。
