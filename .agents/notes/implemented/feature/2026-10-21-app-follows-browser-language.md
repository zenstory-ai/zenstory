# Agent Note: 应用在没有语言偏好时跟随浏览器语言（爬虫除外）

Status: implemented

改变 [org-site-app-handoff](2026-10-09-org-site-app-handoff.md) 里「仍不猜浏览器语言」一条；`?lang=` 交接与 localStorage 偏好的优先级不变。

## Problem

2026-10-21 英文新用户诊断（生产，en-US 浏览器，两个全新会话都复现）：

- 直接打开 `app.zenstory.ai`、`/login`，整页都是中文，`<html lang="zh-CN">`、标签标题、OG 也是中文。i18next 还会把回落的 `zh` 写进 `zenstory-language`，这个访客之后一直是中文。
- 只有从 zenstory.ai 带 `?lang=en` 进来的人能看到英文。搜索结果、书签、分享链接、换设备都不行。
- 在落地页切到英文后，标签标题仍是中文。原因是 `SEOProvider` 对可索引路由沿用静态壳的 `document.title`，而静态壳永远是中文。
- `/docs` 页标题后缀固定写成「ZenStory 文档」。

## Decision

- **检测顺序**（`apps/web/src/lib/i18n.ts` 的 `languageDetectionFor`）：
  - 人：`querystring`（`lang`）→ `localStorage` → `navigator`。
  - 爬虫：仍是 `querystring` → `localStorage`。按 UA 识别 `bot|crawl|spider|slurp|facebookexternalhit|inspectiontool`。
  - 浏览器语言不在支持列表（如 `fr`）时回落中文。结果照旧缓存到 `zenstory-language`。
- **爬虫不跟随浏览器语言**：Googlebot 以 en-US 渲染。如果跟随，应用首页和 `/pricing` 的索引会从中文变成英文，违背每个 URL 一种语言的索引策略。
- **静态壳只在语言一致时沿用**（`SEOProvider`）：模块加载时读一次服务端 HTML 的 `lang`。读者语言和它不同时，不沿用静态壳的标题、描述和 OG，改用该语言的路由配置。
- **`/docs` 标题后缀**按界面语言写「ZenStory 文档」或「ZenStory Docs」。
- **E2E 不受影响**：E2E 默认 `locale: 'zh-CN'`，浏览器语言就是中文，现有用例不变。

## Alternatives considered

- **只在 `/login`、`/register` 等不索引的路由上跟随浏览器语言**。最强理由：可索引的落地页对任何访客都完全不变，SEO 零风险。没有采用：英文访客最常从落地页进来，第一屏仍是中文，诊断里的流失点没有解决；按 UA 排除爬虫同样保住了索引语言。
- **服务端按 `Accept-Language` 302 到 `?lang=en`**。最强理由：首屏 HTML 就是对的语言，无脚本也成立。没有采用：应用是静态托管的 SPA，加重定向要改托管层并引入按请求头的缓存分歧；同一 URL 对爬虫和人返回不同跳转，也更难验证。
- **保持现状，只把语言切换按钮做得更显眼**。最强理由：不改检测逻辑。没有采用：诊断里的访客已经看到了地球图标，仍把整页中文当成「中国专用产品」，首屏语言本身就是问题。

## Consequences

- 收益：
  - 英文浏览器直达应用就是英文，并记住这个选择。
  - 中文浏览器、`?lang=` 交接、已有偏好的用户都不变。
  - 搜索引擎看到的语言不变。
- 代价：
  - 爬虫识别靠 UA 正则，不认识的爬虫会按它声明的浏览器语言渲染。
  - 第一次访问的英文用户仍会先看到一瞬间中文静态壳，直到脚本运行。静态 HTML 仍是中文，这是为了索引。

## Verification

`cd apps/web && pnpm exec vitest run src/lib/__tests__/i18n.languageHandoff.test.ts src/providers/__tests__/`：英文浏览器 → en 并缓存，中文浏览器 → zh，`fr` → zh，Googlebot → zh，已有偏好优先于浏览器语言；中文静态壳 + 英文读者时标题不沿用中文；文档后缀随语言。
