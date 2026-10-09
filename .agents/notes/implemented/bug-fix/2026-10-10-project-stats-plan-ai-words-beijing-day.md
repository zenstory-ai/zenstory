# Agent Note: 项目统计的完成率按大纲规划总数算，今日字数计入 AI 写入，日期按北京自然日

Status: implemented

关联：`bug-fix/2026-10-09-project-stats-units-and-sources.md`（整本大纲不算章、按类型数章/篇/集）；新用户审计 r4 的 P2-N3-a/b/c/d。

## Problem

2026-10-09 新用户审计（drama-26b、drama-m09、targeted-42）在项目统计页看到四个数字错误：

- **完成率分母**：项目有「分集大纲（1-80集）」，只写了 3 集，统计页显示「剧集进度 3/3 100%」「剧集完成 共3集 100%」「接下来可以做：全部完成！」；长篇总纲规划十几章、写了 4 章也是「4/4 100%」。原因：上一批（units-and-sources）把不带章节号的整本大纲直接跳过，分母只剩已写的正文文件数，大纲里的规划总数没有任何地方读取；前端完全信任这个百分比。
- **今日/本周/本月字数不含 AI 写入**：项目 1,587 字全是 AI 当天写的，统计页「今日 0 / 本周 0 / 本月 0」，健康度「写作活跃度 不活跃」。原因：这三个数只来自编辑器保存后的 `POST /stats/record`（`WritingStats`），agent 写入只建 `file_version`（`change_source='ai'`），从不记 `WritingStats`。
- **「上次写作」差一天**：PDT 10/09（北京已是 10/10）显示「上次写作 2026/10/8」。原因：前端 `new Date('YYYY-MM-DD')` 按 UTC 午夜解析，西半球显示成前一天；另外统计请求的 `client_date` / `stats_date` 用浏览器本地日，后端缺省用 UTC 日，都不是额度用的北京自然日。
- **「当前连续 1 1天」**：卡片先单独渲染数字，紧接着又渲染「{{count}} 天」。

## Decision

- **规划总数（只做加法）**：`get_chapter_completion_stats` 对被跳过的整本大纲（不带章节号、没配上正文）额外只取这几份的 `title/content`，用 `_parse_planned_total` 保守解析：区间「1-80集」「第61–80集」「第1集-第20集」取终点；「全60集」「共一百二十章」；标题开头的全书长度「60集分集大纲」（复用 `_WHOLE_BOOK_COUNT_RE`）；正文**行首**的「第N章/集/回/话」标题取最大值。句中提到的「第80集大结局」不算；超过 2000 的数视为误读。多份大纲取最大值。若没有整本规划、但还有「有细纲没正文」的章，规划数就是 `total_chapters`（这些章已在列表里）。规划数已知时 `planned_total = max(解析值, total_chapters)`，`completion_percentage = completed / planned_total`；未知时 `planned_total = None`，`completion_percentage` 保持旧算法（只为兼容 Vercel 先上线、Railway 晚约 25 分钟期间的旧前端，新前端不再展示它）。`ChapterCompletionResponse` 增加可选 `planned_total`。
- **前端（规划总数）**：`statsUnits.plannedTotal()` 把 null/缺字段（旧服务端）都当未知。已知时章节卡片右上显示「已写 N / 计划 M 集」（N = 已完成 + 写作中），百分比与进度条照旧；健康度显示「完成数/计划数」与「{{percent}}% 已完成」。未知时不显示百分比和进度条，章节卡片显示「已写 N 集」和一行「在分集大纲里写明共几集，这里就会显示完成比例」；健康度显示「已写 N 集」、等级 `neutral`，不再把整体拉到「状态不错」。「接下来可以做」的「全部完成！」只在规划已知且已完成数 ≥ 规划数时出现；规划已知但还有没建的集时显示「按大纲还有 77 集待写」；其余没有待办的情况显示中性的「暂时没有待办，接着写下一段就好」。
- **AI 字数（只在读侧算，不改写入路径、不加列）**：`get_ai_words_written(session, project_id, today)` 一条查询算今日/本周/本月：对本窗口内有 AI 版本的正文类文件（`CONTENT_FILE_TYPES`、未删除，与正文总字数同口径，大纲不算），用 `LAG(word_count) OVER (PARTITION BY file_id ORDER BY version_number)` 取上一版字数，AI 版本计 `max(0, 本版 - 上一版)`，按北京自然日的 UTC 边界（`file_version.created_at` 是 naive UTC）分桶。上一版可能是作者自己的保存或 AI 写入前的系统备份，所以作者打的字（已由 `/stats/record` 计入）只作基线，不会重复算成 AI 字数。`change_source='ai'` 只有 agent 工具会写（`api/files.py`、`api/versions.py` 都把用户写入钉成 `user`）。`ProjectDashboardStatsResponse` 增加 `ai_words_today/this_week/this_month`（缺省 0）；`words_*` 含义不变，仍是作者自己在编辑器写的净字数。
- **前端（AI 字数）**：字数卡大数字与今日/本周/本月三个小项显示合计；有 AI 字数时大数字下面加一行小字「你写 X · AI 写 Y」，没有就不显示。健康度的写作活跃度用合计。写作连续天数的口径和文案都不变（仍只算作者自己写的字）。
- **北京自然日**：`dateUtils.getBeijingDateString()`（`Intl` + `Asia/Shanghai`），`writingStatsApi` 的 `client_date`（统计、字数趋势）与 `stats_date`（记录）都用它；后端三个缺省日期（统计、字数趋势、记录）从 `utcnow().date()` 改为 `beijing_date(utcnow())`。显式传入的日期参数仍被接受。
- **日期显示与重复数字**：`dateUtils.formatCalendarDate()` 把 'YYYY-MM-DD' 按日历日格式化（UTC 构造 + `timeZone: 'UTC'`），任何时区都不偏移；写作连续卡的单位改用新 key `statistics.streak.dayUnit`（只含「天 / day(s)」），`statistics.streak.days` 保留给健康度卡用。

