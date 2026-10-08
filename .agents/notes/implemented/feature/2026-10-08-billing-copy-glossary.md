# Agent Note: 订阅与付费文案按术语表统一，兑换失败返回错误码

Status: implemented

## Problem

2026-10-08 文案审阅（copy plan 第 A 节）发现订阅、付费、兑换相关界面有几类问题：

- 同一概念多种叫法：付费方案叫「专业版 / Pro 会员 / 会员」，免费方案在库里叫「免费试用 / Free Trial」（免费版永不到期，叫「试用」会误导），主按钮有「升级专业版 / 购买 Pro / 立即升级」，订阅页叫「订阅与权益 / 订阅中心」，每日额度单位写「次/天」，不设上限写「无限」。
- 设置 > 订阅卡片对免费用户显示「未开通」，像是账号不能用；Pro 用户只看到「剩余 N 天」，看不到到期日，也没有续费入口（没有自动续费）。已经是 Pro 的用户点续费后，弹窗标题仍是「开通 Pro 会员」。
- 权益列表里「素材上传」后端从不扣减（只扣 `material_decompose`），「导出格式 TXT」各方案都一样；对比表与用量卡把不包含的功能显示成「0 次/月」「0/0」加空进度条，像是出错了；Pro 用户也看到「每日额度 00:00 重置」。
- 积分兑换 Pro 成功后弹窗静默关闭；积分余额旁边看不出积分能换什么。
- 兑换码失败时，后端 `redemption_service` 返回英文句子（「You have already redeemed this code」等），限频返回「Rate limit exceeded」，中文界面直接显示英文；前端 `errors.json` 也缺 `ERR_REDEMPTION_*`、`ERR_REFERRAL_MAX_CODES_REACHED` 等码，收到时只能退回「请稍后重试」，对已用过的码有误导。

## Decision

- **术语**：Pro（中文里也写 Pro）、免费版、开通 Pro（免费用户）/ 续费 Pro（Pro 用户）/ 开通或续费 Pro（状态未知）、订阅权益（页面名）、AI 消息（量词「条」）、不限、不含、素材拆解。`settings`、`dashboard:billing`、`points`、`errors` 中受影响的 key 中英文同步改写，组件 fallback 与 zh locale 一致。
- **方案名按 tier 显示**：`getLocalizedPlanDisplayName` 新增可选 `tier` 字段；传入 `free` 时显示「免费版 / Free」，传入 `pro` 时显示「Pro」，不看库里的 `display_name`；其他 tier 或不传 tier 时仍用 `display_name(_en)`。作者侧调用方（`SubscriptionStatus`、`BillingPage`、`PricingPage`）传 tier；后台页面不传，继续显示营运编辑的原值。后端 `DEFAULT_FREE_PLAN_DISPLAY_NAME(_EN)` 改为「免费版 / Free」，只影响没有方案行时的兜底；生产库 plan 行不做数据迁移。
- **状态行**：新增 `getSubscriptionStatusLine`。免费版不显示状态词；Pro 生效中显示「有效期至 {{date}} · 剩余 {{days}} 天」（日期按北京时间，`formatBeijingPeriodDate`）；`expired` 显示「已到期，现在是免费版」；`cancelled` 显示「已取消」。设置卡片与计费页共用。`settings:subscription.{active,none,daysRemaining}` 删除。
- **续费入口**：`SubscriptionStatus` 新增 `onRenewClick`，Pro 用户看到「续费 Pro」按钮；设置弹窗传入的处理是关闭弹窗并跳转 `/dashboard/billing`。
- **权益行**：`getSubscriptionFeatureRows` 删除素材上传与导出格式两行；`getEntitlementMetricDefinitions` 删除导出格式。不限只写「不限」（不再是「不限 条/天」），每日 AI 消息单位改用新 key `dashboard:billing.messagesPerDay`（「条/天」，替换 `timesPerDay`），月度额度为 0 时显示「不含」。计费页用量卡在额度为 0 时显示「Pro 可用」，不画进度条也不显示月度重置提示；Pro（每日上限 -1）显示「Pro 每天的 AI 消息不限条数。」而不是重置时间。
- **升级弹窗**：`UpgradePromptModal` 不再把同一段 description 同时传给 Modal 副标题和正文，只在正文显示一次；徽标从「升级建议」改为固定的「Pro」。
- **积分**：`RedeemProModal` 兑换成功后保留弹窗，显示 `CelebrationBurst` 和「兑换成功！Pro 已增加 {{days}} 天。」，底部只留「关闭」；权益列为「AI 消息不限条数 / 项目数不限 / 每月 5 次素材拆解」，去掉标签后的半角冒号。`PointsBalance` 在拿到 `pro_7days_cost` 后显示「{{cost}} 积分可兑换 7 天 Pro」，价格加载失败时不显示。`settings.json` 里没有读者的 `points.*`（只保留 `checkIn`、`alreadyCheckedIn`、`redeemPro`）与整个 `referral.*` 子树删除，其中包括「所有导出格式」「优先功能体验」这类不存在的权益。
- **兑换错误码**：`redemption_service.redeem_code` 失败时第二个返回值是 `ErrorCode.REDEMPTION_*`（新增 `ERR_REDEMPTION_CODE_ALREADY_REDEEMED_BY_YOU`，取代英文句子），`_get_error_message` 删除。`POST /subscription/redeem` 失败时抛 `APIException(error_code=<该码>, status_code=400)`，限频抛 `APIException(ERR_REDEMPTION_RATE_LIMIT_EXCEEDED, 429)`，HTTP 状态码不变。前端 `errors.json` 补齐全部 `ERR_REDEMPTION_*`、`ERR_REFERRAL_{MAX_CODES_REACHED,ALREADY_EXISTS,NOT_FOUND}`、`ERR_SUBSCRIPTION_{EXPIRED,NOT_FOUND}` 的中英文，由既有的 `translateError` 显示。

