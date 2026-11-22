# 管理后台用户体验健康指标（Phase 1：零新增埋点直读）

## Problem

管理后台已有增长概览、注册来源、模型调用成本和工单反馈，但缺乏面向作者真实写作体验的统一健康视图：

- 供应商首字延迟（`ttft_ms`）≥10s 慢尾率、总耗时 ≥10s 慢尾率与加权 Prompt 缓存命中率未汇总呈现。
- 助手消息 `chat_message.message_metadata` 中已落库的健康完成率、异常终止分布（`run_deadline_exceeded`、`tool_failure_circuit_open`、`terminal_model_failure`、`unexecuted_tool_text` 等）、作者中途主动停止（`user_stopped`）与客户端断线（`client_disconnected`）尚未在后台可读。
- `chat_message.tool_calls` 中由 `chat_tool_cards.py` 回写的 AI 修改撤销状态（`result.undo.undone`）与删除恢复状态（`result.restored`）未形成可追踪的质量信号。
- 作者在对话中提交的点赞/点踩、原因标签与备注已持久化在 `chat_message.message_metadata["feedback"]`，但后台「问题反馈」页只展示 `user_feedback` 工单，导致对话评价不可见。
- 首次价值耗时（TTFV p50）、次周写作留存、成稿章节与净增字数、移动端与桌面端占比缺少与增长页一致的北京时间窗口和上期对比。

## Decision

在不新增埋点表、不引入 Alembic 迁移、不改动流式计费或编辑器代码的前提下，通过只读聚合既有数据库事实交付用户体验指标体系第一阶段：

1. **后端只读服务与瘦接口**：
   - 新增 `apps/server/services/usage/admin_ux_metrics_service.py`，沿用 `admin_growth_service.py` 的口径：仅统计非管理员客户（`User.is_superuser == False`）、排除回填用量记录、采用北京时间半开日历窗口（`days: 7 | 14 | 30`）并提供等长上期与 `delta` 差值。
   - 暴露 `GET /api/admin/dashboard/ux-health?days=7|14|30`：汇总 `llm_usage_event`（TTFT p50/p95、首字与总耗时 ≥10s 慢尾率、加权缓存命中率）、`chat_message`（健康轮次完成率、异常终止分布、`user_stopped` vs `client_disconnected`、AI 修改撤销率与删除恢复率、好评率及高频点踩标签）、`activation_event` + `user` + `file` + `file_version`（TTFV p50、次周活跃写作者留存、草稿/剧本章节与净增字数、移动端/桌面端占比）以及紧凑每日趋势数组。
   - 暴露 `GET /api/admin/feedback/chat-votes?days=7|14|30&vote=all|up|down&page=1&page_size=20`：返回窗口内赞/踩汇总、高频点踩标签统计与分页明细；响应严格限制为元数据与用户备注，绝不返回助手正文、思考过程或作品原稿。
   - 查询保持 SQLite 与 PostgreSQL 双兼容、零分母安全返回 `null`，并将 30 天窗口查询次数控制在有界范围内。

2. **前端接入 6 菜单精简后台架构**：
   - 严格保持管理后台 6 个顶层侧边栏导航项不变；在「概览」下新增 URL 驱动二级标签页 **`体验健康` / `UX Health`**（`/admin/ux-health`），与「仪表盘」和「注册来源」并列。
   - 新增 `apps/web/src/pages/admin/UxHealthPage.tsx`，复用 `StatsCard`、`AdminSegmented`、`AdminPageState` 与既有 HSL 主题令牌，展示 8 张核心体验卡片、异常终止与高频点踩分布、可键盘滚动的每日趋势表，以及单个可折叠 `<details>` 统计口径说明。
   - 在 `apps/web/src/pages/admin/FeedbackManagement.tsx` 中通过 `AdminSegmented` 新增 **`工单反馈` | `对话反馈`**（`Tickets` | `Chat Votes`）二级视图，默认保留「工单反馈」，切换至「对话反馈」后按需加载 `ChatVotesPanel`，提供时间窗口、赞/踩筛选、汇总卡片、点踩原因标签和分页明细。
   - 完整同步 `apps/web/public/locales/{zh,en}/admin.json` 双语键位，补充后端 pytest、前端 Vitest 及 Playwright mocked E2E 回归覆盖。

## Alternatives considered

1. **先建专用 `agent_round_metric` 指标表再开放后台视图**：
   - 最强理由：避免对 `chat_message` JSON 字段做窗口扫描，并能一次性纳入 `first_output_ms`、`prepare_context_ms`、`write_chapter → first prose` 等当前仅打日志的阶段耗时。
   - 拒绝原因：需要数据库迁移及流式结算链路改动，与 Phase 2 工作范围重叠；先通过 Phase 1 零迁移直读已有高信号事实，可以立即解锁体验监控与对话反馈治理，同时将表结构与埋点变更严格隔离到 Phase 2/3。

2. **为体验健康和对话评价新增独立的顶层侧边栏菜单**：
   - 最强理由：入口层级更浅，管理员无需进入「概览」或「问题反馈」后再切换二级标签。
   - 拒绝原因：违反管理后台 6 项顶层导航的信息架构约束，增加侧边栏密度并破坏既有侧边栏契约测试；挂在「概览」二级路由与「问题反馈」分段切换下符合既有导航心智且零侧边栏膨胀。

## Consequences

- **收益**：零迁移、零埋点侵入即交付 8 维体验健康指标、异常终止诊断、AI 修改撤销/删除恢复追踪以及对话评价审阅；严格隔离非管理员客户数据并杜绝正文/提示词泄漏。
- **代价与后续演进**：Phase 1 的轮次状态、撤销/恢复率和对话评价依赖窗口内 `chat_message` JSON 读取，且 `first_output_ms` / `prepare_context_ms` / `chapter_first_prose_ms` 与编辑器 409 冲突率留待 Phase 2（`agent_round_metric`）和 Phase 3（前端网络与编辑安全遥测）补齐。
