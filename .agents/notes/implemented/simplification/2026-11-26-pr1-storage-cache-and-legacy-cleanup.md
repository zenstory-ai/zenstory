# PR 1 Wave 2：聊天落库瘦身、温缓存优先加载、上下文指纹去重与遗留债务清理

## Problem

系统级架构审计识别出 `apps/server/agent/` 中多类直接影响线上数据库体积、延迟、缓存命中率与代码可维护性的瓶颈：

1. **写入时 `chat_message` TOAST 膨胀（P0-2）**：95%+ 未触及 300k 压缩线的会话永久把完整的 `query_files` 整章正文写入 `chat_message.tool_calls`；`message_metadata.display_events` 在纯文本回复时整份双写 `chat_message.content`，且无界写入 200k–400k 字符的 MiniMax `<think>` 块。
2. **每轮重复 TOAST 扫描（P0-3）**：`AgentService._prepare_loop_turn` 在检查 Redis `wire_prefix_cache` 之前无条件执行 `load_loop_history`，且后者执行 `select(ChatMessage)`（`SELECT *`）解压不读的 `reasoning_content` 列；温缓存命中时白扫整库。
3. **`GeneratorExit` 期间内联 `await aclose()`（P0-6）**：`stream_adapter.py` 与 `openai_agents/model.py` 在 `GeneratorExit` 的 `finally` 中直接 `await aclose()`，挂起时会触发 `RuntimeError: async generator ignored GeneratorExit`。
4. **缓存校验缺口与跨轮 `<turn_context>` 线性叠放（P0-9 / P1-1）**：`wire_prefix_cache` 未校验 `tools_sha256` 与 `reasoning_effort`；温缓存路径每轮将 `<project_files>`、焦点文件全文与 `<author_edits>` 线性叠放 N 份，加速消耗上下文与缓存输入成本。
5. **遗留多智能体回放与重型 40 版本逐行归因债务**：`session_loader.py` 保留 ~500 行已下线多角色图回放逻辑；`author_changes.py` 与 `file_ops/edit.py` 在每轮上下文组装与每次读写上执行 40 版本 `SequenceMatcher` 逐行 diff；仍残留薄转发壳文件与无用的 `last_agent` / `RAILWAY_HTTP_CONNECTION_LIMIT_S` 透传。

## Decision

1. **写入时止血 TOAST 膨胀（P0-2）**：
   - `agent/core/message_manager.py`：保持直播 SSE 思考流 100% 原样，但在落库 `display_events`（及 `reasoning_content`）时对单个思考块按 `PERSISTED_THINKING_MAX_CHARS = 8_000` 截断并附简短截断标记；当 `display_events` 仅含单个 `content` 段且与 `chat_message.content` 逐字一致（且无 `tool_calls` / `status_cards`）时省略落库 `display_events`（前端 `MessageList` 与后端 `loop/history.py` 均无缝回退到 `content` 列）。
   - `agent/core/chat_storage.py` + `agent/stream_adapter.py` + `agent/core/session_loader.py`：超过 `PERSISTED_QUERY_FILES_MAX_CHARS = 4_000` 字符的 `query_files` 落库前剥离重载荷字段，保留卡片字段、`updated_at`、全文 `content_sha256` 与 `archived_note`；`session_read_snapshots` 新增 `read_sha256` 校验分支，在不落库正文的前提下仍保持严格的跨轮快照结转与防陈旧编辑保护。
2. **温缓存优先加载与显式列投影（P0-3）**：
   - `agent/service.py`：在 `_prepare_loop_turn` 内先构建静态 `system_prompt` 并以 3 列索引查询（`ChatMessage.id, role, created_at LIMIT 1`）获取最新消息 id，优先查 `wire_prefix_cache`；命中且带有完整 `history_meta` 时直接还原 `LoopHistory`，完全跳过 `load_loop_history` 扫库。
   - `agent/loop/history.py`：`load_loop_history` 改用 6 列显式投影（排除 `reasoning_content`）并保持 keyset 分页。
3. **`GeneratorExit` 后台安全收尾（P0-6）**：
   - `agent/stream_adapter.py` 与 `agent/openai_agents/model.py`（`_iter_with_stream_timeouts`、`_iter_with_idle_timeout`、`FailoverChatModel.stream_response`）显式捕获 `GeneratorExit` 置位标记，在 `finally` 中仅调度后台受跟踪关闭任务，绝不在 `GeneratorExit` 路径内联 `await aclose()`。
