# Agent Note: 已进入模型执行的对话轮次按轮计费，仅未恢复的上游模型报错终止退还额度

Status: implemented

本 note 推翻以下历史决策中“按是否有 60 字回复或文件写入（`produced_output` / `write_succeeded` / `ended_on_reply`）决定停止、断线、墙钟超时、发布交接与失控护栏是否退还 AI 消息额度”的计费规则（服务端优雅停止协议、残稿补存、文件版本保护、退还轮空白占位清理与 `stop_outcome` 历史回执机制保持不变）：

- [2026-10-05-agent-stream-error-and-refund-contract.md](2026-10-05-agent-stream-error-and-refund-contract.md)
- [2026-10-09-agent-graceful-stop-and-no-output-refund.md](2026-10-09-agent-graceful-stop-and-no-output-refund.md)
- [2026-10-09-stop-output-definition-and-round-outcome.md](2026-10-09-stop-output-definition-and-round-outcome.md)
- [2026-10-21-deploy-drain-ends-running-generations.md](2026-10-21-deploy-drain-ends-running-generations.md)

同批还落定了上线后巡检确认的四项有界修复（A-1 非标识符工具名脱敏与回放规范化、A-2 墙钟历史附注事实区分、A-3 精确编辑候选片段与批量约束提示、A-4 子阶段纯数字计时日志）。

## Problem

生产巡检与真实长篇会话记录暴露了旧“无实质产出即退还”规则的结构性漏洞与产品口径偏差：

1. **只读核对、纯思考与墙钟超时形成无成本重度消耗通道。** 长篇多章一致性核对（如单轮 14 次 `query_files`、6 次模型调用累计近 700 秒推理并在 780 秒墙钟硬超时结束）因未改文件、回复不足 60 字而被判 `produced_output=false` 自动退还 AI 消息额度。同样，作者发起请求后在思考阶段或历史压缩阶段主动点击停止、关闭标签页，或触发 `no_progress` / 模型调用上限 / 输出截断护栏时，即使已消耗数十万上下文 token 与多次供应商调用，仍可无限次退回免费或套餐消息条数。
2. **错误码与 `refundable` 标志混同了“上游模型服务故障”与“非模型侧停止”。** `classify_stream_exception` 会将工具或数据库层的 `TimeoutError` / `ConnectionError` 映射为 `ERR_AGENT_UPSTREAM_UNAVAILABLE`，墙钟超时、发布交接、输出截断与重复循环也默认携带 `refundable=True`；仅凭 SSE `error` 帧的错误码或 `refundable` 字段无法区分真正的上游模型报错与平台时限/业务护栏。
3. **前端离开与停止文案仍在承诺“没写出内容就不计入”。** `LeaveWhileGeneratingDialog`、`ChatPanel`（以 `charged && producedOutput` 控制停止说明）及多语言文案与“进入模型执行即计一轮”的新产品口径冲突。

## Decision

### 1. 消息额度结算边界（`model_call_guard.py`、`round_outcome.py`、`model.py`、`runner.py`、`stream_adapter.py`、`stream_billing.py`、`api/agent.py`）

