# Agent 模块架构指南

ZenStory 写作 agent 的服务端：路由 → 多角色工作流（planner / hook_designer / writer / quality_reviewer）→
openai-agents-python 运行时（DeepSeek `deepseek-flash`，OpenAI 兼容 Chat Completions）→ SSE。
工作流是 `graph/` 里的自研编排，**不使用 LangGraph**；`StreamAdapter.process_langgraph_events` 只是
`process_workflow_events` 的兼容别名。

路径约定：不带前缀的路径相对 `apps/server/agent/`；模块外的文件写全路径（例如 `apps/server/utils/logger.py`，
不要和 `agent/utils/` 混淆）。行为细节与取舍以 `.agents/notes/` 里链接的决策记录为准，改行为前先读对应记录。

## 目录结构

```
agent/
├── service.py              # AgentService.process_stream：一次 /agent/stream 的主入口
├── stream_adapter.py       # StreamAdapter：workflow 事件 → SSE 帧，<file> 正文捕获与落库，CHAT_HOLD
├── suggest_service.py      # /agent/suggest 的下一步建议
├── constants.py            # 子系统共享常量
├── context/                # 项目上下文组装
│   ├── assembler.py        # ContextAssembler
│   ├── budget.py           # token 预算与请求级 prompt 台账
│   └── prioritizer.py      # 优先级分档
│                           # 注：没有会话压缩/总结模块，历史只按 token 预算取最近窗口
├── core/                   # 基础设施
│   ├── events.py           # SSE 事件（EventType）与帧构造
│   ├── workflow_events.py  # 图内部事件（StreamEventType / StreamEvent）
│   ├── sse_pump.py         # SSEStreamPump：心跳、请求墙钟时限、作者停止
│   ├── stream_billing.py   # StreamBillingTracker：按发出的帧判定计费 / 退还
│   ├── stream_errors.py    # 错误码、固定文案、retryable / refundable
│   ├── round_outcome.py    # 停止 / 断线的一轮怎样收尾（终态落库、空白占位文件）
│   ├── run_meter.py        # 请求级模型调用计数与耗时
│   ├── steering.py         # 运行中插话（steering）队列
│   ├── progress_channel.py # 长工具（parallel_execute）的进度事件通道
│   ├── output_truncation.py# 输出截断后的续写提示
│   ├── author_facing_text.py # 作者可见文字整理（AgentTextShaper、交接原因、角色中文名）
│   ├── session_loader.py   # 历史窗口 + 面包屑 + 跨轮工作集
│   ├── message_manager.py  # 系统提示构建、历史保存、作者语言判断
│   ├── stream_processor.py # <file>…</file> 捕获状态机
│   ├── llm_client.py / deepseek_client.py # OpenAI 兼容客户端
│   └── metrics.py
├── graph/                  # 多 agent 工作流（自研编排，非 LangGraph）
│   ├── writing_graph.py    # run_writing_workflow_streaming：路由、agent 循环、交接、自动质检
│   ├── router.py           # 意图路由（RouterDecision、跨轮路由延续）
│   ├── nodes.py            # run_streaming_agent：拼系统提示并调用 runner
│   ├── state.py            # WritingState
│   ├── author_scope.py     # 按作者本轮要求收窄工作流
│   ├── body_writes.py      # writer 本轮写进正文类文件的字数
│   ├── completion_evidence.py # run 收尾的文字信号
│   └── review_evidence.py  # 送审前的程序检测证据
├── openai_agents/          # openai-agents-python 适配层
│   ├── runner.py           # run_openai_agents_streaming_agent：Runner.run_streamed + 事件映射
│   ├── model.py            # DeepSeek 模型接线
│   ├── tools_adapter.py    # 项目工具 → SDK FunctionTool（只读 / 先问清楚时拒绝写工具）
│   ├── tool_failure_breaker.py # 请求级工具失败熔断
│   ├── repeat_read_guard.py    # 重复读取无进展守卫
│   ├── usage_hooks.py      # 每次模型调用的用量记账
│   ├── intra_run_trimmer.py# run 内上下文安全阀
│   └── events.py
├── prompts/                # 共享协议与角色提示（项目写作配置只存数据库）
│   ├── base.py             # get_base_prompt、UNIFIED_EXECUTION_PROTOCOL
│   ├── subagents.py        # ROUTER / PLANNER / HOOK_DESIGNER / WRITER / QUALITY_REVIEWER 提示
│   └── suggestions.py      # 建议生成提示
├── schemas/context.py      # 上下文数据模型
├── skills/                 # 技能系统（标准 SKILL.md，渐进式加载，永不执行脚本）
│   ├── active_skills.py    # 当前用户启用中的技能视图
│   ├── context_injector.py # L1 技能目录（名称 + 用途）
│   ├── content_budget.py   # 技能正文 token 预算
│   └── package.py          # SKILL.md 解析、zip 导入安全检查、导出
├── tools/                  # 工具
│   ├── tool_schemas.py     # provider-neutral 工具 schema（TOOL_SCHEMAS）
│   ├── mcp_tools.py        # 工具实现（MCP_TOOL_HANDLERS）与 ToolContext
│   ├── registry.py         # schema / handler / agent 工具集的唯一接线处
│   ├── parallel_executor.py# parallel_execute
│   ├── file_executor.py / file_ops/ # 文件 CRUD、编辑、项目操作
│   ├── author_changes.py   # 作者在 AI 写过之后的净改动
│   ├── author_edit_guard.py# 作者手改文件的删除 / 整份覆盖保护
│   ├── task_progress.py    # 任务板编号范围与实际文件对照
│   └── permissions.py
├── utils/                  # aho_corasick、token_utils、world_model_visibility
└── llm/                    # 空的遗留占位包，不要往里加代码
```

