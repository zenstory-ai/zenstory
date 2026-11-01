# 项目与指南入口：延续首页减法，刷新 Star 快照

Status: implemented locally in the authorized release batch; promotion remains subject to release gates.

## Problem

首页获批后，用户追加更新项目页的 GitHub Star 数据，并用相同思路打磨 `/projects` 与 `/guides`。旧项目数据合计 11,388，快照日期 2026-09-29；页面仍有盒子墙、重复层级编号、装饰性标签。最新范围只含两个双语索引及六个项目详情的 Star 数/快照日期展示，不扩展到 App、具体项目安装说明、文章正文、搜索算法或路由。

## Decision

### Evidence / cleanup plan

- 2026-11-01 15:19 UTC 逐仓经 GitHub API 读取：Oh Story 8,220，Drama Skills 3,615，Novel to Game 952，Video Recap Skills 601，Oh Story DSH 664，ZenStory 84，合计 14,136。原始响应摘要在私有 `/tmp/zenstory-home-release-receipts/project-stars-live.json`；更新 `projects.json` stars 与 `org.json` proof 总数/日期，删除 sources.en/zh 中四项目陈旧的 Star snapshot 日期子句，保留各子句后面的源码复核事实/固定链接。六个项目详情的 proof 行追加一次 plain as-of 文本，目录也每页一次显示同一日期；固定 sources.checked_on、源码提交与 skills 数不变，不冒充重新复核。
- 项目索引：保留全部六项目、用途、skill 数、安装/源码/工作台入口和比较链接；卡片改纸面项目行，保留细分隔线，无白底圆框/青色装饰星。用户随后明确指出“Agent skill 包 / 制作 skill 合集 / 改编 skill 包”不贴切，目录改用已有创作任务的用途标签（workbench 用浏览器小说/短剧写作），不用技术形态分类；详细项目页的实际格式字段与源码事实保留。Star 纯文本展示、全目录一次快照日期。重复三卡关系改简短的有语义关系行，保留小说→短剧/游戏与 footage→解说的输入区别、素材使用权提醒、DSH 社区插件且非 DeepSeek 网页聊天扩展这三项独有边界；九个 Agent 名称保留，但不再各占一个胶囊。不新编安装地址或外链。
- 指南索引：保留四层及原锚点、四条工作流全部步骤/方法名、十任务及篇数、术语、全部搜索索引和文章链接。删眉题和章节 `01 / 04`，顶部锚点变普通文本行；层名与首页统一为入门/完整工作流/技法与模板/排错与参考。十任务改开放两列列表，手机单列；术语为自然换行的文字链接。步骤编号和路径线表达顺序，保留。
- 两页加专用 pageId 以限定样式；导航/字号/留白复用首页已有规则，主标题与分节不形成新的风格。CSS 只作用索引，不改 topic 分页、项目详情、文章/文档或 App；不新增组件、资产、字体、依赖或运行时请求。

### Behavior lock and acceptance

- 整合基线 79 Node +17 首页浏览器、lint/type/build 已通过；原首页 HTML/CSS与用户认可版本一致。改前生成 HTML/截图保存；先补必要行为回归，再改呈现。不得为可逆文案写逐字镜像测试。
- 回归锁定六项目卡目的地/安装/源码/当前 Star 数和快照日期、总数一致；固定源码版本/checked_on 不变，六详情及目录各有一次 org.proof.as_of；禁止残留旧 Star snapshot 日期，不能禁止仍有效的源码复核 2026-09-29 日期。关系的三项独有事实做语义回归，不逐字镜像。指南搜索题目排序/清空/query/空状态、无 JS 路径浏览、四层锚点、工作流步骤/方法、十任务/计数/术语和分页继续成立。
- 保留 canonical/hreflang/JSON-LD 与全部已发布路由；除去重复 href 次数、部分仅装饰文本，不能静默删除唯一来源/文章目的地。主页不能视觉回退。
- 双语 390/1440 light/dark、14 宽度、键盘/触屏44px和 axe；相关 Node/浏览器/完整 lint/type/build、与改前 HTML SEO/link diff、独立代码/视觉审阅通过后，合进一个 PR/main CI/E2E/预发布/生产发布批次。发现失败则修复，不绕过门禁。

### Fallback inventory

