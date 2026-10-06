# Agent Note: 后台按任务分组，用户详情页汇总一个人的全部数据，统计按北京时间切日

Status: implemented

## Problem

后台的页面和统计各自为政，运营看到的数字有几处是错的：

- 侧栏 15 个入口平铺，没有分组；同一个用户的会员、配额、积分、支付订单分散在 6 个页面，用户名不能点，积分和配额页还要手输完整的用户名或 ID 才能查。
- 仪表盘的「今天」「近 7 天」按 UTC 零点切日（北京时间早上 8 点）：凌晨注册的人被算进昨天（签到的日界已由 #140 修正）；`week_referrals` 是滚动 168 小时，其他窗口是「今天 + 前 6 个 UTC 日」，口径不一致。
- 配额页的单用户详情自己重算套餐：直接读 `UserSubscription.plan_id`，不看状态和到期时间，过期的 Pro 仍显示 Pro 的额度；默认值写死（`ai_conversations_per_day` 20、`material_uploads` 5），与执行时的 `get_plan_feature` 预设不同；计数不做懒重置，昨天的对话数照样显示；真正在执行的 `material_decompositions` 没有显示，显示的 `material_uploads_used` 从来没人递增。全局统计把所有周期的行加在一起。
- 「删除用户」其实只是 `is_active=False`，可以在编辑里恢复，弹窗却写「删除后无法恢复」；当前管理员自己那一行也有删除按钮（后端会 400）。
- 审计日志只认 5 种资源类型，后端实际写 11 种；操作名按 `auditLogs.action` + 首字母大写拼 key，没有一个存在，界面直接显示 `update_user` 这类原始字符串。
- 后端返回的 naive UTC 时间（不带 `Z`）被除支付订单外的所有后台页面用 `new Date()` 当成本地时间解析，北京时间的管理员看到的时间早 8 小时。

## Decision

**北京日界**

- 复用 #140 在 `config/datetime_utils.py` 提供的 `BEIJING_TIMEZONE`、`beijing_date(value)`、`beijing_day_bounds(value)`，不另设一套北京日 helper。「近 N 天」的起点写作 `beijing_day_bounds(now - timedelta(days=N - 1))[0]`。
- 仪表盘的 `new_users_today`、`week_referrals`，激活漏斗、升级漏斗、升级转化的窗口起点改为北京日界；「近 N 天」统一为「今天 + 前 N-1 个北京日」。仪表盘查询 naive UTC 存储的 `created_at`，所以边界去掉时区后再比较。
- 仪表盘 `today_check_ins` 与签到统计（今天/昨天/近 7 天/连签分布、记录列表日期）沿用 #140 的实现：`check_in_date` 已按北京日期写入，旧的 UTC 日期记录经 `effective_check_in_date` 归到北京日，同一用户同一北京日只计一次。

**配额**

- `quota_service.get_admin_quota_view(session, user_id)` 建在 `get_quota_snapshot` 上：套餐取 `get_user_plan`（过期回落 free），限额按套餐预设，日/月计数经过 #140 的北京日/北京月懒重置（与执行时同一路径，会创建缺失的配额行并提交重置）。返回 AI 对话、素材拆解、灵感复制、自定义技能（`count_custom_skills`）四项，各带 `used/limit/reset_at`。
- AI 对话日限额只有一个来源 `quota_service.get_ai_conversation_limit(plan)`，`check_ai_conversation_quota`、`reserve_ai_conversation`、`get_quota_snapshot`（因而后台配额视图）都调用它。
- `quota_service.get_current_month_totals(session)`：只汇总月周期覆盖当前时刻的配额行（周期是北京自然月），返回素材拆解、灵感复制和 `skills_created`（本月新建技能，被拒的请求会回滚自己的 +1，所以不是尝试次数），并带上周期起止。去掉从未递增的 `material_uploads`。
- `GET /api/admin/quota/usage`、`GET /api/admin/quota/{id|username|email}` 改为调用这两个方法，响应结构随之改变（`UserQuotaDetail` 为嵌套的 `QuotaCounter`）。

**页面与导航**

