# 首页细节去重、自然文案与浏览器语言入口

Status: implemented locally for owner review; no push, merge or deployment.

## Problem

用户认可第二轮视觉方向后，要求继续打磨、去除重复元素；写作案例“留下书稿，不只是一段对话”无法直接识别为网文创作；项目关系不应折叠；希望默认根据浏览器语言切换。范围为独立 worktree 当前首页与语言入口，不改变 App、文章/指南内容、云配置或已发布版本。

## Decision

### Scope / cleanup order

1. 删除上轮遗留的首案例 `is-feature` 特殊分支及等价 CSS；四案例保持相同层级，竖屏视频原有自有海报背景不变。
2. 删除重复的案例输入/产物行（含义并入原来的简短描述），去掉“作品区→从哪里开始”重复行动及复述标题的 section 标签/说明。保留每个案例的真实媒体、描述、教程、直看/试玩、固定来源、字幕来源；不删数据清单事实。
3. 改写四个案例标题与说明：直接说“用 AI 写网文”“把小说改成短剧”“把视频剪成解说”“把小说改成 3D 游戏原型”。不声称已经写完一部小说/整集剧，也不改变 20 镜、第一集开头、59 秒样片等事实。其余首页标题从隐喻或规格说明改为普通中文，并同步自然英文。
4. 移除项目关系的原生 details/summary 包装和相应专用 CSS，直接显示现有关系图和一句准确说明；不保留无作用的“查看关系”开关。
5. 默认语言只影响组织站根首页入口：有效手动 `zs_lang` 选择优先；未选时按 `navigator.languages` 的第一个受支持语言选择，`zh-*` 中文、`en-*` 英文，其他语言继续找支持项/最终英文。只在生成的英文首页注入、只在 `/` 执行；不改 `/zh` 或深链，不自动写 cookie，不改变 App。仅首页 EN 切换链接使用 `/?lang=en`，无有效手动 cookie 时该明确英文入口优先于浏览器，保留 query 以便刷新；不新增 `lang=zh`（直接用 `/zh`），canonical/hreflang 仍为 `/`。这保证 Cookie 被禁用时 EN 不会被自动判断送回中文。浏览器导航保留 query/hash，以 replace 避免返回键循环。既有 Vercel 手动偏好重定向保持不变。

第 5 项经独立架构审阅与官方资料核对后采用已有静态 head 注入点，无新增运行时、依赖或云规则。独立计划审阅先要求补充 Cookie 禁用时的 EN 入口和独有事实保留，两项修订后 APPROVE；实现与计划审阅保持分离。

### Behavior lock and acceptance

- 去重不丢独有事实：删除输入/产物行时，视频解说“有权使用的素材”等限制须在标题或描述中保留一次；仅删除重复 eyebrow/lede，不删除 H2 或 aria-labelledby 关系。
- 改前完整 70 项生成器测试作为基线；原有静态语言、canonical/hreflang、真实来源/图片 SHA、五案例/四视频、路径→工具→安装、无 JS 原生控制继续保留。
- 新增执行真实语言脚本的 VM 测试与真实浏览器回归：语言顺序、zh/en 地区标签、不支持语言、无 languages 的标准 API 回退、手动选择胜过浏览器、Cookie 禁用下明确 EN 入口与刷新、无自动 cookie 写入、query/hash、非根路径、无 JS。只测试行为，不对可逆措辞加镜像断言。
- 浏览器检查关系图在正常/无 JS 下直接可见，无开关；手机横滑、light/dark、390/1440、14 个中英宽度、展开态 axe 不回退。
- 当前静态测试 fixture 缺根首页到 org-home 的组织站 rewrite；只补这个真实已有路由行为，以便执行 `/` 自动语言回归，不伪造线上 proof。
- GEO 对比保留 canonical/hreflang/JSON-LD 以及全部唯一站内/来源目的地；减少重复锚点次数是本轮预期。不把多余重复链接次数当必须保留。
- ESLint、token、i18n、TypeScript/构建、相关测试和真实截图通过后停止；仍只本地预览，不合并/推送/部署。

### Fallback inventory / classification

- 无 JS 时英文静态根首页、显式中英文 URL 和普通语言链接：grounded compatibility/fail-safe，保留并测试。
- `navigator.languages` 不可用/空时读标准 `navigator.language`；无支持语言时英文：grounded compatibility，明确边界并测试，非吞错。
- CDN 视频不可用时现有直看链接/原生 controls：grounded compatibility，保留；不添加自动请求或付费重试。
- 现有 playerScript 的 play rejection catch 不属于本轮细节/语言改动，保留不扩展，不将其当新模式复制。没有新增泛化 catch、UA 猜测、后台多语言框架或存储层。

## Alternatives considered

