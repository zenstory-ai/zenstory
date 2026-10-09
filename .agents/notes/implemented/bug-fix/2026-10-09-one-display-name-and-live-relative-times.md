# Agent Note: 作者名字只按一条规则显示；积分页不再出现孤立的 0；相对时间会自己走

Status: implemented

## Problem

2026-10-09 新用户审计 P3-20（多个 persona）：

- 同一个作者两个名字：设置 → 个人资料显示 `nickname || 邮箱前缀`（如「audit-serial-20261009-10702」），首页问候用 `nickname || username`，侧栏和用户菜单只用 `username`（「audit-serial-20261009」）。作者以为用户名没存上。
- 设置 → 积分「100 积分可兑换 7 天 Pro」下面有一个没有标签的「0」：`PointsBalance` 用 `pending_expiration && … > 0 && (...)`，`pending_expiration` 为 0 时 React 把 0 渲染出来。
- 编辑器保存状态和作品卡片上的相对时间只在组件因别的原因重渲染时才重新计算，页面停留时「刚刚保存」「3 分钟前」会一直不变。审计里还偶现过一次「✓ 秒前」少了数字，代码里没能复现出缺数字的路径。

## Decision

- 新增 `lib/userDisplayName.ts` 的 `getUserDisplayName`：昵称 → 注册时的用户名 → 邮箱 @ 前面的部分。设置个人资料、工作台侧栏和手机菜单、用户菜单（桌面和手机）、`MobileSidebar`、官网页头都改用它，头像首字母也用同一个名字。首页问候原本就是「昵称 → 用户名 → 默认称呼」，与规则一致，未改。
- `PointsBalance` 改为 `(balance?.pending_expiration ?? 0) > 0` 才显示「N 积分即将过期」。
- 新增 `hooks/useNow`（定时更新的当前时间）。编辑器保存状态抽成 `SavedAgoLabel`（每 10 秒更新：刚刚保存 → N 秒前 → N 分钟前 → 时刻），作品卡片的活动时间用 `RelativeTime`（每 30 秒更新）；`formatRelativeTime` 增加可选的 `now` 参数。

## Alternatives considered

- **个人资料改成「用户名」字段、昵称另起一行。** 最强理由：两个字段都能看到，不需要取舍。被否：多数作者没有设置昵称，多一行「昵称：未设置」只会增加噪音；问题是同一个人被叫成两个名字，统一规则即可。
- **页面级定时刷新整个工作台。** 最强理由：一处改动，所有时间都更新。被否：工作台首页组件很大，每隔几十秒整页重渲染没有必要；只让显示时间的小组件自己更新。
- **返回工作台时重新拉作品列表，修正卡片的活动时间。** 最强理由：卡片会反映刚刚在编辑器里的修改。被否：`refreshProjects` 会把列表置为加载中并重新选择当前项目，工作台会闪骨架屏；单个作品的接口也不返回按文件计算的活动时间。留作后续，与作品卡片字数刷新一起处理。

## Consequences

- 收益：同一个作者在所有位置是同一个名字；积分页没有无标签的数字；停留在页面上时相对时间不会过期。
- 代价：没有昵称、但用户名与邮箱前缀不同的作者，设置页显示的名字会从邮箱前缀变成用户名。刚从编辑器返回时，卡片的活动时间仍是列表加载时的数据。「✓ 秒前」缺数字未能从代码复现，只是改成了定时更新的独立组件。

## Verification

- vitest `src/lib/__tests__/userDisplayName.test.ts`、`src/components/__tests__/PointsBalance.test.tsx`（余额与即将过期都为 0 时页面上只有一个「0」）、`src/components/__tests__/RelativeTimeLabels.test.tsx`（假时钟下保存状态从「刚刚保存」走到「30 秒前」「2 分钟前」，卡片时间 5 分钟后从 2 分钟变 7 分钟），以及 SimpleEditor、SettingsDialog、Dashboard、DashboardHome、DashboardProjects、UserMenu、MobileSidebar、PublicHeader 的现有测试。
