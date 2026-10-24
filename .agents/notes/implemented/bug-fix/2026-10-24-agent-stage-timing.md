# Agent 慢请求阶段耗时

## Problem

已有 `first_output_ms` 是首个实质产出，不是 HTTP 首字节。已有 `llm_duration_ms` 是各 SDK run 的墙钟时间总和，包含工具与消费等待，不能当成模型净耗时。线上脱敏复盘显示部分请求等待较久，但这些总量不足以区分上下文、路由、模型、工具、正文落库及规划/创作/审稿。用户要求先加最小定位点，不修改质量、预算或重试策略。

## Decision

扩展现有 `AgentRunMeter`，仍通过 `service.run_report` 汇入唯一的 `Agent stream billing evaluated` 日志，复用 request_id / agent_run_id；不新增日志流、数据库、观测平台、依赖或 LLM 调用。原字段含义、计费和终态不变。

- `run_stage_timings` 固定阶段：prepare_context、router、model、tool、file_save，以及 agent_planner / agent_hook_designer / agent_writer / agent_quality_reviewer。每项仅 count、total_ms、max_ms、error_count、interrupted_count。未运行的阶段不出现。
- prepare_context 从 process_stream 入口至工作流开始（包含会话、历史、上下文与工具初始化）；早期失败/取消在已有 finally 收尾。路由关闭或可信状态直接恢复时不虚构 router 调用。
- model 使用既有 SDK on_llm_start/on_llm_end 测量每次调用生命周期，排除调用之间的工具时间，包含 SDK/provider/failover/流消费等待；不是供应商推理时间，也不是逐 token 性能。未收到 end 的调用按 error 或 interrupted 封口，不伪造 usage。
- tool 仅围绕权限/守卫通过后的实际项目工具执行；正常返回表示调用返回，不保证业务成功。守卫拒绝不当成实际工具启动。file_save 是流式落库等待；失败与取消仍计时，不改变超时后的原有后台语义。
- `run_first_tool_started_ms` 为首个真实工具执行起点；`run_first_body_persisted_ms` 为首个观察到非空 draft/script 正文实际保存/变更成功的时刻。空文件、非正文、无变化、拒绝/失败都不是成功证据。流式写入由保存结果确认；edit/parallel 使用已有 BodyWriteTracker 的成功结果、逐项 mutation_applied=true 和非空字数；只覆盖已有 writer / planned_body_delivery 观察范围，其他未规划角色的工具正文可能保守漏记，不为观测改变送审流程。未观察到成功为 null，不证明绝未写入（例如超时后线程可能仍完成）。
- 诊断时间原点为 service 入口，独立于原 wrap-up 预算原点。角色耗时包含模型、工具、流消费；阶段会嵌套/重叠，不可相加当请求总时长。
- 仅新增固定分类与数值，不带用户正文、模型原文、工具参数、原始异常、作品/文件/用户标识。已有 request_id 关联保持不变；本批不扩大到全仓旧日志清理。

## Alternatives considered

- 新建追踪框架或逐工具日志：可以提供更细的跨度，但本次只需定位慢在哪个阶段，且已有日志限流证据。复用一个请求结算行可避免增加日志条数。
- 降低推理强度、缩短预算或自动重试：可能改变耗时，但没有阶段证据且会影响质量、成本和授权边界，拒绝。
- 用 TOOL_USE/文件创建事件作为首工具/首正文：接线简单，但前者并非真实执行，后者可能只是空文件，拒绝这种失真口径。

## Consequences

可用同一 request_id 对照阶段总量、最大单次和首个动作/保存，先缩小慢点；本改动不是延迟优化，也不证明历史慢请求根因。各阶段 error 指异常，业务失败返回与 SDK 流中终止由原 stop_reason / billing_reason / usage 一同解释。进程被强杀或日志平台丢弃时仍可能没有结算行。独立审查中用 no-op 编辑与混合批量任务复现了潜在假首写：仅 status=success 或批量顶层 mutation=true 不足够，已改为逐正文产物核验 mutation_applied=true，不改变原质检算法。工具启动也在参数解析成功、handler 存在且即将执行后才计时，不把格式拒绝当动作。不调用计费模型、不重放用户作品；本地使用合成回归、必要后端回归与独立审查。实际线上阶段分布待后续获授权发布并观察，当前不打 tag、不发布生产。
