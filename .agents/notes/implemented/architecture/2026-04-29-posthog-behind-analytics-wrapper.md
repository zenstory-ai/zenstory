# Agent Note: 前端埋点经由 lib/analytics.ts 单一包装层接 PostHog Cloud

Status: implemented

## Problem

Web 应用需要行为可见性（页面浏览、登录生命周期、升级漏斗、前端异常），但把 `posthog-js` 的调用散落在业务组件里，日后换供应商或加反向代理时要改遍全仓；同时编辑器正文、提示词、上传文件都是用户创作，never 能进第三方分析。

## Decision

`apps/web/src/lib/analytics.ts` 是唯一 import `posthog-js` 的位置，对外暴露 `initAnalytics`、`trackEvent`、`trackPageView`、`identifyUser`、`resetAnalytics`。启用条件是 `VITE_POSTHOG_ENABLED=true` 且 `VITE_POSTHOG_KEY` 非空，否则全部函数为 no-op 并记录禁用原因；host 由 `VITE_POSTHOG_HOST` 决定，默认 PostHog Cloud；会话录制固定关闭（`disable_session_recording: true`）。`main.tsx` 在启动时初始化；`RouteChangeTracker` 挂在 `BrowserRouter` 下发 page view；`AuthContext` 在登录/会话恢复后调 `identifyUser`，登出时调 `resetAnalytics`。升级漏斗事件由 `upgradeAnalytics.ts` 双写：后端既有队列 + PostHog。事件属性 may 含 id、路径、目标、动作名和非敏感元数据；never 含提示词文本、编辑器内容、上传文件内容或任意用户生成文本。

URL 也属于隐私边界：`analytics.ts` 的 `sanitizeUrl` / `sanitizeAnalyticsProperties` 去掉 URL 片段（hash），query 只留白名单（`source`、`ref`、`plan`、`utm_*`），`page_url`、`referrer`、`search` 走同一套；`posthog.init` 的 `before_send: sanitizeCaptureResult` 再对 SDK 自带的 `$current_url`、`$referrer`、`$session_entry_url`、`$set` / `$set_once` 里的 `$initial_*`（任何以 `_url` / `referrer` 结尾的键）和 `$pathname` 做同样处理，覆盖 page view、web vital、异常自动捕获。`trackPageView` 不发 hash，`/auth/callback` 永不发 page view。`main.tsx` 在 `initAnalytics()` 之前调用 `lib/authCallbackParams.ts` 的 `captureAuthCallbackParams()`：把 `/auth/callback` 的 query 与 hash（OAuth access/refresh token）读进模块变量后 `replaceState` 清掉，`OAuthCallback` 再 `takeAuthCallbackParams()` 取用（没有捕获时回退读当前 URL）。注册成功后 email 经 `navigate(..., { state: { email } })` 传给 `/verify-email`，不进 URL。

DOM 派生的采集在本地写死关闭，不受 PostHog 后台开关影响：`autocapture: false`、`capture_dead_clicks: false`、`capture_heatmaps: false`、`rageclick: false`、`mask_all_text: true`、`mask_all_element_attributes: true`；Editor 与 ChatPanel 外层的 `ErrorBoundary` 容器、`MessageList` 根节点带 `ph-no-capture`。

用户可在「设置 → 通用」关闭分析：`setAnalyticsOptOut()` 写 localStorage `zenstory:analytics-opt-out`，已初始化时调 `posthog.opt_out_capturing()` / `opt_in_capturing()`；已关闭的浏览器启动时 `initAnalytics()` 直接返回 false（不建 cookie），重新打开时用上次的 env 补做初始化。隐私政策（`public/locales/{zh,en}/privacy.json` 的 `dataSharing.subsections.analytics`，lastUpdated 2026-10-05）按以上实际行为披露：PostHog Cloud 美国区域、发送的事件类别、`ph_` 开头的 cookie/localStorage 标识符、不发送作品正文/提示词/对话/上传文件、关闭方式。

来源：3ef311e（初始提交；设计稿 `docs/plans/2026-03-28-posthog-frontend-analytics-design.md`，已落地）

## Alternatives considered

- **直接在组件里调用 `posthog-js`**。最强理由：最快接通，少一层抽象。被否：供应商锁定和隐私边界无处统一执行；设计稿以"减少业务代码 churn"明确否掉。
- **URL 清洗用黑名单（只删 token、email、code 等已知敏感参数）**。最强理由：新增的营销或功能参数无需改代码就能进分析。被否：漏掉的才是问题——SSO 的 `?token=`、注册的 `?email=` 都是在没人想到的时候进了 PostHog；白名单的代价只是偶尔补一个键。
- **只在 `OAuthCallback` 里更早清 hash**。最强理由：改动最小。被否：`RouteChangeTracker` 是更早的兄弟节点、`OAuthCallback` 是懒加载路由，首个 page view 必然早于它；SDK 自带的 `$current_url`、`$initial_current_url` 也不经过包装层，只能在启动前清 URL 加 `before_send` 兜底。
- **经后端中继/反向代理而非直连 Cloud**。最强理由：隐藏 key、不受广告拦截影响。被否：作为二阶段选项保留；包装层的存在正是为了让这次迁移只改一个文件。

## Consequences

- 收益：隐私边界只在一处执行；本地与 CI 默认无 key 即无网络请求。
- 代价：包装层要随 PostHog 能力增长而扩接口；绕过 `analytics.ts` 直接 import `posthog-js` 的代码属违规，目前没有 lint 规则拦截，只能靠 review。
- 代价：query 白名单是收紧方向的默认——新加的营销参数若不在白名单里会被丢掉，需要显式加进 `URL_QUERY_ALLOWLIST`。白名单之前已经进入 PostHog 的历史事件（含 `/auth/callback` 的 token、`/verify-email?email=`）不会被这次改动清除，需要在 PostHog 后台删除。

## Verification

`grep -rln "posthog-js" apps/web/src` 只应命中 `lib/analytics.ts` 及其测试；`apps/web/.env.example` 列出三项 `VITE_POSTHOG_*`。`lib/__tests__/analytics.test.ts` 断言 init 配置与 URL 清洗，`lib/__tests__/authCallbackParams.test.ts` 与 `__tests__/main.bootstrap.test.tsx` 断言回调参数在分析初始化前被清掉。
