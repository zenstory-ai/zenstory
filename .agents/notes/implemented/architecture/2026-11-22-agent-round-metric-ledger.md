# 每轮写作 Agent 阶段耗时与可靠性事实账本（`agent_round_metric`）

## Problem

单循环写作 Agent 的每次 `/api/v1/agent/stream` 请求已在内存中计算了丰富的阶段耗时与可靠性指标（`prepare_context`、`first_output_ms`、`first_tool_started_ms`、`first_body_persisted_ms`、`tool_failures` 含 `file_body_pending`、`user_stopped`、`client_disconnected`、`deadline_exceeded` 等），但此前只写进结构化日志 `Agent stream billing evaluated`，没有持久化到数据库。这带来三个体验观测盲区：

1. **真实感知等待不可见**：`llm_usage_event.ttft_ms` 只反映单次上游供应商调用的首 token 时间，看不到后端上下文准备（`prepare_context`）、端到端首屏产出（`first_output_ms`）以及作者最敏感的 `write_chapter` / `create_file` 之后等待 `<file>` 第一段正文流出（`chapter_first_prose_ms`）的静默时长与 ≥10 秒慢尾。
2. **可靠性与退化只能靠扫 `chat_message` JSON 或翻日志**：`file_body_pending` 重试率、泄漏工具协议（`leaked_tool_call_count`）、主动停止与断线分离、桌面/移动端分拆缺乏轻量的结构化查询表；`chat_message.created_at` 与 `file_version.created_at` 也缺少独立时间索引。
3. **缺少 AI 初稿 24 小时留存/共创视角**：无法在管理后台直接观察 AI 创建的正文章节（`draft` / `script`）满 24 小时后被作者保留或进一步手改共创的比例。

## Decision

1. **新增窄表只追加事实账本 `agent_round_metric`**（`apps/server/models/agent_round_metric.py` 与迁移 `20261122_000000_add_agent_round_metric.py`）：
   - 严格只存 ID、枚举、整数毫秒、计数与布尔列，**零稿件正文、零提示词、零 PII**。
   - 以 `agent_run_id` 唯一索引防重，按 `occurred_at` 与 `(user_id, occurred_at)` 建索引；同一迁移为 `chat_message.created_at` 与 `file_version.created_at` 幂等补建轻量时间索引。
2. **在 `AgentRunMeter` 与 `StreamAdapter` 精确测量 `chapter_first_prose_ms` 与 `leaked_tool_call_count`**：
   - `StreamAdapter.set_pending_file_write` 挂起待写文件时在 `AgentRunMeter` 记录单调时钟起点（`mark_pending_file_write_armed`），并在第一段非空 `<file>` 正文流出时计算 `chapter_first_prose_ms`（每轮只记首次，空文件纠偏重挂不重置起点以反映真实等待）。
   - `AgentRunMeter` 预置 `leaked_tool_call_count`（默认 `0`），在遇到 `unexecuted_tool_text` 或 `reason="leaked_tool_call"` 终止时递增，便于与泄漏工具调用截停改造无缝对接。
3. **结算旁路非阻塞落库**：
   - `apps/server/api/agent.py` 在 `_AgentStreamSettlement.finish` 记录 `Agent stream billing evaluated` 旁通过 `services/usage/admin_stage_latency_service.py` 的 `record_agent_round_metric` 写入 1 行。
   - 始终使用独立短生命周期 session（PostgreSQL 放进 `asyncio.to_thread` 后台任务并纳入 `wait_for_stream_cleanup`），全链路 `try/except` 捕获日志，**绝不阻塞或中断用户 SSE 交付与额度结算**。
4. **管理后台「阶段耗时与慢尾」与 24h AI 初稿留存率**：
   - 新增 `GET /api/admin/usage/stage-latency?days=7|14|30`（仅超级管理员可访问，非管理员客户口径，按北京时间自然日聚合），返回各阶段 p50/p90/p95、≥10s 慢尾率、`file_body_pending` / `leaked_tool_call` / `user_stopped` / `client_disconnected` 占比、桌面/移动端分拆、每日明细，以及基于 `file` + `file_version` 计算的 24h AI 初稿留存率与共创率。
   - 在 `UsageCostPage` 增加紧凑的「阶段耗时与慢尾」区域，沿用既有 Admin 视觉原语、语义 Token、无障碍滚动区域（`tabIndex={0} role="region"`）与中英双语文案，不新增导航项或第三方图表依赖。

## Alternatives considered

1. **直接扫描 `chat_message.message_metadata` JSON 或解析运行日志**：
   - 最强论点：无需新增数据库表和迁移，零额外写入路径。
   - 拒绝原因：日志非持久化查询数据源且难以按客户/自然日做精确分位数聚合；`chat_message` 含完整对话正文体积大，且原本不落 `prepare_context_ms`、`first_output_ms`、`chapter_first_prose_ms` 与 `file_body_pending_count`，扫描大表 JSON 成本高且信息仍缺失。
2. **复用 `llm_usage_event` 追加轮级字段**：
   - 最强论点：少维护一张表，管理后台只查一份账本。
   - 拒绝原因：`llm_usage_event` 的粒度是「单次供应商模型调用尝试」（一轮写作可包含多次工具调用、压缩、主备切换），把轮级感知耗时和终态硬塞进调用级表会破坏粒度语义、导致分位数重复加权并污染成本账本。
3. **只通过前端 PostHog 事件上报阶段耗时**：
   - 最强论点：可附带浏览器网络耗时，不占后端数据库存储。
   - 拒绝原因：PostHog 受用户 opt-out 与广告拦截影响而有样本偏差，且前端无法直接观测后端 `prepare_context`、`file_body_pending` 重试或精确的 24h 数据库版本留存。

## Consequences

- **收益**：
  - 每一轮 `/agent/stream` 都有可聚合的端到端阶段耗时与可靠性事实，能够直接定位 `write_chapter → 首字` 静默回退、上下文准备变慢、移动端断连偏高及 `file_body_pending` 重试上升。
  - 与 `llm_usage_event` 职责清晰分离，无任何正文或 PII 落库，失败完全隔离于用户写作流。
- **代价与权衡**：
  - 每轮写作在结算后增加一次轻量单行 INSERT（PostgreSQL 异步线程执行）；
  - 24h AI 初稿留存率只统计满 24 小时观察期的 `draft` / `script` 文件，最近 24 小时内新建的章节暂不进入分母，窗口较短时样本数可能偏小（无样本显示 `N/A`，不伪造 `0%`）。