- **Vercel Accept-Language 配置条件**：首次响应即可跳转，不依赖脚本。但正则很难正确表达首选支持语言及 q 权重，本地静态预览也不能验证真实边缘行为；本轮不为语言入口新增云规则或运行时。
- **所有语言 URL 自动匹配浏览器**：表面上一致，但会覆盖分享/深链语言并增加循环和抓取风险；只将根首页作为自动选择入口。
- **只保留手动语言切换**：最稳定且符合历史决策，但不满足本次新增请求。该变更链接 [旧语言记忆决策](2026-10-10-natural-wording-design-unification-language-memory.md)，旧 note 不改写。
- **只改用户点名一句标题**：改动最少，但其他案例和重复说明仍然生硬；限定在当前首页，按“直接说明做什么”的同一标准清理，不扩散到全站文章。

## Official evidence

- [MDN navigator.languages](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/languages)：偏好列表有顺序，使用首个受支持项，不按“列表里出现中文”直接判中文。
- [Vercel configuration redirects](https://vercel.com/docs/routing/redirects/configuration-redirects)：header 条件是匹配机制，不提供完整语言权重协商，本轮不引入这类云规则。
- [Google localized versions](https://developers.google.com/search/docs/specialty/international/localized-versions) 与 [多语言站点说明](https://developers.google.com/search/docs/specialty/international/managing-multi-regional-sites)：保持独立语言 URL/alternate 和手动入口；自动根入口仍有抓取取舍，不能宣称零影响。

## Consequences

自动入口依赖 JavaScript；无 JS 仍读英文根首页，可手动进入中文。显式中文与内页不会被浏览器语言重定向，但原有手动 cookie 的首页边缘规则仍存在。保留独立 URL 和 alternate 链接，不宣称对搜索流量/排名没有影响。手机减少重复信息，项目关系则占用可见空间而不是多一次点击。视觉与文案最终由用户预览判断。既有边缘 cookie 规则在“先选过语言，再关闭 JavaScript”时仍可能先于普通首页链接生效；这是原有云路由行为，本轮不扩展修改或宣称该组合能更新已保存偏好。


## Cleanup report

- Scope：当前首页文案、重复呈现/CSS、直接关系图与根语言入口；不改 App 或全站文章。
- Behavior lock：改前 70 项生成器测试通过；语言脚本、主页专用注入和关系图直接可读回归先红后绿。
- Cleanup plan / passes：先删除失效 is-feature 分支和样式，再去掉重复 IO/eyebrow/lede/行动，改为具体创作用语；普通 H2 和 aria-labelledby 保留；最后补语言与原生阅读回归。
- Fallback findings：无 JS 静态内容、标准 navigator.language 回退、未知语言英文、Cookie 不可写的明确 EN 入口和外部视频直看属于有边界兼容行为；已有播放器 play rejection 处理未改，不新增吞错或隐藏后台规则。
- UI findings：首个案例直接标“网文”；案例独有事实合并描述（包括素材使用许可、旁白、混音/包装）；四卡按钮底部对齐；关系图直接展示，手机节点文字稍放大。
- Changed files：showcases/home-reading 只改文案；build-org-pages 删除重复呈现/接入根脚本；org-pages.css 删除废弃选择器；site-shell 增加根入口逻辑并保留普通链接；测试覆盖行为和边界。
- Remaining risks：自动选择需 JavaScript，不能将浏览器偏好视为绝对意图；历史边缘偏好规则、无 JS + 已保存偏好组合保持原样；未进行部署/生产验证，不宣称索引或转化提升。

## Verification

- 基线 70 项生成器测试通过；本轮新增语言 VM/注入边界、直接关系图测试，最终 77 项通过。旧的“所有页面禁止 navigator.language”断言只对英文根入口开放，其他页面继续禁止；不删除独立 URL/metadata 测试。
- 15 项 Chromium 浏览器测试通过：偏好顺序、未支持语言、Cookie 优先与手选后回根、Cookie 不可存时 EN 点击/刷新、内页不跳、无 JS、四种手机/桌面深浅色、原生横滑、关系图直接可读和展开态 axe。
- 320/360/390/640/768/1024/1440 中英 14 个组合无横向溢出；展开态 axe 零问题。
- 964 份组织 HTML 仅两首页变化；metadata/JSON-LD 与唯一站内/来源目标保留，减少一个重复 `#start-h` 链接。对比将首页 `/?lang=en` 归一到它的英文 canonical `/`；没有删除实际入口。
- ESLint、token、i18n、TypeScript、完整构建、node --check、git diff --check 全部通过。仅原有三条 unused eslint-disable warning；独立代码审查最终 APPROVE。未新增依赖、吞错、凭据或 App/云规则变更。
- 构建输出与本地预览的两首页 HTML、CSS 字节一致。最终截图、写作案例局部、手机关系图、响应式与 GEO 证据：`/tmp/zenstory-home-details-evidence/`。
- 预览入口：`http://127.0.0.1:6183/`（自动选择）、`/zh/`（中文）、`/?lang=en`（明确英文）。没有推送、合并、部署或生产验证。
