# Agent Note: 登出 A 再登入 B 不再回到 A 的项目

Status: implemented

相关：`apps/web/src/App.tsx`（ProtectedRoute / PublicRoute）、`apps/web/src/pages/Login.tsx`、`apps/web/src/lib/authFlow.ts`。认证框架、AuthContext 与服务端都不变。

## Problem

r5 线上审计 P3-1（`journal-retest.md`，2/2 复现）：同一浏览器里账号 A 在 `/project/<A>` 内登出，再用账号 B 登录，落到 A 的项目，页面显示「找不到这个项目」。服务端正确返回 403，没有泄露，但 B 的第一屏是错误页。

原因：ProtectedRoute 在 `user === null` 时把当前位置写进 router state `from` 再跳 `/login`，登出、`auth:logout` 和刷新失败都会走到这里。`from` 不记录属于哪个账号。B 在这条 `/login` 历史记录上登录后，Login.tsx 的登录续接和 PublicRoute 都无条件恢复 `from`，于是 B 被送回 A 的页面。

另外，身份变化时 AuthIdentityQueryBoundary 会整棵重挂路由（`key={identity}`）。登出后重新挂上的 ProtectedRoute 实例一开始就是 `user === null`，所以组件里的 ref 或 state 拿不到「上一个账号」。

## Decision

- `lib/authFlow.ts` 在模块内存里记最近登录的用户 id（`rememberSignedInUser` / `lastSignedInUser`）。这个值按标签页、按页面加载保存，路由重挂后仍在。由 ProtectedRoute 在 effect 里写入。
- ProtectedRoute 跳 `/login` 时，如果知道上一个账号，就在 state 里另加 `fromUserId`。它随历史记录保存，刷新页面后也还在。
- 新增 `ownedReturnTarget(state, userId)`，PublicRoute 和 Login.tsx 都改用它读 `from`：
  - `fromUserId` 和当前登录账号不同时丢弃 `from`，B 走原有的默认落点：自己记住的或最近的项目，没有项目时去 `/dashboard`。
  - 没有 `fromUserId` 时保持原行为。
- 保留的行为：
  - 同账号重新登录照样回到原深链接。
  - 冷启动时未登录打开的深链接没有归属标记，照常回跳。
  - onboarding 的 `from` 不受影响。
- 回归测试：
  - `App.authContinuations` 用真实 App 和身份边界跑两种情况，strict 与非 strict 各一遍：A 在受保护页登出后用 B 登录落到 `/dashboard`，A 重新登录则回到原地址。
  - `Login.test` 覆盖归属不一致和一致两种情况。Login.tsx 这一处读取只由它证明：真实 App 用例里 PublicRoute 会先重定向，单独回退 Login.tsx 时 App 用例仍通过。
  - `authFlow.test` 覆盖判定函数本身。

## Alternatives considered

- 在 ProtectedRoute 里用 `useRef` 或 `useState` 记录归属：身份边界会重挂路由，实例拿不到上一个账号，线上修不好；`react-hooks/refs` lint 也会报错。`App.guards.test` 把边界 mock 成直通，会掩盖这个问题，所以回归放在 `App.authContinuations`。
- 用 sessionStorage 或 localStorage 存上一个账号：刷新之后也能标记，但多了一份需要随登出清理的持久状态，现有测试也要求登出后存储为空，所以不采用。
- 登出时清掉 `from` 或改成跳 `/dashboard`：会破坏同账号重新登录回到原项目的行为（`journal-retest.md` 中已验证正常）。
- 服务端或项目页做「无权限时自动换项目」：改动跨层，超出本轮小修范围。

## Consequences

- 刷新页面后在未登录状态下产生的 `/login` 记录仍然没有归属，任何账号登录都会回跳；这和以前一样，服务端 403 兜底。
- 同一标签页内 A 登出后不刷新页面，又由另一个人点了未登录深链接并以 B 登录：这个链接会被记为 A 的而丢弃，B 落在默认页。这是可接受的小退化。
- OAuth 回调本来就不读 `from`，不受影响。