## Alternatives considered

- **把 AI 字数直接并进 `words_today`（写入时记 `WritingStats`）。** 最强理由：前端一个字段都不用改。被否：`WritingStats` 同时驱动写作连续和上次写作，会悄悄改变这两个习惯类指标的含义（本轮明确不改连续天数口径）；还要动 agent 写入路径。读侧计算不改任何写入，也不需要迁移。
- **规划总数用正文里任意位置的「第N集」最大值。** 最强理由：覆盖更多写法。被否：核心大纲常在叙述里写「第80集大结局」「第3集反转」，任意位置取最大会误读；只认行首标题、明确区间和「全/共N集」，取不到就显示「已写 N 集」而不硬算百分比。
- **规划未知时继续显示旧百分比，只把「全部完成」去掉。** 最强理由：改动更小。被否：3/3 = 100% 本身就是审计指出的误导；没有分母就不该有完成比例。
- **全部细纲都写完时也算规划已知（例如细纲 1-3 章、正文 1-3 章 → 3/3 全部完成）。** 被否：AI 常常写一章细纲接一章正文，这种情况下「全部完成」正是审计看到的误导；只有还有没写的细纲时才把 `total_chapters` 当规划数。

## Consequences

- 收益：审计里的短剧显示「已写 3 / 计划 80 集 · 3%」，不再「全部完成」；纯 AI 写作的项目今日字数和活跃度如实反映；「上次写作」在任何时区都是存储的那一天；连续天数数字只出现一次；统计日界与额度一致。
- 代价：没写明规划总数的项目不再显示完成百分比（只显示已写数和一行提示）。细纲全部写完、又没有整本规划时也不显示百分比和「全部完成」。不在中国时区的作者，统计日界从本地午夜变成北京午夜（与额度一致，有意为之）；已有的 `WritingStats` 行按当时浏览器日期记录，不迁移。AI 删改造成的减少按 0 计，所以「AI 写」是 AI 新增的字而不是净值。统计页有 15 秒缓存，AI 写入后最多晚 15 秒出现。展开的「字数趋势」图仍只含作者在编辑器写的字（本轮未改）。旧版本快照的 `word_count` 若用旧计数规则算出，与正文缓存可能有少量差异。

## Verification

- pytest：`tests/test_api/test_project_stats_chapter_units.py`（新增 9 条：标题区间 80、审计分集大纲正文 80、总纲行首中文章号 12、标题/正文写法参数化含句中「第80集」与超大数不算、无规划为 None 且保留旧百分比、未写细纲作规划、规划数不低于已写数；原短剧用例改为 `planned_total=60`、5%），新文件 `tests/test_services/test_writing_stats_ai_words.py`（AI 新建+编辑、作者版本作基线、缩写记 0、跨日基线、北京日界 16:00Z、大纲/删除/他项目排除、周月窗口、HTTP 接口返回 `ai_words_*` 且无 `client_date` 时按北京日、记录无 `stats_date` 时记北京日）。实现前这些用例都失败。统计相关 155 个用例通过（含 `test_chapter_completion.py`、`test_project_stats_integrity.py`、`test_stats_record_transaction.py` 等；`test_project_stats_integrity.py` 的建表清单补了 `file_version`，精确字典断言补 `planned_total: None`）。真实审计大纲（drama-r3 分集大纲 / 核心大纲、serial-r2、drama-r2 等 5 份）解析结果为 80/80/190/100/60/60，与大纲内容一致。
- vitest：`ProjectDashboardCards.test.tsx`（规划已知显示 3/80 与 3%、77 集待写；规划未知或旧服务端缺字段时没有 100%、进度条和「全部完成」；全部写完才「全部完成」；未知规划不把健康度拉到 good；AI 字数合计与「你写 · AI 写」；只有 AI 写时活跃度为今日；旧服务端无 `ai_words_*` 时回退作者字数；连续天数数字只出现一次；`TZ=America/Los_Angeles` 下「上次写作」显示存储日），`dateUtils.test.ts`（北京日 16:00Z 翻日、PDT 傍晚已是次日、日历日格式化不偏移），`writingStatsApi.test.ts`（三处默认日期用北京日）。
