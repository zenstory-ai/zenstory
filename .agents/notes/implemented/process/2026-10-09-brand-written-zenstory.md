# Agent Note: 品牌名在所有用户可见文字里写作「ZenStory」

Status: implemented

## Problem

同一个产品在不同地方写法不一：组织站生成器、`index.html`、README 标题和 `ZenStory AI` 组织名用「ZenStory」，而应用界面（`appName`、登录页标题、邀请分享文案、设置页 Agent 接入说明）、页面标题（`seo-config.ts` 的「登录 - zenstory」等）、邮件（标题、正文、发件人显示名、页眉字样）、服务端提示（「如果这个邮箱注册过 zenstory」）、`/skill.md`、CLI 提示、帮助文档和隐私政策/服务条款都写成小写「zenstory」。顶栏的 `Logo` 字标是 Arial Black 14px 转成的 SVG 路径，字形也是小写「zenstory」。车主 2026-10-08 决定：品牌在所有用户可见的文字里统一写作「ZenStory」。

## Decision

- 用户可见文字里指代产品或公司的「zenstory / Zenstory / ZENSTORY」一律写作「ZenStory」，只改大小写，不改句子结构、空格和标点。范围：`apps/web/public/locales/**`（含 privacy.json 里的隐私政策和服务条款）、`apps/web/src` 里 `t()` 的 fallback 和写死的界面文字（`seo-config.ts` 的标题/描述/关键词、`SEOProvider.tsx`、`AgentApiSection.tsx`、`ForgotPassword.tsx`）、`apps/server` 的邮件模板（`services/infra/email_client.py`：标题即邮件主题、正文、页眉字样、发件人显示名 `ZenStory <…>`）、`core/messages.py` 的用户提示、`/skill.md`（`services/skill_md_service.py` 的 description、display_name 和标题）、技能导入的警告文案、CLI 的帮助与报错文字（`apps/cli/src`）、CLI README 与随包技能文档的正文、`apps/web/docs/**` 与 `docs/` 下的用户文档、`docs/docker-compose.md`。
- 顶栏字标 `ZENSTORY_WORDMARK_PATH` 用同一字体同一字号（Arial Black 14px，基线 y=22，fontTools 转路径）重新生成为「ZenStory」，宽度从 65.3 变为 69.2，仍在 `viewBox="0 0 120 32"` 内（平移 36 后止于约 105）。
- 正文提到命令行工具时写「ZenStory 命令行工具（`zenstory`）」/「the ZenStory CLI (`zenstory`)」；界面文案里命令就在旁边展示，只写「ZenStory 命令行工具」/「ZenStory CLI」。代码块和命令（`npx zenstory login`、`zenstory files put …`）保持原样。
- 不改的技术标识：域名与 URL（`zenstory.ai`、`app.zenstory.ai`、`api.zenstory.ai`）、邮箱（`support@zenstory.ai`）、GitHub 组织和仓库名（`zenstory-ai`、`zenstory-ai/zenstory`，README 「ZenStory AI 项目」表格里的链接文字是仓库名，也不改）、npm 包名与命令 `zenstory`、技能 frontmatter `name: zenstory` 与 `metadata.cli`、`metadata.zenstory.*` 键、localStorage / sessionStorage 键与事件名（`zenstory:*`、`zenstory-language` 等）、环境变量、`APP_NAME` 默认值 `zenstory API`（同时是日志 `service` 字段）、文件路径、测试 id、JSON 键、Docker 用户名与数据库名。「ZenStory AI」组织名不动。
- 不改的非用户文字：代码注释与 docstring（只有 `Logo.tsx` 和 `structured-data.ts` 里描述输出值的注释随之更新）、`.env*.example` / Dockerfile / compose / CI 脚本注释、`apps/web/e2e/README.md` 等开发者文档、`docs/advanced/` 下的内部设计与治理文档、`docs/qa`、`docs/prd-*`，以及 `.agents/notes` 里的历史记录。品牌图片资源（`public/brand/*.svg` 等）不改。
- 断言旧写法的测试随之更新：`SEOProvider*.test.tsx`、`SEOHelmet.hydration.test.tsx`、`ForgotPassword.test.tsx`、`InviteCodeCard.test.tsx`、`responsive-pages-mocked.spec.ts`、`site-layout.test.mjs`（服务条款正文）、`test_password_reset.py`（邮件主题）、`test_skill_md_service.py`（标题与 description）、CLI `skill.test.ts`；`org-pages.test.mjs` 对文档页 head 的「不得出现 zenstory 文档 / zenstory帮助文档」改为不区分大小写，保持原意。

## Alternatives considered

- **只改界面和营销文案，文档与 CLI 保留小写**。最强理由：改动面小，CLI 和技能文档的读者多是开发者和 Agent，小写和命令名一致，不易混淆。被否：帮助文档和 CLI 提示同样是用户读到的品牌名，车主要求所有用户可见文字统一；命令名混淆的问题用「ZenStory 命令行工具（`zenstory`）」的写法解决。
- **连命令名、包名、存储键一起改成 ZenStory**。最强理由：彻底统一，不再有两种写法。被否：npm 包名必须小写；改存储键会丢掉已登录用户的本地设置和草稿恢复；改域名、仓库名、环境变量是部署层面的迁移，不属于文案改动。
- **字标保留小写路径，只改文字**。最强理由：字标是视觉资产，改形状需要设计确认。被否：车主把顶栏字标文字明确列入范围；用原字体原字号重生成，只有两个字母变成大写，字重和基线不变。

## Consequences

- 收益：应用、邮件、文档、CLI、组织站和 README 的品牌写法一致；组织站页面本来就是 ZenStory，生成结果 0 处变化。
- GEO：对比改动前后的 `build-org-pages.mjs` + `build-docs-pages.mjs` 输出（976 个文件），只有 10 个文档页变化，差异全部是 `zenstory` → `ZenStory` 的大小写（20 处 head 里的 description / og:description，67 处正文），`<title>`、JSON-LD、链接都没有变化。
- 代价：已发出的邮件、已安装的旧版技能和 CLI 仍显示小写，直到用户更新；字标路径变宽约 4 个单位，但 `viewBox` 不变，`Logo` 的渲染尺寸不变，只是字标在框内占得更满；`APP_NAME` 默认值仍是 `zenstory API`，OpenAPI 文档页标题和根路径欢迎语里会出现一次小写（欢迎语后半句已改为 ZenStory）。
- 默认关闭的视觉回归（`E2E_ENABLE_VISUAL_REGRESSION_E2E=true` 的 `e2e/visual.spec.ts`）基线截图里如果有顶栏字标，下次开启时需要重新生成。
- 后续新增用户可见文字沿用「ZenStory」；提到命令时用反引号写 `zenstory`。
