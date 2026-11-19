# Bug-Fix Note: 单循环 Agent 二次生产复盘修复（自查内层时限软着陆、并行建档熔断豁免、纯提示词控思考与原文搬运归一）

Status: implemented

关联决策：补充 [单循环 Agent 生产复盘跟进修复](2026-11-18-loop-prod-review-followups.md)、[时限收尾与墙钟硬超时暂停卡](2026-10-31-wall-clock-pause-card-and-receipt.md)、[锚点未命中不提前熔断](2026-11-04-anchor-misses-do-not-trip-early.md) 与 [简化可恢复改稿与清理冗余](../simplification/2026-11-19-post-prod-review-and-deslop.md)。旧决策记录本身不改写。

## Problem

基于 `prod-v2026.11.18.5` 上线约 25 小时的只读生产数据库与容器日志排查（累计 562 轮单循环对话，其中 `v2026.11.18.5` 后 370 轮），除已在 `2ff1275d` 收口的写后凑字数微调与压缩裸 UUID/表格外，定位出 5 类影响真实创作体验的生产问题：

1. **自查续跑在内层 `runner.py` 跨过 600s 软着陆时无条件弹「可能未完成 / 继续」暂停卡，诱发连环续写（占全部轮次 `27.6%`，102/370 轮）**：
   - 第 1 次 run 在 `<600s` 写完章节落库后，`loop/engine.py` 启动第 2 次 `self_check` run；该 run 的总结调用跨过 `600s` 时，`runner._ModelCallCountingFilter` 注入 `LOOP_TIME_LANDING_PROMPT` 并无条件发出 `ITERATION_EXHAUSTED(layer="tool_call", limit="wall_clock")` 与 `stop_reason="run_deadline_exceeded"`。即使章节已完整保存且没有任何未完成任务，作者仍看到「继续完成」卡片，点击后发送 `"请基于上一步结果继续完成剩余任务"`，触发计划外新章或反复改稿；另有首轮开放式开书提示单轮连写 7 章直至撞墙。
2. **批量建人物/设定档时，单条回复并列调用多个流式 `create_file` 误触请求级失败熔断（线上 46 次 `pending_empty_file_unwritten` 及熔断中断扣费）**：
   - 作者要求一次建立多份角色/设定档案时，MiniMax 常在同一条回复里并列发出 5～7 个不带 `content` 的 `create_file`。第 1 个占住待写槽位，同批其余调用在模型看到 `awaiting_body` 前即被 `pending_empty_file_unwritten` 挡回；`ToolFailureBreaker` 将它们累加进 `total_failures`（上限 10），两轮批量建档即触发不可退费的 `tool_failure_circuit_open`。
3. **空文件补写轮与总表建档在思考中起草整张表（2.5 万字 thinking）挤爆单次输出上限，且压缩重试人为调低 `reasoning_effort`**：
   - 复杂排表建档进入 `FILE_BODY_RESUME` 纠偏轮时，模型在输出 `<file>` 前耗费 25,462 字 `thinking_content` 推演排表，连续两次撞上输出上限而未写出 `</file>`；同时 `compaction.py` 在重试时将 `reasoning_effort` 降为 `"low"`（甚至在首次仅因 400 超窗减半预算重试时也降低了思考强度），人为折损模型默认推理质量。
4. **原文拆分建档 `create_file(source_text=...)` 因换行符与行尾空白微小差异被拒（21 次）**：
   - 作者粘贴含 `\r\n`、行尾空格或多重空行的设定文本时，模型 JSON 参数常归一为 `\n` 并裁去行尾空格，被严格子串 `source_text in author_message` 连续打回 2～3 轮后被迫退回 `<file>` 重抄全文。
5. **长章节局部修改 `edit_file:anchor_not_found` 偏高（191 次）**：
   - 恢复提示笼统要求「按准确 id 重读当前段落」，未明确引导长章节优先用 `query_files(id=..., query=关键词)` 取逐字命中片段或用 `start_char` + `max_chars` 分段读取。

## Decision

1. **结构化收敛 `self_check` 内层 `wall_clock` 软着陆、对齐任务板未完检查与单章默认粒度**：
   - `agent/core/task_board_completion.py` 新增 `has_incomplete_task_board_items()`，读取当前会话任务板是否仍有 `in_progress` / `pending` 任务。
   - `agent/loop/engine.py` 记录当前 run 的 `run_kind`：当 `followup_kind == "self_check"` 在循环顶端撞上 `past_wrap_up`，或 `run_kind == "self_check"` 在内层触发 `ITERATION_EXHAUSTED(layer="tool_call", limit="wall_clock")` 时，先让 `MESSAGE_END` 按 `end_turn` 交由 `StreamAdapter` 结算流状态，再核验结构化事实（无报错、无 `pending_empty_file`、无 `unsaved_chapter_replacement_ids`、无 `cut_bodies`、无未执行工具协议、无运行中/边界插话，且跑完 `auto_finalize_task_board_on_completion` 后 `has_incomplete_task_board_items()` 为 `False`）——全部满足时抑制 `wall_clock` 暂停卡并以 `end_turn` 正常收尾；若任务板仍有未完成章节或存在任一未完结构化待办，照旧发出 `wall_clock` 暂停卡。
   - `LOOP_TIME_LANDING_PROMPT`（`runner.py`）区分「本轮请求内容已全部保存完成（直接写 1～3 句总结，不编造待续项）」与「确有未完成部分」；`LOOP_WORKING_RULES`（`loop/prompts.py`）明确作者仅给开书设定或未要求连写多章时，单轮最多写完当前 1 章即停下确认；中英文 `chat.json` 的 `timeLimitSummary` 与 `actionPrompt.continue` 明确已保存内容完好、仅在确有未完部分时继续且不额外续写未要求新章。
