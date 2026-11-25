# Agent Note: 流式工具参数进度提示、`<project_files>` 紧凑索引与轮后异步历史压缩

## Problem

48 小时生产流式延迟审计（`n = 1,081` 轮）显示，单循环 agent 的 `first_output_ms` 中位数达 `43.0s`、p90 达 `166.2s`，其中三大可直接治理的服务端瓶颈如下：

1. **工具参数流式生成盲区（10–40s 完全静默）**：`agent/openai_agents/runner.py` 仅消费 `response.output_text.delta` 与 `response.reasoning_text.delta`，忽略 `response.output_item.added`（`function_call`）和 `response.function_call_arguments.delta`。大型 `edit_file` 调用（p90 `3,067` 字符、最大 `19,595` 字符）在完整 JSON 生成完毕、触发 `tool_called` 之前，前端持续 10–40s 无任何新事件。
2. **首步空跑 `query_files(list)`（占总轮次 ~11%，每轮 +20–25s）**：`agent/loop/turn_context.py` 刻意不附项目文件清单，导致 48 小时内产生 `115` 次首步 `query_files(list)`（合计 `240` 次产出前 `list` 调用），模型必须多走一整轮推理与工具往返才能拿到文件 ID。
3. **同步轮前历史压缩阻塞首字（占总轮次 7.0%，中位罚时 +125s）**：当会话输入超过 `AGENT_LOOP_COMPACT_AT_TOKENS` 时，`AgentService.process_stream` 在下一轮开始前同步阻塞等待摘要模型调用完成（`164.0s` vs `39.0s` p50；`330.5s` vs `143.1s` p90），既拖慢下一轮首字，又容易在冷却后错失热 KV 缓存。

## Decision

1. **流式工具参数轻量进度提示（`agent/openai_agents/runner.py`、`agent/stream_adapter.py`）**：
   - 监听 `response.output_item.added`（`item.type == "function_call"`），按界面语言立即产出 `StreamEvent(StreamEventType.THINKING, data={"message": ..., "step": "tool_preparing", "tool_name": ...})`（如 `edit_file` →「正在准备编辑文件…」、`query_files` →「正在准备检索文件…」）。
   - 监听 `response.function_call_arguments.delta`，按字节/时间双阈值节流（每 ~`1,500` 字节或 ~`2.0s` 一次，不逐 token 刷屏），为大参数写工具持续发出字符进度提示；在 `response.created`、`response.output_item.done` / `response.function_call_arguments.done` 与 `tool_output` 时清理状态。
   - `StreamAdapter._process_workflow_event` 为 `StreamEventType.THINKING` 的 `data["message"]` 增设直通分支，映射为瞬态 SSE `thinking` 帧（不进入 `thinking_content`、不落库 `display_events`、不改变 `StreamBillingTracker` 计费或产物判据）；原有思考正文增量仍走 `thinking_content_event`。
2. **在 `<turn_context>` 尾部注入紧凑有界 `<project_files>` 索引（`agent/loop/turn_context.py`）**：
   - 在每轮动态 `<turn_context>` 内（静态系统提示与跨轮缓存历史之后，KV 前缀缓存 100% 不受影响）追加 `<project_files>…</project_files>` 块。
   - 仅查询轻量元数据列（`id` / `title` / `file_type` / `order` / `file_metadata` / `created_at`），严禁读取 `File.content` 或 `func.length(File.content)`（规避 PostgreSQL TOAST 解压延迟）；字数仅采信 `cached_word_count` 或本轮已加载进内存的焦点/附加文件实算值，未知时省略字数而不误报 `0字`。
   - 按类型优先级（`outline` / `character` / `lore` / `snippet` / `document` 在前，`draft` / `script` 在后）与 `build_sequence_sort_key` 确定性排序，默认封顶 `PROJECT_FILES_MANIFEST_MAX_FILES = 150`，超出时附 `... (+N more…)` 尾注。
