# Agent Note: 静态文件缓存头与 README logo，减少 Vercel CDN 请求

关联：[发版后提示刷新](../feature/2026-10-21-app-update-prompt.md)（轮询间隔由本条调整）、[README masthead](../process/2026-09-16-readme-masthead.md)（logo 地址由本条调整）。

Status: implemented

## Problem

近 30 天 Vercel CDN 请求接近套餐额度，日均从 10 月初约 1.3 万涨到约 3 万。ergou-ai 项目（zenstory.ai 与 app.zenstory.ai）占 86%（678K）。当前套餐的 Observability 只保留 12 小时，2026-10-29 生产环境的 12 小时样本约 8.7K 次请求：

- `/assets/*` 哈希 chunk 约 25% 以上，`/locales/zh/*.json` 20 个文件约 20%。所有静态文件都以 Vercel 默认的 `public, max-age=0, must-revalidate` 返回，浏览器每次加载都重新请求；实测同一浏览器第二次打开 `/login` 仍发出 55 个 Vercel 请求（15 个 assets + 40 个 locales，英文用户另加载中文回退）。
- `/brand/zenstory-ai-mark.svg` 约 16%（1.4K，而每次组织站页面浏览一次的 `org.css` 只有 302）：六个公开仓库的 README masthead 直接引用 zenstory.ai 上的这个文件，GitHub 图片代理在 README 浏览时回源。
- `/version.json` 约 6%：发版提示每 10 分钟轮询一次。
- 爬虫约 13%，无单一主导；Preview 环境 12 小时仅 9 次；生产 API 已经直连 `api.zenstory.ai`，不经 Vercel rewrite。

## Decision

- `scripts/build-site-layout.mjs` 生成的 `vercel.json` 增加缓存头：`/assets/(.*)` 为 `public, max-age=31536000, immutable`（Vite 按内容哈希命名）；`/locales/(.*)` 只在带 `v` 查询参数时同样 immutable；`/brand/*`、`/org/*`、`/favicon.svg` 为 `public, max-age=3600, stale-while-revalidate=86400`（文件名不带哈希，可能原地修改）。入口 HTML、`version.json`、robots、sitemap 保持默认重新验证。`site-layout.test.mjs` 断言只有带内容哈希或版本参数的 URL 得到 immutable。
- `vite.config.ts` 构建时对 `public/locales` 全部文件内容算 SHA-256（取 12 位），通过 `define` 注入 `import.meta.env.VITE_LOCALES_VERSION`；`src/lib/i18n.ts` 用 i18next-http-backend 的 `queryStringParams` 以 `?v=` 发送。只有翻译文件变化时版本才变，代码发版不会让翻译缓存失效。文件位置、命名空间、预加载和中文回退都不变。
- 发版提示轮询从每 10 分钟改为每 30 分钟；切回标签页、恢复联网时的检查（至少间隔 1 分钟）不变。
- 六个仓库的 README masthead 改为引用 `https://raw.githubusercontent.com/zenstory-ai/zenstory/main/apps/web/public/brand/zenstory-ai-mark.svg`（返回 `image/svg+xml`），由 GitHub 提供，不再计入 Vercel。仍是单一源文件。oh-story-dsh 中 `packages/knowledge/` 的 README 副本受 manifest 哈希约束，随下次上游同步更新。

## Alternatives considered

- **把语言包打进按语言拆分的 JS chunk。** 最强理由：首次访问也从 20 个请求降到 1 个，比版本参数省得更多。暂未采用：改变加载时机（Suspense）、英文缺键时的中文回退方式，且 `build-site-layout.mjs` 与后端 `material_utils.py` 依赖 `public/locales` 原文件，需要单独一批回归。版本参数先拿到回访用户的收益，无行为变化。
- **`/org/*`、`/brand/*` 也设 immutable。** 最强理由：再省约 3–4%。被否：文件名不带哈希，修改 `org.css` 或 logo 后回访用户会长期看到旧样式；给组织站资源加哈希会改动 GEO 基线比对的 HTML。
- **升级托管套餐。** 最强理由：不改代码立即解除限制。不在本批：属于费用决策；且大部分请求是可缓存而未缓存的重复请求，升级不解决浪费。
- **限制或屏蔽爬虫。** 最强理由：约 13% 的请求。被否：robots 政策明确欢迎所有爬虫，且无单一主导者；若某个爬虫增长，再在 Vercel Firewall 限速。

## Consequences

- 收益：按 12 小时样本估算 ergou-ai 请求减少约 50–60%（README logo 约 16%，assets 与 locales 回访命中浏览器缓存，version.json 约 4%），团队用量回到额度一半左右。实际比例取决于回访占比，需上线后在 Vercel Usage 复核。
- 代价：immutable 也可能作用于缺失 chunk 的 404；若生产回滚到旧部署，曾在发版窗口拿到该 404 的浏览器需强制刷新。发版提示最晚 30 分钟才出现（切回标签页时仍立即检查），期间旧 chunk 失败仍由 `chunkRecovery` 兜底。品牌文件修改后最多约 1 小时（之后后台刷新）才对回访用户生效。README logo 依赖 zenstory 仓库公开且该路径不移动。
