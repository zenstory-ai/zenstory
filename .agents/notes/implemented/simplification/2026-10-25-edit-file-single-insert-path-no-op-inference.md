# Agent Note: edit_file 只有一条插入路径，缺 op 时报错而不是猜

## Problem

`FileEditor` 里 `_apply_insert_after` 和 `_apply_insert_before` 各约 190 行，逐字匹配、模糊匹配、近似匹配、段落兜底四层几乎逐行相同，只差插入点取锚点的头还是尾。F-07/F-15（段落兜底点名第 2 处要报越界）这类修复每次都要改两遍，漏改一边就会出现两个方向行为不一致。replace、delete 和两个插入方向还各自复制了一份「模糊候选不唯一就中止、按 occurrence 选第 N 处」的代码。

另外，edit 没写 `op` 时，`_edit_file_impl` 会按字段猜：有 old+new 当 replace，只有 old 当 delete，有 anchor+text 时看 `position`/`where` 里有没有 before/after/前/后 决定插入方向，只有 text 时再按 head/tail 等字样决定 append 还是 prepend。这段从初始 commit 就在，没有事故 note 驱动；它是按关键字推断模型意图，猜错时（例如 `position: "before the storm"`）会把文本插到另一侧或另一端，并照样报成功。`EDIT_FILE_TOOL` 的 schema 本来就把 `op` 列为必填。

## Decision

- 两个方向合并成 `_apply_insert(..., after: bool)`：四层匹配链、错误文案、`applied_edits` 详情字段（含 `fuzzy_paragraph` 的 confidence/fallback）都不变；方向只决定插入点取匹配的尾还是头，以及模糊扩边时扩哪一侧（锚点收尾的「。」、开头的「“」照旧跟着走）。
- 新增 `_select_fuzzy_span`，replace、delete、insert 共用：多处候选且没给 occurrence 时用各自原来的文案中止，occurrence 越界报 `occurrence out of range (1..N)`。错误分类不变（越界原来有的抛 `ValueError`，有的抛 `EditFileError`，两者都归为 `invalid_edit`）。
- 删除按字段推断 op 的分支。op 解析后仍为空时：整条 edit 为空照旧忽略并留告警；否则报既有的 `missing op` 错误，整批回滚、不建版本，模型带上 op 重发。
- 保留：显式 op 别名（`after`、`insert-before`、`operation`/`action`/`type` 键等）、old/new/text 字段别名（[2026-10-08 静默损坏 note](../bug-fix/2026-10-08-agent-edit-file-silent-corruption.md)）、`exact_only`、`_require_insert_text`、occurrence 与歧义错误、默认整批原子编辑、版本并发。

## Alternatives considered

- **保留 op 推断，只把 position 关键字收窄**：模型偶尔漏写 op 时省一轮重试。被否：这是按字面猜作者/模型意图，owner 明确不要；猜错的代价是正文插错位置还报成功，而缺 op 的报错只多一次便宜的工具调用，schema 也早已要求 op。
- **只合并插入两个方向，不碰 replace/delete 的候选选择**：改动面更小。被否：三处是同一段逻辑，只差文案，抽成一个带文案参数的函数后行为与文案逐字不变，以后修 occurrence 规则只改一处。

## Consequences

- `edit.py` 少约 200 行；插入相关的修复只改一处，两个方向不会再分叉。
- 漏写 op 的调用不再生效，模型会收到 `missing op` 错误并重发；以前被猜对的那部分调用要多一轮。
- 直接调用 `_apply_insert_after/_before` 的测试改为 `_apply_insert(after=...)`；`test_author_changes` 里依赖「无 op 被猜成 replace」的参数改用 `operation` 别名，未读文件只逐字匹配这一点继续覆盖。

部分反转：只有 old + new、没写 op 的编辑现在按 replace 执行，见 [2026-10-29](../bug-fix/2026-10-29-edit-old-new-without-op-is-replace.md)。
