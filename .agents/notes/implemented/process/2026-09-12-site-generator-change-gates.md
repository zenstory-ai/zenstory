# Agent Note: 静态站点生成器的改动门禁：共享壳、路由契约同步、GEO 基线 diff

Status: implemented

## Problem

zenstory.ai 的组织页面和文档页由三个生成器脚本产出，服务于非 JS 爬虫和答案引擎。这类页面最容易被"顺手"改坏：一个脚本改了 head 而另一个没改，新增路由只在生成器里出现却没进 Vercel 重写规则，或者一次视觉重构悄悄丢掉一条 JSON-LD 节点。这些回归在浏览器里看不出来，只会在几周后的索引结果里显现。

## Decision

- 生成器 `build-org-pages.mjs` 与 `build-docs-pages.mjs` must 从 `apps/web/scripts/site-shell.mjs` 取 head 元数据、header、footer 和语言助手；样式只用 `org-pages.css` 这一份纯 CSS；never 引入新依赖或测试不会复制的新文件。
- 新增路由 must 同批更新四处：`apps/web/content/site-routing.json` 的 `sitePrefixes`、由 `node scripts/build-site-layout.mjs --write-config` 重新生成的 `vercel.json`（构建会对比配置漂移并拒绝）、`public/llms.txt`、以及生成器测试里的 sitemap 逐条出现断言。
- 每次改动 must 通过：`pnpm test:site` 生成器测试（断言精确的标签、id、class、JSON-LD 顺序）、对基线构建的 GEO diff（元数据、JSON-LD、链接、文本 token 必须全部存活，只放行显式允许的字段）、390px 溢出检查、axe。
- 内容 JSON 与 markdown never 为了排版被改写："style and wrap, never edit asserted nodes"。

来源：11580cc (#32)、2685721 (#58)、cab5f9f (#59)

## Alternatives considered

- **各生成器自带 head/header/footer**（9d95bef 时期组织页与文档预渲染各写一套）。最强理由：脚本彼此独立，改一个不影响另一个。被否：语言切换和 hreflang 一上来就要在两处各写一遍，cab5f9f 因两处不一致把它们合并进 `site-shell.mjs`。

## Consequences

- 收益：任一路由或元数据的遗漏在 CI 就红；GEO 信号变化被限制在允许清单内（目前只有 `og:image`，见 [og-image](../feature/2026-09-13-og-image-1200x630-png.md)）。
- 代价：加一条路由要碰四个文件；做视觉改动前要先产出基线构建。
- 已知缺口：GEO diff 不以脚本形式存在于仓库，它是 DESIGN.md 记录并在 PR 说明里报告结果的人工步骤；若再有一次重设计，应先把它脚本化。

## Verification

`cd apps/web && pnpm test:site && pnpm build:vercel`：前者跑 `scripts/__tests__/*.test.mjs`，后者在 `vercel.json` 与 `site-routing.json` 派生结果不一致时中止。
