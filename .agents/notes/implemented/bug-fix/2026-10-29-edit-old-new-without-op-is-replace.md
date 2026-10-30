# Agent Note: edit_file 只有 old + new、没写 op 时按 replace 执行

## Problem

[2026-10-25 的决定](../simplification/2026-10-25-edit-file-single-insert-path-no-op-inference.md) 删掉了缺 `op` 时按字段推断编辑类型的分支，预计「只多一次便宜的工具调用」。[2026-10-26](2026-10-26-tool-edit-recovery-feedback.md) 又在 schema 里加了带 `op` 的最小示例。线上（MiniMax M3.1 Flash 主用）的结果：

- 10-26 至 10-29 共 141 次 edit_file 因 `missing op` 整批回滚，此前每天 0–1 次；缺 op 的编辑项里 419 条都是 `{old, new}` 形状，其余形状合计 7 条。
- schema 示例上线（10-27）后没有下降。同一轮里模型前几次都带 op，后面又漏掉，提示词与 schema 的 `required` 管不住。
- 约 80% 的失败下一次调用就带上 op 改成功，但每次多一次 10–60 秒的模型调用；出现过缺 op 的 113 轮里 26 轮撞上墙钟时限、8 轮以工具失败熔断结束。

## Decision

- op 缺失、编辑项有字符串 `old` 和字符串 `new`、且没有 `anchor` / `text` 字段时，按 `replace` 执行，并在结果 `warnings` 里写 `Edit N: op omitted; old+new applied as op=replace`，提示模型下次带上 op。
- 其余缺 op 的组合照旧报 `missing op`、整批回滚：只有 `old`（可能是 delete，也可能漏了 new）、`anchor` + `text`、只有 `text`、或 old/new 与 anchor/text 混在一起。
- 不恢复按 `position` / `where` 里 before/after/head/tail 等字样决定插入方向或首尾的推断；那部分属于按字面猜意图，10-25 的删除保持不变。
- 工具 schema 不改：仍写 op 必填，不告诉模型可以省略，也不改变工具清单的缓存前缀。
- 匹配、原子回滚、`exact_only`、版本并发与错误分类不变。`graph/body_writes.py` 与 `stream_adapter.py` 原本就把缺 op 当 replace 计字数和展示，现在与执行一致。

## Alternatives considered

- **保持缺 op 一律报错，继续加强提示或错误文案。** 最强理由是协议最严格、模型终究会学会带 op。被否：10-26 的 schema 示例已经试过，线上频率没降；MiniMax 不强制 schema 的 `required`，这类遗漏是间歇性的，每次都要作者为多出的一轮模型调用付时间。
- **恢复 10-25 之前的全部推断。** 最强理由是少写代码、所有漏 op 都不用重试。被否：只有 old 时 delete 与「漏了 new」无法区分，position 字样推断会把文本插错侧还报成功；这两种猜错会改坏正文，而 `{old, new}` 只对应一种操作。
- **在 runner 或工具适配层统一补 op 字段。** 最强理由是所有调用方看到同样规范化后的参数。被否：编辑器本来就负责 op 别名与字段别名的规范化，放在同一处更容易保证与执行、计字一致，也不需要新的层。

## Consequences

- `{old, new}` 形状的漏 op 编辑直接生效，省掉一次重试的模型调用，减少熔断和墙钟超时。
- 结果多一条告警，模型能看到自己漏了 op；作者界面不变。
- 其余缺 op 形状的安全失败保留；以后若出现别的高频漏写形状，要按字段是否只对应一种操作单独判断，不能借此恢复关键词推断。
