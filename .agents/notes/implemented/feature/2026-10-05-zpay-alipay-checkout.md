# Agent Note: Zpay 支付宝结账只信任 notify，收款事实先落库，开通失败可查单补偿

Status: implemented

## Problem

Zpay 支付宝结账（e880fc6）上线前审查发现，「付了钱却没开通」时系统既发现不了也补救不了：

- notify 被拒或开通失败时没有任何日志（`except CallbackError: return "fail"`），日志中间件又把回调 query 整段打码，只剩一行 `GET /api/v1/payments/zpay/notify - 200`。
- 开通失败时 `session.rollback()` 把刚写的 `trade_no`、`paid_at`、`status=paid` 一起回滚，订单看起来跟放弃结账的 pending 单一样，客服用支付宝交易号搜不到。
- 没有调用 Zpay 的 `act=order` 查单，也没有后台补开通工具。Zpay 只按 0/15/15/30/180/1800×4/3600 秒重送约 3 小时，Railway 停机、WAF 拦截或 `ZPAY_NOTIFY_URL` 配错超过这个窗口，就只能人工比对后台再手改数据库。
- 返回页的轮询计数器在 `refetchInterval` 里自增，实际只轮询约 8 秒；notify 常比浏览器回跳晚十几秒，用户以为失败而重复付款。
- 付款漏斗没有打点，`create_order` 也不带来源，后台无法区分付费转化与管理员手动开通、积分兑换。

## Decision

**只信任 notify 与我们主动发起的查单。** 返回页 URL 上的参数一律不参与开通判断；返回页只读本地订单。

**收款事实与开通状态分离。** `services/subscription/zpay_service.py` 的 `_settle_paid_order` 分两步：

1. 验签、金额（整数分）、`type`、`name`（notify 才比对）都对上后，用条件 UPDATE（`status IN (pending, paid)` 且 `trade_no IS NULL OR trade_no = 本次`）写 `status=paid`、`trade_no`、`paid_at`，**立即 commit**。`trade_no` 唯一约束冲突记 `fulfillment_status=failed, failure_reason=duplicate_trade_no`，不开通。
2. 用条件 UPDATE 把 `fulfillment_status` 从 `pending/failed` claim 成 `processing`，同一事务里 `create_user_subscription` 并写 `succeeded`。只有一个调用方能 claim 成功；输掉的一方重读，已 `succeeded` 且交易号一致则回 `success`，否则抛 `processing` 让 Zpay 稍后重送。开通抛错只把 `fulfillment_status` 记为 `failed`（`subscription_fulfillment_failed`），`status=paid` 和 `trade_no` 保留。下一次 notify 重送或查单会从第 2 步重试，不会重复开通。

SQLite 下每一步开头 `BEGIN IMMEDIATE`（`_begin_serialized_write`），PostgreSQL 靠用户行 `FOR UPDATE` 与 claim 的行锁串行化。

**日志。** `api/payments.py` 对每个 `CallbackError` 写结构化日志：`reason` 加 `callback_log_fields()` 的 `out_trade_no / trade_no / money / trade_status / type`（每项截断到 64 字符），永远不记 `sign`、`key` 或完整 query。`invalid_signature / unknown_order / order_mismatch / trade_mismatch / duplicate_trade_no / order_state_conflict / not_configured` 记 ERROR（可直接设告警），其余记 WARNING；`fulfillment_failed` 用 `logger.exception`，堆栈带上原始异常。query 里出现重复键也记一条 `duplicate_query_keys`。

**主动查单补偿。** `ZpayService.query_provider_order` 以 GET 调 `https://zpayz.cn/api.php?act=order&pid=&key=&out_trade_no=`（字段见 z-pay.cn 文档：`code==1` 表示查询成功，`status==1` 表示已付，另有 `trade_no / out_trade_no / type / pid / money / name`）。httpx 超时 10 秒（连接 5 秒），不跟随重定向；httpx logger 在 `config/logger_config.py` 已压到 WARNING，传输异常以 `from None` 重抛成 `PaymentSyncError("provider_unavailable")`，所以带 key 的 URL 不会出现在日志或异常链里。`sync_order` 在 `code!=1` 时返回 `not_found`，`status!=1` 返回 `unpaid`；已付时比对 `out_trade_no`、`pid`、`type`、金额后走同一个 `_settle_paid_order`（`name` 不比对：查单响应来自我们自己发起的 HTTPS 请求，不需要靠商品名防伪）。SubscriptionHistory 的 metadata 记 `settled_via: notify | query`。

两个触发点：

- 后台 `POST /api/admin/payment-orders/{id}/sync`（superuser；admin 路由前缀沿用既有的 `/api/admin`）。无论成功失败都写 `AdminAuditLog(action="sync_payment_order")`，`old_value/new_value` 记前后的 `status / fulfillment_status`、`outcome` 和失败原因。后台订单详情里「查单并补开通」按钮调用它。
- 用户 `POST /api/v1/payments/orders/{out_trade_no}/sync`，只能查自己的订单，按用户 60 秒 5 次限流。`PaymentReturnPage` 在轮询窗口结束仍未开通时调用一次。

**后台订单列表。** `GET /api/admin/payment-orders` 加 `fulfillment_status`（`pending` 含内部的 `processing`）与 `needs_attention`（`status=paid 且未 succeeded`，或 `fulfillment_status=failed`，后者覆盖本次改动前留下的旧失败单）筛选，响应带全表的 `needs_attention_total` 供徽章显示。订单响应模型的 `status / cycle / payment_method / fulfillment_status` 改成 `str`：营运手改一行数据不会让整页或用户查单回 500。

