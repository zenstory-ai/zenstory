# Agent Note: 发版后提示刷新，旧 chunk 无法自动恢复时明确要求刷新

延续 [退出前草稿恢复](../bug-fix/2026-10-07-editor-draft-recovery-on-chunk-reload.md)、[chunk recovery 不把合法离页取消当成旧 chunk](../bug-fix/2026-10-17-chunk-recovery-navigation-cancellation.md) 与 [前端 ErrorBoundary](../bug-fix/2026-10-05-frontend-error-boundaries.md)。

Status: implemented

## Problem

生产每天发版 2–7 次。发版前打开的标签页继续运行旧代码，懒加载到已被新部署替换的 chunk 时失败。PostHog 近 30 天（2026-09-21 至 10-20）：

- 9/26–10/05 的 `Cannot read properties of undefined (reading 'default')`（11 次 / 8 人）是用户反映的发版白屏，#127 修复后不再出现。
- 10/06 之后约 13 次动态 import 失败，多数在 1–3 秒内被既有一次性自动刷新恢复。未恢复的两类：自动刷新被草稿保护取消（10/20 项目页对话面板 `markdown-vendor` 加载失败，面板停在「出了点问题」）；一次刷新用完后仍失败（10/10 Firefox，整页兜底）。
- 面板兜底的「重试」只重新渲染，React.lazy 复用失败的 import，旧 chunk 错误永远重试失败。
- Chrome 对返回 HTML 的缺失 chunk 报 `'text/html' is not a valid JavaScript MIME type`，不在已知文案里，非权威来源不会识别。

## Decision

- 构建时 `vite.config.ts` 的 `appVersionManifest` 在 `dist/version.json` 写入 `{"entry": "/assets/index-<hash>.js"}`，取自构建后的 `index.html` 入口脚本。入口文件名按内容哈希，任一 chunk 变化都会改变它，因此不需要额外版本号。Vercel 先查静态文件再走 rewrite，该文件在各 host 根路径直接可读。
- `lib/appUpdate.ts`：页面可见时每 30 分钟（2026-10-29 起，原为 10 分钟，见 [减少 CDN 请求](../architecture/2026-10-29-cdn-request-caching.md)）、切回标签页或恢复联网时（间隔至少 1 分钟）以 `cache: "no-store"` 读取 `version.json`，与本页 `script[type=module][src]` 比较。不同即标记可更新；请求失败、格式不符或开发环境（无入口脚本）保持静默。用户点「稍后」后只在出现更新的构建时再提示。
- `components/AppUpdatePrompt.tsx`：非模态的右下角卡片（手机底部，避开底部导航），polite 播报，「刷新」走普通 `location.reload()`，因此仍经过既有 `beforeunload` 草稿快照与失败时的原生确认。可更新状态下，项目编辑页之外的下一次路由切换直接整页加载目标路由，使标签页不再向服务器请求旧构建的 chunk；进入或停留在 `/project/` 时从不自动刷新，由作者决定时机。
- `chunkRecovery`：草稿保护取消自动刷新、或一次性刷新已用完仍失败时，调用 `markAppUpdateRequired()`，卡片改为不可关闭的「需要刷新页面才能继续加载，建议先保存」。既有一次性 guard、草稿同步捕获、导航取消抑制不变。识别文案增加 Chrome 的 MIME 错误。
- `ErrorBoundary` 遇到旧 chunk 错误时兜底文案改为「ZenStory 已更新」，只给「重新加载页面」，不再显示无效的「重试」。

## Alternatives considered

- **Vercel Skew Protection（旧标签页继续拿到旧部署的文件）。** 最强理由：从根上消除旧 chunk 404，用户无感。未采用：需要确认套餐与 Vite 项目的部署 ID 注入方式，且保留期内旧代码继续调用新 API，契约变化时仍需刷新；作为后续可评估项，不替代本提示。
- **检测到新版本立即自动刷新。** 最强理由：最快回到新代码。被否：会打断正在写作或等待 AI 生成的作者，编辑器草稿捕获失败时还会弹出原生离页确认。
- **只靠既有 chunk 失败后自动刷新。** 最强理由：已覆盖多数情况，无新增请求。被否：刷新被草稿保护取消或 guard 用完时用户只看到通用错误；而且只有在失败之后才处理，提示能在失败前完成切换。
- **用 Service Worker 管理版本。** 最强理由：浏览器原生的更新与缓存控制。被否：引入新的缓存层和生命周期复杂度，现有部署没有 SW，失败模式更难排查。

## Consequences

- 收益：发版后长时间打开的标签页在下一次切换页面时静默换到新代码，编辑器内由作者选择刷新时机；旧 chunk 无法自动恢复时有明确的刷新入口，面板不再给出无效重试。
- 代价：每个可见标签页每 30 分钟一次很小的静态请求；`version.json` 依赖构建产物 `index.html` 只有一个模块入口脚本（插件找不到时构建失败）。离开项目页到其他页面的切换不自动刷新，长期停留在编辑器的作者只看到提示。