## 请求与流

1. `apps/server/api/agent.py` 的 `POST /api/v1/agent/stream`：鉴权、项目权限（`verify_project_access`）、按用户限流、
   预扣一次 AI 对话额度；用 `SSEStreamPump` 包住 `AgentService.process_stream`，`StreamBillingTracker` 记账每一帧。
   同文件还有 `/stop`（作者停止）、`/steer`（运行中插话）、`/suggest`、`/health`。新增会触发 LLM 的端点必须同时有
   鉴权 + 项目权限 + 成本上限 + 按用户限流（见该文件模块 docstring）。
2. `AgentService.process_stream`（`service.py`）：`SessionLoader` 取历史与跨轮工作集，`ContextAssembler` 组装上下文，
   `MessageManager.build_system_prompt` 建系统提示，`ToolContext.set_context` 设置工具上下文，然后
   `StreamAdapter.process_workflow_events(run_writing_workflow_streaming(...))`，结束时保存历史。
3. `run_writing_workflow_streaming`（`graph/writing_graph.py`）选初始 agent，顺序是：`resume_route_after_exhaustion`
   （上一轮截停后只回「继续」）→ 笼统要求先问清楚 → `inherit_routing_after_clarification`（仅 llm 策略）→
   `router_node`（llm）或固定从 writer 开始（off）。策略来自 `AGENT_ROUTER_STRATEGY`；前端 `generation_mode=fast`
   强制 off 并关自动质检，`quality` 强制 llm 并开自动质检。之后循环跑 agent，直到完成或达到协作轮数上限。
4. `nodes.run_streaming_agent`：基础提示 + 角色提示 + `scope_directive` 等 → `openai_agents/runner.run_openai_agents_streaming_agent`。
   runner 用 `Runner.run_streamed` 跑 SDK，事件映射：`response.output_text.delta` → TEXT，reasoning delta → THINKING，
   `tool_called` → TOOL_USE，`tool_output` → TOOL_RESULT / HANDOFF / WORKFLOW_STOPPED（熔断后为
   MESSAGE_END(stop_reason=tool_failure_circuit_open) + ERROR）。
5. `StreamAdapter.process_workflow_events`：`core/workflow_events.StreamEvent` → SSE 帧（`core/events.EventType`），
   用 `StreamProcessor` 捕获 `<file>` 正文并落库。

预算（`apps/server/config/agent_runtime.py`，均可用同名环境变量覆盖）：`AGENT_COLLABORATION_MAX_ITERATIONS`（12，agent 轮数）、
`AGENT_TOOL_CALL_MAX_ITERATIONS`（60，单次 run 的 SDK max_turns，最后 `runner.SOFT_LANDING_TURNS` 轮提示收尾）、
`AGENT_RUN_MAX_MODEL_CALLS`（120，请求级）、`AGENT_RUN_WALL_CLOCK_TIMEOUT_S`（780，须早于 Railway 900 秒断连）、
`AGENT_SSE_HEARTBEAT_INTERVAL_S`（15）、`AGENT_AUTO_REVIEW_THRESHOLD_CHARS`（500）。见 [runtime-budget-and-heartbeat][n-budget]、
[no-progress-guard-and-soft-cap][n-noprogress]、[platform-deadline-and-terminal-guard][n-deadline]。