2. **豁免同批并列建档的 `pending_empty_file_unwritten` 累计熔断计数，并明确逐个流式建档规则**：
   - `agent/openai_agents/tool_failure_breaker.py` 将同条回复内并列流式建档产生的 `_PARALLEL_BATCH_ORDERING_ERROR_TYPES = frozenset({"pending_empty_file_unwritten"})` 从 `total_failures` 与 `_streaks` 中豁免；跨回复跳过 `<file>` 的 `file_body_pending` 仍保留在 `_ORDERING_ERROR_TYPES`（不触发同参数三次熔断，但计入请求级 10 次累计兜底）。
   - `CREATE_FILE_TOOL` 与 `prompts/base.py` 写明生成多份新资料文件时逐个调用 `create_file` + `<file>…</file>`。
3. **纯提示词收敛思考起草（不改 `reasoning_effort`），并移除压缩重试的 `reasoning_effort="low"` 覆盖**：
   - `prompts/base.py` 的 `_TOOL_USAGE_THINKING` 将「不在思考里打草稿/逐条预排长表」扩展到 `create_file` 资料与总表文件；`loop/engine.py` 在 `_EMPTY_FILE_CORRECTION_CONTEXT`、`_EMPTY_FILE_CUT_CORRECTION_CONTEXT`（`output_capped=True` 时指出上次因思考或前置内容过长撞上单次输出上限）与 `_CHAPTER_REPLACE_RETRY_CONTEXT` 中要求只留两三行提纲、首行直接输出 `<file>`，全程不修改 `reasoning_effort`。
   - `loop/compaction.py` 移除 `_ATTEMPT_EFFORTS = (None, "low")`，所有压缩尝试均保持 `reasoning_effort=None`（模型默认思考强度）；首次输出为空、截断或混入工具标签时，第二次尝试仅追加精炼 `_COMPACT_RETRY_NOTE` 提示。
4. **`create_file(source_text=...)` 支持有界换行与行尾空白归一匹配**：
   - `mcp_tools._match_verbatim_author_span` 保留字节级精确子串快路径（保护有意保留首尾空白的原文），未命中时仅对 `\r\n`/`\r`、行尾空格/Tab 与连续空行个数做偏移映射归一，在 `author_message` 中唯一命中连续原文切片时返回对应原始文本（统一 `\n` 换行），严禁改写词句、跳段拼接或补字段。
5. **`edit_file:anchor_not_found` 精准重读引导**：
   - `file_ops/edit.py`、`tool_failure_breaker.py` 与 `prompts/base.py` 在保留既有恢复约束的同时，明确引导长文件通过 `query_files(id=..., query=关键词)` 获取逐字命中片段或用 `start_char` + `max_chars` 分段读取。

## Alternatives considered

- **在 `FILE_BODY_RESUME` 空文件补写轮与压缩重试中将 `reasoning_effort` 设为 `"low"`。** 最强理由：物理限制思考 token 占比，直接降低截断概率。未采用：写作与长篇交接摘要高度依赖模型默认推理质量，人为改低思考强度属于用牺牲设定准确性去修提示边界缺口（且在 400 超窗减半预算重试时根本无须降思考强度）；通过明确禁止在思考中预排整张表/起草全文即可在不损失推理深度的前提下消除输出饥饿。
- **在服务端通过匹配模型总结里的「已完成 / 没有待续」关键词抑制 `run=1` 的 `wall_clock` 暂停卡。** 最强理由：连单次 run 跨过 600s 的软着陆暂停卡也能全部消除。未采用：直接违反仓库语义决策红线（禁止用关键词/正则猜测自然语言完成度，模型自报不构成交付证明）；对结构化可证的 `self_check` + 任务板完结路径直接静默正常收尾，其余 `run=1` 软着陆通过收尾提示词与前端中性「继续」提示消除误导，既严守架构边界又切断连环续写诱因。

## Consequences

- **收益**：单章写作自查跨过 600s 不再弹出假「未完成」暂停卡，多章任务板未完时仍保留续写入口；一次创建多份角色/设定档不再误触熔断扣费；大表/资料补写与长历史压缩保持完整默认推理质量且不再在思考里空耗输出配额；Windows/浏览器粘贴原文拆分建档一次通过。
- **代价**：未建任务板的单次长 run（`run=1`）若恰好在最后一次总结调用前跨过 600s，仍会展示中性文案的时限收尾卡（但点击继续不会再强制续写计划外新章节）。
