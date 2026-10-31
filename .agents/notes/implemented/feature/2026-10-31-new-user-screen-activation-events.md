# 新用户首屏停留点：画像页、首页、打开 AI 文件、额度拦截记入服务端激活事件；注册带 Pro 意向的落地可区分

Status: implemented

接续 `feature/2026-10-19-first-message-activation-events.md`（批次漏斗与首条消息事件）和 `feature/2026-10-13-upgrade-visible-exposure-accounting.md`（升级入口曝光口径）。

## Problem

- 服务端激活事件只覆盖注册、建项目、首条消息、首轮回复、AI 写入。注册后没建项目的人停在画像页还是首页，现有服务端数据分不出来；画像页、项目打开等前端事件只进 PostHog，未配置或被拦截时没有数据，也不能按账号精确去重。
- 作者是否真的打开过 AI 写好的文件、是在手机还是桌面打开，没有记录；桌面端写作时编辑器会自动打开正在写的文件，手机端不会，两者无法比较。
- 今日 AI 消息用完后，前端直接拦下发送（输入框、首页想法框），服务端看不到这次尝试；有多少人在额度用尽时仍想继续写，没有数据。
- `/register?plan=pro`、`/login?plan=pro`（含 Google 登录）完成后跳到 `/dashboard/billing?plan=pro` 并自动打开支付弹窗，但跳转不带 `source`：落地不记升级事件，下单的 `upgrade_source` 落成计费页自己的 `billing_header_upgrade`，这条路径无法单独评估。

## Decision

**新的激活事件**（`models/activation_event.py`，沿用 `activation_event` 表和 `(user_id, event_name)` 唯一约束，每个账号每种事件只记第一次，无需迁移）：

| 事件 | 触发位置 | metadata |
|---|---|---|
| `persona_submitted` | 画像页保存成功后（`OnboardingPersonaPage`） | `skipped` |
| `dashboard_viewed` | 首页首次渲染（`DashboardHome`） | `is_mobile` |
| `ai_file_opened` | 作者在编辑器打开一份文件（`Editor.loadData` 的新打开），或 AI 写完作者正开着的那份文件后的重载（`followServer`）；AI 的 `<file>` 流自己选中的文件（桌面端自动打开）不算，作者之后亲自再打开它才算 | `is_mobile`、`while_ai_writing`（作者开着这份文件、AI 写完时记录），`file_type` 由服务端从文件读取 |
| `ai_send_blocked_quota` | 额度用尽时尝试发送：聊天输入框发送或回车、首页带想法的「开始创作」，以及服务端以额度错误拒绝的一轮 | `surface`（`chat` / `dashboard`）、`server_refused`、`is_mobile` |

**上报接口**：`POST /api/v1/activation/events`（`api/activation.py`）。现有前端到服务端的上报只有升级漏斗接口，它写的是另一张表、语义是曝光和点击，所以新加一个最小接口：
- 需要登录；按账号限流（每分钟 60 次，不按 IP 拆分，手机换网络不会多出额度）；请求体 `extra="forbid"`，只接受上面四个事件名和固定的枚举、布尔字段（布尔为严格类型），`project_id` / `file_id` 限长 64。服务端只在事实发生处记录的里程碑（如 `first_ai_message_sent`）不能经此上报。
- 归属在服务端核实（`ActivationEventService.record_client_event`）：不属于当前账号的 `project_id` 直接丢弃；`ai_file_opened` 只在文件属于当前账号、未删除、且至少有一个 `change_source = "ai"` 的版本时记录，否则返回 `{"recorded": false}` 且不写入，不泄露其他账号的文件是否存在。
- 写入失败只记日志并返回 `recorded: false`，不影响页面。
- 画像页事件放在前端上报，而不是在 `PUT /persona/onboarding` 里顺手记录：该处理函数的 SQL 与提交次数有专门的边界测试约束，观测点不应改变它。

**前端去重**（`lib/activationEvents.ts`）：服务端确认过的事件按账号记在内存和 localStorage（读写都包 try/catch），之后不再发送；同一事件在请求进行中不重复发；`ai_file_opened` 每个文件核实一次，直到确认第一份 AI 文件为止；AI 写完某份文件（`<file>` 流结束或编辑刷新）时重新开放该文件的核实（`rearmAiFileOpened`），所以「先打开空白章节、再让 AI 写」的作者在 AI 写完或之后重新打开时会被记到。所有失败静默。

**带 Pro 意向的注册落地**：`lib/authFlow.ts::planIntentBillingPath` 统一生成 `/dashboard/billing?plan=pro&source=auth_plan_intent`，登录页、验证页、Google 回调和已登录跳转共用。计费页沿用既有逻辑，对带 `source` 的到达记一次 `conversion`（`destination=billing`）；免费账号的支付弹窗 `upgrade_source` 随之为 `auth_plan_intent`，付费账号仍是续费来源 `billing_header_renew`（带 Pro 意向登录的 Pro 作者是在续费，不算注册升级路径）。带 `source=auth_plan_intent` 且套餐状态已确认为免费时，自动打开的弹窗记一次 `expose`（`surface=modal`，`meta.entry=plan_intent`，`meta.dialog` 为 `payment` 或 `redeem`）；续费提醒等不带该来源的 `?plan=pro` 链接、以及付费账号，都不记这次弹窗曝光，也就不会因为 `paymentOptions` 与订阅状态谁先返回而落到不同来源。已登录作者打开 `/login?plan=pro` 时由 `PublicRoute` 直接跳转，同样带 `auth_plan_intent`：登录完成后的跳转本身也经过这里，前端无法可靠区分两者，所以该来源的含义是「经 Pro 登录/注册链接到达」。

