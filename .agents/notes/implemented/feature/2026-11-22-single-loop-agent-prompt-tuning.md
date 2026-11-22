# 单循环 Agent 提示词细调：补回行文质量、对齐三类目录规划与收紧自查防反复微调

## Problem

单循环 agent 上线并删除旧多角色图（[remove-graph-agent](../simplification/2026-11-17-remove-graph-agent.md)）后，`apps/server/agent/prompts/subagents.py` 里的 `WRITER_PROMPT`（含 [2026-10-31 行文与自查决策](../bug-fix/2026-10-31-prose-craft-and-continuity-self-check.md) 补入的「行文」八条、起名、标点、篇幅与前提忠实规则）随之删除，没有完整迁移到 `loop/prompts.py`；同期 11-18～11-22 连续落地了直接改稿（[direct-ai-edits](2026-11-18-direct-ai-edits.md)）、批量 `query_files(ids=[...])` 与批量 `edit_file`（[batch-tool-calls-and-session-snapshots](../architecture/2026-11-21-batch-tool-calls-and-session-snapshots.md)）、以及分项目类型的默认目录规划，提示词也留有若干脱节：

1. **行文质量与网文/跨题材适配退化**：对标真实已出版网文/短篇/短剧样本库（350+ 部线上已发表作品的统计基线：章长中位数 2200–2800 字、段长中位数 17–47 字、对白段占比 31%–34%、否定对举 `不是…而是/是…` 0.08–0.13 次/千字、孤立空拍 0.00–0.05 次/千字），在 staging `MiniMax-M3.1-Flash` 上跑 7 组真实单循环端到端评测（覆盖搞笑历史男频、古言、第 3 章续写含后章剧透红线、现代爽文短篇、偏现实治愈青春对照、短剧第 1 集、含 `<author_edits>` 的批量修改），基线暴露出多类稳定复现的问题：
   - 剧本把角色直接叫作「女主」「庶妹」，944 字台词塞入 14 处括号舞台提示；
   - 中文古风正文混入英文词、半角引号 `'`、`---` 分隔行、问句末误用句号 `。`；
   - 篇幅普遍超出作者明确目标 30%–50%，章末常以「回房吹灯」「明天开始……」等卸力套话收尾；
   - `GENRE_NORMS["short"]` 写「短段不等于一句一行」，而共用 `_WEB_FICTION_VOICE` 又写「一句一段是常见排版」，存在正面冲突；
   - 偏现实/治愈向作品容易被网文口感拉偏成套路打脸。
2. **目录规划与工具协同留有旧口径与并行冲突**：
   - `LOOP_WORKING_RULES` 未写清长篇（`设定/角色/素材/大纲/正文`）、短篇（`人物/构思/素材/正文`，无设定目录）、短剧（`角色/设定/素材/分集大纲/剧本`，无正文目录）三类项目的根目录映射、`素材` 放原创片段与项目参考文本、以及 `分章大纲`/`分集大纲` 边界；
   - 首轮给一两段简介开书时，模型易把开书简介当成待归档设定文档另建多余的「设定」文件，或把 `create_file` 与 `update_project` 放在同一次模型回复里并行调用，触发 `file_body_pending` 守卫拒执行与额外纠偏轮；
   - `base.py` 仍有「作者可以审阅或撤销」旧表述（现已改为直接落库 + 修改卡一键撤销 / 历史版本找回）。
3. **写后自查容易诱发反复重读与多次微调**：
   - 当写后自查续跑提醒（`_BODY_SAVED_CONTEXT`）把轻度标点检查与事实硬伤并列、且未禁止重读刚写完的章节时，实测 `novel_ch3_continuation` 在 `write_chapter` 后反复调用 `query_files` + `edit_file` 达 6 次，单轮耗时飙升到 327.6 秒。

## Decision

只改提示词与单测，不新增关键词/正则语义分支、不加禁词/人名黑名单、不设固定对白比例或括号配额，保持系统提示固定前缀跨轮逐字稳定（不破坏前缀缓存）与共享 `<file>` 示例格式中性（[ai-chapter-first-paragraph-indent](../bug-fix/2026-11-17-ai-chapter-first-paragraph-indent.md)）：

1. **补齐单循环 `LOOP_WORKING_RULES` 与「行文与成稿质量」六条正面原则（`apps/server/agent/loop/prompts.py`）**：
   - 写明三类项目默认根目录映射与命名约束（长篇/短篇/短剧区别、`素材` 目录定位、不散落根目录或自建重复顶级目录、新建角色/设定前查重），并明确长篇建 `分章大纲`、短剧建 `分集大纲`，单篇短篇不额外建大纲；
   - 明确作者开书随口给的一两段书名/简介记进 `update_project(summary=...)` 而非另建设定文件；读 2–5 份已知文件优先一次 `query_files(ids=[...])`；单轮未要求连写多章时最多写完当前 1 章即停下等作者确认；
   - 新增 `### 行文与成稿质量` 六条正面原则：前提忠实与克制补设定、篇幅贴近目标（写前按场景数控体量，±15% 内不反复微调）、语言纯净与标点排版（绝不夹杂英文词与半角引号、问句用 `？`、空行切场不用 `---`/`——`、缩进换行跟随已有章节）、去 AI 套路与自然收尾、起名/指代/连贯承接（绝不用「女主」「庶妹」「男主」作角色名）、跨题材自觉（作者要现实/治愈/文艺/克制风格时彻底服从，不硬套网文口感）。
