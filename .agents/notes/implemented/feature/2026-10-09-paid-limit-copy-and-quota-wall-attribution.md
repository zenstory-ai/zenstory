# Agent Note: Pro 用户撞上限不再看到「开通 Pro」；付费归因单列「撞过 AI 上限」与续费

Status: implemented

付费用户只显示通用一句、以及套餐未知时先按免费渲染的部分，已由 [2026-10-09-paid-limit-hints-and-neutral-pending](2026-10-09-paid-limit-hints-and-neutral-pending.md) 取代。

## Problem

1. Pro 也有有限的额度（每个文件 100 个版本、20 个自定义技能、每月 100 次灵感复制），用满时弹出的是给免费用户写的升级弹窗，按钮写「开通 Pro」。版本、技能、灵感各自调用 `UpgradePromptModal`，都不判断套餐。
2. 订阅页顶部按钮对 Pro 用户显示「续费 Pro」，但点击和订单的 `upgrade_source` 仍记为 `billing_header_upgrade`，续费和升级在归因里混在一起（10-01 至 10-09 现役 Pro 用户产生了 11 次这样的点击）。
3. 付费归因只记最后一次点击。10-08 的 4 个真实付费者里有 3 个付费前撞过每日 AI 消息上限（其中 2 人隔了几天才回来付费），但他们的 `upgrade_source` 是订阅页或设置页按钮，复盘因此得出「没有人是撞墙后才付的」的错误结论。

## Decision

- `UpgradePromptModal` 打开时用 `usePaidPlanWhenOpen` 读取 `/subscription/me`（先用 react-query 缓存，60 秒内不重复请求；没有 QueryClient 时视为未知、按免费处理）。已是付费套餐时只显示调用方的标题、「当前套餐的这项额度已经用满了。」和「知道了」，不渲染升级按钮，也不记升级曝光；套餐未确定前不记曝光。所有调用方一处生效。
- `BillingPage`：付费用户直接从页头点按钮（没有带 `source` 参数进入）时，点击与收银台的 `upgradeSource` 记为 `billing_header_renew`；带归因参数进入的仍沿用该参数。
- `/api/admin/dashboard/upgrade-conversion` 新增 `after_ai_quota_wall_conversions` 与 `paid_after_ai_quota_wall_conversions`：窗口内的转化中，用户在转化之前出现过 `chat_quota_blocked` 或 `settings_subscription_upgrade:blocked` 升级入口事件（即每日 AI 消息用完）的条数，及其中经支付宝付费的条数。只看 `created` 与 `upgraded`，续费（`renewed`）不算撞墙后转化；撞墙时间取事件的服务端写入时间 `created_at`，不用客户端可以传入的 `occurred_at`（新用户审计 P3-13）。管理后台转化卡片新增「撞过每日 AI 上限后转化（其中付费 N）」。

## Alternatives considered

- **每个调用方各自判断套餐并传入 `isPaid`。** 最强理由：显式、不在通用弹窗里发请求。被否：9 个调用方都要改，新加的上限弹窗很容易漏；套餐状态本来就在 react-query 缓存里，弹窗自取更不容易出错。
- **订单上新增「付费前撞过上限」字段。** 最强理由：每笔订单可直接查看，不依赖事件表。被否：需要迁移，且撞墙事件已经完整记录在 `upgrade_funnel_event`，按需联查即可；后台统计口径也可随时调整。
- **把任何 `*_quota_blocked` 都算「撞墙」。** 最强理由：口径更宽，覆盖版本、技能等上限。被否：复盘关心的是每日 AI 消息这道墙对付费的作用；混入其他上限会稀释这个信号，需要时可再加一列。

## Consequences

- 收益：Pro 用户不再被引导「开通」已经拥有的套餐；续费不再计入升级来源；撞墙对付费的作用第一次在后台可见。
- 代价：付费用户打开上限弹窗时，若缓存里没有套餐状态，会先短暂看到免费文案再切换。撞墙统计依赖前端上报的升级入口曝光事件，屏蔽了统计的用户会被漏算。

## Verification

- 前端：`pnpm exec vitest run src/components/subscription/__tests__/UpgradePromptModal.test.tsx src/pages/__tests__/BillingPage.test.tsx src/lib/__tests__/adminApi.test.ts`。
- 后端：`.venv/bin/pytest tests/test_api/test_admin_metrics.py -q --no-cov`：撞墙先于转化才计入、转化之后撞墙与其他上限不计入、付费只认支付宝；续费不计入，客户端回填的早于转化的 `occurred_at` 不会把转化之后才写入的撞墙事件算进去。
