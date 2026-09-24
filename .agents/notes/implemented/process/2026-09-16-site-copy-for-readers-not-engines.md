# Agent Note: 组织站文案面向读者和搜索引擎，而不是答案引擎

Status: implemented

## Problem

2026-09-10 起的 GEO 工作把 zenstory.ai 的组织页写成了给 LLM 抽取用的形态：首页和 /projects 的第一屏是同一段罗列五个产品名的 canonical 段落，H1 是"让故事走向更多形态"，没有任何用户会搜的词；每张项目卡带 star 数、许可证、技能数、安装命令和"截至 2026-09-11"，指南卡带"核对于 2026-09-13"，首页一共 8 处日期戳；项目页 title 长 155–177 字符、description 长 333–548 字符，`/` 与 `/projects` 共用一段 description。geo 仓库的 GitHub 引荐快照显示 Google/Bing/Baidu 带来的访问约为 ChatGPT/通义/豆包的 7 倍，页面却几乎只为后者写。owner 于 2026-09-15 明确要求回到用户体验和传统 SEO。

## Decision

组织页生成器 `apps/web/scripts/build-org-pages.mjs` 按以下规则出页：

- 首页与 /projects 的 lede 使用 `content/org.json` 新增的 `intro`（一段面向作者的人话）。原 `canonical` 段落是罗列五个产品名的抽取用摘要，页面不再渲染它，`org.json` 里也随之删除；`public/llms.txt` 有自己的一段摘要，不依赖这个字段。首页 H1 为"ZenStory AI 用 AI 写小说，再改成短剧、游戏和解说视频"（英文同义），包含用户会搜的词。
- 每个项目在 `content/projects.json` 里有 `seo.title` 与 `seo.description`：title 以关键词开头、以 `| ZenStory AI` 结尾，英文 ≤ 85 字符；description 英文 ≈ 160 字符、中文 ≈ 90 字符。`definition` 只用于正文和 JSON-LD，六段 definition 改写为不含"不是…/不保证…"从句的产品描述。
- 指南页与比较页的 meta description 由 `summary()` 从 answer 截取到句末（英文 160 / 中文 90），正文和 JSON-LD 仍是完整 answer。
- 日期戳只出现一次：首页与 /projects 末尾的事实行（star 总数截至日期），项目页"来源"段一句话（源码读取日期 + star 截至日期），指南页与比较页的"更新于"。hero、卡片、指南卡上的 `as of / 截至 / Checked / 核对于` 全部移除；`proofRow` 不再接受日期参数。
- 项目卡只显示任务、名称、一句话、形态与 star / 技能数两枚 chip，以及"安装与详情"、"GitHub 源码"两个链接；安装命令只在项目页和首页旗舰 hero 出现。
- 项目页"来源与边界"改名"来源"，去掉"不保证后续版本一致"一类措辞；工作台迁移提示缩为一句。

## Alternatives considered

- **保留 canonical 段落作为首屏 lede，只缩短 meta**。最强理由：这段话是 llms.txt、组织 profile 和各仓库 README 共用的"标准答案"，一处改动会让抽取结果不一致。被否：首屏是给人看的，罗列产品名的句子无法回答"这是什么、我能得到什么"；`public/llms.txt` 本来就写着自己的摘要，页面正文不需要再复述一遍。
- **把日期戳改成隐藏文本（`sr-only`）保留在每个数字旁**。最强理由：答案引擎和 JSON-LD 消费者仍能读到 provenance，肉眼不受干扰。被否：隐藏文本对搜索引擎是负面信号，且同一日期在一页重复八次没有信息增量；一页一次、可见即可。
- **用 Accept-Language 把中文用户重定向到 /zh**。最强理由：用户主体是中文作者，x-default 指向英文首页对他们多一次点击。被否：按语言头重定向会让抓取器拿到不稳定的首页，hreflang 已经让搜索结果按语言落到对应页；本次不改路由。

## Consequences

- 收益：首屏用一句话说清价值，description 全部落在搜索结果预算内，`/` 与 `/projects` 有各自的 description，页面上的数据表感消失；GEO 层（llms.txt、JSON-LD、canonical、hreflang）原样保留。
- 代价：`org-pages.test.mjs` 里与旧措辞绑定的断言（首页标题短语、"Source notes"标题）随之更新；新增项目时 `seo` 字段为必填，缺失会在生成时抛错。
- 未做：英文指南标题未缩短；21 篇指南正文里"结果边界"段落的措辞未改，比较页的第一方披露按测试要求保留。

## Verification

`cd apps/web && pnpm test:site` 20/20 通过。对生成目录（86 页）统计：英文 description 最长 160、中文 117，无重复、无空值；中文 title 最长 60。英文 title 最长 99，来自指南标题本身（`Learn writing craft from fiction: …`）——指南标题是编辑内容，本次未改写，因此英文指南页标题仍可能被搜索结果截断。首页 `as of|截至` 仅出现一次，`Checked|核对于` 为零。
