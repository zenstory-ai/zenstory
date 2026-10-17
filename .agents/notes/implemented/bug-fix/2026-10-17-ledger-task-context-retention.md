# Agent Note: 用量账本后台任务只继承日志关联上下文

Status: implemented

## Problem

`schedule_llm_usage_record` 使用 `asyncio.create_task(asyncio.to_thread(...))` 将用量账本写入移出请求主路径，但 `create_task` 和 `Task.add_done_callback` 默认都会复制调用时的完整 `contextvars.Context`。写作请求的 `ToolContext` 包含正文快照、项目上下文等大对象；数据库写入尚未结束时，即使请求侧已经 `ToolContext.clear_context()`，任务或完成回调保存的旧上下文仍会继续持有这些对象。

实测阻塞账本写入时，8 MB 的正文快照在请求清理后仍然存活。只给 task 一个空 Context 仍不能释放，因为 done callback 也会复制原请求上下文。该滞留不会改变账本正确性，但会延长大对象生命周期并抬高 API 进程的并发内存峰值。

## Decision

`services/usage/llm_usage_service.py` 在调度前先冻结 token、归属、模型和 `call_started_at` 时间戳，再分别为后台 task 与 done callback 创建两个独立的轻量 `contextvars.Context`。每个轻量上下文只通过现有 `utils.request_context.get_log_context` 和 `bind_request_context` 携带 `request_id`、`trace_id`、`agent_run_id`，不继承 `ToolContext`、成本预留上下文或请求内的其他 ContextVar。

task 和 callback 必须使用不同的 Context 对象，避免共享可变执行状态，也确保二者各自保留异常日志关联。模块级 `_pending_records` 强引用、异常消费、非阻塞调度与 `drain_pending_usage_records` 的 shutdown 收尾语义保持不变。

回归覆盖阻塞写入时的大快照释放、task 的非阻塞与 pending/drain 生命周期、线程内和 callback 异常日志关联、计费归属与 model/token 参数，以及跨高峰/低谷和北京时间日界线时仍使用模型调用起始时刻。

## Alternatives considered

- **只给 `create_task` 传空 Context。** 最强理由是改动最小，也能阻止工作线程继承 `ToolContext`。未采用，因为 `add_done_callback` 默认仍在注册时复制完整请求上下文；诊断复现证明快照仍被 callback 持有，而且空 Context 会丢失账本线程的日志关联 ID。
- **调度前全局清空 `ToolContext` 或修改快照持有者。** 最强理由是能从根源断开大对象引用。未采用，因为同一请求后续 agent/tool 路径仍依赖这些状态，全局清理会破坏文件并发保护、账本归属和工具行为；问题只存在于后台任务的继承边界，不应改变请求上下文本身。
- **在主请求内同步等待账本写入。** 最强理由是任务不再跨越请求生命周期。未采用，因为建议、路由和模型 hooks 明确要求账本失败或延迟不能阻塞首 token、模型轮次或接口超时预算。
- **完全不传上下文。** 最强理由是最彻底地隔离内存。未采用，因为后台线程和完成回调的错误需要保留 request/trace/agent run 关联，便于排查漏记与数据库故障；三个短字符串不会带来有意义的内存压力。

## Consequences

- 收益：在途账本写入不再延长正文快照及其他请求级重对象的生命周期，并发模型调用下可降低 API 进程峰值和滞留内存。
- 收益：账本线程与完成回调的错误日志仍包含 request、trace、agent run 三个关联 ID。
- 不变：模型、token、用户/项目/来源归属、调用起始时间、价格档位和北京日归属不变；账本仍为 fire-and-forget，进程关闭仍会 drain 当前事件循环的任务。
- 代价：每次调度创建两个很小的 Context，并绑定最多三个日志字符串；相较数据库线程任务成本可忽略。
- 边界：此修复只消除账本后台任务造成的 ContextVar 引用滞留，不代表 API 的全部长期内存增长都已解决，也不直接等价于 Railway 账单降幅。