## 多 Agent 协作

`graph/router.py` 的 `WORKFLOW_AGENTS` 是初始 agent 之后的计划序列上限：

| workflow_plan | 序列 |
|---|---|
| quick | writer（必要时再 review） |
| standard | planner → writer |
| full | planner → hook_designer → writer |
| hook_focus | hook_designer → writer |
| review_only | quality_reviewer |

每条序列之后都可能经自动质检门或显式交接进入 quality_reviewer。`plan_workflow_agents` 按用户范围裁剪：`write_content=false` 剔除 writer，`read_only=true` 整条清空。交接有三种：
显式 `handoff_to_agent`；按 `workflow_agents` 的计划交接；自动质检门（writer 本轮写进 draft / script 的字数达到阈值后交给
quality_reviewer；计划内 planner→writer 同一交付的成功正文写入接续计算）。审查最多 `MAX_REVIEW_ROUNDS` 轮，返修稿不再自动送审。

### Router

- 输入：用户本轮原话；有上一条 assistant 消息时，user 消息为 `[上一轮助手结尾]`（最后 600 字 + 状态卡摘要）+
  `[上一轮路由]`（落库 routing 的一行 JSON）+ `[用户本轮原话]`（`build_router_user_message`，系统提示不变）。确定性的延续（见上文第 3 步）先于 LLM 路由，命中时不调路由模型。
  ROUTER_PROMPT 区分补问作答（沿用上一轮全部路由字段）和确认提议的下一步（按那一步路由，不沿用
  read_only=true / write_content=false / 已交付的 scope）。
- 输出：`current_agent`、`workflow_plan`、`workflow_agents`（已裁剪）、`routing_metadata`（`RouterDecision` 全量，含
  `write_content`（None = 旧格式未给出）、`read_only`、`scope`；ROUTER_PROMPT 要求 scope <= 40 字，`MAX_SCOPE_CHARS`=80 硬截断）。
- 决策记录：[router-previous-turn-context][n-router-ctx]、[routing-continuity][n-routing]、[routing-inherits-the-task-owner][n-owner]。

### 用户范围与交接的不变量

每条一句规则 + 负责的代码 + 决策记录。改动前先读记录，不要只看这里的摘要。

- **以提问收尾就停下**：最后一次工具调用之后的文字以问号结尾（`nodes.ends_with_question_to_user`）时不走计划交接，等作者回答；
  `request_clarification` 仍是首选信号；显式 handoff 和自动质检门不看这个信号；规划类角色以提问收尾但 `write_content=true`
  且计划里还有 writer 时保留 writer、交接说明写明按推荐方案写（`author_scope.planning_question_keeps_writer`）。
  [scope][n-scope]、[planner-question][n-planner-q]
- **计划交接不扩大交付**：交接说明固定追加 `author_scope.PLANNED_HANDOFF_SCOPE_NOTE`（已完成的不重做、不改刚交付的文件、
  不追加作者没要的产出）。[vague-and-plan-files][n-vague]
- **笼统要求先问清楚**：`author_scope.is_vague_edit_request` 命中时不走路由，writer 单独一轮先问（`state["clarify_first"]`，写文件
  工具以 `error_type="clarify_first"` 拒绝，显式交接丢弃）；上一轮只提了一处修改或已经问过一次时不问。[vague-and-plan-files][n-vague]
- **规划要落进文件**：作者要了规划、规划师却把至少 5 个「第 N 章 / 集」只贴在对话里且本请求的大纲文件没覆盖时，同一规划师补一轮
  写进大纲文件（每请求一次，`author_scope.chat_only_plan_units` / `request_asks_for_a_plan`）。[vague-and-plan-files][n-vague]
- **作者改了什么给模型看，不猜授权**：`tools/author_changes.py` 按版本历史算净差异放进 `<author_edits>`（`query_files` 按 id 读全文时也带）；
  `edit_file` 在锁内比对本请求模型看过的正文（`mcp_tools._ReadSnapshots`，读到的内容由 `usage_hooks.ReadSnapshotPromotionMixin`
  在下一次模型调用起生效），作者在生成期间又改了就以 `stale_content` 拒绝，改到作者写的段落时结果带 `author_edits_touched`。
  [author-edits-shown-not-guessed][n-author-edits]