## Alternatives considered

- **数据迁移把生产 free 方案的 `display_name` 改成「免费版」**。最强理由：真相只在库里一处，后台和作者界面看到同一个名字。被否：生产迁移要人工执行（先备份再跑），而作者界面的名字本来就该跟术语表走；前端按 tier 显示立即生效，也不怕营运以后再把名字改回去。后台仍显示库里的原值，营运可以自行修改。
- **兑换服务直接抛 `APIException`**。最强理由：调用方不需要再判断返回值，和其他服务的错误风格一致。被否：`redeem_code` 的主体在 `try/except Exception` 里，失败时会回滚并转成 500；把业务拒绝改成异常需要重排事务边界，风险大于收益。保留三元组、只把失败消息换成错误码，改动面最小，且所有调用方（API 与测试）都已核对。
- **Pro 续费按钮直接打开付款弹窗**（跳 `/dashboard/billing?plan=pro`）。最强理由：少一次点击。被否：线上支付未开时 `?plan=pro` 会打开兑换码弹窗，对续费用户不一定合适；先到订阅权益页能看到到期日与两种续费方式。
- **积分兑换成功后用 toast 提示并关闭弹窗**。最强理由：实现简单，不用改弹窗结构。被否：owner 要求开通后有即时、明显的反馈，与兑换码、付款成功保持同一种庆祝方式更一致。

## Consequences

- 收益：订阅相关界面只有一套叫法；免费用户不再看到「未开通」，Pro 用户能看到到期日并找到续费入口；兑换失败在中文界面显示中文且说清原因；对比表和用量卡不再出现像故障的 0/0。
- 代价：方案名在作者界面写死在 `GLOSSARY_PLAN_NAMES`，以后新增 tier 要同步补表，否则回落到库里的 `display_name`。付款弹窗与积分弹窗里「每月 5 次素材拆解」是写死的文案，Pro 默认值（`DEFAULT_PRO_PLAN_FEATURES.material_decompositions`）变化时要同步改 locale。`common:upgradePrompt.badge` 已无读者但保留在 `common.json`（本次不改 `common` 命名空间）。后端仍保留 `ERROR_MESSAGES` 里兑换码的中英文短句，作为日志与非网页客户端的兜底。
- 部署顺序：Vercel 先于 Railway 上线时，前端已能翻译新码；旧后端仍返回英文句子，`translateError` 会原样显示，与现状相同，不会更差。

## Verification

`cd apps/server && venv/bin/python -m pytest tests/test_api/test_subscription.py tests/test_services/test_redemption_service.py tests/test_services/test_entitlement_hardening.py tests/test_services/test_redemption_service_wave_a.py tests/test_api/test_subscription_plans_catalog.py -q --no-cov -p no:cacheprovider`；前端 `npx vitest run src/lib/__tests__/subscriptionEntitlements.test.ts src/components/__tests__/SubscriptionStatus.test.tsx src/pages/__tests__/BillingPage.test.tsx src/pages/__tests__/PricingPage.test.tsx src/components/__tests__/PaymentCheckoutModal.test.tsx src/components/__tests__/RedeemProModal.test.tsx src/components/__tests__/PointsBalance.test.tsx`。
