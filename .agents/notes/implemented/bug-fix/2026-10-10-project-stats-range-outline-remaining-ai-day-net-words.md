# Agent Note: 统计页同页数字对齐——区间大纲不当第 1 集、未开始按规划数、AI 协作按北京日、AI 字数取净值

Status: implemented

关联：`bug-fix/2026-10-10-project-stats-plan-ai-words-beijing-day.md`（PR165，规划总数、AI 字数、北京自然日）。本 note **推翻**其中两条口径：「AI 删改造成的减少按 0 计」和「`change_source='ai'` 只有 agent 工具会写（AI 版本只认这一个来源）」。其余口径（规划总数解析、作者字数、连续天数只算作者亲手写的字）不变。

## Problem

r4 生产审计（drama / serial / short / target 四个新项目）在同一个统计页看到互相矛盾的数字：

- **区间标题的分集大纲被当成第 1 集**：大纲规划师建的「1-30集分集大纲」开头是区间，`_extract_chapter_number` 的「标题开头数字」规则把它读成 1，于是它和「第1集」配成一章，不再是整本规划，`planned_total` 变成 None，剧集卡显示「已写 1 集 / 在分集大纲里写明共几集…」，列表行标题是「1-30集分集大纲 685 字」。「第1集-第30集分集大纲」会先被序号正则读成第 1 集，问题相同（PR165 note 已把这种写法列为规划区间）。
- **「0 集还没开始」与「按大纲还有 77 集待写」同屏**：`not_started_chapters` 仍按已有行数 `total_chapters` 算，而 PR165 已把完成率分母改成 `planned_total`。
- **AI 协作「今日」按 UTC 日切**：`get_ai_usage_summary` 用 `datetime.combine(day, min)` 拼 naive UTC 午夜，缺省日期是 UTC 当天；北京 0–8 点发的消息算到「昨天」（r4drama：3 条消息今日 0、本周 3），与额度（`beijing_day_bounds`）和统计页其他数字的北京日不一致。
- **当天新建的项目今日字数和总字数差 7–16 字**：作者字数是净值，AI 字数却是逐版本正增量（AI 缩写记 0，r4serial 多 7 字）；编辑器 AI 审阅「应用更改」保存为 `change_type='ai_edit'`、`change_source='user'`，不走 `/stats/record`，AI 侧又只认 `change_source='ai'`，这部分两边都没算（r4short 少 16 字）。两处差值由字数推算，版本行未直接取到，属于高可信推断。
- **连续天数 0 天与「今日写作 1922 字」并列没有说明**：连续天数只算作者在编辑器亲手写的字（既定口径），说明文案只在健康度卡的描述里，而手机端健康度卡不显示描述，连续卡 0 天时只有「0 天 • 尚未开始」。

## Decision

