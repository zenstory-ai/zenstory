# Agent Note: 后台按任务分组，用户详情页汇总一个人的全部数据，统计按北京时间切日

Status: implemented

## Problem

后台的页面和统计各自为政，运营看到的数字有几处是错的：

- 侧栏 15 个入口平铺，没有分组；同一个用户的会员、配额、积分、支付订单分散在 6 个页面，用户名不能点，积分和配额页还要手输完整的用户名或 ID 才能查。
- 「今天」「近 7 天」都按 UTC 零点切日（北京时间早上 8 点）：凌晨注册、签到的人被算进昨天；`week_referrals` 是滚动 168 小时，其他窗口是「今天 + 前 6 个 UTC 日」，口径不一致。
- 配额页的单用户详情自己重算套餐：直接读 `UserSubscription.plan_id`，不看状态和到期时间，过期的 Pro 仍显示 Pro 的额度；默认值写死（`ai_conversations_per_day` 20、`material_uploads` 5），与执行时的 `get_plan_feature` 预设不同；计数不做懒重置，昨天的对话数照样显示；真正在执行的 `material_decompositions` 没有显示，显示的 `material_uploads_used` 从来没人递增。全局统计把所有周期的行加在一起。
- 「删除用户」其实只是 `is_active=False`，可以在编辑里恢复，弹窗却写「删除后无法恢复」；当前管理员自己那一行也有删除按钮（后端会 400）。
- 审计日志只认 5 种资源类型，后端实际写 11 种；操作名按 `auditLogs.action` + 首字母大写拼 key，没有一个存在，界面直接显示 `update_user` 这类原始字符串。
- 后端返回的 naive UTC 时间（不带 `Z`）被除支付订单外的所有后台页面用 `new Date()` 当成本地时间解析，北京时间的管理员看到的时间早 8 小时。

## Decision

**北京日界**

- `config/datetime_utils.py` 新增 `BEIJING_TZ`（`ZoneInfo("Asia/Shanghai")`，镜像缺 tzdata 时退回固定 +08:00，中国 1991 年后无夏令时，两者等价）、`beijing_now`、`beijing_today`、`beijing_day_bounds_utc(day)`、`beijing_window_bounds_utc(days, now)`。边界一律返回 naive UTC，与大部分表的存储一致。
- 仪表盘的 `new_users_today`、`today_check_ins`、`week_referrals`，签到统计的今天/昨天/近 7 天/连签分布，激活漏斗、升级漏斗、升级转化的窗口起点，全部改为北京日界；「近 N 天」统一为「今天 + 前 N-1 个北京日」。
- 签到统计按 `CheckInRecord.created_at` 落在哪个北京日计数，不再看 `check_in_date`。用户侧写入的 `check_in_date` 仍是 UTC 日期，连签判断照旧（见 Consequences 的后续事项）。

**配额**

- `quota_service.get_admin_quota_view(session, user_id)`：套餐取 `get_user_plan`（过期回落 free），限额取 `get_plan_feature`（按套餐预设）；日窗口（`last_reset_at` 起 24 小时）或月窗口（`monthly_period_end`）已结束的计数按 0 返回，也就是下一次懒重置会写入的值；读取不创建、不重置配额行。返回 AI 对话、素材拆解、灵感复制、自定义技能（`count_custom_skills`）四项，各带 `used/limit/reset_at`。
- `quota_service.get_current_month_totals(session)`：只汇总月周期覆盖当前时刻的配额行，返回素材拆解、灵感复制和 `skills_created`（本月新建技能，被拒的请求会回滚自己的 +1，所以不是尝试次数），并带上周期起止。去掉从未递增的 `material_uploads`。
- `GET /api/admin/quota/usage`、`GET /api/admin/quota/{id|username|email}` 改为调用这两个方法，响应结构随之改变（`UserQuotaDetail` 为嵌套的 `QuotaCounter`）。

**页面与导航**