- **唯一“已进入模型执行”判据（`RunOutcome.provider_attempted`）**：在所有异步 OpenAI 兼容客户端共用的 `services/usage/model_call_guard.py:install_async_cost_guard.create` 中，于 `reserve_model_call` 成本预检通过、`bind_call_start` 及 `original_with_options(max_retries=0)` 客户端克隆选定 `transport` 完成之后、紧贴 `await transport(*args, **kwargs)` 发起之前置 `run_outcome.provider_attempted = True`（覆盖免费账户 `reservation != None` 与付费账户 `reservation == None`，同时覆盖轮前历史压缩/压缩重试与主循环 `FailoverChatModel`；本地客户端克隆抛错仍算未发起请求）。`run_outcome` 经 `utils/request_context.py` 的 `ContextVar` 向同一请求派生的子任务共享，并在流结束时按 token 重置；生产计费绝不从 SSE 帧猜测模型是否已启动。
- **唯一“因模型报错终止”判据（`RunOutcome.terminal_model_failure`）**：仅当共同 `model_call_guard` 在真实 `await transport` 或 `_BudgetedAsyncStream` 流式迭代抛出正向核验通过的上游服务/网络/响应流异常（`is_upstream_provider_error_type`：`openai.APIError`、`httpx.HTTPError`、`httpx2.HTTPError`、`TimeoutError`、`ConnectionError`，排除平台 `APIException` 与取消异常，且不递归 `__context__` 以免把此前已恢复的异常残留当成当前故障）并打上 `_zenstory_provider_transport_error = True` 可信标记后，该异常在 `FailoverChatModel` 完成全部允许的主备切换尝试后仍向上逃出（含已输出大段正文后在流中抛出的 `APIError`，因不能切备重答而直接上抛），经 `runner.py` 标记 `error_source="model"` 发出 `StreamEventType.ERROR` 并由 `StreamAdapter` 提交整轮致命终止时，才置 `run_outcome.terminal_model_failure = True`。SDK `ModelBehaviorError`、普通本地 `RuntimeError`/`TypeError`/`ValueError` 以及在 `model_call_guard` 之外由 SDK 本地构造的同类型异常均不具备可信 transport 标记，默认按已执行的非服务错误计费；主备切换成功或 `compaction.py` 捕获跳过的压缩异常绝不遗留失败标记。
- **结算优先级（`StreamBillingTracker.decide`）**：
  1. 终止帧（`done` / 终态 `error`）已产生后的断线或墙钟到点不改写原结束方式（沿用既有竞态归一）。
  2. `not provider_attempted`（请求在进入任何供应商 transport 前被停止、断线、到时、预检拒绝、成本预检拦截或客户端克隆报错终止）：**一律退还预扣额度**（`user_stopped_before_model` / `client_disconnected_before_model` / `run_deadline_exceeded` / `server_restarting` / `pre_model_error`）。鉴权、限流、会话忙与额度预检在预扣前失败的请求照旧不产生预扣。
  3. `provider_attempted == True` 且 `terminal_model_failure == True`（未恢复的上游供应商服务/网络/流错误导致整轮终止，无论是否已流出部分正文）：**退还预扣额度**（`upstream_model_error`）。
  4. `provider_attempted == True` 且 `terminal_model_failure == False`：无论正常完成、纯读取/核对、只思考、简短旁白、600s 软着陆、780s 墙钟硬超时、发布交接提前时限、作者停止、客户端断线，还是 SDK `ModelBehaviorError`、工具失败熔断 / `no_progress` / `repetition_loop` / 模型调用上限 / 输出截断 / 非模型内部异常，**一律计 1 轮 AI 消息额度，不退还**。
- **单次结算、停止先结算后发 `done` 与空白占位如实回执**：`api/agent.py:_settle_billing` 维持 `settlement is None` 门闩与 `asyncio.shield` / 后台退还任务；在 `StreamStoppedByUser` 连接仍开着的路径上先按增量游标同步补记迟到写工具结果并完成真实 `await _settle_billing()`，仅在 `refund_applied == True` 且满足 `assistant_message_id` / `keep_file_ids` / 版本未变安全条件时软删本轮空白占位文件，再用真实 `removed_ids` 构造 `stopped_delivery` 发出 `done(charged=not refund_applied)` 并在 `refund_applied == True` 时补发 `quota_refunded`；已计费轮及因保留/改名/退款未生效而未删除的新建空壳保留在库并在 `done.unwritten_files` 与 `stop_outcome.unwritten_files` 如实列出，不将 `charged` 当作 `saved_output`。结构化日志 `charged` 字段同样取真实 `not refund_applied`（后台待定为 `None`），账户消息额度与免费客户端 IP 共享网络额度仍通过同一 `agent_run_id` `reservation_id` 在单事务内扣减且至多退还一次；`llm_usage_event` 与每日成本兜底账本按真实供应商调用独立记账。

### 2. 前端与用户指南同步（`LeaveWhileGeneratingDialog.tsx`、`ChatPanel.tsx`、`useAgentStream.ts`、`agentApi.ts`、`chat.json`、`docs/`）

- 移除 `LeaveWhileGeneratingDialog`、`chat.json` 与中英文用户指南中“没有实质产出就不计入”的旧承诺，同步为仅在请求尚未开始模型处理或因未恢复的模型服务错误终止整轮且实际退还时提示未计入，已开始处理的只读、停止与超时均计入今日消息。
- `ChatPanel` 停止说明优先取服务端真实回执 `completionMeta?.charged`，仅在兼容缺少该字段的旧服务端响应时回退旧估算 `charged && (completionMeta?.producedOutput ?? roundProgressRef.current.producedOutput)`（不单独用客户端启动即置真的 `charged` 兜底）；仅当服务端真正发回 `quota_refunded(kind="stopped")` 时展示“尚未进入模型处理，不计入今日 AI 消息”。保留 `producedOutput` 用于停止后不自动触发 `/agent/suggest`。

