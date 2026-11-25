# Agent Note: 流式工具参数进度提示与 `<project_files>` 紧凑索引

## Problem

48 小时生产流式延迟审计（`n = 1,081` 轮）显示，单循环 agent 的 `first_output_ms` 中位数达 `43.0s`、p90 达 `166.2s`，其中两大可直接治理的服务端瓶颈如下：

1. **工具参数流式生成盲区（10–40s 完全静默）**：`agent/openai_agents/runner.py` 仅消费 `response.output_text.delta` 与 `response.reasoning_text.delta`，忽略 `response.output_item.added`（`function_call`）和 `response.function_call_arguments.delta`。大型 `edit_file` 调用（p90 `3,067` 字符、最大 `19,595` 字符）在完整 JSON 生成完毕、触发 `tool_called` 之前，前端持续 10–40s 无任何新事件。
2. **首步空跑 `query_files(list)`（占总轮次 ~11%，每轮 +20–25s）**：`agent/loop/turn_context.py` 刻意不附项目文件清单，导致 48 小时内产生 `115` 次首步 `query_files(list)`（合计 `240` 次产出前 `list` 调用），模型必须多走一整轮推理与工具往返才能拿到文件 ID。

## Decision

1. **流式工具参数轻量进度提示（`agent/openai_agents/runner.py`、`agent/stream_adapter.py`）**：
   - 监听 `response.output_item.added`（`item.type == "function_call"`），按界面语言立即产出 `StreamEvent(StreamEventType.THINKING, data={"message": ..., "step": "tool_preparing", "tool_name": ...})`（如 `edit_file` →「正在准备编辑文件…」、`query_files` →「正在准备检索文件…」）；对 Chat Completions 返回的共享 `FAKE_RESPONSES_ID` 改按 `output_index` 追踪并发工具调用，避免相互覆盖。
   - 监听 `response.function_call_arguments.delta`，按字节/时间双阈值节流（每 ~`1,500` 字节或 ~`2.0s` 一次，不逐 token 刷屏），为大参数写工具持续发出字符进度提示；在 `response.created`、`response.output_item.done` / `response.function_call_arguments.done` 与 `tool_output` 时清理状态。
   - `StreamAdapter._process_workflow_event` 为 `StreamEventType.THINKING` 的 `data["message"]` 增设直通分支，映射为瞬态 SSE `thinking` 帧（不进入 `thinking_content`、不落库 `display_events`、不改变 `StreamBillingTracker` 计费或产物判据）；原有思考正文增量仍走 `thinking_content_event`。
2. **在 `<turn_context>` 尾部注入紧凑有界 `<project_files>` 索引（`agent/loop/turn_context.py`）**：
   - 在每轮动态 `<turn_context>` 内（静态系统提示与跨轮缓存历史之后，KV 前缀缓存 100% 不受影响）追加 `<project_files>…</project_files>` 块。
   - 仅查询轻量元数据列（`id` / `title` / `file_type` / `order` / `file_metadata` / `created_at`），严禁读取 `File.content` 或 `func.length(File.content)`（规避 PostgreSQL TOAST 解压延迟）；字数仅采信 `cached_word_count` 或本轮已加载进内存的焦点/附加文件实算值，未知时省略字数而不误报 `0字`。
   - 按类型优先级（`outline` / `character` / `lore` / `snippet` / `document` 在前，`draft` / `script` 在后）与 `build_sequence_sort_key` 确定性排序，默认封顶 `PROJECT_FILES_MANIFEST_MAX_FILES = 150`，超出时附 `... (+N more…)` 尾注。

## Alternatives considered

- **把 `<project_files>` 放进静态系统提示词**：最强理由是每轮无需重复发送数百 token 的文件清单。未采用：项目每新建、改名或更新一章正文都会改动系统提示词，导致系统提示与整段历史的前缀缓存全线 miss（10× 输入成本），且破坏 `wire_prefix_cache` 的 `system_prompt_sha256` 校验。放在 `<turn_context>` 动态尾部完全零缓存破坏。
- **在 `runner.py` 复用 `thinking_content` 推送工具参数准备文案**：最强理由是不改 `stream_adapter.py`。未采用：`thinking_content` 会落库写入 `ChatMessage.display_events` 污染历史思考过程，且前端默认折叠 `ThinkingContent`，无法在活跃状态行即时可见。在 `stream_adapter.py` 增加 6 行瞬态 `thinking` 事件直通最为干净。

## Consequences

- **收益**：
  - 消除大型 `edit_file` / `create_file` 参数生成期间 10–40s 的静默盲区，用户在工具开始生成参数瞬间即可看到状态反馈。
  - 消除首步 `query_files(list)` 往返（约 11% 轮次节省 20–25s `first_output_ms`），同时严守 PostgreSQL TOAST 安全与 KV 前缀缓存稳定。
- **代价与风险**：
  - 每轮 `<turn_context>` 新增约 200–650 token（150 文件封顶约 3.3k token）的文件索引，属于尾部增量，不破坏前序历史缓存。