- 侧栏分六组：概览（仪表盘）、用户与用量（用户管理、用量与成本 → `/admin/usage`）、营收（支付订单、订阅管理、订阅计划、兑换码）、增长（积分、签到、邀请）、内容与运营（技能审核、灵感、问题反馈）、系统（Prompt、配额、审计日志）。所有旧路由不变；「用量与成本」页由另一条工作线实现，本分支只放入口。
- 新增 `/admin/users/:userId`：账号信息、会员（当前生效套餐 + 订阅记录，可修改套餐/延长/改状态，复用 `PUT /admin/subscriptions/{id}`，提交规则抽到 `lib/adminSubscription.buildSubscriptionUpdate` 与订阅列表共用）、配额（`UserQuotaCards`，与配额页同一个组件）、积分（余额、最近 10 条记录、调整）、最近 10 笔支付订单（`GET /admin/payment-orders` 新增 `user_id` 过滤），以及「查看用量」链接到 `/admin/usage?user=<id>`。
- 用户管理、订阅、支付订单、问题反馈、积分、邀请、签到、审计日志里的用户名/邮箱都链接到用户详情页（`AdminUserLink`）。订阅列表新增按用户名或邮箱搜索（`GET /admin/subscriptions?search=`）。
- 「删除用户」改名「停用用户」，文案写明可在编辑里重新启用；当前管理员和已停用的账号不显示该按钮；后端对自己的 400 保留，接口与审计 action（`delete_user`）不变。
- 审计日志的资源类型与操作名由 `lib/adminAuditLabels.ts` 统一列出（与后端 `log_action` 调用一一对应），中英文标签在 `auditLogs.resourceLabels` / `auditLogs.actionLabels`；未知值显示为可读的「Sync payment order」形式。操作筛选改为精确的操作名，选了资源类型后只列该类型的操作。仪表盘「最近操作」用同一套标签。

**时间显示**

- 后台 typed schema 的时间字段改用 `UTCDateTime`（序列化为带 `+00:00` 的 ISO 字符串）。
- 后台所有页面的时间格式化改走 `dateUtils.formatAdminDateTime / formatAdminDate`，内部用 `parseUTCDate`，同时兼容 naive UTC、带偏移的字符串和纯日期（纯日期按本地日历日解析，不再变成 Invalid Date）。这样仍以 `model_dump()` 返回 naive 时间的接口也能正确显示。

## Alternatives considered

- **只在前端修时间，不动后端 schema**。最强理由：一处改动覆盖所有接口，包括返回 dict 的旧接口。没有完全采用：带偏移的输出让其他客户端（脚本、导出）也不会误读；两者并存，前端兼容两种格式。
- **签到统计继续按 `check_in_date`，同时把写入改成北京日期**。最强理由：一个字段同时服务连签和统计，口径最统一。被否：改写入会改变用户可见的连签与每日奖励边界，需要单独评估迁移和当天重复签到的边界情况，不应夹在后台整理里上线。
- **配额详情直接复用 `get_quota_snapshot`**。最强理由：与 `/subscription/quota` 完全同源。被否：它会为没有配额行的用户创建行并提交懒重置，后台查看一个人不应产生写入；新方法只读，但读出的是同样的值。
- **侧栏保持平铺，只加用户详情页**。最强理由：改动最小、与另一条工作线的冲突最少。被否：入口已经 17 个，不分组时新加的「用量与成本」找不到合适的位置。

## Consequences

- 收益：后台的「今天」与运营的直觉一致；配额页显示的就是限额执行时用的值；一个用户的数据一页看完；审计日志每条都能读懂；时间不再差 8 小时。
- 代价：`/api/admin/quota/*` 响应结构变更。前端在 Vercel 先上线、Railway 约 25 分钟后才上线，期间配额页会显示 0；只影响管理员。
- 代价：审计日志的操作筛选不再接受 `create`/`update` 这类前缀简写（后端仍支持，只是界面不再提供）。
- 后续：用户侧 `CheckInRecord.check_in_date` 仍按 UTC 日期写入，用户的签到日在北京时间早上 8 点翻页；改成北京日期需要单独的决策。
- 后续（本次刻意不做）：后台列表分页契约统一（page/page_size + 稳定排序）、各页弹窗迁移到共享表格/对话框组件、积分总量按 FIFO 账本计算（`total_points_in_circulation` 与 `points/stats` 目前与 `points_service.get_balance` 的回放结果可能不一致）。

## Verification

- `cd apps/server && python -m pytest tests/test_api/test_admin_cleanup.py tests/test_api/test_admin*.py tests/e2e/test_admin*.py tests/test_api/test_payments.py tests/test_api/test_entitlement_consistency.py -q`
- `cd apps/web && pnpm exec vitest run src/pages/admin src/components/admin src/lib/__tests__/dateUtils.test.ts src/lib/__tests__/adminAuditLabels.test.ts src/lib/__tests__/adminSubscription.test.ts`
- Mocked Playwright：`admin-routes-smoke-mocked`、`admin-users-mocked`、`admin-audit-mocked`、`admin-commercial-mocked`、`admin-responsive-mocked`
