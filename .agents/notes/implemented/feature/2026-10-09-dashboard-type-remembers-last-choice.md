# Agent Note: 工作台创作类型记住上次建的作品类型，没有记录时跟最近的作品走

Status: implemented

Reverses part of [入口选的作品类型贯通到工作台](2026-10-09-landing-type-and-onboarding-promise.md): that note cleared the stored type after the first successful project creation.

## Problem

2026-10-09 新用户审计 P3-17（4/4 复现）：短篇和短剧作者在官网卡片或引导问卷里选了类型，第一次进工作台默认正确；建完第一个项目回到工作台、或重新登录后，创作类型又默认成「长篇小说」，占位示例换成六皇子权谋，要多点一次，还可能建错类型。原因是 `DashboardHome` 在建项目成功后调用 `clearPreferredProjectType()`，之后只剩写死的 `"novel"`。

## Decision

- 新增 `hooks/useDashboardProjectType.ts`，`DashboardHome` 用它代替 `useState(() => getPreferredProjectType() ?? "novel")`：
  1. 有仍有效的偏好（官网卡片、引导身份，或本设备上次建的作品类型）就用它；
  2. 没有偏好时，用作者最近活动的作品（`updated_at` 最新、类型为长篇/短篇/短剧）的类型，作品列表加载完成后生效，所以换设备登录也不会回到长篇；
  3. 都没有才是长篇。
  作者在本次访问里点过类型标签后，以他的选择为准，不再被上面两条覆盖。
- 三处建项目成功的路径（快速创建、创建弹窗、「今天可以做的事」）不再清掉偏好，而是 `setPreferredProjectType(建出的类型)`，同时刷新 7 天有效期；失败时不写。存储仍按设备、读写包 try/catch。

## Alternatives considered

- **保留「建完即清」，只加「跟最近作品走」。** 最强理由：不在本地留长期偏好，完全由服务端数据决定。被否：作品列表加载前有一帧仍是长篇，网络慢时作者可能已经开始输入；本地偏好能让首屏就对。
- **点标签时就写入偏好。** 最强理由：最贴近「上次的选择」。被否：作者点标签常常只是看看其他类型的示例，并没有决定要写；以真正建出的作品为准更可靠。
- **在用户资料里存服务端偏好。** 最强理由：跨设备一致。被否：需要新字段和接口；「最近活动作品的类型」已经能覆盖换设备的情况。

## Consequences

- 收益：短篇、短剧作者回到工作台或重新登录后，默认就是自己写的类型。
- 代价：偏好会被「最后建的作品类型」覆盖：一位长篇作者偶尔建一个短篇后，下次默认短篇，直到他再建长篇或偏好过期（7 天后改为跟最近活动的作品走）。

## Verification

- vitest `src/pages/__tests__/DashboardHome.test.tsx`：官网选的短剧建完后仍是短剧，重新打开工作台再建仍是短剧；没有偏好时按最近活动作品（短篇）建；建失败保留原偏好；过期或非法偏好回到长篇。
