# 首页减法：关系、工具与教程各说一次

Status: implemented locally for owner review; no push, merge or deployment.

## Problem

用户认可当前视觉，但指出“改编依据与源码”及“从你手头的素材开始”三列步骤过于繁杂；步骤导航、项目关系和工具选择重复解释同一批项目。本次承接[上一轮细节决策](../../implemented/feature/2026-11-01-home-details-browser-language.md)，只简化首页，不改变已发布教程、案例媒体或语言入口。

## Decision

### Cleanup plan

1. 删除首屏 evidence disclosure 及专用样式；原著标题直接链接固定原文，原画标注直接链接固定原图，现有案例说明链接固定案例目录。不再展开原文、设定集和 QA 资料；完整资料仍在案例目录，不用隐藏 HTML 保留多余链接。
2. 删除整块三列素材路径，包括仅为它服务的 JSON paths、渲染分支、工具起步辅助函数和 CSS。文章及 `/guides` 完整工作流不删；知识库继续链接完整流程和各层教程。
3. 阅读顺序收敛为：首屏 → 作品 → 从写作到改编（直接关系图）→ 选择创作工具 → 教程。关系图解释项目关系，不再带重复 CTA；安装与开始使用只在工具区处理。首屏主行动直接到 `#choose-h`，次行动到作品。
4. 保留工具区四套创作工具与两种工作环境、现有安装/项目/源码入口。浏览器工作台行保留介绍链接，原来重复介绍链接的位置改为唯一明确的“打开工作台”注册入口（沿用 appHref 语言/归因与注册路由，不触发请求）。
5. 更新 DESIGN 和行为回归，删除失效断言而非删除来源、媒体、语言或路由安全检查。无新依赖、抽象、云修改或 App 改动。

### Behavior lock / acceptance

- 改前生成器全套测试和静态 HTML 作为基线。先写新的区段顺序、正确 CTA、现有标注中的三个固定来源链接、唯一工作台工具行入口、旧教程仍可达的行为测试，再改实现。
- 保留五案例/四视频、原画 SHA 与真实标注、素材权限说明、native controls/直看、无 JS 阅读和键盘操作、语言 VM/浏览器边界、canonical/hreflang/JSON-LD。
- 全部组织页面对比：只有两首页 HTML 改变；列出本轮刻意从首页移除的目的地，确认站内文章仍生成且可由知识库找到，不为维持链接数量塞隐形内容。
- 生成器/浏览器测试、lint/token/i18n/typecheck/build、响应式与深浅色截图、axe 通过后完成，仅本地预览。

### Fallback inventory

- 无 JS 静态阅读、原生视频 controls、外部 CDN 直看和浏览器语言标准回退是 grounded compatibility/fail-safe，原样保留且继续覆盖。
- 既有播放器 play rejection catch 不在本次信息架构清理范围，不扩展；没有新增吞错、跳过验证或隐藏备用 UI。
- 本次选定 smell 为重复导航和随删除产生的 dead code，一次集中删除，不扩展到全站指南或 App。

## Alternatives considered

- **把三列继续折叠**：可减少初始高度且保留全部内容；但会留下同一任务的第三套导航及额外开关，不能解决重复本身。
- **只缩短三列文字**：最小视觉改动，但关系图、工具表仍会重复解释相同项目；选择移除整段，完整步骤交还知识库。
- **删除关系图、保留三列**：步骤更可操作；但用户已明确希望关系直接可见，图也更适合区分小说改编与视频解说这两条不同来源，保留图而非步骤墙。
- **删除全部来源**：页面最干净；但原画不能被误解为实机，真实原作也须可核对，因此把来源放进已有标注，不增新面板。

## Consequences

首页更短，不再提供九条逐步文章捷径；这些文章仍存在于完整指南与知识库。来源需要点击原有标注查看，而不是首页展开全文。自动语言入口和其既有边界不变。本次不声称带来转化或排名改善，视觉效果由本地预览确认。


## Cleanup report

- Writer / reviewer separation：先写本计划；独立架构审阅 APPROVE 后实现。代码审阅未发现运行时问题，指出 DESIGN 仍有已删除路径/末尾关系章的旧描述及 proposed note 的链接状态问题，已逐项修正并转入 implemented，独立复核最终 APPROVE。
- Behavior lock：改前 77 项生成器测试全绿；新断言先跑 6 项预期红、22 项绿，实现后首页 28 项及全套 77 项通过。保留 nine-article discoverability、安装目的地、原画 SHA、素材许可、固定来源、无 JS 和语言边界检查。
- Passes：删除 evidence/paths 及死 CSS/JSON/工具函数 → 去掉关系区重复 CTA → 工具行承接唯一工作台注册入口 → 更新行为/无 JS/键盘和手机锚点回归。通用指南 `.learning-paths`、`.path-direct` 没有删除。
- Fallback：现有原生视频/直看、无 JS、标准语言偏好兼容原样保留；未新增 masking fallback、自动模型请求、依赖或存储层。
- Changed files：build-org-pages / org-pages.css 删除专用呈现；home-reading 仅删除不再消费的 paths；DESIGN 与本 note 记录新职责；生成器/浏览器测试更新真实行为，移除 fixture 的失效 paths 字段。
- Remaining risks：来源全文和 QA 文档改由固定案例目录进入，首页少了直接文章捷径；全部文章仍可由知识库找到。根语言入口和历史边缘 cookie 行为未变；不宣称部署、转化或排名效果。

## Verification

- 77 项 Node 生成器/语言测试通过；15 项 locale/polish Chromium 测试通过；另 3 项本地域名 smoke 通过（中英文无 JS 六项目/工作台入口、360px 主 CTA 到工具标题且不被 sticky header 遮住）。只对 localhost 运行，不是云环境 proof。
- 中英 × 320/360/390/640/768/1024/1440 共 14 组合无横向溢出；所有知识层展开后 axe 零问题；390/1440 深浅色与无 JS 来源链接检查通过。
- 对比 964 份组织 HTML，仅两首页发生变化，metadata/JSON-LD 不变。每页 14 个刻意移除的唯一目的地已分类记录：九篇文章仍在知识库、详情通过固定案例目录、旧注册归因/路径跳转由工具区承接。原文/原画/案例三个固定来源及图片 SHA 保留；没有隐藏链接凑数。
- ESLint、token、i18n、TypeScript 与完整 build 通过；只有原有三条 App hook unused-eslint-disable warnings。补跑改动的 browser tests lint 通过。node --check 与 git diff --check 通过。构建两首页/CSS 与本地预览逐字节相同。
- 实际截图已检查：首页保留原构图，三列步骤消失，关系图无按钮、工具与教程各出现一组。证据位于 `/tmp/zenstory-home-simplify-evidence/`（中英桌面/手机/深色截图、responsive.json、geo-diff.json）。
- 本地预览：`http://127.0.0.1:6183/zh/`，明确英文 `/?lang=en`，根入口自动语言 `/`。原工作树保持干净；没有推送、合并、部署或生产验证。