- **区间标题（`_extract_chapter_number`）**：新增 `_LEADING_RANGE_RE`，标题（strip 后）以「N-M集/章/回/话」「第N集-第M集」开头且 N < M 时返回 None。判断放在序号正则**之前**，这样两种写法都覆盖；不含区间的「第12章 第一节课」「第1集」「1-2 初遇」（终点后没有单位）不受影响，「第1-2章」本来就是 None。规划数仍由 `_parse_planned_total` 取区间终点，多份区间大纲取最大值（1-30/31-60/61-80 → 80）。
- **未开始数**：`not_started_chapters = completion_base - completed - in_progress`，规划已知时按 `planned_total`（含还没建文件的规划集），未知时与以前相同。前端只有 `ChapterCompletionCard` 读这个字段，不改前端，旧前端也没有部署时间差问题。
- **AI 协作北京日**：把 `get_ai_words_written` 里的局部函数提成模块级 `_beijing_day_start_utc(day)`，`get_ai_usage_summary` 的今日 / 本周 / 本月边界都用它；缺省日期改为 `beijing_date(utcnow())`。`api/stats.py` 已按北京日传 `reference_date`，接口行为只在边界计算上变化。`get_ai_usage_trend` 只有测试调用，本次不改。
- **AI 字数取净值并计入审阅接受**（推翻 PR165 两条口径）：每个 AI 版本贡献「本版字数 − 上一版字数」（可为负），每个周期求和后 `max(0, ·)`，与作者净字数同口径；AI 版本 = `change_source='ai'` **或** `change_type='ai_edit'`，与 `author_edit_guard` 把 ai_edit 版本视为 AI 文本的既定语义一致。agent 工具写的 ai_edit 版本本来就是 ai 来源，`or_` 不会重复计入；`edit`/`user` 版本仍只作基线。
- **连续天数说明**：口径不变。`statistics.projectHealth.indicators.streakInactive` 改为「在编辑器里亲手写满 10 个字开始计天数（AI 写的不算）」/「Type 10 words yourself in the editor to start a streak (AI-written words don't count)」；`WritingStreakCard` 在 `current_streak === 0` 时于「当前连续」下加一行 text-xs 复用这个 key（不新增 key），手机和桌面都能看到。

## Alternatives considered

- **只在「标题开头数字」规则前加区间判断。** 最强理由：改动位置最贴近 :219 的误读。被否：「第1集-第30集分集大纲」会先被序号正则读成第 1 集，覆盖不到。
- **把区间大纲交给前端识别或让 agent 写 `metadata.chapter_number`。** 被否：agent 工具不写这个字段，前端只展示后端数；责任在后端的标题解析。
- **AI 字数保持「新增」口径，只把审阅接受补进来。** 最强理由：「AI 写」在大幅缩写那天不会变小。被否：作者侧是净值，同页「今日字数」与「总字数」在当天新建项目上对不上正是审计看到的误导；周期合计最低 0，不会出现负数。
- **AI 审阅接受走 `/stats/record` 算成作者字数。** 被否：那是 AI 生成的文本，计入作者会改变连续天数和「上次写作」，与 `author_edit_guard` 的语义冲突。
- **只改连续天数文案。** 被否：手机端健康度卡不显示描述，连续卡 0 天没有说明，390 宽下用户仍看不到解释。

## Consequences

- 收益：「1-30集分集大纲」类项目显示「已写 1 / 计划 30 集 · 3%」，列表只有「第1集」；未开始数与「还有 N 集待写」一致；AI 协作今日与额度、统计页其他数字同按北京日；当天新建、经历 AI 缩写或审阅接受的项目，今日字数等于总字数；连续天数为 0 时在连续卡上说明口径。
- 代价 / 风险：中文数字区间（「第一集-第三十集」）仍不识别。新规则对所有文件类型生效：正文标题若是「1-2集」这类多集合稿，不再按章号 1 配对，但仍作为未认领正文计一章；「1-10章细纲」这类分批细纲现在也按规划算（计划至少到 M，与「第1-2章」原本就不配对、PR165 把区间当规划一致），只有分批细纲、正文多于 M 章时计划数取 `max(规划, 已有章数)`，以前这种项目显示「计划未知」。`not_started_chapters` 的含义变成「规划里还没开始的」，包含未建文件的集。「AI 写」从新增字数变为净增字数，大幅缩写那天会变小（周期最低 0）。作者进入 AI 审阅前若有未保存的手打内容，会随 ai_edit 版本算成 AI 字数（边缘情况）。外部 Agent API（`api/agent_api.py`）的内容更新同样存为 `ai_edit`/`user`，现在也计入 AI 字数（以前两边都不计）；它新建文件用 `create`，仍不计。`PUT /files/{id}` 的 `change_type` 由客户端声明，任何客户端都可以把自己的保存标成 `ai_edit`，从而让统计页把手打字数算成 AI 字数；来源仍钉死为 `user`，额度和计费不受影响，只影响统计页展示。user 消息的 `created_at` 晚于发送时刻，跨北京零点的一轮仍可能与额度差 1 条（时间戳问题，未改）。健康度卡桌面描述有 `truncate`，新文案较长可能显示省略号，完整说明在同页连续卡上。桌面总览在连续 0 天时健康度卡描述与连续卡会出现同一句说明（重复但无害，待 staging 浏览器目视确认）。连续卡文案「亲手写满 10 个字」是近似说法，实际规则是 `words_added + words_deleted ≥ 10`，删字也计入。
- 剩余（本次不改）：Editor 冲突审阅保存（`edit`/`user`，直接调 `fileApi.update`）的字数仍两边都不计；restore 回滚不进任何字数统计；`get_ai_usage_trend` 仍按 UTC；手机健康度「写作活跃度」只有文字没有数值。已核实不会重复计数：审阅应用后 `SimpleEditor` 退出审阅模式时把 `lastSavedContentRef` 同步为审阅后的内容（`SimpleEditor.tsx` 退出审阅模式的 effect），下一次自动保存不会把 AI 文本再算成作者字数。
- 统计接口有 15 秒缓存，线上回归需等缓存过期。无迁移，不改 worker。

## Verification

- pytest：`tests/test_api/test_project_stats_chapter_units.py`（标题解析参数化新增 7 条：五种区间标题为 None、「第1-2章」None、「1-2 初遇」仍为 1；「1-30集分集大纲」与「第1集-第30集分集大纲」+「第1集」→ 计划 30、1 行、3%、未开始 29；三份区间大纲 → 80、未开始 77；原 80 集用例补未开始 77，无规划用例未开始仍为 1）。`tests/test_services/test_writing_stats_ai_words.py`（缩写取净值 1000→400 记 400、只有缩写的一天记 0；ai_edit/user 审阅接受计入；只有审阅接受的文件也计入；r4short 复现链 6422→8243→8267(ai_edit)→8259→8338 + 番外 3030 = 两文件字数之和）。`tests/test_services/test_writing_stats_ai_usage_tokens.py` 新增北京日界用例（23:45Z、00:05Z 算 10-10，15:59Z 不算；缺省日期按北京日）。以下现有测试按北京日边界同步修正：`test_writing_stats_summary_queries.py` 的独立预期改用北京午夜、`test_ai_usage_metrics.py` 传 `reference_date=beijing_date(test_day)`（原先在 UTC 16–24 点运行会随时刻失败）、`test_project_stats_integrity.py` 的 client_date 参数化用例把消息放在北京零点两侧。新增与修正的用例在旧实现上 22 条失败，实现后全部通过；本地后端全量 5504 passed。
- vitest：`ProjectDashboardCards.test.tsx` 新增连续 0 天显示 streakInactive、大于 0 天不显示；ProjectDashboard 25 条通过。zh/en `dashboard.json` JSON 合法；`build:typecheck` 通过。
