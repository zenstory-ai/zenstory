# Bug-fix Note: 并行子任务的写入进入本轮回执

Status: implemented

关联：[正常收尾也给出写入回执](../feature/2026-10-18-completed-round-write-receipt.md)、[部分交付的保存回执](2026-10-14-partial-delivery-saved-receipts.md)、[并行结果报告实际执行了什么](2026-10-25-parallel-results-report-what-ran.md)。前两份记录写的是“并行写章列不出文件，沿用计费口径”，本文修订这一事实，不改它们的其余决定。

## Problem

`RoundWrittenFiles._observe_tool_result` 只认工具结果顶层的 `data.id`。`parallel_execute` 的结果是 `{execution_id, tasks: [...]}`，没有顶层 id，于是整批子任务都被跳过：

- 只靠并行子任务写成的一轮，正常收尾时 `completion_receipt` 返回空，`done` 不带回执，`stop_outcome` 也不落库，刷新后看不到这一轮写了什么。计费照常（`StreamBillingTracker` 已展开子任务），所以这不是“没写”或“免费”。
- 先直接 `create_file` 建空壳、再用并行 `edit_file` 写入正文时，空壳仍被记为“未确认保存”，一轮实际完成的回合被标成 incomplete。

## Decision

- `parallel_execute` 的整批 `tool_result` 按子任务展开（`RoundWrittenFiles._observe_parallel_tasks`），只认有证据的写入：
  - 子任务状态是 `completed` 或 `partial`（与计费同一口径）；
  - 类型是 `write_chapter` 或 `edit_file`；
  - 内层结果 `mutation_applied is True`，且能取到内层 `data.id`；
  - `write_chapter` 命中复用（`reused_existing`）的不列。
- 失败、no_change、复用、退化成截断标记、拿不到 id 的子任务都不列，不按标题或下标去猜文件。
- 子任务正文在结果里可能被截断，字数无从确认：并行写章的条目不报 `words`；`edit_file` 条目沿用“edit 不报字数”。`partial` 状态或 `partial_success` 时条目带 `partial: true`。
- 同一批里先登记写入，再处理成功的 `delete_file` 子任务（目标 id 取自 params 回显）；之后直接删除同样移除。按 file_id 去重，与直接写入合并。
- 正常、停止、断线共用这份回执：`done` 与落库的 `stop_outcome` 来自同一个 `RoundWrittenFiles`。计费、退款、`parallel_executor`、API 层和前端都不改。

## Alternatives considered

- **按子任务 `params` 回显的标题直接列文件。** 最强理由是不依赖内层结果，截断时也能列。未采用：标题不唯一，失败或复用的子任务也有标题，会把没写成的东西列成“已保存”。
- **回执沿用计费口径（只看子任务状态）。** 最强理由是两套口径一致、实现最简单。未采用：`completed` 包含复用和 no_change，作者会看到没有改动的文件被列为已保存；回执比计费更严是允许的。
- **同时给 `parallel_task_end` 帧加 `file_id/title`，覆盖批次中途截停。** 最强理由是停止、断线、墙钟超时发生在批次执行中时，整批 `tool_result` 根本不会发出，本次修复覆盖不到。未采用于本批：需要改事件合同和执行器，超时回合里这类情况占多少尚无统计；列为延期项（A1b），本批不宣称覆盖全部超时回合。

## Consequences

- 只靠并行子任务写成的一轮，作者在当场和刷新后都能看到写了哪些文件、哪个只部分生效；并行写入空壳不再被误报为未确认保存。
- 并行写章不显示字数，比直接写入的回执信息少一项。
- 批次执行中被截停的一轮仍只显示“已写入的内容已保存”，不列文件（延期）。
- 旧消息和旧形态的子任务结果（不带内层 `result`）照旧不列文件。
