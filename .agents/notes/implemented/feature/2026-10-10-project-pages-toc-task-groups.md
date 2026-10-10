# Agent Note: 项目页按路径与任务进入、手机目录折叠、大分类按 skill 分组

Status: implemented（承接 [guides-layers-and-glossary-demotion](2026-10-09-guides-layers-and-glossary-demotion.md)）

## Problem

2026-10-10 上线后对线上页面在 390px 宽度复查：

- 项目页把全部写作技法文章逐条列出：Oh Story 页 22962px（约 27 屏），其中“写作技法”一节 12524px、199 个链接；Drama Skills 页 14761px。首页工具表“安装与上手”正把读者送到这里。
- 每篇指南和文章在“一句话结论”后先放约 410px 的本页导航，`novel-opening` 第一个 h2 在 1139px。
- 大分类分页后每页 24 篇、每行带描述：“分镜与 AI 视频”72 篇，第一页 8048px，按文件顺序排列，看不出方法出自哪个 skill。
- 23 篇文章的方法来源标签只写“本文方法来源 / Method used in this article”。
- `llms.txt` 没有术语表。

用户授权继续优化并上线。

## Decision

- **项目页**：“写作技法”一节改为一句总述（共 N 篇，每篇依据本项目 skill 的方法文件）、本项目的完整创作路径（复用 `WORKFLOWS` 与 skill 标签，步骤全属本项目时显示），以及按任务的紧凑卡片（本项目在该任务下的篇数，链到任务页）。Oh Story 页 22962 → 11248px，Drama Skills 14761 → 8253px。文章仍列在任务页与 /guides 搜索索引中；`article-pages` 测试的“列出该文章的入口”改为任务页与 /guides。
- **文章目录**：`tocNav` 统一两类页面，改为 `<nav class="guide-contents"><details><summary>本页导航 · N 节</summary>…</details>`，HTML 中默认关闭；一行内联脚本在 ≥961px 时打开，作为原有吊顶侧栏。手机上目录占一行（50px），`novel-opening` 第一个 h2 在 774px。无 JavaScript 时保持关闭，点开可用。
- **任务页分组**：“先看这几篇”保留描述；其下改为紧凑行（标题与方法文件）。任务内文章至少有两个 skill 各 ≥3 篇时按 skill 分组，组名来自新增的 `content/skill-labels.json`（按各 skill 固定版本 SKILL.md 的描述撰写，中英文），组头链到该 skill 的固定版本 SKILL.md，按篇数从多到少；guides.json 的分步指南作为“操作指南”组排在最前，不足 3 篇的 skill 合入“其他方法”。分页沿用 24 篇一页、URL 不变，页内顺序随分组变化。“分镜与 AI 视频”第一页 8048 → 5235px。
- **笼统来源标签**：标签只有“本文方法来源 / Method used in this article”时，出处行与紧凑行显示该文件名（如 `world-design-method`）；文章末尾“用 skill 来做”的原文不变。
- **llms.txt**：在 Guides 与 Workbench docs 之间加 Glossary 一节，列出 11 个术语、英文对照与对应文章。`update-guide-directory.mjs` 与文件当前 Guides 格式已不一致（运行会改写该节），本次手工插入，未运行该脚本。

## Alternatives considered

- **项目页保留全量清单但折叠**：最强理由是一页内仍可看到全部文章，爬虫链接不变。没有采用：DESIGN 不用 CSS 隐藏内容，`<details>` 折叠 199 个链接仍是同一份清单；任务页已提供完整列表与分页。
- **按方法文件分组**：最强理由是最贴近 skill 里的具体 knowhow。没有采用：各任务有 29–40 个不同首个方法文件，分组过碎；按 skill 分组 3–6 组，方法文件留在每行显示。
- **把分页改为每页 48 篇**：最强理由是紧凑行后每页能放更多。没有采用：会让现有 `/page/3` 等 URL 失效。
- **目录在手机上默认展开、脚本收起**：最强理由是无 JavaScript 时手机也能直接看到目录。没有采用：脚本收起发生在正文之前，会造成首屏跳动；桌面侧栏展开不影响正文布局。
- **重新运行 update-guide-directory.mjs 生成 llms.txt**：最强理由是单一来源。没有采用：会改写现有 Guides 节格式（37 增 74 删），超出本次范围；脚本同步留待单独处理。

## Consequences

- 项目页与任务页在手机上大幅缩短；项目页不再直接链接每篇文章，内部链接由任务页承担。
- 任务页第 2、3 页内容随分组顺序变化（URL 不变）；JSON-LD ItemList 顺序随之变化。
- 紧凑行不再显示描述，任务页可见文字减少；描述仍在文章页与 meta description。
- 新增 skill 时需在 `skill-labels.json` 补名称（测试会失败提醒）。
- 每篇指南与文章多一段约 120 字节的内联脚本。

## Verification

- `node --test apps/web/scripts/__tests__/*.test.mjs` 63 例通过。新增：项目页显示本项目路径与任务卡片、写作技法一节链接少于 20 个；目录为 HTML 中关闭的 `<details>` 并带宽屏打开脚本；“分镜与 AI 视频”分组顺序为视频提示词 → 分镜与关键帧 → 剪辑成片，组名与 `skill-labels.json` 一致并链固定版本 SKILL.md，紧凑行无描述段；所有文章用到的 skill 都有中英文名；`llms.txt` 含全部术语。
- 本地预览 390 与 1440 宽度检查项目页、文章页（目录开合）、任务页（分组与分页），无横向溢出。
