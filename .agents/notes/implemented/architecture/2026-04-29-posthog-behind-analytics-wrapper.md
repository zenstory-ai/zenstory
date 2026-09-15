# Agent Note: 前端埋点经由 lib/analytics.ts 单一包装层接 PostHog Cloud

Status: implemented

## Problem

Web 应用需要行为可见性（页面浏览、登录生命周期、升级漏斗、前端异常），但把 `posthog-js` 的调用散落在业务组件里，日后换供应商或加反向代理时要改遍全仓；同时编辑器正文、提示词、上传文件都是用户创作，never 能进第三方分析。

## Decision

`apps/web/src/lib/analytics.ts` 是唯一 import `posthog-js` 的位置，对外暴露 `initAnalytics`、`trackEvent`、`trackPageView`、`identifyUser`、`resetAnalytics`。启用条件是 `VITE_POSTHOG_ENABLED=true` 且 `VITE_POSTHOG_KEY` 非空，否则全部函数为 no-op 并记录禁用原因；host 由 `VITE_POSTHOG_HOST` 决定，默认 PostHog Cloud；会话录制固定关闭（`disable_session_recording: true`）。`main.tsx` 在启动时初始化；`RouteChangeTracker` 挂在 `BrowserRouter` 下发 page view；`AuthContext` 在登录/会话恢复后调 `identifyUser`，登出时调 `resetAnalytics`。升级漏斗事件由 `upgradeAnalytics.ts` 双写：后端既有队列 + PostHog。事件属性 may 含 id、路径、目标、动作名和非敏感元数据；never 含提示词文本、编辑器内容、上传文件内容或任意用户生成文本。

来源：3ef311e（初始提交；设计稿 `docs/plans/2026-03-28-posthog-frontend-analytics-design.md`，已落地）

## Alternatives considered

- **直接在组件里调用 `posthog-js`**。最强理由：最快接通，少一层抽象。被否：供应商锁定和隐私边界无处统一执行；设计稿以"减少业务代码 churn"明确否掉。
- **经后端中继/反向代理而非直连 Cloud**。最强理由：隐藏 key、不受广告拦截影响。被否：作为二阶段选项保留；包装层的存在正是为了让这次迁移只改一个文件。

## Consequences

- 收益：隐私边界只在一处执行；本地与 CI 默认无 key 即无网络请求。
- 代价：包装层要随 PostHog 能力增长而扩接口；绕过 `analytics.ts` 直接 import `posthog-js` 的代码属违规，目前没有 lint 规则拦截，只能靠 review。

## Verification

`grep -rln "posthog-js" apps/web/src` 只应命中 `lib/analytics.ts` 及其测试；`apps/web/.env.example` 列出三项 `VITE_POSTHOG_*`。