- 侧栏分六组：概览（仪表盘）、用户与用量（用户管理、用量与成本 → `/admin/usage`）、营收（支付订单、订阅管理、订阅计划、兑换码）、增长（积分、签到、邀请）、内容与运营（技能审核、灵感、问题反馈）、系统（Prompt、配额、审计日志）。所有旧路由不变；「用量与成本」页（`UsageCostPage`）由用量分支实现，路由 `/admin/usage` 只注册一次。
- 新增 `/admin/users/:userId`：账号信息、会员（当前生效套餐 + 订阅记录，可修改套餐/延长/改状态，复用 `PUT /admin/subscriptions/{id}`，提交规则抽到 `lib/adminSubscription.buildSubscriptionUpdate` 与订阅列表共用）、配额（`UserQuotaCards`，与配额页同一个组件）、积分（余额、最近 10 条记录、调整）、最近 10 笔支付订单（`GET /admin/payment-orders` 新增 `user_id` 过滤），以及「查看用量」链接到 `/admin/usage?user=<id>`。
- 用户管理、订阅、支付订单、问题反馈、积分、邀请、签到、审计日志里的用户名/邮箱都链接到用户详情页（`AdminUserLink`）。订阅列表新增按用户名或邮箱搜索（`GET /admin/subscriptions?search=`）。
- 「删除用户」改名「停用用户」，文案写明可在编辑里重新启用；当前管理员和已停用的账号不显示该按钮；后端对自己的 400 保留，接口与审计 action（`delete_user`）不变。
- 审计日志的资源类型与操作名由 `lib/adminAuditLabels.ts` 统一列出（与后端 `log_action` 调用一一对应），中英文标签在 `auditLogs.resourceLabels` / `auditLogs.actionLabels`；未知值显示为可读的「Sync payment order」形式。操作筛选改为精确的操作名，选了资源类型后只列该类型的操作。仪表盘「最近操作」用同一套标签。

**时间显示**

- 后台 typed schema 的时间字段改用 `UTCDateTime`（序列化为带 `+00:00` 的 ISO 字符串）。
- 后台所有页面的时间格式化改走 `dateUtils.formatAdminDateTime / formatAdminDate`，内部用 `parseUTCDate`，同时兼容 naive UTC、带偏移的字符串和纯日期（纯日期按本地日历日解析，不再变成 Invalid Date）。这样仍以 `model_dump()` 返回 naive 时间的接口也能正确显示。
- 配额页的统计周期按北京日期显示（`formatBeijingPeriodDate`，固定 `Asia/Shanghai`，不随管理员本地时区变化）；`period_end` 是下个周期开始的时刻（不含），显示时减一天，展示为本周期的最后一天。

## Alternatives considered

- **只在前端修时间，不动后端 schema**。最强理由：一处改动覆盖所有接口，包括返回 dict 的旧接口。没有完全采用：带偏移的输出让其他客户端（脚本、导出）也不会误读；两者并存，前端兼容两种格式。
- **配额详情只读，自己按窗口判断计数是否过期**。最强理由：后台查看一个人不应产生写入，也不会为没有配额行的用户建行。被否：#140 把日/月窗口改为北京日历并在懒重置里修正旧窗口，只读视图得重复这套窗口规则，迟早与执行口径分叉；复用 `get_quota_snapshot` 的写入只是下一次请求本来就会做的重置。
- **侧栏保持平铺，只加用户详情页**。最强理由：改动最小、与另一条工作线的冲突最少。被否：入口已经 17 个，不分组时新加的「用量与成本」找不到合适的位置。

## Consequences

- 收益：后台的「今天」与运营的直觉一致；配额页显示的就是限额执行时用的值；一个用户的数据一页看完；审计日志每条都能读懂；时间不再差 8 小时。
- 代价：`/api/admin/quota/*` 响应结构变更。前端在 Vercel 先上线、Railway 约 25 分钟后才上线，期间配额页会显示 0；只影响管理员。
- 代价：审计日志的操作筛选不再接受 `create`/`update` 这类前缀简写（后端仍支持，只是界面不再提供）。
- 代价：后台查看一个用户的配额会写库（为没有配额行的用户建行，并提交到期的懒重置）；写入内容与该用户下一次请求触发的相同。
- 后续（本次刻意不做）：后台列表分页契约统一（page/page_size + 稳定排序）、各页弹窗迁移到共享表格/对话框组件、积分总量按 FIFO 账本计算（`total_points_in_circulation` 与 `points/stats` 目前与 `points_service.get_balance` 的回放结果可能不一致）。

## Verification

- `cd apps/server && python -m pytest tests/test_api/test_admin*.py tests/e2e/test_admin*.py tests/test_beijing_calendar.py tests/test_services/test_quota_service.py tests/test_api/test_payments.py tests/test_api/test_entitlement_consistency.py -q`
- `cd apps/web && pnpm exec vitest run src/pages/admin src/components/admin src/lib/__tests__/dateUtils.test.ts src/lib/__tests__/adminAuditLabels.test.ts src/lib/__tests__/adminSubscription.test.ts`
- Mocked Playwright：`admin-routes-smoke-mocked`、`admin-users-mocked`、`admin-audit-mocked`、`admin-commercial-mocked`、`admin-responsive-mocked`
