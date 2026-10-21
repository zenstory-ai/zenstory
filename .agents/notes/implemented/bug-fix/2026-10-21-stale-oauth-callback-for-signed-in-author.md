# Agent Note: 已登录作者打开过期的 Google 回调时直接进入工作台

Status: implemented

## Problem

PostHog 近 30 天 `ERR_AUTH_TOKEN_INVALID`（`feature_area=auth, action=oauth_callback`）15 次 / 13 人，对比 265 次 `oauth_callback_success`。10-12 起事件带 `oauth_stage` 后，全部 5 次都是 `state_mismatch`，且每位用户都在 0–5 分钟前刚完成一次成功的 Google 登录：第二个回调（返回 Google 账号选择页、另一个标签页）到达时，状态 cookie 已被第一次成功登录消费，API 按 CSRF 规则拒绝并重定向到 `/auth/callback?error_code=ERR_AUTH_TOKEN_INVALID&error_stage=state_mismatch`。前端把它当成登录失败：显示「登录状态已失效，请重新登录」，并上报为异常，而作者其实已经登录。

## Decision

- `OAuthCallback` 遇到 `missing_state` / `invalid_state` / `state_mismatch` 时，若本地已有会话（`AuthContext` 的 user 或已存的 access token），直接 `navigate("/dashboard", { replace: true })`，只记 info 日志，不上报异常。受保护路由照常校验令牌，失效时仍走既有登录流程。
- 没有会话时仍显示失败并上报，但文案改为通用的「这次登录没有完成」，不再用会话过期的说法。
- 后端 CSRF state 校验、cookie 有效期与错误码不变。

## Alternatives considered

- **后端为 state 类失败返回独立错误码。** 最强理由：指标与文案在源头区分。未在本批采用：需要同步前后端发布，而前端按已有的 `error_stage` 即可区分；可作为后续清理。
- **延长 state cookie 有效期或在成功后保留 cookie。** 最强理由：减少第二个回调被拒。被否：削弱一次性 state 的 CSRF 保护，而被拒的回调本来就不需要再登录一次。

## Consequences

- 收益：已登录作者不再看到误导性的失败页，错误追踪不再被这类无害回调占据。
- 代价：本地残留但已失效的令牌会让作者先到工作台再被要求重新登录，比直接显示失败多一次跳转。
