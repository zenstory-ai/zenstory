# Simplification Note: 写作工具集精简（B1 退役任务板、B2 拆分 read_files/search_files、B3 精简 edit_file 与工具层架构收敛）

Status: implemented

关联：[单循环 agent](../architecture/2026-11-16-single-loop-agent.md)、[以 Pydantic v2 入参模型统一工具 Schema 与运行时参数校验](./2026-11-21-agent-pydantic-tool-inputs.md)、[跨轮未变更会话快照结转、edit_file 批量合并指引与 query_files 批量 ids 读取](../architecture/2026-11-21-batch-tool-calls-and-session-snapshots.md)。

## Problem

单循环写作 agent 的工具层与运行时在多次迭代后积累了四类结构性噪音与隐蔽缺陷：

1. **B1（僵尸 Redis 任务板）**：`update_project.tasks` 与 `services/infra/task_board_service.py`、`agent/core/task_board_completion.py`、`agent/tools/task_progress.py` 共约 1,200 行代码只写不读——`ContextAssembler` 从未把任务板注入回上下文、API 也未向前端暴露。它不仅浪费约 500 token 工具 schema 声明与每轮落库/收尾 Redis 往返，还把墙钟自查软着陆绑在无感知的外部任务板状态上。
2. **B2（过载的 `query_files`）**：单一 `query_files` 工具同时承担 13 个参数与「精确读取全文/区间、关键词检索、目录筛选」三种正交职责，参数交互（`id`/`ids` + `query` + `response_mode` + `include_content` + `content_preview_chars`）极易诱导模型传错模式或整批拉取大正文。
3. **B3（冗杂的 `edit_file` 与重复提示）**：`edit_file` 暴露 6 种 `op` 及 3 个低价值开关（`match_mode`、`ignore_punct_whitespace`、`replace_all`），其中 `delete` 与 `replace(new="")` 重叠、`prepend` 与 `insert_before` 重叠；同时 `WORD_COUNT_CONTRACT` 重复拼接到 4 个工具描述中，`prompts/base.py` 与 `loop/prompts.py` 亦存在多处规则重复及已过时的串行调用说明。
4. **工具层架构欠账**：
   - **P1-4**：`tools_adapter.py` 单次工具调用里对 `raw_arguments` 与 `output` 重复做多达 4–6 次 `json.loads` / `parse_json_object`，且 `RepeatEditGuard._plan_self_read` 改写混合批读 `ids` 后 `tools_adapter.py` 未把 `edit_plan.arguments` 透传给 `read_guard` 与实际工具执行。
   - **P1-5**：待写 `<file>` 正文逾期时，允许穿插的非冲突工具（`read_files` / `search_files` / `update_project`）无次数上限，模型可能无限查文件而逃避写完正文。
   - **`batch_replace_files` 记账缺口**：Track A 新增的跨文件批量替换工具尚未接入 `StreamBillingTracker`、`RoundWrittenFiles`、`chat_tool_cards` 撤销标记、`service.py` 上下文指纹失效、`SessionLoader` 读写快照及 `RepeatEditGuard`。

## Decision

1. **B1 — 彻底退役并删除僵尸任务板**：
   - 删除 `apps/server/services/infra/task_board_service.py`、`apps/server/agent/core/task_board_completion.py`、`apps/server/agent/tools/task_progress.py` 及 `file_ops/project.py` 中的 `execute_update_plan` 全部实现。
   - 从 `UpdateProjectInput` 移除 `tasks` 字段与 `UnitSpecInput` / `UpdateProjectTaskInput` 模型，仅保留项目记忆更新（`title`、`summary`、`writing_style`、`current_phase`、`notes`、`expected_notes`）；`extra="ignore"` 兼容旧模型偶发透传的 `tasks`。
   - `loop/engine.py` 移除 `auto_finalize_task_board_on_completion` 调用，墙钟自查软着陆直接以已有确认落库正文与无未完待写状态作为完结条件；前端 `ToolResultCard.tsx` 保留对历史 `ChatMessage` 行中 `data.plan` 的只读容错渲染。
2. **B2 — 拆分 `query_files` 为 `read_files` + `search_files`**：
   - 在 `tool_inputs.py`、`tool_schemas.py`、`mcp_tools.py`、`registry.py`（`LOOP_TOOL_NAMES`）中以一等工具暴露 `read_files`（按 `ids` 精确读取全文/区间，支持单 `id` 别名回退与 `show_paragraph_stats`）与 `search_files`（按关键词在正文与标题中检索，或按类型/目录/状态列出摘要，永不返回大正文）。
   - 保留 `query_files` 在 `MCP_TOOL_HANDLERS`、`TOOL_SCHEMAS` 与 `TOOL_INPUT_MODELS` 中的隐藏兼容实现；全面更新 `repeat_read_guard.py`、`repeat_edit_guard.py`、`tool_failure_breaker.py`、`tools_adapter.py`、`intra_run_trimmer.py`、`runner.py`、`loop/history.py`、`loop/compaction.py`、`core/chat_storage.py`、`core/session_loader.py`、`service.py`、`stream_adapter.py` 与提示词，确保新工具与历史 DB 中的旧 `query_files` 调用行均可无缝回放、防重复、归档与记快照。
