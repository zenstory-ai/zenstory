# 美元收款：Creem 一次性 Pro 结账、退款扣回与多币种后台统计

关联路线图：[2026-10-21-usd-checkout-roadmap.md](../../proposed/feature/2026-10-21-usd-checkout-roadmap.md)

## Problem

ZenStory 已有英文界面与公开美元定价展示（阶段 1），但收款链路仍只支持 Zpay 支付宝人民币（CNY）：`payment_order` 缺少 `provider`、`currency` 与 `provider_checkout_id` 字段且 `trade_no` 为全局唯一，`subscription_plan` 缺少美元价格列，`admin_growth_service` 直接对 `amount_cents` 求和，前端 `PaymentCheckoutModal` 与后台订单页也写死了 Zpay 表单提交与人民币符号。Creem 商户审核通过后，需要以最小风险接入美元卡付主链路（阶段 2），并补齐卡支付必需的退款扣回、拒付告警与查单补偿。

## Decision

1. **增量数据库迁移（`20261124_120000_add_multicurrency_creem_payments.py`、`20261124_180000_harden_payment_order_refund_dispute.py`）**：
   - `payment_order` 新增 `provider`（默认 `'zpay'`）、`currency`（默认 `'CNY'`）、`provider_checkout_id`（可空索引，存 Creem `ch_...` 会话 ID），并将唯一约束调整为 `(provider, trade_no)`；后续增量加固迁移新增 `paid_amount_cents`（记录渠道实扣金额）、`refunded_at`、`refunded_amount_cents`（默认 0）、`refund_ids` 与 `disputed_at`，用于原子幂等扣天、按实扣金额累计部分退款与保留拒付记录。
   - `subscription_plan` 新增 `price_monthly_usd_cents`、`price_yearly_usd_cents`（默认 0，自动回填 `pro` 为 `999 / 7900`），同步暴露在 `/api/v1/subscription/catalog`、`/api/v1/subscription/plans` 与后台套餐管理接口；后台修改 Pro 美元价时给出需同步 Creem 商品价的警告。
2. **渠道无关的幂等结算层（`apps/server/services/subscription/payment_fulfillment.py`）**：
   - 从 `zpay_service.py` 抽出共享的两步结算函数 `settle_paid_order`（先持久化 `paid / trade_no / paid_at`，再条件 claim `fulfillment_status` 并开通会员，`SubscriptionHistory.event_metadata.source` 记录实际 `provider`），并新增 `record_paid_amount_mismatch`（签名与商品校验通过但金额/币种不一致时仍落库 `status=paid`、`trade_no`、`paid_at`、`fulfillment_status=failed`、`failure_reason='amount_mismatch'` 进入待处理队列，不自动开通）。
   - `record_refund` 以 `refunded_at IS NULL` 作为原子幂等条件（防止退款→拒付→再退款二次扣天），对 `refund_ids` 去重，部分退款仅累计 `refunded_amount_cents` 并进入待处理队列、累计退满才全额收回天数；`pending` 订单收到提前到达的退款/拒付也写入终态阻止后续延迟结算开通；`record_dispute` 不再把 `refunded` 覆盖回 `disputed`，而是写入 `disputed_at` 与 `failure_reason`；`NEEDS_ATTENTION_CONDITION` 将 `fulfillment_status=failed` 限定在 `status IN ('pending','paid')`，避免已退款的失败订单长期滞留待处理队列。