4. **`wire_prefix_cache` 契约加固与 `<turn_context>` 跨轮指纹去重（P0-9 / P1-1）**：
   - `agent/loop/wire_prefix_cache.py` 在缓存信封中加入并严格校验 `tools_sha256`（基于 `get_exposed_tools()` 规范化序列化）与 `reasoning_effort`（基于 `AgentRunMeter.last_served_reasoning_effort` / `effective_reasoning_effort(provider)`），不匹配时 fail-open 回退到 `load_loop_history`。
   - `agent/loop/turn_context.py` 与 `agent/service.py` 在温缓存信封中记录轻量 `context_fingerprints`（`project_files_sha256`、`full_file_hashes`、`author_edits_sha256`）：温缓存命中且未变更时，本轮分别输出 `<project_files unchanged="true"/>`、焦点/附加文件未变更短指针与 `<author_edits unchanged="true"/>`，零改动历史前缀字节（0 KV 缓存破坏），同时完整保留 `read_snapshots[file_id]`；一旦文件修改立刻重新下发最新全文；若本轮触发 Stage 2 LLM 摘要或历史滑窗截断，自动恢复完整 `full_text`。
5. **遗留债务清理与轻量手改提醒**：
   - 删除 `session_loader.py` 中 ~500 行旧多智能体图回放（`_LEGACY_GRAPH_WRITE_ROLES`、`_format_pending_rework`、`_clarification_cards_history_content`、`_format_chat_message_for_history`、`parallel_execute` 面包屑），`loop/history.py:_legacy_row_items` 简化为纯文本 `row.content` 兜底。
   - 以单次批量 `FileVersion` 元数据查询取代 40 版本 `SequenceMatcher` 逐行归因（保留 `AI`/`AUTHOR`/`ORIGINAL`/`version_actor` 及 Batch B P0-1 的无版本同长手改、空时间戳 fail-closed、旧版 `AI edit (reviewed)` 与系统恢复检测口径）；焦点/附加文件已有全文，仅对本会话写过且不在当前上下文的非焦点文件在 `<author_edits>` 输出 1 行提醒；移除 `file_ops/edit.py` 中 `attach_author_edits_for_agent` 与 `touched_author_text` 计算，更新 `prompts/base.py`。
   - 删除薄壳模块 `agent/core/request_context.py` 与 `agent/tools/file_ops/executor.py`（`FileToolExecutor` 移入 `agent/tools/file_ops/__init__.py`）；清理无用 `last_agent` 内部透传与 `RAILWAY_HTTP_CONNECTION_LIMIT_S`；将 `empty_files.py` 与 `round_outcome.py` 的空文件软删合并为单一共享函数 `soft_delete_empty_file_if_unchanged`（统一推进 `updated_at`）。

## Alternatives considered

1. **保留全量 `query_files` 正文落库，仅依赖 300k 压缩后离线归档**：
   - 优点：冷回放路径在任何时间点都无需重新调用 `query_files`。
   - 驳回原因：生产 95%+ 会话永不触发 300k 压缩线，导致单次多章读取永久沉淀在 `chat_message` TOAST 中并持续拉高 PostgreSQL 磁盘与页缓存；而活跃会话 20 分钟内走 Redis 温缓存，冷回放时重读单章成本极低，结合 `content_sha256` 还能完整保留跨轮 `session_read_snapshots`。
2. **在 `wire_prefix_cache` 温缓存里原地替换前几轮 `<turn_context>` 以去重**：
   - 优点：可进一步把历史轮的旧 `<turn_context>` 也清掉。
   - 驳回原因：修改历史前缀任何字节都会立刻击穿供应商 KV Prompt Cache；只在最新追加的本轮 `<turn_context>` 做指纹去重可兼得 0 前缀破坏与 60%–80% 跨轮输入减负。
3. **继续维护 40 版本 `SequenceMatcher` 逐行归因并加缓存**：
   - 优点：可向模型展示逐段旧→新 diff。
   - 驳回原因：焦点与附加文件本身已给模型最新全文，模型并不需要再看一份冗余逐行 diff；非焦点文件只需 1 条低开销提醒触发模型按需 `query_files` 读取即可，彻底消除版本链回放 CPU 与多条 SQL 往返，并削减数百行高复杂度维护面。

## Consequences

- **收益**：
  - 写入侧即遏制 `chat_message` TOAST 膨胀（截断超大思考块、消除纯文本 `display_events` 双写、超 4k `query_files` 落库即瘦身）；
  - 温缓存命中轮次彻底跳过 `load_loop_history` 全会话 TOAST 扫库，冷回放不再解压 `reasoning_content`；
  - 工具 schema 或 `reasoning_effort` 变更后自动 fail-open，不再复用陈旧 wire prefix；
  - 温缓存连续多轮不再重复堆叠 150 行文件索引与整章焦点正文；
  - 消除 `GeneratorExit` 期间 `RuntimeError` 风险，并删除 ~1,000+ 行遗留多智能体回放与逐行归因代码。
- **代价与权衡**：
  - 超过 8,000 字符的历史思考过程在刷新页面后仅展示前 8k 字加截断标记（实时流式展示不受影响）；
  - 超过 4,000 字符的 `query_files` 在超过 20 分钟冷回放时仅保留元数据与 `archived_note`，模型如需原文细节会再次调用 `query_files(id=...)`；
  - 已下线多智能体图写下的极早期历史行回放简化为纯文本正文，不再重建旧版面包屑或未执行返修块。
