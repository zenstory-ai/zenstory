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
