# 以强类型 `AgentRequestContext` 与 SDK `RunContextWrapper` 取代全局 `ToolContext`

## Problem

单循环 agent 的工具层、SDK 钩子、外层编排器与流式适配器长期依赖 `agent/tools/tool_context.py` 中的全局 `ToolContext` 服务定位器（底层为 `_tool_context_var: ContextVar[dict[str, Any]]`、`_owned_session_var: ContextVar[Session | None]` 以及 `SESSION_ISOLATION_KEY` 哨兵）。这套设计带来四类持续性的架构与并发隐患：

1. **隐式状态耦合**：`mcp_tools`、`tools_adapter`、`usage_hooks`、`runner`、`repeat_read_guard`、`loop/engine`、`stream_adapter`、`empty_files` 与 `task_board_completion` 之间通过无类型 dict 隐式读写 20+ 个键；缺少静态类型边界，新调用方遗漏初始化或拼错键名时只能在运行期才暴露。
2. **跨线程与子任务的 DB Session 生命周期脆弱**：每次工具调用都会通过 `asyncio.to_thread` 卸载到工作线程。为了避免工作线程继承并关闭事件循环侧懒建的 SQLAlchemy `Session`，旧实现被迫在每个异步包装器里手写 `_run_sync_tool_with_owned_session_cleanup`、`_owned_session_var` 和 `SESSION_ISOLATION_KEY` 三层防线来补救 `ContextVar` 复制语义。
3. **偏离 OpenAI Agents SDK 原生上下文通道**：SDK 原生通过 `Runner.run_streamed(..., context=...)` 向 `FunctionTool.on_invoke_tool`、`RunHooks`、`call_model_input_filter` 和 `tool_use_behavior` 注入 `RunContextWrapper[T]`（以及 `ToolContext[T]`），但旧代码始终不传 `context`，绕回全局 `ContextVar` 取值。
4. **测试与并行隔离成本高**：每个单测都必须在 `try/finally` 中手动调用 `ToolContext.set_context` / `clear_context`，一旦漏清就会污染后续测试，且多个并发流或工具任务间只能依靠脆弱的可变容器引用共享状态。

## Decision

以强类型 dataclass `AgentRequestContext` + OpenAI Agents SDK 原生 `RunContextWrapper[AgentRequestContext]` + 每工具短生命周期 `session_scope()` 彻底取代全局 `ToolContext`：

1. **删除全局服务定位器与线程 Session 补丁**：
   - 删除 `_tool_context_var`、`_owned_session_var`、`SESSION_ISOLATION_KEY`、`ToolContext` 类以及 `mcp_tools._run_sync_tool_with_owned_session_cleanup`。
   - 保留并发安全的 `_current_tool_call_id_var`（仅作单次工具调用内的轻量 `tool_call_scope` 兜底，同时 `tools_adapter` 也显式将 `tool_call_id` 传给 `edit_file`）。
2. **强类型 `AgentRequestContext`（`agent/tools/tool_context.py`，并由 `agent/core/request_context.py` 导出）**：
   - 以显式字段持有每请求状态：`project_id`、`user_id`、`session_id`、`create_session_func`、可选的调用方注入 `session`（单测路径）、`author_message`、`author_steering`、`current_agent`、`notes_append_allowed`、`pending_empty_file_state`（`_PendingEmptyFileState`）、`read_snapshots`（`_ReadSnapshots`）、`tool_ui_payloads`（`_ToolUiPayloadStore`）等。
   - **每工具短生命周期 DB Session**：提供 `with req_ctx.session_scope() as session:` 与 `req_ctx.executor(session)`。当提供 `create_session_func`（生产路径）时，优先在每次工作单元内新建并在 `finally` 中确定性关闭独立 `Session`，彻底消除跨 `asyncio.to_thread` 共享懒建 Session 的问题；仅在未提供 `create_session_func` 而显式注入 `session`（单测路径）时复用调用方 `Session`（不关闭，异常时回滚）。
   - 提供 `unwrap_request_context(source)` 统一解包 `AgentRequestContext` 或 SDK `RunContextWrapper` / `ToolContext`。
