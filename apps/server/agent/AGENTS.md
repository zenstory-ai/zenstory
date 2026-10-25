# Agent 模块架构指南

ZenStory 写作 agent 的服务端：路由 → 多角色工作流（planner / hook_designer / writer / quality_reviewer）→
openai-agents-python 运行时（OpenAI 兼容 Chat Completions，主备两个供应商，见 `core/llm_providers.py` / `core/llm_failover.py`）→ SSE。
工作流是 `graph/` 里的自研编排，**不使用 LangGraph**。

路径约定：不带前缀的路径相对 `apps/server/agent/`；模块外的文件写全路径（例如 `apps/server/utils/logger.py`，
不要和 `agent/utils/` 混淆）。行为细节与取舍以 `.agents/notes/` 里链接的决策记录为准，改行为前先读对应记录。

## 语义判断边界

遵循仓库的 [Agent semantic decisions](../../../AGENTS.md#agent-semantic-decisions)：禁止用关键词表、子串/整句匹配、正则、字数或标点启发式判断自然语言里的意图、确认、澄清、续接、角色、读写范围及是否完成。语义由现有 router/model 的结构化合同表达，权限和交付由程序核验真实状态与产物；不能只因模型自报就授权或结算完成。

协议标记、JSON/枚举、文件路径、精确编辑锚点与版本并发校验不属于自然语言语义猜测，保护仍须保留。下文是现有实现的架构说明，不豁免其中尚待迁移的字符串语义分支；不能为遵守本规则而直接删除只读、内容、终态或计费保护，也不默认新增一轮分类模型调用。

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
│   ├── author_facing_text.py # 作者可见文字整理（AgentTextShaper、交接原因、角色名：中文界面用中文名，英文界面只换内部 id）
│   ├── session_loader.py   # 历史窗口 + 面包屑 + 跨轮工作集
│   ├── message_manager.py  # 系统提示与本轮上下文构建、历史保存、作者语言判断
│   ├── stream_processor.py # <file>…</file> 捕获状态机
│   ├── llm_providers.py    # 主 / 备供应商配置与 OpenAI 兼容客户端
│   ├── llm_failover.py     # 主备切换：会话固定、熔断、只在没有输出时换供应商
│   ├── llm_client.py       # 输入建议 / 润色的单次调用
│   └── metrics.py
├── graph/                  # 多 agent 工作流（自研编排，非 LangGraph）
│   ├── writing_graph.py    # run_writing_workflow_streaming：路由、agent 循环、交接、自动质检
│   ├── router.py           # 意图路由（RouterDecision、跨轮路由延续）
│   ├── nodes.py            # run_streaming_agent：组装本次运行的系统指令并调用 runner
│   ├── state.py            # WritingState
│   ├── author_scope.py     # 按作者本轮要求收窄工作流
│   ├── body_writes.py      # writer 本轮写进正文类文件的字数
│   ├── completion_evidence.py # run 收尾的文字信号
│   └── review_evidence.py  # 送审前的程序检测证据
├── openai_agents/          # openai-agents-python 适配层
│   ├── runner.py           # run_openai_agents_streaming_agent：Runner.run_streamed + 事件映射
│   ├── model.py            # FailoverChatModel：每次模型调用按主备选择供应商
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
└── utils/                  # aho_corasick、token_utils、world_model_visibility
```

## 请求与流

1. `apps/server/api/agent.py` 的 `POST /api/v1/agent/stream`：鉴权、项目权限（`verify_project_access`）、按用户限流、
   预扣一次 AI 对话额度；用 `SSEStreamPump` 包住 `AgentService.process_stream`，`StreamBillingTracker` 记账每一帧。
   同文件还有 `/stop`（作者停止）、`/steer`（运行中插话）、`/suggest`、`/health`。新增会触发 LLM 的端点必须同时有
   鉴权 + 项目权限 + 成本上限 + 按用户限流（见该文件模块 docstring）。
2. `AgentService.process_stream`（`service.py`）：`SessionLoader` 取历史与跨轮工作集，`ContextAssembler` 组装上下文，
   `MessageManager.build_system_prompt` 建只含跨轮、跨角色不变内容的系统提示，`build_turn_context` 建本轮上下文
   （拼进本轮用户消息，见「上下文与跨轮记忆」），`ToolContext.set_context` 设置工具上下文，然后
   `StreamAdapter.process_workflow_events(run_writing_workflow_streaming(...))`，结束时保存历史。
3. `run_writing_workflow_streaming`（`graph/writing_graph.py`）使用现有 `router_node` 的结构化决策选择角色；
   不按「继续」词表、短答字数、只读关键词或笼统改稿正则绕过路由。`turn_disposition` 区分新/变更请求、
   中断续接和澄清作答；只有匹配可信持久化状态才继承，权限只收窄。`AGENT_ROUTER_STRATEGY=off` 普通请求仍固定 writer，
   但存在可信中断/澄清状态时也走同一个 router，避免续接丢失范围。前端 `generation_mode=fast` 照常路由、只关自动质检，
   `quality` 强制 llm 并开自动质检。[fast-mode-default][n-fast-default]之后循环直到完成或协作轮数上限。
4. `nodes.run_streaming_agent`：共用系统提示 + `nodes.build_agent_directive`（角色提示 + `scope_directive` 等，作为这次输入的最后一条消息）
   → `openai_agents/runner.run_openai_agents_streaming_agent`。[cache-stable-prompt-layout][n-cache-layout]
   runner 用 `Runner.run_streamed` 跑 SDK，事件映射：`response.output_text.delta` → TEXT，reasoning delta → THINKING，
   `tool_called` → TOOL_USE，`tool_output` → TOOL_RESULT / HANDOFF / WORKFLOW_STOPPED（熔断后为
   MESSAGE_END(stop_reason=tool_failure_circuit_open) + ERROR）。
5. `StreamAdapter.process_workflow_events`：`core/workflow_events.StreamEvent` → SSE 帧（`core/events.EventType`），
   用 `StreamProcessor` 捕获 `<file>` 正文并落库。

预算（`apps/server/config/agent_runtime.py`，均可用同名环境变量覆盖）：`AGENT_COLLABORATION_MAX_ITERATIONS`（12，agent 轮数）、
`AGENT_TOOL_CALL_MAX_ITERATIONS`（60，单次 run 的 SDK max_turns，最后 `runner.SOFT_LANDING_TURNS` 轮提示收尾）、
`AGENT_RUN_MAX_MODEL_CALLS`（120，请求级）、`AGENT_RUN_WALL_CLOCK_TIMEOUT_S`（780，须早于 Railway 900 秒断连）、
`AGENT_RUN_WRAP_UP_AFTER_S`（默认时限减 180 秒：之后每次模型调用都提醒收尾、图不再开新的 agent run）、
`AGENT_SSE_HEARTBEAT_INTERVAL_S`（15）、`AGENT_AUTO_REVIEW_THRESHOLD_CHARS`（500）。见 [runtime-budget-and-heartbeat][n-budget]、
[no-progress-guard-and-soft-cap][n-noprogress]、[platform-deadline-and-terminal-guard][n-deadline]、[wall-clock-wrap-up][n-wrap-up]。

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
quality_reviewer；同一交付在 writer 及其之前的计划内可写角色中成功写入的正文累计，中间经过只读角色不清零，计划外跳转不累计）。审查最多 `MAX_REVIEW_ROUNDS` 轮，返修稿不再自动送审。

### Router

- 输入：用户本轮原话；有上一条 assistant 消息时，user 消息为 `[上一轮助手结尾]`（最后 600 字 + 状态卡摘要）+
  `[上一轮路由]`（落库 routing 的一行 JSON）+ `[用户本轮原话]`（`build_router_user_message`，系统提示不变）。另附可信停止/待澄清状态、结构化问题和本轮真实选区/引用存在标记；不从正文标签猜这些事实。
  ROUTER_PROMPT 区分补问作答、中断续接和确认提议的下一步（按那一步路由，不沿用
  read_only=true / write_content=false / 已交付的 scope）。
  作者在这部作品里的第一条消息（`service.FIRST_MESSAGE_ENTRIES`，会话里还没有 AI 回复）时 user 消息为
  `[新作品的第一条消息]`（作品类型）+ `[用户本轮原话]`，ROUTER_PROMPT 按「先写再给方向」判断交付范围；模型输入另附
  `service.first_message_hint`（不落库）。[first-turn-writes][n-first-turn]
- 输出：`current_agent`、`workflow_plan`、`workflow_agents`（已裁剪）、`routing_metadata`（`RouterDecision` 全量，含
  `write_content`（None = 旧格式未给出）、`read_only`、`scope`、`requested_file_types`，以及可选严格 `turn_disposition` / `clarify_first`）。缺新字段按普通成功路由、不继承；显式非法技术失败并保留真实 usage。普通旧格式 scope 保留80字符兼容；真实继承不截断旧限制或新收窄，服务端 `scope_constraints` 最多8项、每项80字符、JSON2048字符，完整跨轮持久化；超限在工具前失败。requested types 沿用旧列表，不扩张并集。
- 决策记录：[semantic-routing-and-evidence](../../../.agents/notes/implemented/bug-fix/2026-10-24-agent-semantic-routing-and-evidence.md)、[router-previous-turn-context][n-router-ctx]、[routing-continuity][n-routing]、[routing-inherits-the-task-owner][n-owner]。

### 用户范围与交接规则

- **等作者回答使用结构化事件**：需要回答才能继续时调用 `request_clarification`，普通问号和条件句不控制计划交接；跨轮延续只认落库的 `clarification_pending`，不从回复标点猜等待状态。参见 [typed-boundaries][n-typed-boundaries]。
- **计划交接不扩大交付**：交接说明固定追加 `author_scope.PLANNED_HANDOFF_SCOPE_NOTE`（已完成的不重做、不改刚交付的文件、
  不追加作者没要的产出）。[vague-and-plan-files][n-vague]
- **补问由结构化路由决定**：`clarify_first=true` 时本轮不写文件、不交接，复用现有拒写工具与 request_clarification；不按词表/选区文字标记判断。技术路由故障不伪装成「请再说清楚」。
- **规划补写只认请求类型和真实产物**：明确 requested_file_types 含 outline、planner 正常结束、无显式/延期交接或异常停止、非只读/补问、有剩余轮数且本请求写过的真实 typed 文件中没有非空 outline，最多补一次（新任务计划中待执行 writer 不跳过此检查）；已由 resolver 核实的可信续接/澄清不凭本轮台账断言上轮大纲未写，不把该跳过当作交付证明；读取失败不补，不按聊天章号或80%覆盖判断。
- **作者改了什么给模型看，不猜授权**：`tools/author_changes.py` 按版本历史算净差异放进 `<author_edits>`（`query_files` 按 id 读全文时也带）；
  `edit_file` 在锁内比对本请求模型看过的正文（`mcp_tools._ReadSnapshots`，读到的内容由 `usage_hooks.ReadSnapshotPromotionMixin`
  在下一次模型调用起生效；换 agent 时清空，同一 agent 接着跑时保留），作者在生成期间又改了就以 `stale_content` 拒绝，改到作者写的段落时结果带 `author_edits_touched`。
  [author-edits-shown-not-guessed][n-author-edits]
- **受保护稿件不由聊天删除或整份覆盖**：作者保存过正文的文件，在应用内对话中的 delete_file（含递归删除）和 create_file 同名剧集复用会拒绝；附加、焦点、点名、否定词、上一轮提议不授予权限，核验失败也不放行。作者使用现有文件菜单/编辑器；普通 edit_file、AI 自己的产物和直接人工 API 保持既有路径。参见 [typed-boundaries][n-typed-boundaries]。
- **作者可见文字**：每个 agent run 的 TEXT 经 `core/author_facing_text.AgentTextShaper` 补段落空行；作者写中文时，紧接工具调用的
  英文过程句不发给作者。[no-english-narration][n-narration]
- **只给作者一份总结**：writer（非快速模式、非返修、未到审查上限，且开着自动质检门或计划里下一个是 quality_reviewer）写正文类文件、
  可能送审时（create_file 的正文还没写出来；edit_file / write_chapter 累计字数到了送审阈值；或计划送审）图发 `CHAT_HOLD(begin)`，StreamAdapter 扣住之后的对话文字（正文不扣）；真的交给
  quality_reviewer 时在 HANDOFF 前发 `CHAT_HOLD(discard)`，由审稿人（或返修后的 writer）写唯一总结，writer 的有界原话以
  `[上一角色未展示的回复]` 交给它们（仅最后闭合 file 之后、600字符含截断标记，不含文件正文，不是新授权）；不送审时扣着的文字在下一个 agent / 交接 / 收尾 / 出错事件前或流结束时原样放出
  （run 中途的 STEERING_RECEIVED 不放出）。CHAT_HOLD 不发给前端。[single-summary][n-summary]
  质量门可用且作者要正文时，writer 前的原计划可写角色也按成功正文写入扣留；同一交付的累计正文达到门槛、实际沿计划前缀交接才丢弃，不按问号决定；未展示原话交接下游，
  正文记录接续给 writer。只有规划、失败写入、未执行交接和结构化澄清原样释放。[planned-body-review][n-planned-body]
- **送审证据用落库后的事实**：送审交接附 `[本轮正文字数]`（落库后的 `count_words`）和 `[作者要求来源摘录（可能包含无关内容）]`
  （`review_evidence.quote_author_requirement_sources`：来源标记和有预算的原话，各来源先留份额、余量优先最新，不按数字/否定正则换算要求；
  写作/审稿在原调用中理解上下文、最新要求和范围；摘录不代表新增授权），读取有时间上限、超时用已读到的；`[待审查内容]` 取本请求写过的正文落库后的样子，
  放不进预算的长文件只附本请求改动的那段（写入前正文由 `ToolContext.remember_pre_write_content` 记下）。
  多文件在既有总预算内逐文件分配，每份保留来源文件 ID 与 `updated_at`；附了全文的来源行写「以下是全文，不用再读取」，未附全文的部分明确标注，按 ID 精确 `query_files` 按章节读取，不把省略部分当作已经核对。返修交接的 `[返修对象当前正文]` 只附审稿意见点到的文件。
  [single-summary][n-summary]、[writer-delivery-requirements][n-length]、[thinking-loop-inputs][n-thinking]、[quality-mode-lean][n-lean]
- **自动质检门只认成功的正文写入**：按 `graph/body_writes.py` 计 draft / script 里实际写进的字数；改大纲、设定、角色和聊天文字不算；
  写入全部失败时不送审。[single-summary][n-summary]、[tool-failure-breaker][n-breaker]、[review-gating][n-review]
- **作品第一条消息不送审**：`state["first_message_project_label"]` 非空的一轮关掉自动质检门，writer 自己 `handoff_to_agent(quality_reviewer)` 也丢弃；交接前没给作者写话时，同一个 writer 再跑一轮只写总结（`FIRST_ROUND_SUMMARY_CONTEXT`，一次）。作者读到的是 writer 自己的交付说明，不是返修记录；路由显式安排的审稿照常。[first-turn-skips-review][n-first-review]
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
- **范围指令进每个 agent 运行的系统指令**：路由的范围判断转成 `state["scope_directive"]`，由 `nodes.build_agent_directive` 以
  `## 本轮用户范围约束 [最高优先级]` 放进这次运行的系统指令（`[本次运行的系统指令]`，runner 把它作为输入最后一条 user 消息，不写回
  `state["messages"]`；系统提示里固定写明它与系统提示同等效力），每次跑 agent 前按当前 agent 重新生成（「规划完直接交接 writer」只给规划类角色）；
  计划交接的 `handoff_packet.todo` 带用户 `scope`。角色提示、截断续写、篇幅要求、快速模式同样放在这里，不进系统提示。
  [scope][n-scope]、[cache-stable-prompt-layout][n-cache-layout]
- **空文件纠偏轮由建空文件的 agent 自己补写**；被纠偏打断一轮的有界回复原话带过纠偏轮，不从标点推断等待状态。[scope][n-scope]
  纠偏轮前图发内部事件 `FILE_BODY_RESUME`（`core/workflow_events.FileBodyResume`，不发给前端）：先落库核验最近那份空文件仍在本项目、未删除、为空，StreamAdapter 只为自己按 create_file 结果在等、版本未变、待写守卫确实恢复的同一文件重新等待 `<file>`；回执 `armed` 才跑纠偏轮，指令与守卫一致（下一次回复以 `<file>` 写正文，不用 edit_file）。当前角色没有写权限时不改派 writer。空 / 只有空白的 `<file>` 块不算写成。补不成时回滚 / 报告（只回滚本轮新建、版本未变的；复用的已有文件与版本变了的不回滚），整轮停在未完成，不再交接、送审或宣告完成；工具调用轮数耗尽时同样核验 / 回滚，结果写在同一张 `tool_call` 耗尽卡上。[resume-capture][n-resume]
- **写完 `</file>` 就结束 run 时同一 agent 续写**：有写权限的 agent 这次 run 里 create_file 成功、最后的文字以写完的 `<file>` 正文
  结束（`completion_evidence.ends_on_file_block`），且没有显式交接、澄清、轮数耗尽、错误时，同一 agent 再跑一轮（交接说明以
  `FILE_STREAM_CONTINUATION_MARKER` 开头）；放在计划交接 / 自动质检之前，正文与正文写入记录接续给续写轮；受 `max_iterations` 约束，
  每轮都要真的新建并写完文件才再续。同一 agent 紧接着自己再跑（续写、纠偏、只写总结、插话追加、截断续写）时，runner 从上一轮完整的
  SDK 输入接着来（`runner._continued_run_input`，`state["native_run_holder"]`），请求只在末尾追加、读过的文件不用重读；换了 agent 照旧从折叠后的历史重建。
  writer 的运行指令要求写多个文件时 `</file>` 之后直接 create_file 下一个（`nodes.WRITER_CHAIN_FILES_DIRECTIVE`）。
  [file-stream-continuation][n-continue]、[same-agent-continuation][n-continued-run]
- **「已保存 / 已还原 / 已修改」不做文字检测**：靠 `prompts/base.py` 的 `UNIFIED_EXECUTION_PROTOCOL` 约束（只有本轮对应工具成功后才这样说，
  没改就直说没改）。[file-stream-continuation][n-continue]
- **任务板声明产物类型和来源**：文件任务声明 artifact_file_type、artifact_refs/file_id 或 unit_spec；收尾只用本请求写过、仍存在非空且类型匹配的项目文件。正文范围每个编号须有实际正文产物，大纲标题不能证明正文；新 done 声明缺证据时不保存任务板。缺类型的旧任务可读但不自动收尾。参见 [typed-boundaries][n-typed-boundaries]。
- **重复建挡路的空文件**：空文件守卫（一次只流式写一个文件）挡住的 create_file，若要建的正是挡路的那个空文件，返回那个文件
  （`content=""` + `next_step`）；挡路的是别的文件时照旧 `pending_empty_file_unwritten`（`mcp_tools._repeat_of_pending_empty_file`）。
  [repeat-create][n-repeat]
- **AI 记忆备注对 Agent 只追加，作者让记才记**：备注是作者内容（未标来源 / 作者确认）；应用内 Agent 的 notes 不能删除、改写、重排或升格已有内容（逐字节保留），不在其中的条目按普通备注（未标来源）追加到末尾（形似分区标题的行或「；」片段不追加，追加内容不能开出作者确认分区或旧提议块），结果 `notes_added` 列出存下的原文，聊天结果卡显示「已记入 AI 记忆」和一个撤销（人工 `expected_notes` PATCH，只删仍逐字一致的行和紧挨其上方删空的「未标来源」标题，409 时用新基线重试一次，不消耗 AI 消息）。只靠提示词约束「作者明确让记住时才写、用原话、不加推断」，没有意图或原话匹配代码。追加后超过 4000 字则备注整段不写，结果带一句 `notes_note`；没有新条目时不写、`updated_fields` 不含 notes。旧备注里遗留的「AI 提议·待作者确认」块在每次读取（API 序列化、上下文、作者原话来源、灵感复制）里视为不存在，任何备注写入都不写回，人工 PATCH 的 `expected_notes` 比较两边都去掉该块（`config.project_notes.without_legacy_proposals`）。参见 [notes][n-notes]、[remove-proposals][n-remove-proposals]。作者自己的项目名也不能由模型布尔值或原话放行改名。参见 [typed-boundaries][n-typed-boundaries]。
- **回复语言跟随作者最近一条消息**（`message_manager.detect_author_language`），本轮上下文 `<turn_context>` 末尾写明本轮语言，所有角色共用；界面语言只在
  看不出时兜底。[reply-language][n-lang]

## 流的终止、停止与计费

- 每个 `/agent/stream` 恰好以一个终止帧结束（`done` / `workflow_complete` / 终止性 `workflow_stopped` / `error`）；逃逸异常先补发 error 帧。
  错误码与固定文案在 `core/stream_errors.py`。[terminal-frame][n-terminal]、[error-and-refund][n-refund]
- 计费规则以 `core/stream_billing.py` 模块 docstring 为准：作者停止 / 断线时本轮没有实质产出则退还；墙钟超时（含发布重启）只认写进
  文件的正文、真正的回复或结束时正在写回复，只改了项目信息（AI 记忆）照样退还；失控停止（no_progress / 模型调用上限 / 工具轮数耗尽）且没有任何写入成功则退还；正常结束计费；出错时 error 帧
  `refundable: false`（如工具失败熔断）、已有产出或正在写回复则计费，其余退还。「实质产出」只有写入留下正文，或一段回复正文达到
  `PROSE_MIN_CHARS` 个单位（`prose_units`：拉丁字母单词算 1.5，其余非空白字符算 1，英文过渡句与中文同口径）；思考、读文件、只建空文件、过渡旁白都不算。退还落库后补发 `quota_refunded`，结算只做一次。
  [error-and-refund][n-refund]、[stop-output-definition][n-output]
- 作者停止走 `POST /agent/stop` → `SSEStreamPump.request_stop()`，连接上补发 `workflow_stopped(user_stopped)` 与 `done`；停止 / 断线 / 超时
  的取消路径会把进行中的 `<file>` 捕获补存，并由 `core/round_outcome.py` 写终态、退还时移除本轮空白占位文件。
  [graceful-stop][n-stop]、[abort-flushes-capture][n-flush]、[stop-output-definition][n-output]
- 墙钟时限由 `SSEStreamPump` 执行：到点先发真实超时终态再等生产者收尾。[platform-deadline][n-deadline]、[deadline-terminal][n-deadline2]
- 发布交接：SIGTERM 后 `core/server_drain.py` 让所有运行立即按时限收尾提醒，`api/agent.py` 把进行中的泵的时限提前到 `AGENT_DRAIN_STOP_AFTER_S`（240 秒），到点以 `ERR_AGENT_SERVER_RESTARTING` 收尾、计费同墙钟超时（`billing_reason=server_restarting`）；lifespan 关闭前等后台退还与终态任务。[deploy-drain][n-drain]
- 运行中插话：`/agent/steer` 写入 `core/steering.py` 的队列，runner 在边界处消费。[steering-busy-409][n-steer]
- SSE 事件类型见 `core/events.py` 的 `EventType`。

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
  `AGENT_WORKING_SET_MAX_FILES`=8 份、`AGENT_WORKING_SET_MAX_CHARS`=60000 字）。
- 项目资料快照（`context/turn_snapshot.py`）：组装的项目上下文与工作集按条目给出带键的块；会话第一轮（及重建时）渲染成
  `<project_snapshot>`，作为模型输入第一条消息紧跟系统提示（`runner.build_run_input`，不写回 `state["messages"]`）；之后每轮只把
  新出现或改过的块放进 `<turn_context>` 的「本轮更新的项目资料」，工作集只发清单。改动超过快照一半、20 轮、2 小时或项目 / 语言变化时重建。
- 本轮上下文（`MessageManager.build_turn_context`，`<turn_context>`）：叙事约束、作者本条消息选的技能、本轮更新的项目资料、当前文件、
  任务计划板快照、本轮回复语言，加工作集清单，放在本轮用户消息最前面（`SessionLoader.attach_working_set_to_user_content`）。
  请求布局 `[系统提示][项目资料快照][历史][本轮上下文 + 作者消息][本次运行指令]`，前四段跨轮、跨 agent 逐字不变
  （`tests/test_agent/test_prompt_cache_prefix.py`）。历史超出预算时窗口起点对齐锚点（`session_loader.anchor_history_window`），
  停几轮再整段跳，不每轮滑动。
- 决策记录：[full-text-and-working-set][n-context]、[characters-and-core-lore][n-lore]、[cache-stable-prompt-layout][n-cache-layout]、
  [context-snapshot-and-cache-stable-tools][n-snapshot]。

## 工具

工具清单以 `tools/registry.py` 为准：每个角色、每一轮在请求里看到的都是同一份 `EXPOSED_TOOL_NAMES`（前缀缓存不按角色分裂）；
角色能执行哪些工具由 `AGENT_TOOL_NAME_MAP` 决定（`DEFAULT_TOOL_NAMES` 给 planner / hook_designer / writer，`QUALITY_REVIEWER_TOOL_NAMES`
给审稿人，没有写工具），越权调用在 `tools_adapter` 里以 `tool_not_permitted_for_role` 拒绝，本轮不可用的工具写进运行指令的
`## 本轮工具限制`。create_file 建好空文件后返回 `awaiting_body` 与 `next_step`；下一次回复没先以 <file> 写完正文就调用的工具
（request_clarification 与按精确 id 非递归删除本请求新建的这份待写文件除外，删成功后守卫随之清除）以 `file_body_pending` 拒绝（`tools_adapter`，按 on_llm_start 记的回复序号判断），
拒绝后 runner 同步结束当前 run，由图核验并经 `FILE_BODY_RESUME` 安排纠偏轮；这类拒绝不参与同参数熔断，仍计入请求累计上限。
先写完正文再 create_file 下一个文件的连写照常。SDK tracing 每次 run 都关掉（`RunConfig(tracing_disabled=True)`），不向 OpenAI 导出内容。
[fast-mode-summary-and-file-body-turn][n-body-turn]、[pending-refusal-returns-to-correction][n-pending-refusal]

| 工具 | 用途 |
|---|---|
| `create_file` | 新建文件（只建空文件，正文随后用 `<file>` 流式写入） |
| `edit_file` | 精确编辑（replace / insert / append / delete） |
| `delete_file` | 删除文件 |
| `rename_file` | 只改文件标题，id 与正文不变（同文件夹重名拒绝；[rename-file][n-rename]） |
| `query_files` | 查询和读取文件；按 id 可只读 `section` / `tail_chars` / `start_char`+`max_chars`，结果标明范围，范围读取只做陈旧检查快照、不算看过全文（[range-reads][n-range]） |
| `update_project` | 项目信息、备注、任务板 |
| `handoff_to_agent` | 交接给另一个 agent |
| `request_clarification` | 请作者澄清并暂停工作流 |
| `parallel_execute` | 并行执行多个子任务（schema 在 `tools/parallel_executor.py`） |
| `load_skill` / `read_skill_resource` | 技能 L2（方法与资源清单）/ L3（参考文件或 `path="SKILL.md"`，支持 `offset` 续读）；受每请求技能 token 预算限制，每请求最多读 `skills/content_budget.MAX_LOADED_SKILLS_PER_REQUEST`（3）个不同技能 |

文件流式写入分两次回复：这次回复以 `create_file` 工具调用结束；下一次回复以 `<file>` 开头写正文、`</file>` 结束，StreamAdapter 捕获后落库。
一次只流式写一个文件。[two-replies][n-two-replies]

## 提示词

- 真实失败稿用于回归和正确反例，不把样例人物、账目、物件或情节关键词写成提示词/Agent 的特判。先定位通用的范围、输入、执行证据或权限缺口；提示策略变更须验证跨题材效果与误改，不能只证明已知样例修好了。
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
5. 作者可见名字：`core/author_facing_text.py` 的 `_AGENT_NAMES`（英文界面见 `_AGENT_NAMES_EN`）、`graph/writing_graph.py` 的 `agent_names`；规划类角色加进
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

[n-typed-boundaries]: ../../../.agents/notes/implemented/architecture/2026-10-14-typed-authority-and-delivery-evidence.md
[n-remove-proposals]: ../../../.agents/notes/implemented/feature/2026-10-25-remove-memory-proposals.md
[n-rename]: ../../../.agents/notes/implemented/feature/2026-10-19-agent-rename-file-tool.md
[n-range]: ../../../.agents/notes/implemented/feature/2026-10-19-query-files-range-reads.md
[n-first-turn]: ../../../.agents/notes/implemented/feature/2026-10-19-first-turn-writes-before-asking.md
[n-first-review]: ../../../.agents/notes/implemented/feature/2026-10-20-first-turn-skips-auto-review.md
[n-fast-default]: ../../../.agents/notes/implemented/feature/2026-10-20-fast-mode-default-keeps-router.md
[n-cache-layout]: ../../../.agents/notes/implemented/bug-fix/2026-10-20-agent-cache-stable-prompt-layout.md
[n-snapshot]: ../../../.agents/notes/implemented/architecture/2026-10-21-context-snapshot-and-cache-stable-tools.md
[n-body-turn]: ../../../.agents/notes/implemented/bug-fix/2026-10-21-fast-mode-summary-and-file-body-turn.md
[n-resume]: ../../../.agents/notes/implemented/bug-fix/2026-10-24-empty-file-correction-resumes-capture.md
[n-pending-refusal]: ../../../.agents/notes/implemented/bug-fix/2026-10-25-pending-body-refusal-returns-to-correction.md
[n-wrap-up]: ../../../.agents/notes/implemented/bug-fix/2026-10-21-wall-clock-wrap-up.md
[n-continued-run]: ../../../.agents/notes/implemented/architecture/2026-10-21-same-agent-runs-continue-the-previous-input.md
[n-lean]: ../../../.agents/notes/implemented/simplification/2026-10-20-quality-mode-length-tolerance-and-attached-text.md
[n-drain]: ../../../.agents/notes/implemented/architecture/2026-10-21-deploy-drain-ends-running-generations.md