3. **B3 + Option A — 精简 `edit_file` 与提示词/Schema 去噪**：
   - `edit_file` 对外 schema 收敛为 4 个核心操作（`replace`、`append`、`insert_after`、`insert_before`），明确支持 `replace(old=…, new="")` 删除片段，并将同批内的「`replace(new="")` + `insert_*`/`append`」同样视作耦合移动（默认 `continue_on_error=False`）。
   - 隐藏 `match_mode`、`ignore_punct_whitespace`、`replace_all`（服务端自动走逐字 → 引号归一 → 模糊/近似匹配阶梯），运行时保留对旧 `delete`、`prepend` 及隐藏参数的完整兼容；`body_guards`、陈旧读检查、写锁、撤销锚点与段号前缀剥离重试保持 100% 不变。
   - 从工具描述中移除重复拼接的 `WORD_COUNT_CONTRACT`，合并去重 `prompts/base.py` 与 `loop/prompts.py` 中的重复规则并删除过时的「不能并行调用工具」矛盾表述。
4. **工具层架构修复（P1-4 / P1-5 / `batch_replace_files` 记账）**：
   - **P1-4**：`tools_adapter.py` 对单次工具调用的 `raw_arguments` 与 `output` 各只解析一次，将 `parsed_args` / `parsed_output` 直接传入 `RepeatEditGuard`、`RepeatReadGuard` 与 `ToolFailureBreaker`；同时把 `EditPlan.arguments` / `EditPlan.parsed_arguments` 透传给 `read_guard.plan` 与实际调用，修复混合批读剔除刚写章节 ID 时改写未生效的缺陷。
   - **P1-5**：`_PendingEmptyFileState` 与 `AgentRequestContext` 增加待写 `<file>` 逾期后的非冲突工具穿插计数上限（每轮待写最多允许 2 次 `read_files` / `search_files` / `update_project`，第 3 次起一律返回 `file_body_pending`），并在 `clear_all()` 中完整重置全部状态。
   - **`batch_replace_files` 全链路记账**：补齐 `StreamBillingTracker`（忽略 `dry_run=True`）、`RoundWrittenFiles`（记录全部成功修改文件并在有跳过时标 partial）、`chat_tool_cards.record_edit_card_undone`（匹配 `data["files"]` 内逐文件 `undo` 锚点）、`service._applied_file_mutations_for_fingerprints`（失效全部受影响文件指纹）、`SessionLoader`（计入已写文件与最新读取快照）、`IntraRunToolOutputTrimmer` 与 `RepeatEditGuard`。

## Alternatives considered

- **保留任务板但将其接入 `ContextAssembler` 和前端 UI**：最强理由是能让模型与作者在长任务里共享显式进度列表。未采用：现有单循环 agent 已有目录清单、章节缺口提示与 `update_project.notes` 持久化记忆，维护第二套易失 Redis 任务板会引入状态同步与漂移负担（如作者手动增删文件后任务板失真），收益远低于删除 ~1,200 行代码的简化收益。
- **直接删除 `query_files` 实现与旧 `edit_file` 操作分支**：最强理由是代码最彻底干净。未采用：线上持久化的历史 `ChatMessage.tool_calls` 仍包含大量 `query_files` 调用，且模型在过渡期或回放历史后仍可能偶发输出旧参数；仅在对外工具清单与 schema 层隐藏、在回放与运行时保留轻量兼容的成本极低，可做到零线上回归。
- **把 `edit_file` 进一步压缩成仅 `replace` 单操作**：最强理由是 schema 更短。未采用：流式写作中断后从章末续写（`append`）与段前/段后精确插入（`insert_before` / `insert_after`）不需要在 `new` 里重抄整段锚点原文，可显著降低输出 token 消耗与锚点抄错风险，4 个操作是表达力与简洁度的最佳平衡。

## Consequences

- **收益**：
  - 删除约 1,200 行死代码及 3 个僵尸单测文件，工具清单与提示词 token 体积明显瘦身，参数语义边界（读 vs 搜、编辑 4 操作、项目记忆）清晰直观。
  - 单次工具调用 JSON 解析从 4–6 次降至 1 次（入参/出参各 1 次），混合批读自读改写真正生效，待写正文逾期不再被无限穿插读工具绕过。
  - `batch_replace_files` 与现有计费、停顿回执、修改卡撤销、热缓存指纹失效与重复编辑守卫完全对齐。
- **代价与注意事项**：
  - `LOOP_TOOL_NAMES` 发生变化，发布后首次请求会刷新 `tools_sha256` 并自然重建前缀缓存（预期内的一次性切换）。
  - 后续若彻底下线历史上古会话的 `query_files` 兼容分支，需先确认历史库回放需求并保留 `loop/history.py` 对旧 `tool_calls` JSON 的只读渲染能力。
