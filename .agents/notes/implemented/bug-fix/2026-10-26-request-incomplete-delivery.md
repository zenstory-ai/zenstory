# Bug Fix Note: 请求未完成事实不随空占位撤回而丢失

Status: implemented

关联：[正常收尾写入回执](../feature/2026-10-18-completed-round-write-receipt.md)、[停止 / 断线终态](../architecture/2026-10-09-stop-output-definition-and-round-outcome.md)。

## Problem

工作流明确发出 `iteration_exhausted(layer=empty_file_correction)` 后，可能把本轮新建但未写成的空占位全部安全撤回。原实现同时从交付清单移除了这些文件，API 又只在 `unwritten_files` 非空时记录 `incomplete`，于是交付清单变成空字典后，这轮会被误记为 `completed`。真实质量模式运行还暴露了另一条同类路径：writer 已保存返修稿，后续 run 发出结构化 `iteration_exhausted(layer=tool_call, limit=wall_clock)` 后直接 `done`，历史却同时保留 `stop_reason=run_deadline_exceeded` 和 `stop_outcome.reason=completed`。此外，实时 `done` 只携带可列名文件，不能表达并行写章或删除等已确认但无法列名的保存事实，可能与刷新后的 `stop_outcome.saved_output` 不一致。

## Decision

- `RoundWrittenFiles` 的 `known_incomplete` 合并两类结构化事实：`empty_file_correction` 是请求级粘性事实，撤回空占位或后续选择角色都不清除；`tool_call` / `collaboration` 是待定的 run 耗尽事实，若之后出现合法 `agent_selected`，说明编排层已接住并继续运行，清除这一待定事实，否则保留到终态。
- API 另存独立 `known_incomplete` 布尔。正常 `done` 若出现上述事实，或实际交付清单明确含 `unwritten_files`，就按既有 `incomplete` 终态落库；即使交付字典为空也照常记录。
- `SSEStreamPump` 在源帧入队前观察请求事实。若 `done` 已由 producer 产生、但客户端在该帧出队前断开，API 在 `finally` 仍按源端 `done` 重新计算 delivery，并以源端结构化事实记录 `incomplete`；不能把“消费端尚未调用 `_with_delivery`”当作 completed 证据。
- 未完成的实时 `done` 增加可选 `delivery_incomplete: true`，并使用既有 `_saved_output(delivery)` 口径增加 `saved_output`。可列名写入、不可列名写入和未确认正文保存仍使用原来的真实执行/保存依据。
- 作者停止、普通错误和墙钟时限的优先级不变；计费、退还、`file_mutated`、保存回执与版本并发规则不变。正常完成、只读请求和作者明确要求的合法空文件不增加这两个字段。

## Alternatives considered

- **继续以 `partial_delivery` 字典真假判断未完成。** 最强理由是改动最少。未采用：全部空占位撤回后字典必然为空，正是本缺陷的根因。
- **把撤回的空占位继续留在 `unwritten_files`。** 最强理由是无需新增请求级事实。未采用：文件已经不存在，向作者列为仍待写会制造错误恢复目标，并公开内部清理过程。
- **从回复措辞、目标章节数或运行时长推断未完成。** 最强理由是能覆盖更多看似未交付的轮次。未采用：这些都不是可靠交付事实，会把正常阶段性交付和只读讨论误判为失败。
- **所有 run 耗尽一旦出现就永久记为未完成。** 最强理由是状态机最简单。未采用：工作流可以在一个 agent run 耗尽后合法选择下一个角色继续；后续 `agent_selected` 是编排层已经恢复的结构化证据，不能污染最终终态。
- **新增 SSE 类型或数据库字段。** 最强理由是协议更显式。未采用：既有 `done`、`incomplete` 与 `stop_outcome` 已能承载事实；扩展现有可选字段兼容性更好、范围更小。

## Consequences

- 全部临时空占位撤回后，实时与刷新仍一致显示本轮未完成，不再生成矛盾的 completed 回执。
- `tool_call` / `collaboration` 耗尽后若直接结束，已有产物仍如实保留为已保存，但本轮不会再误记为 completed；若编排层随后选择新角色继续，正常完成不受污染。
- `done` 已产生但尚未送达时断线，也会留下同一份 incomplete 历史终态；不会另发终止帧或重复退款。
- 本轮已有 edit、并行写章或删除等确认写入时，实时和历史都能保留 `saved_output=true`；没有确认保存时不会声称已保存。
- 旧客户端会忽略新增可选字段；旧历史仍读取既有 `stop_outcome.reason` 与 `saved_output`。
- `known_incomplete` 是请求内存事实，不新增表、迁移、模型调用或状态系统。