### 3. 同批四项有界稳定性修复（A-1 ～ A-4）

- **A-1**：按 OpenAI `FunctionDefinition.name` 协议（`^[a-zA-Z0-9_-]{1,64}$`）校验工具名；合法名称（含已知与未知工具、数字开头、连字符与大小写名称）保持原样，混入 `<file>` 正文或非法字符的名称在当轮事件、错误回喂、同轮后续请求的 `_ModelCallCountingFilter` 输入过滤及跨轮 `history.py` 回放中统一映射为带输入摘要后缀的确定性合法标识（`invalid_tool_name_<12hex>`），并在 `history._tool_output_text` 回放非法工具名历史行时将旧 `error` 替换为固定通用说明，彻底阻断原稿回显与同轮/跨轮污染，同时保留 `call_id` 1:1 配对及不同异常名称的独立失败计数。
- **A-2**：`runner.py` 在 600s 软着陆且无截断时写入显式结构化标记 `wall_clock_landing="soft"`，`api/agent.py` 硬超时取消写入 `"hard"`；`history.py` 对显式软着陆（无显式未完文件）回放客观提醒收尾说明（不推定任务已完成），对硬截断或存在未完/回滚文件保留“没做完”说明，对缺少软/硬标记的存量行回放中性“因时间限制结束”，不改动 `stop_outcome` 完成枚举。
- **A-3**：`edit.py` 在 `match_mode == "exact"` 定位失败分支补齐只读 `suggest_similar_lines` 候选片段（不自动模糊应用，整批失败照旧原子回滚）；`mcp_tools._attach_query_matches` 在无 `id` 关键词列表查询的既有单处前后 30 字预算内优先对齐换行/句末边界并附 `matches_note` 逐字匹配提示；工具参数说明明确禁止混入无变更 `old == new` 项，独立不重叠修改可同批提交，仅依赖或重叠修改才拆批。
- **A-4**：`_prepare_loop_turn` 拆分纯数字毫秒字段（`history_load_ms`、`turn_context_ms`、`author_edits_ms`、`skill_catalog_ms`、`system_prompt_ms`），`_edit_file_impl` 拆分 `author_blame_ms` 与 `commit_ms`，均不记录任何作者正文。

## Alternatives considered

- **保留停止/断线无产出退还，仅将 780s 墙钟超时改为计费。** 最强理由：误触发送后几秒内停止的作者不会损失免费条数。被否：一旦模型开始推理或压缩历史，作者在看到完整思路、大纲推理或读取核对过程后点击停止/关页，仍可零额度反复白用模型算力，与用户明确的“只要不是模型报错导致结束都应计费、消除无限利用漏洞”政策直接冲突；未进入模型前的停止仍保留退还，已覆盖预检与排队阶段。
- **按 SSE `error` 帧的 `refundable` 或错误码字符串推断是否为模型报错。** 最强理由：无需从 `FailoverChatModel` / `runner` 向下传递错误来源。被否：`classify_stream_exception` 会把工具/数据库 `TimeoutError`、平台配置异常及墙钟超时均标为 `refundable=True` 或上游类错误码，单靠表层错误码会把非模型故障误退还。
- **为兼容旧纯帧测试，在 `StreamBillingTracker` 中用 SSE 事件反推模型是否启动。** 最强理由：旧 `_FakeService` 测试无需接 fake transport。被否：生产代码多出一条与真实 transport 脱节的猜测路径，且无法覆盖“已过成本预检发起请求、但首 token 到达前即被停止”的真实计费场景；在唯一共享的 `install_async_cost_guard` 记账更准确。

## Consequences

- **收益**：彻底堵住通过纯读核对、看思考后停止/断线、反复触发运行护栏或拖满 780s 超时来免费消耗模型 token 的漏洞；真实上游模型故障（主备均失败终止）与未进入模型执行的请求仍如实退还；前后端文案、实时停止回执与刷新后 `RoundEndNote` 完全一致。
- **代价**：模型请求一旦发出（即使尚未收到首 token 或仅完成思考/读取），作者主动停止、离开页面、撞上 780s 时限或遭遇非模型侧内部错误都会消耗本轮 AI 消息额度。旧测试中不经过 transport guard 的桩服务需显式声明 `provider_attempted` 或接入受保护的模拟客户端。