3. **全链路显式透传 + SDK 原生 `RunContextWrapper[AgentRequestContext]`**：
   - `AgentService.process_stream` 为每轮请求实例化唯一一份 `AgentRequestContext`，写入 `WritingState["request_context"]` 并传入 `StreamAdapterConfig.request_context`。
   - `runner.run_openai_agents_streaming_agent` 将 `context=req_ctx` 传给 `Runner.run_streamed(...)`；`FunctionTool.on_invoke_tool`（`tools_adapter.py`）、`RunHooks`（`usage_hooks.py`）、`call_model_input_filter`（`runner.py`）与 `tool_use_behavior`（`_stop_run_after_tools`）全部直接从 SDK 注入的 `RunContextWrapper[AgentRequestContext]` 解包 `req_ctx`。
   - `mcp_tools.py`、`file_ops/`、`permissions.py`、`empty_files.py`、`task_board_completion.py`、`loop/run_support.py`、`loop/engine.py`、`repeat_read_guard.py` 与 `stream_adapter.py` 全部以显式 `request_context` 参数通信。
   - 保留 `FailoverChatModel`（`openai_agents/model.py`）与请求上下文零耦合的设计：逾期未写 `<file>` 正文的拦截完全由 `tools_adapter` 与 `_stop_run_after_tools` 通过 `RunContextWrapper[AgentRequestContext]` 同步完成，不引入 `tool_choice="none"`（保持前缀缓存稳定）。
4. **全量保留既有不变量**：
   - 完整保留 Batch A（`_ToolUiPayloadStore`、`tool_call_scope`、`_ReadSnapshots` 区间追踪与 `shift_spans_for_hunks`/`shift_spans_for_edit`、`slim_edit_file_result_for_llm`）、Batch B（`session_read_snapshots`、`drop_initial_read_snapshots`、`query_files(ids=[...])`、`RepeatReadGuard`）、Arch-PR 1（单一 `state["run_items"]`、`pending_steering`、`RunExecutionPolicy`、`agent/core/usage.py`）与 `<file>...</file>` 流式写入协议。

## Alternatives considered

### 1. 保留 `ToolContext` 壳，仅把底层 `dict` 换成 `ContextVar[AgentRequestContext]`
- **最强论点**：改动面最小，无需调整 50+ 个测试文件以及工具函数与编排层的调用签名，就能拿到字段级的静态类型检查。
- **放弃原因**：仍然把请求状态藏在隐式全局 `ContextVar` 中，跨 `asyncio.to_thread` 与子任务仍须维护隐藏的拷贝语义和清理约束，也无法利用 OpenAI Agents SDK 原生的 `RunContextWrapper[T]`；长远看只是把无类型服务定位器换成了有类型服务定位器，没有解决最核心的隐式耦合与 Session 生命周期问题。

### 2. 在整个请求周期内复用同一个请求级 DB `Session`（加锁串行化所有工具调用）
- **最强论点**：减少每次工具调用创建/归还 SQLAlchemy `Session` 的开销，让同一轮内的多次工具读写共享同一套 ORM identity map。
- **放弃原因**：SQLAlchemy 同步 `Session` 本身非线程安全；单循环 agent 支持模型在单次回复内并行发起多个只读或工具调用并在 `asyncio.to_thread` 线程池里执行，长生命周期 Session 既会拉长数据库连接占用（挤压 PostgreSQL 连接池）又会引入陈旧 identity map 读到过期行的问题（例如作者并发编辑与版本并发检测）。短生命周期 `session_scope()` 连接来自连接池、归还即时、天然线程隔离。

### 3. 在 `FailoverChatModel` 中读取 `AgentRequestContext` 并在逾期未写正文时切换 `tool_choice="none"`
- **最强论点**：可在模型调用前从请求参数层硬禁止模型再发工具调用，减少一次工具拒回往返。
- **放弃原因**：一方面会让底层模型传输层（`FailoverChatModel`）反向耦合业务写作协议状态，另一方面生产实测（见 `2026-11-20-agent-prompt-cache-stability.md`）表明 MiniMax 在 `tool_choice="none"` 时会剥离工具段并击穿前缀缓存。因此继续保持模型层无业务状态耦合，由 `tools_adapter` 与 `_stop_run_after_tools` 经 `RunContextWrapper[AgentRequestContext]` 执行同步守卫。

## Consequences

- **收益**：
  - 彻底消除了全局可变 `ContextVar` 服务定位器、`_owned_session_var` 与 `SESSION_ISOLATION_KEY` 三层补丁，所有上下文依赖在函数签名与静态类型上显式可见。
  - 生产工具调用严格按次开关短生命周期 DB Session，并发 `asyncio.to_thread` 工具调用之间零 Session 共享与提前关闭风险。
  - 与 OpenAI Agents SDK 的 `RunContextWrapper[T]` / `ToolContext[T]` 原生对齐，为后续工具参数校验与编排层演进打平地基。
  - 单测不再依赖全局 `set_context` / `clear_context` 清理，测试之间天然隔离。
- **代价**：
  - 外层编排器、工具函数与测试调用点需要显式传递 `request_context`（单测与内部调用签名略微变长）。
  - 每次生产工具调用从连接池借还一次短生命周期 `Session`（连接池复用底层连接，开销极低，但测试中若同时传入 `session` 和 `create_session_func` 会优先走工厂路径）。
