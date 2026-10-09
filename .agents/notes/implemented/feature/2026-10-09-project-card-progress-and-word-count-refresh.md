# Agent Note: 正文字数随每次写入刷新；作品卡片显示写作进度

Status: implemented

## Problem

1. 正文类文件（draft / script）的字数缓存 `file_metadata.word_count` 只在作者保存和上传时更新。AI 的新建、编辑、流式写入、恢复版本都只改 `content`，缓存停在旧值；`writing_stats_service.get_total_word_count` 又优先相信缓存，项目统计页的总字数因此会错。
2. 作品列表卡片只有标题、简介、类型和更新时间，看不出写到哪了；拿到框架还没开写的作品也没有任何提示（复盘中这类作者回访最少）。

## Decision

- `models/file_model.py`：`before_update` 事件里，正文类文件只要 `content` 有改动就按编辑器口径（`utils.text_metrics.count_words`）重算 `word_count` 并写 `word_count_rev = 2`。`cached_word_count()` 只信任带当前 rev 的缓存；`writing_stats_service.resolve_prose_word_counts()` 统一做「读缓存，旧口径的加载 `content` 重算并回写、提交一次」，总字数、章节完成度（两处）与进度接口共用，原来三份重复的回填代码与 `_read/_set_word_count_in_file_metadata`、`_parse_non_negative_int` 删除。新建文件不在插入时盖章，首次读取时按需重算。只覆盖 ORM 写入；仓库里唯一的 Core `update(File)` 只做软删除，不改内容。
- `GET /api/v1/projects/progress`（`services/project_progress.py`，注册在 `/projects/{project_id}` 之前，可选 `project_id` 只看一个作品）：当前用户每个作品的 `written_units`（内容非空的正文类文件数）、`word_count`、`framework_ready`（大纲/角色/设定已有、正文还没有；聊天里的「写第一章」按钮也用它）。
- 首页「近期项目」与「我的项目」卡片在页脚上方显示一行进度：长篇「已写 12 章 · 3.2万 字」、剧本「已写 3 集 · …」、短篇「已写 …」（数字用 `Intl.NumberFormat` 的 compact 格式），框架已就绪但未开写时「框架已就绪，可以开写了」；什么都没有时不显示。请求失败时卡片照旧，不显示进度。

## Alternatives considered

- **在每个 AI 写入路径里各自更新字数。** 最强理由：显式，看调用点就知道哪里会改缓存。被否：写入路径有 create/edit/流式保存/Agent API/版本恢复多处，以后新加路径很容易再漏；ORM 事件一处兜住。
- **一次性数据迁移重算所有正文的字数。** 最强理由：上线后缓存立即全对，读取不用再兜底。被否：需要在发布时对生产全表跑数据迁移；按 rev 懒重算只在第一次读取时多加载一次正文，效果相同，风险更小。
- **卡片显示「第 N 章」（取最大章节号）。** 最强理由：更像作者口中的「写到第几章」。被否：章节号只能从标题或 `order` 推断，标题没写「第 N 章」时 `order` 是任意值，会显示错的数字；非空正文文件数是可验证的事实。
- **卡片右上角放进度。** 最强理由：一眼可见。被否：右上角已有删除按钮，手机和平板上常显；放在页脚上方一行不挤占交互区域。

## Consequences

- 收益：AI 写过的正文字数准确，统计页和卡片口径一致；作者在列表里就能看到每部作品的进度，框架已就绪的作品有一句开写提示。
- 代价：正文每次内容更新多一次字数统计（与编辑器同一个正则，开销很小）；上线后每个作者第一次打开列表会为旧缓存加载一次正文并回写。「已写 N 章」把分节、番外也算作一章。

## Verification

- 后端：`.venv/bin/pytest tests/test_models/test_file_model.py tests/test_api/test_projects_progress.py -q --no-cov`：内容改动后字数随之刷新、旧口径缓存不被信任、只改标题或非正文文件不动元数据；进度接口忽略旧缓存的错误字数、只返回自己的作品、区分「已开写」「框架已就绪」「空」。相关的 26 个统计、文件、版本、快照测试文件全部通过。
- 前端：`pnpm exec vitest run src/pages/__tests__/DashboardHome.test.tsx src/pages/__tests__/DashboardProjects.test.tsx`。
