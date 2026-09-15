# Agent Note: 组织站点每个 URL 只承载一种语言（/zh 前缀 + hreflang）

Status: implemented

## Problem

zenstory.ai 的组织页面面向两类读者：从 AI 答案跳来的中文网文作者，和英文 Claude Code 用户。搜索引擎和答案引擎对每个 URL 只索引一个 title、一份 description、一段正文。提示面板里三分之二的查询是中文，而如果所有页面标题都是英文，中文查询词（网文写作 skill 包、去AI味、剪映草稿）永远匹配不到标题。

## Decision

每条组织路由生成两份静态页：英文在根路径，中文在 `/zh` 前缀下（`/zh` 是中文首页）。生成器 `apps/web/scripts/build-org-pages.mjs` 按语言各跑一次；`t(en, zh)`、`pick(field)`、`pair()`、`heading()` 只渲染当前语言，`<html lang data-lang>` 在构建期写死。每页 must 携带 en、zh-CN、x-default（指向英文）三条 hreflang、`og:locale` 及其 alternate、JSON-LD `inLanguage`；sitemap 在两个条目上各重复一次这组配对。中文页的站内链接 must 指向 `/zh/...`，并去掉逐元素的 `lang="zh-CN"`；英文页只在纯中文片段（术语表词条）上保留它。语言切换是指向对应页面的普通链接，never 依赖脚本或 `localStorage`。英文 URL 保持稳定。工作台文档 `/docs` 是唯一例外，见 [workbench-docs-on-org-shell](2026-09-14-workbench-docs-on-org-shell.md)。

来源：cab5f9f (#59)

## Alternatives considered

- **单 URL 双语 + 客户端切换**（2685721 采用的模型：两种语言都在初始 HTML 里，英文在前，行内脚本按存储偏好或 `navigator.language` 设 `data-lang`，样式只显示一种）。最强理由：URL 集合不变、无 JS 也能读到全部内容、不需要翻倍生成。被否：引擎对该 URL 只索引一个标题，而标题必然是英文；中文查询词匹配不到，双语正文还稀释了每种语言的信号。该模型上线次日即被替换。

## Consequences

- 收益：每个 URL 的 title/description/JSON-LD 与其语言一致，中文页标题能直接命中中文查询词；页面无脚本即完整。
- 代价：页面数量翻倍，生成器测试要断言"无跨语言泄漏"和"中文页内链只指向 /zh"；新增组织路由 must 同时进 `sitePrefixes`（含 `zh`）并通过 [site-generator-change-gates](../process/2026-09-12-site-generator-change-gates.md) 的全部门禁。
- 已知上限：内容 JSON 里任何缺少中文字段的条目会在 `/zh` 页上露出英文，只能靠生成器测试兜底。

## Verification

`cd apps/web && pnpm test:site` 中 `org-pages.test.mjs` 断言按语言分页、hreflang 配对、无跨语言泄漏和 zh 内链；`pnpm test:geo` 的 `geo-domain-smoke.spec.ts` 覆盖线上 `/zh`。
