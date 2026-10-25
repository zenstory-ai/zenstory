# Agent Note: edit_file 的匹配守卫：工作量上限、拒绝歧义匹配、精确与模糊分支同一套唯一性规则

Status: implemented

## Problem

`edit_file` 靠定位片段来改章节。模糊匹配在 2 万字章节上曾把 asyncio 事件循环阻塞约 113 秒（守卫漏算一个因子后又实测 21.93 秒），拖死同进程的 SSE 流；约 75% 相似的错误段落会被静默覆盖；精确分支的 insert/replace/delete 命中多处时只改第一处并忽略 `occurrence`。任何一条都意味着用户正文被无声改坏。

## Decision

`agent/tools/file_ops/text_matching.py`：`find_approximate_match` 的工作量 `window_count × len(norm_content) × pattern_len` 超过 `MAX_APPROX_WORK`（5,000,000）时返回 None，落到"请提供更长、唯一的片段"错误；扫描先收集所有达到阈值的窗口，扫完后再取最佳与不重叠的次佳，若次佳与最佳近乎并列则拒绝自动应用（歧义守卫）。`edit.py` 的精确 insert_after / insert_before / replace / delete must 应用与模糊路径相同的唯一性与 `occurrence` 守卫。PostgreSQL 上加载目标文件时取 `SELECT ... FOR UPDATE` 行锁，让 `parallel_execute` 对同一文件的并发编辑串行；SQLite 退回 `session.get`。模糊匹配是否 offload 到线程的判据与数据库类型无关。

来源：689ae3b (#14)、ea85c83

## Alternatives considered

- **用 `max(best_score, min_similarity)` 做 quick_ratio 剪枝**（689ae3b 首版）。最强理由：剪枝更激进、更快。被否：它在记录之前就剪掉了近乎并列的次佳窗口，歧义守卫形同虚设，且结果依赖扫描顺序；同一 PR 的对抗复审换成扫描后再比较。

## Consequences

- 收益：单次 edit_file 的 CPU 上限可预期（实测 20k 字章节约 4ms）；歧义时宁可报错让模型给更长片段，也不覆盖错段。
- 代价：超大章节上模糊匹配直接不可用，模型必须提供更长片段；行锁只在 PostgreSQL 生效，SQLite 开发环境复现不了并发丢更新。

## Verification

`cd apps/server && .venv/bin/pytest tests -k "text_matching or edit_file or parallel" -q`；ea85c83 以 git worktree 逐条验证过拆掉守卫后测试变红。

### 补充（2026-10-25，审计 F-15）

insert_after / insert_before 的第三级兜底（`find_unique_line_span` 的 `fuzzy_paragraph`）以前不看 `occurrence`：长文里锚点有错字、模型点名 occurrence=2 时，照样插到唯一那一段。现在与近似匹配一样，occurrence 不是 1 就报 `occurrence out of range (1..1)`，正文不动。回归 `test_round3_edit_match.py::test_fuzzy_paragraph_fallback_rejects_occurrence_beyond_one`（两个方向，改动前都插入成功）。

### 补充（2026-10-25，审计 F-25）

`find_unique_line_span`（段落兜底）和 `suggest_similar_lines`（失败时的候选片段）以前按 `\n\n` 切段，只用单个换行分段的文件整篇被当成一段：兜底永远失败，候选片段是整篇开头 160 字。现在按任意连续换行切分（`_paragraph_blocks`），两种分段的文件都逐段比较；`\n\n` 分段、段内又有单换行的文件改为逐行比较，跨行的锚点只能靠前面的 exact / 模糊 / 近似匹配。候选片段加相似度下限 0.3（`_SUGGEST_MIN_SCORE`），与 old/anchor 无关的段落不再列出，全部低于下限时候选为空。`test_edit_file_semantics.py` 里一条用例的 old 原本与正文只有约 0.24 的相似度，靠「没有下限」才拿到候选，已改成相似度过下限的 old，用例要验证的「候选片段可能含已回滚的 new」不变。回归在 `test_round3_edit_match.py`（单换行文件的兜底插入与候选、无关段落不列出），改动前都失败。
