# Agent Note: 并行读取过滤后保留正文写入的原始任务下标

关联：[并行结果只报告实际执行项](./2026-10-25-parallel-results-report-what-ran.md)、[范围读取使用独立重复读取键](./2026-10-25-range-reads-own-repeat-read-key.md)。

## Problem

`RepeatReadGuard` 会在执行前从 `parallel_execute.tasks` 删除达到重复读取上限的 `query_files`。执行器随后按过滤后的批次重新编号结果，但 `BodyWriteTracker` 仍按结果位置读取模型原始工具参数。若被删除的读取排在 `edit_file` 或 `write_chapter` 前，成功写入会拿到另一项参数，新增正文被记成 0 字，可能漏掉自动质量检查。

执行器结果里的 `params` 只回显 id、标题等安全定位字段，不包含 `edits` 或 `content`，不能用来计算实际写入字数。

## Decision

- `ReadPlan` 保存过滤后每个任务对应的过滤前原始下标。映射由服务端解析原始任务数组时生成，不读取模型提供的索引字段。
- `RepeatReadGuard._observe_parallel` 把该原始下标作为 `source_index` 写进实际返回的子任务结果；同名字段由服务端映射覆盖。
- `BodyWriteTracker._observe_parallel` 用合法的 `source_index` 回查原始工具参数，以取得完整 `edits` / `content`；旧结果或测试 fixture 没有该字段时继续按结果位置兼容。
- `completed` / `partial` 才计入正文、失败任务排除的既有规则不变。执行器不回显完整正文或编辑参数，工具结果体积和信息暴露边界不变。

## Alternatives considered

- **让执行器回显完整 `params`。** 最强理由是正文计数器可直接使用实际执行参数，不需要索引映射。未采用：`write_chapter.content` 和 `edit_file.edits` 可能很大，扩大工具结果并把正文重复送回模型。
- **使用执行器的过滤后 `index` 回查原始数组。** 最强理由是不增加字段。未采用：该索引从过滤后的批次重新从 0 编号，正是错位来源。
- **在 `BodyWriteTracker` 按类型或文件 id 搜索原始任务。** 最强理由是无需修改守卫结果。未采用：同一文件可有多个同类任务，搜索会产生歧义，也会重新引入推断。

## Consequences

- 重复读取与正文编辑混在一个并行批次时，成功编辑和章节写入按正确的原始参数计字，自动质量检查门得到真实写入量。
- 并行结果增加一个服务端生成的整数 `source_index`；旧的无字段结果保持兼容。
- 回归测试真实执行“直接读取三次 → 并行批次过滤重复读取 → 成功部分编辑 → 正文计数”，修改前因缺少映射失败，修改后通过。