- **删除和整份覆盖仍拦**：作者本轮没点名要删 / 重写、也不是正开着它提出时，`delete_file`（含递归删除）和剧本 `create_file` 复用同名
  剧集不执行，返回 `error_type="author_edit_protected"` 让 agent 问作者；作者在消息里点名这个文件并在那一处说删（或整份重写）时才执行
  （`tools/author_edit_guard.py`），工作集也标注这类文件；前端卡片把它和
  `clarify_first` 显示成「保留了你改过的内容」/「先问清楚再改」，不显示失败。[respects-manual-edits][n-manual]、[guard-reads-offer][n-offer]
- **作者可见文字**：每个 agent run 的 TEXT 经 `core/author_facing_text.AgentTextShaper` 补段落空行；作者写中文时，紧接工具调用的
  英文过程句不发给作者。[no-english-narration][n-narration]
- **只给作者一份总结**：writer（非快速模式、非返修、未到审查上限，且开着自动质检门或计划里下一个是 quality_reviewer）写正文类文件、
  可能送审时（create_file 的正文还没写出来；edit_file / write_chapter 累计字数到了送审阈值；或计划送审）图发 `CHAT_HOLD(begin)`，StreamAdapter 扣住之后的对话文字（正文不扣）；真的交给
  quality_reviewer 时在 HANDOFF 前发 `CHAT_HOLD(discard)`，由审稿人（或返修后的 writer）写唯一总结，writer 的问题以
  `[内容创作者要问作者的问题]` 交给它们；不送审时扣着的文字在下一个 agent / 交接 / 收尾 / 出错事件前或流结束时原样放出
  （run 中途的 STEERING_RECEIVED 不放出）。CHAT_HOLD 不发给前端。[single-summary][n-summary]
  质量门可用且作者要正文时，计划下一位为 writer 的 planner 也按成功正文写入扣留；实际交给 writer 且没有收尾问题才丢弃，
  正文记录接续给 writer。只有规划、失败写入、未执行交接和提问原样释放。[planned-body-review][n-planned-body]
- **送审证据用落库后的事实**：送审交接附 `[本轮正文字数]`（落库后的 `count_words`）和 `[作者的篇幅要求]`
  （`review_evidence.find_length_requirements`），读取有时间上限、超时用已读到的；`[待审查内容]` 取本请求写过的正文落库后的样子，
  放不进预算的长文件只附本请求改动的那段（写入前正文由 `ToolContext.remember_pre_write_content` 记下）。
  [single-summary][n-summary]、[writer-delivery-requirements][n-length]、[thinking-loop-inputs][n-thinking]
- **自动质检门只认成功的正文写入**：按 `graph/body_writes.py` 计 draft / script 里实际写进的字数；改大纲、设定、角色和聊天文字不算；
  写入全部失败时不送审。[single-summary][n-summary]、[tool-failure-breaker][n-breaker]、[review-gating][n-review]
