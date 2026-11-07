# Agent Note: token 审计后的低风险批次与快照成本日志

Status: implemented

关联：[上下文快照](../architecture/2026-10-21-context-snapshot-and-cache-stable-tools.md)、[锚点失败不提前熔断](2026-11-04-anchor-misses-do-not-trip-early.md)、[强度评估](../process/2026-10-19-reasoning-effort-evaluation.md)、[主备切换](../architecture/2026-10-20-llm-primary-backup-failover.md)。

## Problem

一次生产用量审计发现请求字节在 run 内确实只在尾部追加，没有破坏前缀的顺序或时间戳问题；可改进的地方集中在几处小的多余 token 和判断依据缺失：

- 快照多数 run 都因 `outgrown` 重建，但日志不记录过时内容的比例。调高 `REBUILD_CHANGED_RATIO` 会把旧副本留在此后每次调用里，MiniMax 的命中价又只有未命中的 1/10，模型估算结果可能是亏的；没有生产数字就无法在调阈值和调整布局之间取舍。
- 同 agent 续跑时 SDK 输入和工具结果都保留，交接消息却每次再附一份完整文件清单，写 N 个文件就有 N 份。
- 审稿人的系统指令已说明 `parallel_execute` 不可用，但它想批量读文件时仍会调用并被拒绝，每次白花一轮模型调用。指令没告诉它该怎么批量读。
- 多处注释把命中价写成未命中的 1/50，这只对 DeepSeek 成立；`model.py` 称 MiniMax 要求回放 reasoning，与 `llm_providers.py` 和实际行为不符；`runner.py` 称强度取舍未评估，实际 DeepSeek 上有对照评估，MiniMax 只有[少量耗时样本](2026-10-21-primary-model-slow-turns.md)、没有质量结论。

## Decision

1. `turn_snapshot._stale_accounting` 给「Context snapshot rebuilt / reused」日志增加 `previous_snapshot_chars`、`live_chars`、`dead_chars`、`stale_chars`、`changed_chars`、`gone_chars`、`changed_chars_by_section`。过时占比 = `dead_chars` / (`live_chars` + `dead_chars`)；`stale_chars` 是重建判定的分子（含本轮新内容），不是过时占比。首次构建没有旧快照，不带这些字段；统计出错只丢字段，不影响这一轮。只记分区名和长度：文档 key 可能退回标题，不写入日志。不改变送给模型的内容和重建判定。
2. `continue_native_run` 为真时，`writing_graph` 不再查询和附加 `[当前项目文件清单]`。runner 只在上一轮完整结束时才保留 SDK 输入，没有保留时该标志为假，清单照发。
3. `REVIEWER_TOOLS_NOTE` 增加一句：读多份文件时在同一条回复里并列发出多个 `query_files`。工具权限不变。
4. 修正上述注释。价格事实以 `services/usage/pricing.py` 为准。

## Alternatives considered

- **直接调高快照重建阈值。** 最强理由是改一个常量就能让首次调用多命中。被否：省下的未命中可能被此后每次调用重发的旧副本抵消，先用这次的日志量出过时占比再决定。
- **把 `parallel_execute` 加进审稿人的工具集。** 最强理由是直接解决被拒。被否：`parallel_execute` 含写入子任务，加入后 `_agent_can_write_files` 会把审稿人判定为可写，破坏只读约束。
- **anchor_not_found 时返回最接近的片段。** 最强理由是审计中这类失败最多。被否：错误里已经带候选片段，也已有批内失效提示和近似匹配（见 2026-10-26、2026-11-04 两份记录），这里没有简单的增量改动；继续改进前要先分析剩余失败的具体形态。
- **同 run 重复读取从第 2 次起返回占位内容。** 最强理由是立刻去掉重复全文。被否：真正冗余的读取只占很小一部分，而 trimmer 可能已折叠先前结果，模型会读不到需要的内容，风险大于收益。
- **设置 `parallel_tool_calls` 或加强提示词要求并列读取。** 最强理由是合并读取步骤能减少调用。被否：模型未设该参数时已会在一条回复里发多个工具调用，MiniMax 文档也没有列出它；提示词已有并列读取的规则，再加强没有低成本的验证方式。

## Consequences

- 收益：几天的生产日志就能回答快照该不该调整以及怎么调整；同 agent 续跑少一次查库和一份清单；审稿人的被拒调用预计减少。
- 代价：续跑期间作者新建的文件不会出现在清单里，模型要到下一次换 agent 或下一轮请求才看到。这与 run 内原本就看不到外部改动一致。
- 代价：提示词改动没有做真实模型对照，效果要看上线后 `tool_not_permitted_for_role` 的数量。
- 节省有限：这批不改变主要成本结构（思考量、上下文体量、重度使用）。
