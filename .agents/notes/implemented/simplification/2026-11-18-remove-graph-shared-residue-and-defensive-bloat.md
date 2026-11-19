# Simplification Note: 清理多角色图引擎共享残留、死事件管线与前后端过度防御

Status: implemented

接续 [删除多 agent 图](2026-11-17-remove-graph-agent.md)（完成该笔记备选 3 留待后续批次的共享分支清理）与 [聊天改稿直接展示](../feature/2026-11-18-direct-ai-edits.md)。

## Problem

在删除 `agent/graph/`（#388）与取消聊天强制逐段 diff 审阅（#393/#395）之后，前后端共享路径与工具层仍保留大量零生产调用的旧架构残留与过度防御分支：

1. **后端旧图输入与死模块残留**：
   - `agent/core/session_loader.py` 仍保留约 700 行旧图输入组装器（`load_session_and_context`、`load_chat_session`、`attach_working_set`、`_load_history_window_under_token_budget` 等）与 `config/agent_runtime.py` 的 `AGENT_CHAT_HISTORY_TOKEN_BUDGET`、`AGENT_WORKING_SET_MAX_*`，单循环已全部改走 `load_loop_history` 与 `build_loop_turn_context`；
   - `agent/utils/world_model_visibility.py` 全仓零引用；`ContextData.blocks` 与 `_format_context_with_blocks` 仍在每次组装上下文时为已删除的会话快照重复拷贝分段字符串；
   - `agent/core/progress_channel.py` 专供已删的 `parallel_executor.py` 推送实时进度，全仓零生产调用，且使 `openai_agents/runner.py` 额外维护一层后台 `_pump_sdk_events` 任务与队列混流。
2. **前后端实时流中的死事件与并行分支（及硬超时暂停卡统计缺陷）**：
   - 后端实时流层（`workflow_events.py`、`events.py`、`stream_adapter.py`、`service.py`、`round_outcome.py`、`stream_billing.py`、`run_meter.py`、`repeat_read_guard.py`、`tool_failure_breaker.py`、`intra_run_trimmer.py`）仍维护 `CHAT_HOLD`、`router_thinking`、`router_decided`、`agent_selected`、`handoff`、`workflow_complete`、`parallel_start`..`parallel_end`、`no_file_changes`、`apply_action` 等实时路径；而旧库记录不经过这些实时类；
   - `api/agent.py` 墙钟硬超时暂停卡仍靠监听已删除的 `event: agent_selected` 统计 `agent_runs_started`，导致单循环下硬超时暂停卡恒输出 `iterations_used=0`；
   - 前端流式链路（`agentApi.ts`、`useAgentStream.ts`、`useChatStreaming.ts`、`ChatPanel.tsx`、`MessageList.tsx`、`ToolResultCard.tsx`、`types/index.ts`）仍维护上述实时事件回调、后端从未存在的 `conflict` / 复数 `skills_matched` 事件、`applyAction` 与旧请求字段。
3. **只写不读状态与过度防御负担**：
   - `mcp_tools.py` / `edit.py` / `crud.py` 的 `_PreWriteContents` 每次改稿加锁拷贝整章原文进内存，全仓零读取；`author_edit_guard.py` 的 `AuthorEditRefusals` 与 `referenced_file_ids_from_metadata` 生产零读取；`tool_schemas.py` 维护双份 `create_file` schema 再覆盖；
   - `mcp_tools._should_offload_tool_execution()` 硬编码 `return True`，却在 9 个工具与 `service.py` 保留不可达同步 `else` 分支；`loop/turn_context.py` 保留死兜底字典 `order_by_key`；`service.py` 通过 `getattr` 在 4 个出口同步 `StreamAdapter` 超时集合到 `RunOutcome`；`stream_adapter.py` 与 `loop/` 重复同步 Session 样板与 MCP JSON 双层解包；
   - 前端 `useAgentStream.ts` 同时维护两份 `error`/`errorCode`（`clearError` 漏清 `state.error`）、保留零触发的 3 秒 `errorTimeoutRef`、并在 `setState` 纯函数内调用外部 `onContent`；`ChatPanel.tsx` 重复维护 `onCompleteCalledRef` 并把 `streamingFileId` state 混入已有 ref getter 的 `streamCallbacks` 依赖；`SimpleEditor.tsx` 维护冗余 `naturalPolishBaselineRef`；`Editor.tsx` 409 冲突完成分支残留 `agent_write` 嵌套判断，`ProjectContext.enterDiffReview` 在 `origin === 'polish'` 时白跑整章段落 diff。

## Decision

分前后端两个互不重叠的子树实施有界清理，并由独立审查流程核验，**严格保留所有线上兼容边界**：