**后台**：`admin_growth_service` 的指标按用量和订单计算，没有按激活事件计算的漏斗，不改。首日漏斗（`get_funnel_stats`）的步骤和口径不变；新事件只入库，按需查询。

## Alternatives considered

- **继续只用 PostHog 前端事件**（`persona_submitted`、`project_opened` 等已存在）
  - 最强论据：不加接口、不加服务端代码，事件已经在发。
  - 不选的原因：只在配置了 PostHog 的环境有数据，会被广告拦截器挡掉，也不能和服务端的注册批次按账号精确对齐；漏斗正是要按账号回答「停在哪一屏」。
- **在 `PUT /persona/onboarding` 和 `GET /files/{id}` 里由服务端直接记录**
  - 最强论据：不依赖浏览器发第二个请求，事实最可靠。
  - 不选的原因：画像页处理函数的 SQL 次数由边界测试固定；文件读取接口被编辑器重载、Agent 流结束后的刷新、导出等多处调用，分不出「作者打开」，也拿不到设备形态。只把「是否有 AI 版本」放在服务端核实，打开动作由编辑器上报。
- **复用升级漏斗接口记录这些事件**
  - 最强论据：已有接口、队列和重试。
  - 不选的原因：它的语义是升级入口的曝光、点击、到达，写进 `upgrade_funnel_event` 后会混入升级报表的来源统计；激活事件要按账号只记一次，两者模型不同。
- **在服务端额度拒绝处（`/agent/stream` 的 402）记录 `ai_send_blocked_quota`**
  - 最强论据：不依赖前端。
  - 不选的原因：用尽额度后前端在发送前就拦下，绝大多数尝试到不了服务端；前端在拦截处和收到额度错误时都会上报（后者带 `server_refused=true`），覆盖两种情况，也不需要改动正在变化的 Agent 流路由。
- **在计费页把无 `source` 的 `?plan=pro` 一律归为注册意向**
  - 最强论据：只改一个文件。
  - 不选的原因：续费提醒等其他入口也用 `?plan=pro`，会被错算成注册路径；在生成跳转的地方带上来源更准确。

## Consequences

**收益**
- 能按注册批次看到新用户走到画像页提交、首页、首次打开 AI 文件的比例，以及额度用尽后仍尝试发送的人数；`ai_file_opened` 可按手机与桌面、是否在 AI 写作时开着这份文件拆开。
- 注册时带 Pro 意向的路径有独立的到达、弹窗曝光和下单来源，可以和计费页按钮分开比较。

**代价**
- 每个新用户最多多出几次小请求；`ai_file_opened` 在确认第一份 AI 文件前，每打开一份新文件多一次带归属校验的查询。
- 前端上报依赖浏览器发出请求，关闭页面或断网时可能漏记；服务端事件仍是首日漏斗的主口径。
- 上线前注册的账号没有这些事件，只能从上线后的注册批次开始比较；`dashboard_viewed` 等只记第一次，不反映回访次数。
- 带 Pro 意向注册后下单的订单，`upgrade_source` 从 `billing_header_upgrade` 变为 `auth_plan_intent`，按来源看的升级报表会多出一行，原来那行相应减少；付费账号的续费订单仍记 `billing_header_renew`。`auth_plan_intent` 也包含已登录作者从定价页 Pro 链接经 `/login?plan=pro` 被直接转到计费页的情况。
- `ai_file_opened` 不把桌面端 AI 流自动打开算作作者打开；AI 编辑（diff 审阅）选中文件时没有流状态可区分，仍按打开记录。

**测试**
- `apps/server/tests/test_api/test_activation_client_events.py`：每个账号只记一次、AI 文件核实与跨账号文件、跨账号项目丢弃、非法事件和自由文本字段被拒、未登录 401、画像提交区分跳过。
- `apps/web/src/lib/__tests__/activationEvents.test.ts`：确认后不再发送（含重新加载）、进行中不重复、按文件核实、AI 写完后按账号重新核实、失败静默可重试。
- `Editor.test.tsx`：AI 写完作者开着的文件后再上报（`while_ai_writing=true`）；AI 流自己打开的文件在流结束后也不上报。`BillingPage.test.tsx`：Pro 账号经续费提醒或 Pro 登录到达仍是续费来源且不记弹窗曝光；注册意向的弹窗曝光等套餐状态确认为免费后才记。服务端测试断言限流键按账号、不含 IP。
- `DashboardHome`、`Editor`、`ChatPanel.mount`、`OnboardingPersonaPage`、`BillingPage` 的调用点断言；登录、验证、Google 回调与 App 跳转测试改为期望带 `source=auth_plan_intent` 的计费地址。