3. **轮后异步预压缩 + 在途去重复用 + 同步兜底（`agent/service.py`、`agent/loop/compaction.py`、`agent/loop/history.py`、`agent/core/session_loader.py`）**：
   - 轮次干净结束并准备发出 `done` 帧、实测输入 token 达到 `AGENT_LOOP_COMPACT_AT_TOKENS` 且本轮未已有压缩点、进程未处于 SIGTERM drain 时，通过 `AgentService._schedule_post_turn_compaction` 异步调度后台压缩任务（协程首步 `await asyncio.sleep(0)` 让出事件循环确保 `done` 优先冲刷；任务挂入 `AgentService.background_cleanup_tasks` 由关机 `wait_for_stream_cleanup` 统一收尾）。
   - 后台压缩优先复用刚结束轮次的 `completed_wire_items`（命中热 KV 缓存），在独立 `AgentRequestContext.bind_cost_budget_user()` 下运行以保持费用账本与免费用户每日成本兜底生效；任何模型或配额异常均在后台安全捕获记日志，绝不阻塞或破坏用户的 SSE `done` 帧。
   - 压缩成功后经 `persist_post_turn_compaction_sync` 幂等写回刚结束的 assistant 行（`covers_through_message_id == assistant_message_id`），归档压缩前查询载荷，并仅在该行仍是会话最新行时失效旧 Redis `wire_prefix_cache`。
   - `history.load_loop_history` 与 `SessionLoader.session_read_snapshots` 识别自覆盖锚点（`covers_through_message_id == row.id`），跳过该行压缩前的 `prompt_tokens` 读数与工具快照结转，并置 `LoopHistory.compacted_at_tail = True` 防止加载旧的未压缩 Redis wire prefix，使下一轮实现 0ms 压缩等待且后续轮次前缀逐字一致。
   - 进程内按 `session_id` 登记在途任务（`_INFLIGHT_COMPACTIONS`）：同一会话拒绝重复调度；若下一轮在后台压缩尚未完成时到达，直接 `asyncio.shield` 复用该在途任务结果而不重复发起 LLM 调用；若无预计算压缩且无在途任务（如冷启动或上次后台失败），保留原有同步轮前压缩作为安全兜底。

## Alternatives considered

- **把 `<project_files>` 放进静态系统提示词**：最强理由是每轮无需重复发送数百 token 的文件清单。未采用：项目每新建、改名或更新一章正文都会改动系统提示词，导致系统提示与整段历史的前缀缓存全线 miss（10× 输入成本），且破坏 `wire_prefix_cache` 的 `system_prompt_sha256` 校验。放在 `<turn_context>` 动态尾部完全零缓存破坏。
- **使用独立的消息队列 / Prefect Worker 执行轮后压缩**：最强理由是跨进程解耦与重启重试。未采用：引入序列化整个 300k-token wire prefix 的跨进程传输开销，且丢失同进程内「下一轮紧接着到来时直接 join 在途任务」的低延迟去重能力，也容易错失热 KV 缓存窗口。进程内后台任务 + `background_cleanup_tasks` 优雅关机 + 同步轮前兜底已覆盖重启与失败场景且零新增基础设施。
- **在 `runner.py` 复用 `thinking_content` 推送工具参数准备文案**：最强理由是不改 `stream_adapter.py`。未采用：`thinking_content` 会落库写入 `ChatMessage.display_events` 污染历史思考过程，且前端默认折叠 `ThinkingContent`，无法在活跃状态行即时可见。在 `stream_adapter.py` 增加 6 行瞬态 `thinking` 事件直通最为干净。

## Consequences

- **收益**：
  - 消除大型 `edit_file` / `create_file` 参数生成期间 10–40s 的静默盲区，用户在工具开始生成参数瞬间即可看到状态反馈。
  - 消除首步 `query_files(list)` 往返（约 11% 轮次节省 20–25s `first_output_ms`），同时严守 PostgreSQL TOAST 安全与 KV 前缀缓存稳定。
  - 将 7% 高长会话轮次的 `+125s` 同步压缩延迟移出用户关键路径，且趁热复用刚结束轮次的 KV 缓存，同时保留完整的计费台账、并发去重与同步兜底。
- **代价与风险**：
  - 每轮 `<turn_context>` 新增约 200–650 token（150 文件封顶约 3.3k token）的文件索引，属于尾部增量，不破坏前序历史缓存。
  - 轮后后台压缩在用户结束对话后不再继续提问的场景下会多付一次高缓存命中的摘要调用；相比长会话继续提问时节省的 125s 阻塞等待与热缓存折价，整体收益显著为正，且受免费用户每日成本兜底与 SIGTERM drain 跳过机制约束。