1. **保留不动**：
   - 历史 `ChatMessage` 落库回放：后端 `SessionLoader._format_chat_message_for_history`、`_LEGACY_GRAPH_WRITE_ROLES`、`_pending_rework_history_content`、`_extract_parallel_file_actions`（供 `session_written_file_ids` 识别旧并行写文件）、`MessageManager.client_message_metadata`（含读取旧消息 `_status_card_routing_hints`）；前端 `parseChatDisplayEvents`（将 `router_thinking`/`router_decided` 折叠为无操作 `break` 防触发 `default: undefined`）、`MessageList` 旧状态卡、`ToolResultCard` 历史 `parallel_execute` 卡片、`nextStep.ts` 历史澄清卡判断、`thinkingDisplay.ts`、`agentDisplayName.ts`；
   - `/suggest` 与外部 OpenClaw API 使用的 `ContextAssembler.assemble()`（含 `AGENT_CONTEXT_TOKEN_BUDGET`）；
   - `update_project(written_artifact_ids=...)` 的服务端任务板收尾契约；`file_edit_end` 的 `newContent`/`originalContent` 整章替换自动选中信号；请求端忽略 `generation_mode` 的向后兼容。
2. **后端清理批次（`apps/server/`）**：
   - 删除 `world_model_visibility.py`、`progress_channel.py`（简化 `runner.py` 直接迭代 `result.stream_events()`，并在提前退出与非取消异常时关闭流且 `cancel("immediate")` 未完成的 SDK run）、`SessionLoader` 旧图输入加载与 `working_set`（保留 `attach_working_set_to_user_content` 与历史行格式化）、`ContextData.blocks`；
   - 删除 `CHAT_HOLD` 与实时死事件/并行分支（`workflow_events.py`、`events.py`、`stream_adapter.py`、`service.py`、`message_manager.py` 的写入侧 `assistant_routing` 参数、`round_outcome.py`、`stream_billing.py`、`run_meter.py`、`repeat_read_guard.py`、`tool_failure_breaker.py`、`intra_run_trimmer.py`）；在 `RunOutcome` 记录实际启动的 `runs_started` 修复 `api/agent.py` 硬超时暂停卡 `iterations_used`；
   - 删除 `_PreWriteContents`、`AuthorEditRefusals`、`referenced_file_ids_from_metadata`、`output_truncation.trailing_output_truncations`；合并 `CREATE_FILE_TOOL`；清理 `_should_offload_tool_execution` 假同步分支、`turn_context.py` `order_by_key`，将 `run_outcome` 直接传入 `StreamAdapter` 移除 4 处 `_sync_uncertain_saves` 胶水，并收敛重复的 `_with_sync_session` 与 `parse_mcp_tool_payload`；同步清理/迁移孤儿单测。
3. **前端清理批次（`apps/web/`）**：
   - 删除实时 `router_*`、`agent_selected`、`handoff`、`workflow_complete`、`parallel_*`、`conflict`、复数 `skills_matched`、`applyAction`、死 `AgentRequest` 字段、从未赋值的 `Message.conflicts`/`reasoningContent`/`reasoningSegments`/`contextItems` 与未用 `onFileEditApplied` 参数；
   - 收敛 `Editor.tsx` `handleFinishReview` 409 分支、`ProjectContext.enterDiffReview`（`polish` 跳过 `buildParagraphReviewData`）、`diffReview.ts` 单行包装函数；
   - 修复并精简 `useAgentStream.ts`（统一 `state.error`、移除 `errorTimeoutRef`、将 `onContent` 移出 `setState`）、`ChatPanel.tsx`（移除重复 `onCompleteCalledRef` 与 `streamCallbacks` 的 `streamingFileId` 依赖）、`SimpleEditor.tsx`（移除 `naturalPolishBaselineRef`）及 `agentApi.ts` 尾部缓冲解析重复分支；同步更新前端测试与无用并行进度/冲突 i18n 键。

## Alternatives considered

- **连同 `SessionLoader._format_chat_message_for_history` 与前端 `ToolResultCard` 的 `parallel_execute` 卡片一并删除。** 最强理由：彻底抹去仓库里所有 `parallel_execute` 与 `handoff` 字样。未采用：生产 PostgreSQL 中已存有 2026-11-17 前的旧会话消息，删除回放与渲染分支会让老项目打开历史对话时丢失工具记录或整段 `display_events` 时间线，违背无损兼容原则。
- **将 `diffReviewState` 整套从 `ProjectContext` 迁入 `Editor.tsx` 局部 Hook。** 最强理由：收窄 Context 订阅范围。未采用：改动涉及 25+ 个前端测试夹具改造，收益主要是审阅点击时的局部重渲染优化，留待单独前端状态重构更稳妥；本批仅消除 `polish` 入口白跑整章 diff 计算与死冲突分支。

## Consequences

- 收益：前后端合计净删约 6,800+ 行旧图/并行死代码与冗余防御，消除每次改稿的内存原文冗余拷贝与每次去 AI 味的无用段落 diff 计算，同时修复硬超时暂停卡 `iterations_used=0`、`useAgentStream.clearError` 状态残留及 `setState` 内外部回调副作用。
- 代价：专门测试已删除死入口（如旧 `SessionLoader.load_session_and_context`、`progress_channel`、`CHAT_HOLD`、实时 `parallel_*`/`conflict` SSE）的孤儿单测同步删除或迁移至现行单循环入口；由 `test_loop_legacy_graph_rows.py` 与前端历史回放测试继续锁住存量会话兼容性。
