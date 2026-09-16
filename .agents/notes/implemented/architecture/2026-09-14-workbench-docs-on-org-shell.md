# Agent Note: 工作台文档渲染在静态组织壳上，而非 React 应用壳

Status: implemented

## Problem

`/docs` 下 24 篇工作台文档是 zenstory.ai 上最长的文本，也是非 JS 爬虫（GPTBot、ClaudeBot）最需要完整读到的内容。把它们挂在 React 应用壳上意味着每页都要带应用 bundle，文档间相对 markdown 链接在运行时解析，线上曾有 22 条 `.md` 链接 404，而构建并不会报错。

## Decision

`apps/web/scripts/build-docs-pages.mjs` 把 `/docs` 和每篇叶子页渲染成纯静态 HTML，与组织页面共用 `site-shell.mjs` 的 head、header、footer，不带应用 bundle。侧栏由 `src/data/docsNavigation.ts` 生成；分区链接打开该区第一页。相对 markdown 链接在构建期解析为站点路由，指向不存在页面的链接 must 让构建失败。文档页是 [one-language-per-url](2026-09-14-one-language-per-url.md) 的唯一例外：一个 URL 承载中文文章在前、英文文章在后，各带锚点，语言切换指向锚点。"Docs" never 出现在站点主导航，只从 `/workbench` 项目页和页脚进入。React `DocsPage` 保留给开发构建；应用内文档搜索在这些静态页上不可用。

来源：cab5f9f (#59)

## Alternatives considered

- **在 SPA 壳上预渲染 `#root`**（9d95bef 的做法：构建期把渲染好的 markdown 塞进 SPA 壳，React 在同一 URL 上挂载）。最强理由：保留应用内搜索和路由，爬虫也能读到正文。被否：每个文档页仍要加载应用 bundle，链接解析和 404 检查留在运行时，无法在构建期拦下死链，且样式与组织页面不一致。
- **只靠 SEOProvider/Helmet 给 JS 爬虫补元数据**（a08e68f 的做法）。最强理由：零构建改动。被否：非 JS 爬虫只拿到没有正文的壳。

## Consequences

- 收益：文档页首字节就是完整正文；死链在构建期暴露而不是线上 404；一套壳、一套 CSS。
- 代价：应用内文档搜索在 `/docs` 上不可用；`docsNavigation.ts` 与 markdown 目录 must 保持同步，否则侧栏漏页。
- 待定：应用内的文档入口是否该带语言提示链接到 `/docs`（DESIGN.md 开放问题）。

## Verification

`cd apps/web && pnpm build` 在任一 markdown 链接指向缺失页面时失败；`pnpm test:site` 覆盖文档页生成；`pnpm test:geo` 烟测覆盖线上静态 docs。