2. **对齐共享工具协议与题材规范（`apps/server/agent/prompts/base.py`、`apps/server/agent/prompts/__init__.py`）**：
   - `base.py`：更新改稿说明为「改稿直接保存……可在修改卡片上一键撤销，或在历史版本里找回旧稿」；明确 `<file>` 第一行直接从正文第一句（剧本从 `1-1`）开始，不重复写章节标题，示例保持无全角缩进的格式中性；提醒 `create_file` / `write_chapter` 与 `update_project` 不在同一次回复里并列调用；鼓励 `edit_file(..., continue_on_error=true)` 批量提交、对白与动作/心里盘算交错推进（不设固定比例）。
   - `prompts/__init__.py`：消除 `_WEB_FICTION_VOICE` 与 `GENRE_NORMS["short"]` 的段落口径冲突（改为「段落轻快精炼，每段一两句、对白独立成段」），强调网文口感仅适用于网文/短剧题材、绝不覆盖作者显式要求的非网文风格，并维持 `own` 与 `_WEB_FICTION_VOICE` 各 `<= 8` 条的上限约束。
3. **收紧写后自查续跑说明防循环微调（`apps/server/agent/loop/engine.py`、`loop/prompts.py`）**：
   - `_BODY_SAVED_CONTEXT` / `_BODY_SAVED_REPLIED_CONTEXT`（含英文版）与 `LOOP_WORKING_RULES` 统一要求：直接对照上方刚写完的正文做一次快速自查，**禁止为自查再调 `query_files` 重读刚写完的章节**；仅对确凿的事实、连贯、错字或外文词硬伤调用**至多一次** `edit_file`（合并进单个 `edits` 列表并传 `continue_on_error=true`），完成后立即停手回复作者，绝不为标点微调、凑字数或润色措辞连续多次编辑。

## Alternatives considered

- **在服务端增加正则后处理或自动质检打回**（检测英文词、问号误作句号、否定对举密度或超字数后强制返工）：确定性高，可拦截显性格式瑕疵。但违背仓库语义判断边界原则（不以关键词/正则/字数启发式驱动代理返工），且不同题材/译名/外文作品误报高，还会成倍增加模型调用与等待时间。本轮仅用正面提示原则约束，将指标统计留给离线基准评测。
- **在自查提示里同时要求逐字查标点（如问号误作句号）**：初版实验（`tuned_v3`）确实把该项放进了 `_BODY_SAVED_CONTEXT`，结果实测诱发模型反复重读正文并连续调用 6 次 `edit_file`，耗时翻倍至 327.6 秒。将其留在写作阶段 `行文与成稿质量`、把写后自查严格收口在事实/连贯/错字/外文词硬伤且禁止重读与二次编辑后（`tuned_v4`），`novel_ch3_continuation` 耗时由 327.6 秒降至 130.3 秒、`edit_file` 从 6 次降为 1 次，同时盲评与质量保持领先。
- **直接把旧 `WRITER_PROMPT` 全文照搬进 `loop/prompts.py`**：改动看似省事，但旧提示绑定了多角色分工（planner / writer / reviewer / 快速模式）、含已废弃的审阅表述且冗长，会与单循环职责和前缀预算冲突。改以精炼的六条「行文与成稿质量」重写，增量控制在约 2.3k 字符以内。

## Consequences

- **提示体量与缓存影响**：静态系统提示文件字符变化为 `base.py` +574、`prompts/__init__.py` +217、`loop/prompts.py` +1573（合计约 +2.36k 字符，约 +1.6k–2.1k token），`engine.py` 续跑常量合计 +838 字符（中英文共 4 段）。系统提示仍保持完全静态，发布后活跃会话前缀缓存一次性失效一次，随后跨轮照常命中。
- **staging `MiniMax-M3.1-Flash` 真实端到端评测（`baseline_v0` vs 最终 `tuned_v4`）**：
  - 全部 7 组用例完成且 0 轮次失忆；`edit_batch_and_author_edits` 单次 `edit_file` 完成三处改稿并保留 `<author_edits>`（38.2s，0 错误）；`short_revenge` 2027 字（目标 2000 字，不建多余大纲，0 错误）；`novel_emotional_literary` 2235 字（目标 2200 字，116.4s）；`screenplay_ep1` 685 字（目标 700 字，98.2s，0 错误，无「女主/庶妹」占位名，无多余括号堆叠）；`novel_ch3_continuation` 130.3s（0 错误，自查 `edit_file` 收敛为 1 次，未剧透第 4 章）。
  - 位置去偏（正反序各评一次）双盲 A/B 评审结果（`tuned_v4` : `baseline_v0`，含 3 处评委输出截断 JSON 跳过）：
    - `craft_closer_to_ref`：**9 : 2**（tie 0）
    - `page_turner`：**7 : 4**（tie 0）
    - `fidelity_and_format`：**10 : 1**（tie 0）
    - `naturalness_no_ai_slop`：**9 : 2**（tie 0）
- **局限与后续**：提示词对偶发字数浮动（如复杂多节拍续写章仍可能偏长）和极个别外文漏词无法做到 100% 消除，属于模型生成概率边界；后续若需进一步收紧，应继续通过离线多题材样本评测观察，不以服务端正则拦截替代。
- **回归测试**：在 `apps/server/tests/test_agent/test_prose_craft_prompts.py` 新增结构契约断言，覆盖三类目录映射、`分章大纲`/`分集大纲`、行文关键条款、共享 `<file>` 中性示例、无固定比例配额、自查禁重读/单次编辑与 `update_project` 不与待写 `<file>` 并列调用约束。