**配置检查。** `config/payment_settings.py` 的 `PaymentSettings.configuration_problems()` 是唯一规则：notify URL 必须 https、无 query，路径必须**精确**等于 `/api/v1/payments/zpay/notify`，主机不得等于 `FRONTEND_URL` 的主机（Vercel 前端不转发 `/api`）。任何一项不满足，`checkout_enabled` 为假；已建订单的回调只需要 PID/KEY，不受影响。`ZPAY_ENABLED=true` 但配置有问题时，启动时 `zpay_service.log_configuration_status` 打一条 ERROR 列出问题。`scripts/release_preflight_check.py` 直接调用同一函数，另外要求设置 `FRONTEND_URL`。

**返回页与结账。** `PaymentReturnPage` 以进页时间为基准轮询 120 秒（前 20 秒每 2 秒、60 秒内每 4 秒、之后每 8 秒），`fulfillment_status=failed` 也继续轮询并显示刷新按钮；手动刷新重置基准时间。窗口结束仍未开通时提示「请勿重复支付」并调用一次用户查单。`PaymentCheckoutModal` 建单成功、表单提交后进入 `redirecting`，锁住付款与取消按钮，`pageshow`（`persisted`，bfcache 还原）时解锁；建单错误由后端返回 `ERR_PAYMENT_*` 错误码，前端在 `errors` 命名空间翻译；422 / `ERR_VALIDATION_ERROR` 提示刷新页面（页面与服务版本不一致，见 `.agents/notes/implemented/bug-fix/2026-10-05-payment-order-ignores-unknown-fields.md`），网络错误、5xx 与其他未知错误显示通用的「暂时无法创建订单，请稍后重试」，不再透出英文 detail。`BillingPage` 在订阅状态未就绪时显示中性的「开通或续费 Pro」。

**漏斗与归因。** 前端经 `lib/analytics` 的 `trackEvent` 打 `checkout_started`、`checkout_redirected`、`checkout_failed`、`payment_return_result`（`succeeded / failed / pending_timeout`）、`redeem_code_succeeded`、`redeem_code_failed`，只带周期、`out_trade_no`、来源和错误码，不带兑换码或个人信息。`create_order` 接收 `upgrade_source`（与 `/subscription/redeem` 同样的 `^[A-Za-z0-9_:-]+$`，≤64），存进新增的 `payment_order.upgrade_source`（迁移 `20261005_180000`，可空列），开通时写入 `SubscriptionHistory.event_metadata.upgrade_source`。后台「付费转化归因」新增 `paid_conversions`（只算 `source=zpay`）与按渠道（zpay / redemption_code / points_redemption / admin_update / other）的拆分。

## Alternatives considered

- **以返回页参数或前端轮询结果开通**。最强理由：用户回跳就能立刻看到 Pro，不依赖 notify 能否打进来。被否：回跳 URL 可以被伪造，开通必须以签名 notify 或我们主动查单为准。
- **开通失败时整体 rollback，只记 `fulfillment_status=failed`**（原实现）。最强理由：一个事务，状态永远一致，不会出现「已收款但未开通」的中间态。被否：这个中间态在现实里本来就存在；把它藏起来只会让对账和补救失去依据。
- **一并上线排程，定期扫近 24 小时的 pending/failed 订单主动查单**。最强理由：不依赖用户回到返回页或管理员发现，Railway 停机超过 3 小时也能自动恢复。被否（本次延后）：生产 Prefect worker 刻意停用，其他排程基础设施还没有；先上手动与返回页两个触发点，排程另案处理。
- **后台直接用 `PUT /admin/subscriptions/{user_id}` 补开通**。最强理由：已经存在，零开发。被否：它不碰订单，之后 Zpay 重送 notify 会再开通一次；补偿必须以订单为单位、走同一个 claim。

## Consequences

- 收益：每一笔被拒或开通失败的回调都有可告警的 ERROR；收款事实永远落库，后台一个筛选就能列出「已收款未开通」，一个按钮就能补开通且有审计记录；查单与 notify 共用同一条幂等路径，竞态下也只开通一次。
- 收益：返回页覆盖 notify 常见的延迟，并在超时后主动查单，减少重复付款。
- 代价：多了一个 `status=paid / fulfillment_status=failed` 的中间态，前端与后台都要把它显示成「支付已确认，会员暂未开通」而不是「待支付」。
- 代价：查单依赖 Zpay `api.php` 的可用性与字段格式；格式变化会表现为 `provider_invalid_response`，需要人工处理。
- 已知未做：没有订单逾时或 `closed` 状态，已签名的旧价格表单可以无限期付款；没有退款状态与退款端点，Zpay 后台退款后订单仍显示已支付、权益保留；没有排程自动对账；PostgreSQL 的并发回调测试还没有接进 CI（目前只在 SQLite 的 `BEGIN IMMEDIATE` 分支上回归）。

## Verification

`cd apps/server && venv/bin/python -m pytest tests/test_services/test_zpay_service.py tests/test_api/test_payments.py tests/test_models/test_payment_order_migration.py -q -p no:cacheprovider`；`cd apps/web && pnpm exec vitest run src/pages/__tests__/PaymentReturnPage.test.tsx src/components/__tests__/PaymentCheckoutModal.test.tsx src/pages/admin/__tests__/PaymentOrderManagement.test.tsx`。上线后从外网经 Cloudflare `curl` `ZPAY_NOTIFY_URL` 应回 `fail`（不是 404/403），并完成一笔最小金额的真实付款。