静态无 JS 浏览、原生链接、搜索的已有相关结果/空状态是 grounded compatibility，算法和 markup 数据属性保持；不新增隐藏内容、吞错、运行时 GitHub 拉取、数字估算或自动付费调用。

## Alternatives considered

- 保留所有盒子只调阴影：变动最小，但与首页已确认的纸面排版不统一，且不解决重复层级。
- 删工作流/分类只留搜索：最简，但破坏首次使用与无 JS 访问、知识可达性，拒绝。
- 在线请求实时 stars：数字常新，但引入 GitHub 依赖/限流/页面跳动；采用有来源与日期的静态快照。
- 顺便刷新每个项目源码/skill 数：可让全部信息最新，但需要重审方法及实际版本，不属于本次 Star 更新；保留原固定来源与核对日期。

## Consequences

两个索引更接近首页的编辑式层级，功能与知识范围不减。Star 是有日期的快照，不承诺实时同步；搜索/文档与主线其他新功能保持不动。此次发布仍不触发 API、数据库或 Prefect 修改；生产严格走仓库发布门禁，不因 scope 追加绕过验证。


## Cleanup report / verification

- 作者/审阅分离：独立计划审阅先指出旧 Star 子句及关系独有边界，修订后 APPROVE；项目页交由原生 executor 拥有局部 helper/index/CSS/测试，root 拥有指南、共享页壳、数据和整体验证。未覆盖 main 的较新 App/法律/后台设计事实。
- 项目页六项目开放行、关系 definition list、九 Agent 普通列表；用户追加反馈后，副标题由技能包形态改为创作用途，复用已有 taskChoices，workbench 明确浏览器小说/短剧写作。所有安装/源码/登录与比较地址保留，不发明 host 链接。
- 指南页四层 ID、四工作流 16 步、十任务/计数、术语与全搜索索引保留；删除重复的章节分数/眉题、任务和术语盒子。H1 简化为“创作指南”，避免手机把“什么”拆成两行。只复用页壳/字体；不改搜索/推荐算法，不新增依赖、资产或运行时 API。
- 行为锁：整合基线79 Node+17浏览器通过；Star 日期和指南开放结构验收先红，新增项目/星标/目录回归。新全套82 Node与37浏览器通过（包括原首页17项、目录20项），用途文案最后轻改另跑3项项目/Star回归通过；最终同源质量门禁在发布 PR 前收集。
- 42个 route×语言×宽度组合（首页/项目/指南，中英，320–1440）无横向溢出；展开首页axe零问题。目录 390/1440 light/dark 的严重/关键axe违规为零；搜索query刷新、清空、空状态、键盘和无JS完整浏览通过。
- 964份HTML中恰好16份变更：两语言项目/指南索引与六项目详情；metadata/JSON-LD、全部 href 目的地及次数不变。认可的首页HTML逐字节不变，原画SHA不变；固定源码日期/提交和skills数不变。Star API采集时间为2026-11-01 15:19UTC，数据14136非估算；新日期只标Star，不伪造源码复核。
- 删除无消费者的旧 project-card/grid/card-meta/actions、model numbered-card、layer-step CSS；保留 guide-card、project-detail task-index、QA steps 等仍在用的通用样式。现有兼容/原生链接、搜索相关结果/空状态为 grounded fail-safe，未新增 masking fallback。
- 独立视觉验收通过，认为两个索引层级/来源与步骤可读、纸墨身份保持，建议停止扩展。证据 `/tmp/zenstory-org-indexes-{before,after}/`、`/tmp/zenstory-org-release-evidence/`。
- Remaining risks：Star为有日期的快照，数字会继续变化；目录开放项目行比旧三列卡片更长，但视觉更清楚，知识/来源未减。未因本地验证宣称生产或搜索/转化效果；生产仍按 main exact CI+E2E→预发布→prod-v 门禁走。

- 最终同源82 Node、37浏览器、lint/token/i18n、TypeScript/完整构建通过；当前 main 基底只有两条原有 unused-eslint-disable warnings，无新增。最后代码审查要求锁住小说/footage与各工具的关联，已按 Adapt 行内语义补足（不是只检查项目名存在）；删除余下无消费者 host-grid 与 model media 规则，3项聚焦回归、token及最终静态构建再通过。