- **StreamProcessor 丢掉 IDLE 下独占一行的标记**：没有进行中的捕获时，独占一行（不在 ``` 围栏里）的 `<file>` / `</file>` 不进对话，
  行内提到、反引号包着的照常保留。[single-summary][n-summary]
- **只读请求不改文件**：router `read_only=true` 时不交接给有写权限的 agent（显式 handoff 也拦），提示卡
  `WORKFLOW_STOPPED(reason="read_only_handoff_blocked")` 推迟到终止事件之前或作为图的最后一个事件发出，`apps/server/api/agent.py` 按
  `core/events.NON_TERMINAL_WORKFLOW_STOPPED_REASONS` 不把它当终止帧（之后若异常仍补发兜底 error 帧并退款）；交接给 quality_reviewer
  这类只读 agent 照常；`tools_adapter` 对 `registry.FILE_WRITE_TOOL_NAMES` 一律以 `error_type="read_only_request"` 拒绝（工具仍在清单里，
  描述标明本轮不可用）并经熔断器记账，`parallel_execute` 整次拒绝，
  `update_project` 照常；只读请求留下的空文件直接回滚、不补写。[scope][n-scope]
- **调了不存在的工具不致命**：runner 的 `RunConfig` 设 `tool_not_found_behavior="return_error_to_model"`，返回
  `error_type="tool_not_found"` 并经熔断器记账（同名连续 3 次的熔断原因为 `repeated_unavailable_tool_call`）。[scope][n-scope]、[tool-failure-breaker][n-breaker]
- **范围指令进每个 agent 的系统提示**：路由的范围判断转成 `state["scope_directive"]`，由 `nodes.run_streaming_agent` 追加到系统提示
  末尾，每次跑 agent 前按当前 agent 重新生成（「规划完直接交接 writer」只给规划类角色）；计划交接的 `handoff_packet.todo` 带用户
  `scope`。[scope][n-scope]
- **空文件纠偏轮由建空文件的 agent 自己补写**；被纠偏打断的一轮若以提问收尾，「等用户回答」带过纠偏轮。[scope][n-scope]
- **写完 `</file>` 就结束 run 时同一 agent 续写**：有写权限的 agent 这次 run 里 create_file 成功、最后的文字以写完的 `<file>` 正文
  结束（`completion_evidence.ends_on_file_block`），且没有显式交接、澄清、轮数耗尽、错误时，同一 agent 再跑一轮（交接说明以
  `FILE_STREAM_CONTINUATION_MARKER` 开头）；放在计划交接 / 自动质检之前，正文与正文写入记录接续给续写轮；受 `max_iterations` 约束，
  每轮都要真的新建并写完文件才再续。
  [file-stream-continuation][n-continue]
- **「已保存 / 已还原 / 已修改」不做文字检测**：靠 `prompts/base.py` 的 `UNIFIED_EXECUTION_PROTOCOL` 约束（只有本轮对应工具成功后才这样说，
  没改就直说没改）。[file-stream-continuation][n-continue]
- **任务板只按实际产物收尾**：`_auto_finalize_task_board_on_completion` 只按本请求实际写出、仍存在且有正文的文件收尾 in_progress 任务；
  编号范围（`tools/task_progress.py`）全部有文件才 done，否则记 `done_units` / `progress`（模型重存任务板时同名任务的记录接过去）；
  带 `artifact` 的要写出这个文件；本请求什么都没写不补；模型自己标 done 而
  缺文件时 `execute_update_plan` 返回 `unverified_done`。[file-stream-continuation][n-continue]
- **重复建挡路的空文件**：空文件守卫（一次只流式写一个文件）挡住的 create_file，若要建的正是挡路的那个空文件，返回那个文件
  （`content=""` + `next_step`）；挡路的是别的文件时照旧 `pending_empty_file_unwritten`（`mcp_tools._repeat_of_pending_empty_file`）。
  [repeat-create][n-repeat]
- **项目备注分来源**：未标来源 / 【作者确认】/ 【AI 提议·待作者确认】三块（`apps/server/config/project_notes.py`）；`update_project` 的 notes
  条目只有逐字出现在作者本轮消息里的 `author_quotes` 原话才进「作者确认」，否则记成 AI 提议（`notes_recorded_as_proposals`），一句原话不给别的条目作保；
  `proposals` 整体替换 AI 提议块，与 notes 共用 4000 字（超出返回 `notes_over_budget`，不写任何字段）；上下文分开渲染
  （`render_project_notes`），标题行能解析回原分区，AI 提议写明不是设定。[project-notes][n-notes]
- **回复语言跟随作者最近一条消息**（`message_manager.detect_author_language`），系统提示末尾写明本轮语言，所有角色共用；界面语言只在
  看不出时兜底。[reply-language][n-lang]

## 流的终止、停止与计费

- 每个 `/agent/stream` 恰好以一个终止帧结束（`done` / `workflow_complete` / 终止性 `workflow_stopped` / `error`）；逃逸异常先补发 error 帧。
  错误码与固定文案在 `core/stream_errors.py`。[terminal-frame][n-terminal]、[error-and-refund][n-refund]
- 计费规则以 `core/stream_billing.py` 模块 docstring 为准：作者停止 / 断线 / 墙钟超时时，本轮没有实质产出（墙钟超时还要求结束时不在写
  回复）则退还；失控停止（no_progress / 模型调用上限 / 工具轮数耗尽）且没有任何写入成功则退还；正常结束计费；出错时 error 帧
  `refundable: false`（如工具失败熔断）、已有产出或正在写回复则计费，其余退还。「实质产出」只有写入留下正文，或一段回复正文达到
  `PROSE_MIN_CHARS`；思考、读文件、只建空文件、过渡旁白都不算。退还落库后补发 `quota_refunded`，结算只做一次。
  [error-and-refund][n-refund]、[stop-output-definition][n-output]
- 作者停止走 `POST /agent/stop` → `SSEStreamPump.request_stop()`，连接上补发 `workflow_stopped(user_stopped)` 与 `done`；停止 / 断线 / 超时
  的取消路径会把进行中的 `<file>` 捕获补存，并由 `core/round_outcome.py` 写终态、退还时移除本轮空白占位文件。
  [graceful-stop][n-stop]、[abort-flushes-capture][n-flush]、[stop-output-definition][n-output]
- 墙钟时限由 `SSEStreamPump` 执行：到点先发真实超时终态再等生产者收尾。[platform-deadline][n-deadline]、[deadline-terminal][n-deadline2]
- 运行中插话：`/agent/steer` 写入 `core/steering.py` 的队列，runner 在边界处消费。[steering-busy-409][n-steer]
- SSE 事件类型以 `core/events.py` 的 `EventType` 为准，不在这里抄表。

## 上下文与跨轮记忆

- `ContextAssembler`（`context/assembler.py`）收集焦点文件、附加文件、用户引用、相关大纲 / 角色 / 设定。分档（`context/prioritizer.py`）：
  CRITICAL（焦点文件、用户引用）→ CONSTRAINT（角色设定、高重要度设定、前一章，角色卡与设定排在章节之前）→ RELEVANT（兄弟章节、
  中重要度设定、检索片段）→ INSPIRATION。非 CRITICAL 档截断后不足 500 字且不到原文一半的残片丢弃（仍在文件清单里）；检索只排除最终
  入选的条目；角色 / 设定名作为子串出现在查询里时加分。
- 预算：上下文 `AGENT_CONTEXT_TOKEN_BUDGET`（32000）、历史窗口 `AGENT_CHAT_HISTORY_TOKEN_BUDGET`（32000），与系统提示、工作集共用
  `context/budget.DEFAULT_PROMPT_TOKEN_LEDGER_CEILING`（160000）台账，台账只收缩历史。
- 渲染：条目标题行为 `标题 (id=…) [全文]` 或 `[已截断：显示 x/y 字；全文用 query_files(id="…")]`（检索片段标 `[检索片段，非全文…]`）；
  标注 [全文] 的条目就是该文件截至本请求开始的完整内容（本请求内被修改过的文件除外）。`MessageManager` 只对原始上下文 strip + 兜底封顶；已在原始上下文完整出现的条目，
  world_model 里只列标题 + id。
- 跨轮（`core/session_loader.py`）：runner 回放历史时丢弃工具结果，新一轮靠面包屑（assistant 历史末尾的「[此前的工具操作]」，列创建 /
  编辑 / 删除和按 id 精确读取的文件）和工作集（最近 2 条 assistant 回复读写过的文件，按当前库内容整份注入，最多
  `AGENT_WORKING_SET_MAX_FILES`=8 份、`AGENT_WORKING_SET_MAX_CHARS`=60000 字）。`service.py` 用
  `SessionLoader.attach_working_set_to_user_content` 把它拼在本轮用户消息之前，不进系统提示、不改历史，前缀缓存可以一直命中。
- 决策记录：[full-text-and-working-set][n-context]、[characters-and-core-lore][n-lore]。

## 工具

工具清单以 `tools/registry.py` 为准：`DEFAULT_TOOL_NAMES`（planner / hook_designer / writer）、`QUALITY_REVIEWER_TOOL_NAMES`（审稿人，没有写工具）。

| 工具 | 用途 |
|---|---|
| `create_file` | 新建文件（只建空文件，正文随后用 `<file>` 流式写入） |
| `edit_file` | 精确编辑（replace / insert / append / delete） |
| `delete_file` | 删除文件 |
| `query_files` | 查询和读取文件 |
| `update_project` | 项目信息、备注、任务板 |
| `handoff_to_agent` | 交接给另一个 agent |
| `request_clarification` | 请作者澄清并暂停工作流 |
| `parallel_execute` | 并行执行多个子任务（schema 在 `tools/parallel_executor.py`） |
| `load_skill` / `read_skill_resource` | 技能 L2（方法与资源清单）/ L3（参考文件或 `path="SKILL.md"`，支持 `offset` 续读）；受每请求技能 token 预算限制，每请求最多读 `skills/content_budget.MAX_LOADED_SKILLS_PER_REQUEST`（3）个不同技能 |

文件流式写入分两次回复：这次回复以 `create_file` 工具调用结束；下一次回复以 `<file>` 开头写正文、`</file>` 结束，StreamAdapter 捕获后落库。
一次只流式写一个文件。[two-replies][n-two-replies]

## 提示词

- `prompts/base.py`：`get_base_prompt` 与 `UNIFIED_EXECUTION_PROTOCOL` 等共享协议。
- `prompts/subagents.py`：`ROUTER_PROMPT`、`PLANNER_PROMPT`、`HOOK_DESIGNER_PROMPT`、`WRITER_PROMPT`、`QUALITY_REVIEWER_PROMPT`。
- `prompts/suggestions.py`：建议生成。
- 项目写作配置只存数据库（`prompts/__init__.py`）；项目类型的默认模板在 `apps/server/config/project_templates.py`。

## 开发清单

### 添加工具

1. schema 加进 `tools/tool_schemas.py` 的 `TOOL_SCHEMAS`。
2. 实现加进 `tools/mcp_tools.py` 的 `MCP_TOOL_HANDLERS`（需要适配入参的可像 `parallel_execute` 一样在 `registry.TOOL_FUNCTIONS` 里接）。
3. 在 `tools/registry.py` 把它加进对应 agent 的工具集（`DEFAULT_TOOL_NAMES` / `QUALITY_REVIEWER_TOOL_NAMES`）。会改项目文件的工具还要加进
   `FILE_WRITE_TOOL_NAMES`（只读请求拒绝、`writing_graph._agent_can_write_files` 判断写权限）。
4. 会产生作者可见产出的工具：在 `core/stream_billing.py` 里定义它怎样算产出（`WRITE_TOOL_NAMES` 等），否则停止 / 出错时会被退还。
5. 前端：`apps/web/src/components/ToolResultCard.tsx` 的卡片、`apps/web/public/locales/{zh,en}/chat.json` 的 `chat:tool.<name>` 文案、
   `apps/web/src/lib/thinkingDisplay.ts` 的 `UNDERSCORE_IDENTIFIER_KEYS`。
6. 测试：`apps/server/tests/test_agent/test_tool_registry.py`（`validate_registry_alignment`）加上工具自己的用例。

### 添加 Agent 类型

1. 提示：在 `prompts/subagents.py` 写角色提示，注册到 `graph/nodes.py` 的 `specialized_prompts`；需要路由到它时更新 `ROUTER_PROMPT`。
2. 路由：`graph/router.py` 的 `AgentType`、`_AGENT_TYPES`、`get_next_node`、`_normalize_router_payload`、`_infer_workflow_from_agent`；
   新工作流还要改 `WorkflowType` 和 `WORKFLOW_AGENTS`。
3. 工具集：`tools/registry.py` 的 `AGENT_TOOL_NAME_MAP`（缺了会落到 writer 的工具集）。
4. 交接目标：`tools/tool_schemas.py` 里 `handoff_to_agent` 的 `target_agent` enum，和 `tools/mcp_tools.py` 的 `handoff_to_agent` 目标校验。
5. 作者可见名字：`core/author_facing_text.py` 的 `_AGENT_NAMES`、`graph/writing_graph.py` 的 `agent_names`；规划类角色加进
   `graph/author_scope.py` 的 `PLANNING_AGENTS`。
6. 前端：`apps/web/src/lib/agentDisplayName.ts` 的 `AGENT_DISPLAY_KEYS`、`apps/web/public/locales/{zh,en}/chat.json` 的 `chat:workflow.agents.<id>`、
   `apps/web/src/lib/thinkingDisplay.ts` 的 `UNDERSCORE_IDENTIFIER_KEYS`（带下划线的 id）或 `BACKTICK_ONLY_IDENTIFIER_KEYS`（普通英文词）。

## 调试

- 日志：`apps/server/utils/logger.py` 的 `log_with_context`（自动带 `apps/server/utils/request_context.py` 里的 `request_id` / `agent_run_id`）。每个请求结束时 `apps/server/api/agent.py`
  写一条 `Agent stream billing evaluated`，含 `billing_reason`、`produced_output` 和 `run_*` 运行摘要（模型调用数等，来自 `core/run_meter.py`）。
- 工具执行看 `tools/mcp_tools.py` 的返回结构（`status` / `error_type`）；事件转换看 `stream_adapter.py`；图的决策看 `graph/writing_graph.py` 的日志。
- 测试在 `apps/server/tests/test_agent/` 与 `apps/server/tests/test_api/`（如 `apps/server/tests/test_api/test_agent.py`、`apps/server/tests/test_api/test_agent_stream_hardening.py`）。

[n-budget]: ../../../.agents/notes/implemented/feature/2026-10-05-agent-stream-runtime-budget-and-heartbeat.md
[n-noprogress]: ../../../.agents/notes/implemented/bug-fix/2026-10-08-agent-no-progress-guard-and-soft-cap.md
[n-deadline]: ../../../.agents/notes/implemented/bug-fix/2026-10-13-agent-stream-platform-deadline-and-terminal-guard.md
[n-deadline2]: ../../../.agents/notes/implemented/bug-fix/2026-10-14-deadline-terminal-before-cancellation-cleanup.md
[n-router-ctx]: ../../../.agents/notes/implemented/bug-fix/2026-10-08-agent-router-previous-turn-context.md
[n-routing]: ../../../.agents/notes/implemented/bug-fix/2026-10-08-agent-routing-continuity.md
[n-owner]: ../../../.agents/notes/implemented/bug-fix/2026-10-11-routing-inherits-the-task-owner.md
[n-scope]: ../../../.agents/notes/implemented/bug-fix/2026-10-03-agent-handoff-respects-user-scope.md
[n-planner-q]: ../../../.agents/notes/implemented/bug-fix/2026-10-09-planner-question-keeps-requested-prose.md
[n-vague]: ../../../.agents/notes/implemented/bug-fix/2026-10-09-agent-vague-requests-and-plan-files.md
[n-author-edits]: ../../../.agents/notes/implemented/architecture/2026-10-12-author-edits-shown-not-guessed.md
[n-manual]: ../../../.agents/notes/implemented/bug-fix/2026-10-09-agent-respects-author-manual-edits.md
[n-offer]: ../../../.agents/notes/implemented/bug-fix/2026-10-11-author-edit-guard-reads-the-ais-offer.md
[n-narration]: ../../../.agents/notes/implemented/bug-fix/2026-10-09-agent-text-no-english-narration-or-glue.md
[n-summary]: ../../../.agents/notes/implemented/bug-fix/2026-10-11-author-reply-single-summary-and-real-word-counts.md
[n-planned-body]: ../../../.agents/notes/implemented/bug-fix/2026-10-14-planned-planner-body-review-and-summary.md
[n-length]: ../../../.agents/notes/implemented/feature/2026-10-14-writer-delivery-author-requirements.md
[n-thinking]: ../../../.agents/notes/implemented/bug-fix/2026-10-11-thinking-loop-contradictory-inputs.md
[n-breaker]: ../../../.agents/notes/implemented/bug-fix/2026-10-03-agent-tool-failure-circuit-breaker.md
[n-review]: ../../../.agents/notes/implemented/bug-fix/2026-10-08-agent-review-gating-and-handoff-details.md
[n-continue]: ../../../.agents/notes/implemented/bug-fix/2026-10-11-file-stream-continuation-and-truthful-completion.md
[n-repeat]: ../../../.agents/notes/implemented/bug-fix/2026-10-11-repeat-create-of-pending-empty-file.md
[n-notes]: ../../../.agents/notes/implemented/architecture/2026-10-11-project-notes-source-sections.md
[n-lang]: ../../../.agents/notes/implemented/bug-fix/2026-10-11-agent-identity-reply-language-rewrite-in-place.md
[n-terminal]: ../../../.agents/notes/implemented/architecture/2026-07-18-sse-stream-always-ends-with-terminal-frame.md
[n-refund]: ../../../.agents/notes/implemented/architecture/2026-10-05-agent-stream-error-and-refund-contract.md
[n-output]: ../../../.agents/notes/implemented/architecture/2026-10-09-stop-output-definition-and-round-outcome.md
[n-stop]: ../../../.agents/notes/implemented/architecture/2026-10-09-agent-graceful-stop-and-no-output-refund.md
[n-flush]: ../../../.agents/notes/implemented/bug-fix/2026-10-05-agent-stream-abort-flushes-file-capture.md
[n-steer]: ../../../.agents/notes/implemented/bug-fix/2026-10-05-steering-run-heartbeat-and-busy-409.md
[n-context]: ../../../.agents/notes/implemented/bug-fix/2026-10-08-agent-context-full-text-and-working-set.md
[n-lore]: ../../../.agents/notes/implemented/bug-fix/2026-10-08-agent-context-keeps-characters-and-core-lore.md
[n-two-replies]: ../../../.agents/notes/implemented/bug-fix/2026-10-10-file-streaming-protocol-two-replies.md
