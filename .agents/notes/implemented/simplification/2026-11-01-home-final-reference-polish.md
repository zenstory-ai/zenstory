# 首页最终参考打磨：一致层级、节奏与反馈

Status: implemented locally for owner review; no push, merge or deployment.

## Problem

用户已认可结构与逐轮减法，要求最终再对照 https://zen-browser.app/ 学习打磨，而不是克隆。承接[标签减法](../../implemented/simplification/2026-11-01-home-secondary-labels.md)，限组织首页 CSS 和相关回归/设计事实；不改文案、结构、语言、项目/教程、真实媒体、依赖或云状态。

## Decision

### Evidence and scope

- 新抓取官方公开首页 1440×1000、390×844、1440 深色；当前版本同规格截图与实际 computed styles 在 `/tmp/zenstory-home-final-reference/`。浏览器工具 host 不可用，采用已安装 Playwright 只读加载/截图，不下载产品、不登录、不复用第三方资产。
- Zen 首屏有明确的主次、紧凑正文与保持重量的行动；不复制其 Junicode、128px 居中主标题、珊瑚色、滚动 reveal、隐藏导航或重阴影。官方全页未滚动时中段空白是未触发 reveal，不作为布局证据；另有真实滚动状态截图。
- 本站桌面四个主 H2 实测 42/40/30/30px、字重 600/500/600/600，手机来源 10–11px。独立 designer 建议收紧 hero→案例间约 32–40px、降低关系章留白、提高手机小字、停止按钮 opacity 淡出；保留已认可首屏构图。

### Cleanup plan (three passes)

1. **排版一致性**：首页四个主区段 H2 统一 `clamp(30px, 3vw, 40px)` / 500，手机保持 29px；移除 tools/library/relationship 的等价覆写。Hero 正文由 pretty 改 balance，避免中英短尾行；不更换字体或主标题。手机原著标题/图注至少 12px、章节号 11px，不添加说明。手机旁栏由 54px+16px 间隙微调为 58px+12px，合计 70px 不变，避免增大后的中文章节号挤成孤行。
2. **节奏**：桌面案例区上 padding 在现有带状间距上减 40px；关系章桌面上下为 `clamp(56px, 6vw, 84px)`，手机 52px（原 64px）。手机案例区现有 64px 不变，主画面仍进入首屏；环境组 44px 间隔、内容顺序和直接关系图保持。
3. **交互反馈**：主按钮 hover 不再 opacity .85，保持文字/底色，以最多 1px 上移和明确 active 回位反馈；首页主 CTA 向下箭头改为向下微移，非全站通用的横移。secondary provenance/tool/caption 链接修复较高 specificity 覆盖 hover 的问题，hover 和键盘 focus-visible 同用已有 accent token。无新阴影、自动播放或滚动动效；reduced-motion 下不移位。原图外侧焦点环仍保留。

### Behavior lock / acceptance

- 改前首页/语言 34 项 Node 测试通过，静态生成基线已保存。先补手机来源字号下限、四个 H2 同规格、按钮不淡出/向下箭头、次链接 hover 与键盘反馈、reduced-motion 的实际浏览器回归，预期先红再改样式；不测试可逆措辞。
- 全部 HTML 应逐字节不变（包括 canonical/hreflang/JSON-LD、所有来源/路由），媒体 SHA 不变；不为视觉补充隐藏元素。只检查 CSS 变更和最终截图，不把两种不同产品做像素相似率打分。
- 跑 Node/浏览器、lint/token/i18n/typecheck/build、14 个中英屏宽溢出/展开态 axe、桌面手机深浅色截图，并让独立审阅者检查最终小 diff。满足三项后停止，不开启下一轮重设计。

### Fallback inventory

无 JS 内容、原生 controls/直看、来源与语言边界均为 grounded fail-safe，保持现状；CSS balance 在不支持的浏览器自然普通换行，不添加检测/脚本兼容层。旧播放器 play rejection catch 不在范围，不扩展；无新 masking fallback。

## Alternatives considered

- **完全模仿 Zen 首屏与字体**：参考一致性最强，但会推翻已认可方向，且不能证明产品效果更好，拒绝。
- **只增加动画/阴影**：容易显得“精致”，但带来装饰和动态干扰；采用现有 token 和极小、可关闭的状态反馈。
- **继续删除模块/来源**：页面更短，但已经完成结构减法；此轮只修规格、节奏和状态，不再改变信息架构。

## Consequences

主 H2 会更一致，工具/教程标题相对之前略大；页面收紧部分空档，手机来源略大。视觉判断仍有主观性，保留前一提交便于比较。仅本地预览，不宣称转化、排名或云环境验证。


## Cleanup report / verification

- 独立 designer 从真实截图提出三类微调；计划单独经 code-reviewer APPROVE。实现只触碰 `org-pages.css` 的首页选择器、`org-home-polish.spec.ts`、DESIGN 与本 note，不改生成 HTML 的源代码或共享控件规则。
- 基线 34 项首页/语言 Node 测试通过。六个浏览器新验收先红（来源字号、H2 规格、按钮/交互），实现后全套 78 Node + 17 Chromium 测试通过。按钮 opacity 还受共享 `.btn:hover {opacity:.9}` 影响，最终在首页状态显式设 1，不修改内页行为；测试等待真实 transition 完成，不在第一帧采样过关。
- 手机加大章节字号后最初出现末字孤行；来源栏 58px + 间隙 12px 仍合计 70px，并补章节单行的实际行高回归，最终深浅色均通过。原画位置未移动；主张/画面仍进入 1440×900 和 390×844 首屏。
- 源码/案例等次链接 hover 与 focus-visible 共享 accent，实际 pointer/keyboard 回归证明旧灰色覆写已修复；按钮保持文字/底色和 opacity，active 回位，下箭头只向下微移。reduced-motion 下按钮与箭头 computed transform 均为 none。原图焦点环、无 JS 原生视频和静态来源继续可用。
- 14 个中英屏宽组合（320–1440）无横向溢出，展开态 axe 零问题。964 份组织 HTML 逐字节相同：所有文案、来源、路由、metadata、JSON-LD、语言脚本完全不变；原画 SHA 也不变。没有新依赖、抽象、吞错、模型调用、云操作或第三方资产拷贝。
- 桌面/手机/深色中英截图位于 `/tmp/zenstory-home-final-evidence/`；参考与滚动状态、采集脚本/测量位于 `/tmp/zenstory-home-final-reference/` 和 `/tmp/capture-zen-final-reference.cjs`、`/tmp/capture-zen-final-scroll.cjs`。不比较两种产品的像素相似率，不声称复刻完成。
- 最终独立视觉验收通过：手机章节单行、来源可读、桌面/全页章节关系清楚、暗色对比稳定；建议停止，不扩大范围。
- Remaining risks：视觉偏好主观，浏览器对 balance 支持不同则按普通换行；root 自动语言与历史 cookie 边界保持原样。未部署，不宣称转化、排名或生产会话稳定性收益。

- 最终 ESLint、token、i18n、TypeScript/完整 build、git diff --check 全通过；保留三条原有 App hook unused-eslint-disable warnings，没有新增警告。完整 build 的两首页 HTML/CSS 与预览逐字节一致。
- 最终独立代码审阅 APPROVE；原工作树干净，仍只在独立 feature worktree 本地提交，未推送、合并或部署。
