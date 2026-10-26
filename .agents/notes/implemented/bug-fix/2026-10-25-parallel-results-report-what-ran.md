# Bug-fix Note: parallel_execute 只报告实际执行了什么

Status: implemented

关联：[审稿交接与 parallel 面包屑配对](2026-10-08-agent-review-gating-and-handoff-details.md)（本 note 取代其中「parallel 面包屑配对」一条的游标配对做法）、[并行读取的参数转发与结果截断](2026-10-08-agent-read-path-no-history-rewrite.md)。审计编号 F-09（A-B8）、F-20（A-B7）。

## Problem

- **F-09**：下一轮的面包屑和跨轮工作集从落库的 `arguments.tasks`（模型原始请求）出发，再拿结果去配对。一次请求超过 5 个子任务时，尾部任务根本没执行，只出现在 `dropped_tasks` 里，但它们照样被写成「已删除文件」面包屑；被「删除」的文件还会从工作集里拿掉。10-08 为读取守卫加的游标配对（类型一致才配对、找不到 id 的读取视为被拦下）只处理了被拦下的读取，处理不了截断。另外，没有落下结果的工具调用（回合中途断开，`status=pending`）被当成成功。
- **F-20**：`continue_on_error=true` 的内层 `edit_file` 返回 `partial` 时，子任务被记成 `completed`，`all_completed=True`、`any_failed=False`，界面显示完成。直接调用 `edit_file` 时 `_derive_edit_status` 已经消除了这种假成功，并行分支又把它带回来了。

## Decision

- 执行器在 `data.tasks[]` 每项回显 `index`（在本批里的下标）和 `params`。`params` 只含目标：`id` / `file_id` / `title`，不回显正文、`edits` 这类大字段。
- `SessionLoader._extract_parallel_file_actions` 只看结果里的 `tasks`，只记 `status` 为 `completed` 或 `partial` 的子任务，目标取回显的 `params`。被截断的任务只在 `dropped_tasks` 里，被守卫拦下的读取没进执行器，两者都不在 `tasks` 里，自然不记。删掉游标、`dropped_reads` 名额和 `_result_mentions_id` 配对。
- 旧结果没有回显 `params`：只有任务一个不少、类型逐个对上时才按下标取落库的原始参数；对不上就只用子任务结果里自带的 id（`edit_file` 结果带 id，`delete_file` 结果是 `true`，记不到）。
- 顶层 `status=pending` 的调用标 `outcome_unknown`：面包屑写「删除文件（结果未知）」这类说法，结果未知的删除不把文件移出工作集。
- 内层 `edit_file` 返回 `partial` 时子任务记成 `partial`：计入 `any_failed` 和新的 `partial` 计数，不计入 `all_completed`；`parallel_task_end` 带 `partial`，`parallel_end` 多一个 `partial` 字段（默认 0）。已生效的部分照旧算写入：`mutation_applied` 聚合、计费产出（`stream_billing`）、正文写入统计（`body_writes`）、重复读取清零都把 `partial` 当成已落地，与之前被误记成 `completed` 时口径一致，退款政策不变。熔断器与直接调用的 `partial` 一致，不计失败。
- 前端先能接受 `partial`：子任务行显示「部分修改没有生效」，标题与收尾句带部分完成数；不认识的状态显示「结果未知」，不再一律当成完成。旧服务端只发 `completed` / `failed`，显示不变。

## Alternatives considered

- **保留游标配对，再加上 dropped_tasks 的下标排除**。最强理由：不改执行器输出，旧数据新数据同一条路径。未采用：配对依赖「守卫只剔除读取、截断只剔除尾部」两条隐含假设，每多一种改写就要再补一条启发式；结果里直接回显目标更简单，也不会配错。
- **回显完整参数**。最强理由：下游（例如正文字数统计）也能直接用。未采用：`write_chapter` 的整章正文会在结果里重复一遍，占掉每个任务的结果预算；面包屑只需要目标。
- **partial 直接记成 failed**。最强理由：前端和熔断器都不用认新状态。未采用：部分修改已经落地，记成失败会让计费、正文统计和重复读取清零都误以为没写，模型也会以为要整个重做。

## Consequences

- 收益：面包屑和工作集只记真的做过的事，截断的删除不会被说成已删除；部分生效的并行编辑不再显示完成，模型会补做失败项。
- 代价：`parallel_execute` 结果多了 `index`、`params`、`partial`，tool schema 不变，但结果形状变了；旧会话里截断过、又没有回显的批次，部分子任务的面包屑会缺失（宁缺不错）。前端显示 `partial` 要等 Vercel 先上线；Railway 先上而前端未更新时，`partial` 子任务按旧逻辑显示成完成，不会报错。

## Verification

`tests/test_agent/test_agent_routing_continuity.py`（截断尾部、守卫拦下的读取、pending 与 partial 子任务、旧格式配对、顶层 pending 面包屑）、`tests/test_agent/test_session_loader.py::TestCrossTurnWorkingSet::test_unexecuted_or_unknown_deletes_keep_files_in_the_working_set`、`tests/test_agent/test_parallel_executor.py`（回显与 partial）、熔断器 / 计费产出 / 正文统计 / 重复读取的 partial 用例；前端 `ToolResultCard`、`useChatStreaming`、`agentRoundProgress` 的 vitest。
