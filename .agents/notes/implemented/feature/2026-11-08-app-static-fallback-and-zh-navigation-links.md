# Agent Note: App 静态 HTML 兜底正文与组织站中文指南显式入口

Status: implemented

## Problem

`app.zenstory.ai` 的构建产物 `_app/home.html` 与 `_app/pricing.html` 此前仅包含空的 `<div id="root"></div>`，在不执行 JavaScript 的抓取器或预览抓取中只能看到 `<title>` 与 meta 描述，缺少页面主标题、核心功能说明和方案对比正文。同时，`zenstory.ai/zh` 及各项目主页到高频中文聚合页（`/zh/faq`、`/zh/compare`、`/zh/glossary`、`/zh/workbench`、`/zh/<owner>/guides`）缺少首屏可见的静态锚点，中文深层指南页的内部链接深度偏高。

## Decision

1. `apps/web/scripts/build-site-layout.mjs` 在生成 `_app/home.html` 与 `_app/pricing.html` 时，直接从 `apps/web/public/locales/zh/home.json` 和 `apps/web/public/locales/zh/dashboard.json`（`planComparison`）读取线上真实文案，注入 `<div id="root" data-static-app-fallback="true"><main>...</main></div>` 静态兜底正文（含唯一 `<h1>`、核心工作流段落、Free/Pro 方案对比与公开导航链接）。浏览器加载 JavaScript 后，React `createRoot(rootEl).render(...)` 原地替换 `#root` 子节点并按客户端语言正常渲染。
2. 在组织站首页与项目页静态布局中补齐高频中文指南聚合入口（`/zh/faq`、`/zh/compare`、`/zh/glossary`、`/zh/workbench` 及各项目 `/zh/<owner>/guides`），缩短中文页面抓取路径。
3. 实质修订过正文、语义边界或直接源码出处的 `articles.json` 与 `guides.json` 条目同步更新 `updated_on` / `checked_on` 为 `2026-11-08`，由 `finalizeSite` 自动写入 `sitemap.xml` 对应 URL 的 `<lastmod>`。

## Alternatives considered

- 在 `build-site-layout.mjs` 内硬编码一套独立的 App 营销文案：实现简单，但容易与 `public/locales/zh/*.json` 中的真实产品文案、额度说明（免费版每日 3 条 AI 消息）或功能边界脱节，舍弃。
- 对 `app.zenstory.ai` 启用完整服务端 SSR：需要改动现有 Vite 静态托管与 Vercel 路由架构，超出本次静态入口可索引性修复范围，舍弃。

## Consequences

- 无 JS 抓取器访问 `_app/home.html` 与 `_app/pricing.html` 时可直接读取完整 `<h1>`、产品工作流与方案对比；有 JS 时中文与英文客户端渲染不受影响（已通过 6/6 浏览器有/无 JS 及中英文 smoke 验证）。
- `site-layout.test.mjs` 的临时构建 fixture 需同步拷贝 `public/locales/zh/home.json` 与 `public/locales/zh/dashboard.json`，确保测试隔离环境与真实构建读取同一套文案源。
