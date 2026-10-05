# Agent Note: 前端全局与面板级 ErrorBoundary，泛用来源的重载标记可过期清除

Status: implemented

## Problem

前端没有任何 React ErrorBoundary：`main.tsx` 只有 StrictMode > QueryClientProvider > Suspense > App，路由用声明式 `BrowserRouter`，没有 `errorElement`。React 19 遇到未捕获的渲染错误会卸载整棵树，用户看到白屏且没有重试入口；生产 PostHog 已记录到 commit 阶段的 `removeChild` / `insertBefore` NotFoundError。`chunkRecovery` 在重载保护用掉后会刻意抛出，也直接落到白屏。

另外，`chunkRecovery` 由泛用来源（`vite:preloadError`、`unhandledrejection`，例如悬停预加载失败）设置的 sessionStorage 重载标记永远不会被清除：`lazyRoute` 只清除与自己路由名相同的标记。同一分页之后再遇到一次部署就不会自动重载，直接白屏。

## Decision

- `components/ErrorBoundary.tsx`：类组件，`componentDidCatch` 记 `logger.error` 并 `captureException(error, { feature_area, boundary, component_stack })`（堆栈截断到 2000 字符）。`variant="page"` 显示整页兜底和「重新加载页面」；`variant="panel"`（默认）只替换所在面板，并多一个「重试」按钮（清除错误状态重新渲染）。兜底 UI 在 `components/ErrorFallback.tsx`，翻译用 `useTranslation("common", { useSuspense: false })` 且每个键带默认文案，键在 `common.json` 的 `errorBoundary.*`（zh/en）。传入 `className` 时用 `display: contents` 的容器包住，不影响布局。
- `main.tsx`：`<ErrorBoundary area="app" variant="page">` 包住 QueryClientProvider 及以下。
- `App.tsx` 的项目编辑页：`Layout` 的 `middle` 与 `right` 分别是 `<ErrorBoundary area="editor" className="ph-no-capture"><Editor /></ErrorBoundary>` 与 `<ErrorBoundary area="chat" className="ph-no-capture"><ChatPanel /></ErrorBoundary>`；只包外层，`ChatPanel.tsx` 不改。
- `chunkRecovery`：设置重载标记时同时写 `zenstory:chunk-reload-at`（时间戳）。任意 `lazyRoute` 成功导入后，若标记属于泛用来源且已存在至少 `GENERIC_GUARD_MIN_AGE_MS`（10 秒），连同时间戳一起清除；没有时间戳的旧标记视为过期。路由自己的标记仍按原规则在该路由成功导入后清除。

## Alternatives considered

- **改用 data router（`createBrowserRouter`）的 `errorElement`**。最强理由：路由级错误处理是 React Router 的标准做法，能按路由定制。被否：要重写整个路由树，改动面远超本次修复；它也接不住路由之外（Provider、Toast）的错误，仍需要根级 boundary。
- **只用 `createRoot` 的 `onUncaughtError` 上报**。最强理由：一行代码就能上报所有未捕获错误。被否：只能上报，不能阻止整树卸载，用户仍然白屏。
- **泛用标记在任何成功导入后立即清除**。最强理由：最简单，下一次部署一定能恢复。被否：如果某个页面每次加载都会触发同一个泛用预加载失败，重载后的首个成功导入立刻清掉标记，再次失败又重载，形成无限重载循环；10 秒门槛让「每次加载都失败」只重载一次。

## Consequences

- 收益：渲染错误不再整页白屏；编辑器或对话面板出错时另一侧仍可用并可单独重试；错误按 `feature_area` 进入 PostHog 错误追踪。悬停预加载触发过一次重载的分页，之后的部署仍能自动恢复。
- 代价：面板「重试」只重新挂载组件，不会重置面板依赖的上下文状态，若错误来自上下文数据仍会再次失败（此时可整页重载）。泛用标记在设置后 10 秒内的路由导入不会清除它，这段时间内再次遇到新部署不会自动重载。
