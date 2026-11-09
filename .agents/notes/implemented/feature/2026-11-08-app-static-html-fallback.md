# Agent Note: App 静态 HTML 兜底正文与创作者指南内容修订

Status: implemented

## Problem

`app.zenstory.ai` 的构建产物 `_app/home.html` 与 `_app/pricing.html` 此前仅包含空的 `<div id="root"></div>`，且 `<meta name="description">` 直接复用 `<title>`，在不执行 JavaScript 的抓取器或链接预览中缺少 `<h1>` 主标题、核心工作流说明和方案对比正文。

## Decision

1. `apps/web/scripts/build-site-layout.mjs` 在生成 `_app/home.html` 与 `_app/pricing.html` 时，直接从 `apps/web/public/locales/zh/home.json`（`homeContentZh`）和 `apps/web/public/locales/zh/dashboard.json`（`dashboardContentZh.billing`）读取线上真实中文文案，写入页面专属 description 并向 `<div id="root">${body}</div>` 注入 `<main>...</main>` 静态兜底正文（含唯一 `<h1>`、核心工作流与项目类型说明、免费/Pro 方案摘要及公开入口链接）。浏览器加载 JavaScript 后，React 挂载 `#root` 并按客户端语言渲染完整应用。
2. 实质修订过正文、语义边界或直接源码出处的 `apps/web/content/articles.json` 与 `apps/web/content/guides.json` 条目同步更新 `updated_on` / `checked_on` 为 `2026-11-08`，由 `finalizeSite` 自动写入官网 `apps/web/dist/_site/sitemap.xml`（共 991 个 URL，其中 874 条带 `<lastmod>`；另 `apps/web/dist/_app/sitemap.xml` 含 2 个 App 入口 URL，合计 993 个 URL）。

## Alternatives considered

- 在 `build-site-layout.mjs` 内硬编码一套独立的 App 静态文案：实现简单，但容易与 `public/locales/zh/*.json` 中的真实产品文案、额度说明（免费版每日 3 条 AI 消息）或功能边界脱节，舍弃。
- 对 `app.zenstory.ai` 启用完整服务端 SSR：需要改动现有 Vite 静态托管与 Vercel 路由架构，超出本次静态入口可索引性修复范围，舍弃。

## Consequences

- 无 JS 抓取器访问 `_app/home.html` 与 `_app/pricing.html` 时可直接读取完整 `<h1>`、产品工作流与方案对比；有 JS 时中文与英文客户端渲染不受影响（已通过 6/6 浏览器有/无 JS 及中英文 smoke 验证）。
- `apps/web/scripts/__tests__/site-layout.test.mjs` 的临时构建 fixture 需同步拷贝 `public/locales/zh/home.json` 与 `public/locales/zh/dashboard.json`，确保测试隔离环境与真实构建读取同一套文案源。
