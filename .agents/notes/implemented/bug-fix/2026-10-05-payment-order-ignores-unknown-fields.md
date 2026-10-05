# Agent Note: 建单请求忽略未知字段，前端先于后端部署时不再 422

Status: implemented

## Problem

2026-10-05 23:25 起，生产用户点「支付宝支付」后看到「创建支付订单失败，请重试」。#136 合并后 Vercel 立即部署了新前端，Railway 后端要等 E2E 跑完约 25 分钟才部署。新前端在 `POST /api/v1/payments/orders` 多送 `upgrade_source`，旧后端的 `PaymentOrderCreateRequest` 设了 `model_config = ConfigDict(extra="forbid")`，于是回 422（`loc=["body","upgrade_source"]`，`type=extra_forbidden`）。前端把 422 当成泛用失败，用户只能反复点、反复失败。后端部署完成后自动恢复。

前后端分属两个平台、部署时间不同步是常态，不是这次独有。任何「前端会送的请求体」只要拒收未知字段，下一次加字段就会再出一次同样的故障窗口。

## Decision

- `apps/server/api/payments.py` 的 `PaymentOrderCreateRequest` 改为 `extra="ignore"`：未知字段丢弃，不进订单、不参与计价。已知字段仍严格验证：`plan_name` 只能是 `pro`，`cycle` 只能是 `month / year`，`payment_method` 只能是 `alipay`，`upgrade_source` 仍是 `^[A-Za-z0-9_:-]+$` 且 1–64 字符，缺字段或值不合法照样 422。金额始终由服务端按方案计算，客户端送的 `amount_cents` 之类字段被忽略。
- 同文件的另外两个前端调用端点不需要改：`POST /orders/{out_trade_no}/sync` 没有请求体，`GET /zpay/notify` 是 Zpay 回调、读 query 而非 Pydantic 模型。
- `PaymentCheckoutModal` 的建单错误改成按已确认原因给出下一步：422 / `ERR_VALIDATION_ERROR` 提示「页面已更新，请刷新页面后重试」（打点 `error_code=ERR_VALIDATION_ERROR`）；`ERR_PAYMENT_RATE_LIMITED` 提示「1 分钟后再试」（与后端每用户 60 秒滑动窗口 10 次一致，查单端点 60 秒 5 次，同为 1 分钟窗口）；`ERR_PAYMENT_PLAN_UNAVAILABLE / ERR_PAYMENT_UNSUPPORTED_OPTION` 提示刷新页面；`ERR_PAYMENT_UNAVAILABLE` 保持引导兑换码；网络错误、5xx 和其他错误统一为「暂时无法创建订单，请稍后重试」。

## Alternatives considered

- **保留 `extra="forbid"`，改部署顺序（先后端、后前端）。** 最强理由：契约最严格，客户端拼错字段名会立刻暴露。被否：Vercel 与 Railway 各自由 Git 触发，要保证顺序得改两边的发布流程，且回滚前端或后端时顺序又会反过来；拼错字段名由前端类型检查和测试兜底即可。
- **前端检测 422 后自动去掉新字段重试。** 最强理由：用户无感，不必刷新。被否：要在前端维护「哪些字段可能不被旧后端接受」的清单，重试还会让限流计数翻倍；后端忽略未知字段一次解决所有未来字段。

## Consequences

- 收益：新增可选字段时，前端先上线不会再打断付款；已知字段的校验强度不变，计价仍只信服务端。
- 收益：用户在版本错位、限流、方案下架时看到可执行的下一步，而不是一句「请重试」。
- 代价：客户端拼错字段名不再得到 422，而是被静默忽略；靠 TypeScript 类型（`CreatePaymentOrderRequest`）和 `test_create_order_ignores_unknown_fields_but_validates_known_ones` 兜底。
- 代价：前端无法区分「旧后端拒收新字段」与其他 422，统一提示刷新；对其他 422 原因（理论上前端不会送出非法值）提示略不精确。

## Verification

`cd apps/server && python -m pytest tests/test_api/test_payments.py -q -p no:cacheprovider --no-cov`（`test_create_order_ignores_unknown_fields_but_validates_known_ones`、`test_checkout_uses_server_price_and_owner_isolation` 覆盖未知字段 200 且金额仍为服务端价格、已知字段非法仍 422）；`cd apps/web && pnpm exec vitest run src/components/__tests__/PaymentCheckoutModal.test.tsx`（zh/en 两种语系下 422、429、方案不可用、5xx、网络错误的提示）。