3. **Creem 后端服务与安全校验（`apps/server/services/subscription/creem_service.py`、`config/payment_settings.py`、`api/payments.py`、`scripts/release_preflight_check.py`）**：
   - `CreemSettings` 校验 `CREEM_ENABLED`、`CREEM_TEST_MODE`（`test-api.creem.io/v1` vs `api.creem.io/v1`）、`CREEM_API_KEY`（严格校验 `creem_test_` 前缀与 `CREEM_TEST_MODE` 一致）、`CREEM_WEBHOOK_SECRET`、`CREEM_PRODUCT_ID_PRO_MONTH/YEAR`、`CREEM_RETURN_URL`（兜底默认为 `{FRONTEND_URL}/dashboard/billing/payment-return`，并严格校验路径必须为前端 `PaymentReturnPage` 路由 `/dashboard/billing/payment-return`）；`release_preflight_check.py` 复用同一配置校验、禁止生产环境开启 `CREEM_TEST_MODE=true`，并在显式传入 `--verify-creem-products` 时调用 `GET /v1/products` 核对月付/年付商品价格与币种和数据库 `pro` 套餐美元价一致（不与离线 `--strict` 耦合）。
   - 建单调用 `POST /v1/checkouts`（一次性 30 天 / 365 天，传 `request_id=out_trade_no`、`success_url`、`metadata`、`customer.email`），严格校验返回的 `checkout_url` 主机白名单（`creem.io` / `www.creem.io` / `checkout.creem.io`）与 `mode`，保存 `provider_checkout_id` 并返回 `method="GET"` 跳转指令。
   - `POST /api/v1/payments/creem/webhook` 使用原始请求体验证 `creem-signature`（HMAC-SHA256 hex），核对 `mode`、产品 ID、币种与金额后处理 `checkout.completed`、`refund.created`、`dispute.created`；请求日志中间件同步脱敏该路径与请求体。
   - 用户侧与管理侧 `/sync` 接口按 `order.provider` 分发，对 Creem 订单调用 `GET /v1/checkouts?checkout_id=...`（若 `order` 仅返回字符串 ID 则补充查询 `GET /v1/transactions/search?order_id=...`）校验实扣金额与币种；遇到已标记 `amount_mismatch` 或无法获取权威金额时拒绝自动开通并落库 `needs_attention`。
4. **后台分币种统计与前端双币种结账**：
   - `admin_growth_service.py` 在单条条件聚合 SQL 内分别汇总 `revenue_cents`（CNY）与 `revenue_usd_cents`（USD）及 `revenue_by_currency`，并将 `creem` 纳入付费渠道（`PAID_SOURCES`、`PAID_CONVERSION_CHANNELS`），绝不混加币种。
   - 前端 `PaymentCheckoutModal.tsx` 支持 `alipay`（CNY 表单 POST）与 `card`（USD 白名单主机 `creem.io` / `www.creem.io` / `checkout.creem.io` GET 跳转），默认支付方式随语言切换（英文默认银行卡 USD，中文默认支付宝 CNY），两者同时开启时允许手动切换；`PricingPage` 与 `BillingPage` 均仅在英文界面且 `payment_options` 包含 `card` 时展示 USD 价格；`GrowthOverview`、`PaymentOrderManagement`、`AdminUserDetail` 按订单/套餐币种正确格式化金额并展示 `refunded` / `disputed` 状态。

## Alternatives considered

- **首版直接接入 Creem 自动续费订阅（`subscription.*`）**：虽更贴近海外 SaaS 惯例，但需新增周期续费、取消、宽限期和客户门户状态机。沿用路线图分阶段推进，首版复用一次性 30 天 / 365 天开通链路，降低对账与发版风险。
- **在 `GET /api/v1/payments/options` 新增冗余 `methods` 对象数组**：虽能携带元数据，但现有 `payment_methods: ["alipay", "card"]` 已能与套餐目录里的多币种价格 cleanly 配合，保持原响应结构可做到 100% 向前向后兼容。

## Consequences

- **收益**：海外作者可直接用银行卡以 USD 开通 Pro；Creem 作为 MoR 处理全球税务；Zpay 与 Creem 共用幂等结算与查单补偿；后台收入严格分币种；退款自动扣回时长，拒付自动进异常工单队列。
- **代价与注意**：生产发版前需先按 `docs/ops/release-ci-cd.md` 执行备份与 `alembic upgrade head`（staging 同步补增量列），再配置 `CREEM_*` 环境变量；生产必须设置 `CREEM_TEST_MODE=false` 与 Live Mode 密钥和商品 ID。
