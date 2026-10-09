# Agent Note: Pro 撞上限恢复各自的解决办法；套餐未确定前上限弹窗不显示免费文案

Status: implemented

部分推翻 [2026-10-09-paid-limit-copy-and-quota-wall-attribution](2026-10-09-paid-limit-copy-and-quota-wall-attribution.md) 中「付费用户只显示通用一句」的做法；该笔记里不记曝光、续费归因、后台撞墙统计的决定不变。

## Problem

- 上一版让 `UpgradePromptModal` 对付费用户只显示「当前套餐的这项额度已经用满了。」。PR163 审计（P3-12，`shots/tgtpay-22/26`）发现：Pro 用满 20 个自定义技能时，原本对 Pro 仍然有用的「删掉不用的技能就能腾出名额」被一起丢掉了；灵感复制（每月 100 次）看不到什么时候恢复；文件版本存满时也不再说「正文照常保存」。作者只知道「满了」，不知道能做什么。
- 同一条笔记记下的代价：缓存里没有套餐状态时，付费用户打开上限弹窗的第一帧会闪一下免费文案「开通 Pro」（P3-14）。
- 订阅权益页副标题对 Pro 也写「需要更多额度时可升级或使用兑换码」（P3-12 后半）。

## Decision

- `UpgradePromptModal` 新增可选 `paidDescription`：付费用户看到调用方给的解决办法；没有传时仍用通用句。目前传入的调用方：
  - 技能上限（`SkillsPage`）：「删掉不用的技能就能腾出名额。」
  - 文件版本上限（`Editor`、`FileVersionHistory`、`ChatPanel`）：「正文照常保存，只是这个文件不再生成新版本。」
  - 灵感复制月额度（`InspirationGrid`）：「北京时间下月 1 日 00:00 恢复。」
  - 项目数、导出：Pro 不会撞到（项目不限，导出格式两档相同），不传。
- `usePaidPlanWhenOpen` 每次渲染都先读 react-query 里已有的 `/subscription/me`（弹窗挂载后页面才取到的也算），套餐未知时 `resolved=false`；4 秒内仍没有答案（或请求失败）时按免费处理，避免弹窗一直空着；弹窗关闭时清掉这个「放弃」，下次打开重新先显示中性占位、等这一次的答案，不会因为上一次超时就直接给付费用户看免费文案。
- `UpgradePromptModal` 在 `open && !resolved` 时只显示标题和骨架占位，不显示「开通 Pro」、升级说明或「知道了」，也不记曝光；套餐确定后再渲染免费或付费分支。
- 订阅权益页副标题按套餐与线上付款是否可用分四种：免费 + 可付款沿用原句；免费 + 不可付款「查看当前套餐和用量，需要更多额度时可以用兑换码开通 Pro。」；Pro + 可付款「查看当前套餐和用量，到期前可以续费 Pro 或使用兑换码。」；Pro + 不可付款「查看当前套餐和用量，到期前可以用兑换码续期。」；套餐未知时「查看当前套餐和用量。」

## Alternatives considered

- **在 `UpgradePromptModal` 里按 `source` 自动选付费文案。** 最强理由：调用方不用改，新加的上限也会有兜底映射。被否：弹窗要认识每个场景的业务含义（技能能删、版本不能删、灵感按月恢复），文案和场景分在两处维护；调用方本来就传 title/description，多传一句最直接。
- **套餐未知时先按免费渲染、确定后再切换（保持现状）。** 最强理由：免费用户不会多看到一帧骨架。被否：免费用户多等的是几十到几百毫秒，而付费用户看到「开通 Pro」会以为自己的 Pro 失效了，代价不对称。
- **不加超时，一直等 `/subscription/me`。** 被否：接口挂住时作者会看到一个永远空白的弹窗。

## Consequences

- 收益：Pro 用户撞上限时知道下一步（删技能、等月初），不再只看到「满了」；付费用户不会在第一帧看到「开通 Pro」；Pro 订阅页不再出现「可升级」。
- 代价：没有套餐缓存时，免费用户的上限弹窗先显示一小段骨架。新增上限弹窗需要调用方自己判断要不要传 `paidDescription`，漏传时退回通用句。

## Verification

`pnpm --dir apps/web exec vitest run src/hooks/__tests__/usePaidPlanWhenOpen.test.tsx src/components/subscription/__tests__/UpgradePromptModal.test.tsx src/pages/__tests__/SkillsPage.test.tsx src/pages/__tests__/BillingPage.test.tsx src/components/__tests__/InspirationGrid.test.tsx`：Pro 显示调用方的解决办法；套餐未知时第一帧是中性占位、没有「开通 Pro」；免费用户在套餐返回后看到升级按钮；4 秒放弃前保持中性，放弃后关闭再打开会重新等答案；Pro 订阅页副标题不含「可升级」。
