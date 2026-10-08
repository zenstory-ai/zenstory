# Agent Note: 工作台、新手引导和应用首页的文案只写作者能核实的事

Status: implemented

## Problem

2026-10-08 的文案审阅（车主第 2 个问题：首页输入框让人有负担）在工作台、新手引导、项目统计和应用首页（app.zenstory.ai）找到几类问题：

- 首页输入框的想法被写成了前提条件：「写一句灵感就能开始」「保存第一个文件」「上手必做的一步」；新建项目弹窗的标签和占位符是同一句「给你的作品起个名字」，看起来像必填，其实留空会用模板默认名（`我的小说` / `我的短篇` / `我的短剧`）。
- 首页填了想法后自动发出的第一条消息（`chat:message.createProject`）不管想法多模糊，都要求 AI 直接给出完整故事框架；中文里还有半角逗号，英文用的是 `{{type}}`。
- 项目页（`/dashboard/projects`）的空状态提示「点击『开始创作』」，但这一页没有这个按钮；页头写「所有项目」，导航写「我的项目」。
- 项目统计把状态写成系统告警（健康 / 需关注 / 需行动），待办卡把「没有大纲」当成问题，连续写作的提示是翻译腔。
- 应用首页 `HomePage.tsx` 写死了 `SOCIAL_PROOF_METRICS`（2,000+ 创作者、1200 万+ AI 协作字数、4.9 评分），这些数字没有来源，产品也没有评分系统，违反 DESIGN.md「No invented metrics」。`2026-10-05-workbench-copy-for-authors.md` 当时把它列为「未做」。
- 首页定价预告没有写清免费版的真实额度；素材库没标 Pro；页脚是「© 2026 zenstory. All rights reserved.」（年份写死，和 MIT 开源矛盾）；Agent API 卡片只说「把 API Key 填到 Claude Code」，和实际接入流程（创建密钥 → `zenstory login` → `zenstory skill install`）不符。
- 新手问卷承诺了「灵感模板」（灵感库默认关闭）和「团队协作」（产品没有多人协作）。

## Decision

- 工作台：`todayActionPlan.title` 改为「今天可以做的事」（灵感库关闭时只有 2 件）；创建项目建议写「不用先想好，点一下就能开始」；用量建议改为「查看用量 / 看看今天还剩几条 AI 消息，不够用时再开 Pro」（免费额度按天算）；`activationGuide.steps.first_file_saved` 改为「写下第一段内容」；引导第 4 步改为「点这里就开始」，说明写了想法 AI 会先理思路、空着也行。`dashboardActionPlan.ts` 和 `dashboardFirstRun.ts` 里的 fallback 与 zh locale 一致。示例占位符 `dashboard:inspiration.example.*` 不变。
- 新建项目弹窗：标签用新 key `projects.nameLabel`（「作品名（可不填）」），并用 `htmlFor` 关联输入框；占位符 `projects.namePlaceholder` 插值 `{{name}}`，值取当前类型模板的 `default_project_name`（与 `handleCreateProject` 留空时用的名字是同一个来源），例如「不填就先叫「我的小说」，之后随时能改」。
- 空状态：工作台首页的 `projects.emptyHint` 指向上方「开始创作」；项目页改用新 key `projects.emptyHintProjectsPage`，指向页头的「新建项目」。`projects.all` 改为「我的项目」。
- 首条自动消息 `chat:message.createProject`：先判断想法是否具体，具体就给主要人物、背景、核心冲突和大致走向；模糊就先问两三个关键问题。中英文都插值 `{{typeLabel}}`（`ChatPanel.tsx` 同时传 `type` 和 `typeLabel`，代码不变）。
- 项目统计：状态改为「状态不错 / 可以加把劲 / 好久没动笔了 / 还没开始」；待办卡标题改为「接下来可以做」，「无大纲」改成「可补大纲（可选）」，「未写作」改成「待写正文」；`streak.daysUntilBreak` 改为「再过 {{count}} 天不写，连续记录会中断」，并补上 `_one` / `_other` 复数形式；「今天写满 10 个字就开始计天数」对应后端 `STREAK_MIN_WORDS_FOR_DAY = 10`。
- 应用首页：删除 `SOCIAL_PROOF_METRICS`、整条社会证明栏以及 `home:stats.*`，不放替代数字（hero 下方已有「自动保存 / 手机电脑都能接着写 / 改错了可退回旧版本」三条可核实的事实）；定价预告写明免费版「每天 10 条 AI 消息、最多 3 个项目」，Pro「AI 消息和项目不限，可用素材库拆解参考小说」（对应 `services/subscription/defaults.py`），Pro 徽标统一写「Pro」；素材库功能和长篇卡片的「引用素材库」标 Pro；版本历史写「AI 的每次改动和你的保存都会记成版本」。
- 页脚 `home:footer.copyright` 改为「© {{year}} ZenStory AI」，`HomePage`、`Login`、`Register` 传入 `new Date().getFullYear()`。
- Agent API 卡片按 `apps/cli/README.md` 和设置页 `AgentApiKeysPanel` 的三步写：在设置里创建 API 密钥 → 用 zenstory 命令行工具在终端登录 → 给 AI 助手装上 zenstory 技能；徽标加上 Codex（`zenstory skill install --target codex`）。按钮改为「创建 API 密钥」。
- 新手问卷：`preview.items.explorer` 改为「一句话就能开新书，附带一步步的上手提示」，`preview.level.beginner` 改为「先建一个项目，再慢慢补角色和章节」；`persona.options.studio` 的标签改为「工作室」，描述改为同时推进多部作品、素材和进度分开管理。`auth:plans.pro` 中文改为「Pro」。

## Alternatives considered

- **社会证明栏换成一行信任信息**（审阅清单 H1 的备选：「每天 10 条免费 AI 消息 · 自动保存 · 随时退回旧版本」）。最强理由：保留首屏下方的视觉分隔，同时让访客一眼看到免费额度。被否：hero 下方已经有自动保存和退回旧版本两条，再放一行就是重复；免费额度在定价预告里写得更完整。
- **只改数字、不删栏**（例如改成真实注册数）。最强理由：真实数字比没有数字更有说服力。被否：没有可公开引用的统计来源和更新机制，写死的数字过一阵就会失真；DESIGN.md 明确不放推广性统计。
- **首条消息保持「直接给完整框架」**。最强理由：作者一进项目就能看到一份成形的框架，第一印象更强。被否：只写了一句模糊想法时，硬塞的框架和作者本意往往对不上，还会让作者觉得必须先想清楚才能开始，正是车主第 2 个问题。先问两三个问题，多花一轮对话，但方向是作者自己定的。

## Consequences

- 收益：首页和工作台不再暗示「必须先有想法 / 必须起名」；应用首页不再有没有来源的数字；定价预告、素材库、Agent 接入流程和后端实际行为一致。
- 代价：模糊想法的首条回复会多一轮问答，消耗一条 AI 消息才能看到框架；首页少了一块视觉内容；默认项目名一变（`project_templates.py`），占位符自动跟着变，但如果模板接口失败，占位符用的是前端 fallback 的 `defaults.*` 名字。`DashboardCoachmark.test.tsx`、`dashboard-coachmark.spec.ts`、`responsive-pages-mocked.spec.ts` 的文案断言随之更新；`HomePage.test.tsx` 的两条社会证明数字测试换成「不渲染统计数字」和「页脚年份取当年」。
- 未做：作品类型名（清单 C33）和订阅相关按钮（`billing.ctaUpgradePro` 等）不在这次改动范围内；首页输入框下方加说明、按输入状态切换按钮文案（清单 B1、B2）车主已否决。
