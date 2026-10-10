# Agent Note: 官网进应用的链接带上语言与来源，不再经过应用落地页

Status: implemented

## Problem

2026-10-09 首页走查发现，从 zenstory.ai 点“Open app / 打开工作台”等入口，链接都是裸的 `https://app.zenstory.ai`：

- 未登录访客先落到应用自己的营销首页（hero、演示、定价），看完才到注册；已在官网被说服的人要再被说服一次，两页视觉也完全不同。
- 应用语言只读 localStorage，默认中文（`src/lib/i18n.ts`），英文官网来的访客看到中文页面；注册请求不带语言，服务端默认 `zh`，英文用户收到中文验证邮件。
- 官网静态页没有统计脚本，链接也不带来源，官网到注册的转化无法测量；应用的 PostHog 会保留 URL 里的 `source`，但 `register_success` 不带来源，访客从 `/login` 换到 `/register` 或经 Google 往返后 URL 里也不再有它。

## Decision

- **官网链接**：`site-shell.mjs` 新增 `appHref(lang, path, source)`，生成 `https://app.zenstory.ai<path>?lang=<en|zh>&source=org_<slot>`。“打开应用”类入口（页头 `org_header`、页脚 `org_footer`、/projects 工作台卡 `org_projects`、/workbench 页首 `org_workbench`、“你需要什么”里的 `org_workbench_need`、工作台文档说明 `org_docs`）指向 `/login`；开始写作类入口（首页收尾 `org_home_closing`、Oh Story 页“或在浏览器里” `org_oh_story_need`）指向 `/register`。`PublicRoute` 已会把已登录用户从这两页送到工作台，所以老用户不受影响。正文 markdown 里的应用链接（文档的注册、登录、找回密码说明）不变。
- **应用语言**：i18next 检测顺序改为 `querystring`（`lang`）→ `localStorage`，结果照旧缓存到 `zenstory-language`；不支持的值（如 `fr`）被忽略，回落到已存偏好，再回落到中文。仍不猜浏览器语言。
- **注册语言**：`AuthContext.register` 把 `getLocale()` 作为 `language` 发给 `/api/auth/register`，服务端已有该字段并据此发验证邮件（支持 `zh`/`en`），无需改服务端。
- **入口来源**：新增 `src/lib/entrySource.ts`，应用启动时把落地 URL 的 `source`（仅 `[a-z0-9_]{1,64}`）记进 sessionStorage，同一标签页只记第一个；密码注册与 Google 新用户的 `register_success` 都带 `entry_source`。存储不可用时静默跳过。

## Alternatives considered

- **只在本地没有语言偏好时才采用 `?lang=`**：最强理由是尊重用户在应用里手动选过的语言。没有采用：i18next 首次加载就会把默认的中文写进 localStorage，曾被错误落到中文页的英文访客会一直保持中文；官网语言切换本身就是明确选择，且应用内可随时切回。
- **所有入口都指向 `/register`**：最强理由是新访客少一次点击。没有采用：页头、页脚的“打开应用”也服务已注册但未登录的老用户，送到注册页会让他们找“登录”；登录页本身有注册入口和 Google 登录。
- **在官网加统计脚本**：最强理由是能看到官网页面级流量与点击。没有采用：官网约定保持无应用 JavaScript、无第三方脚本（`site-shell.mjs` 注释），而应用侧已有 PostHog，只差来源参数；本次只让链接带上 `source`。
- **把来源写进注册请求、存到用户表**：最强理由是不依赖 PostHog 是否开启也能按来源统计注册。没有采用：需要服务端 schema 与接口改动，超出本批；事件属性已足够回答“哪个入口带来注册”。

## Consequences

- 英文官网进来的访客、注册页和验证邮件都是英文；官网各入口带来的登录页/注册页访问和注册可以按 `source` 区分（前提是生产环境开启了 PostHog，仓库里无法确认）。
- 官网不再把访客送到应用落地页；从官网卡片选作品类型（短篇、短剧）的偏好只在应用落地页存在，官网直达注册的访客默认长篇，与官网“写小说”入口一致。
- 一个曾在应用里选了中文的用户如果从英文官网点进来，应用会切到英文并记住，需要在应用里切回。
- `geo-domain-smoke.spec.ts` 中首页与文档页头的应用链接断言改为新地址；官网 HTML 中 `&` 以 `&amp;` 输出。

## Verification

- vitest：`i18n.languageHandoff.test.ts`（`?lang=en` 打开英文并记住、覆盖已存中文、忽略 `fr`、无参数默认中文；把检测顺序改回仅 localStorage 时 2 例失败）、`entrySource.test.ts`、`AuthContext.test.tsx`（注册带 `language`，`register_success` 带 `entry_source`）。
- `node --test scripts/__tests__/*.test.mjs` 56 例通过，首页断言页头去 `/login`、收尾去 `/register`，且不再出现裸应用根链接。
