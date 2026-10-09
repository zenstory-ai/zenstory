# Agent Note: 交接以用户本轮范围为准——只要大纲不写正文、明说别改不改、提问就停

Status: implemented

后续修改：「提问就停」对规划类角色 + 作者本轮已经要正文（`write_content=true`）不再适用，见 `2026-10-09-planner-question-keeps-requested-prose.md`。

## Problem

多 Agent 工作流会做用户没要求的事。真实 deepseek-flash 复现：

- 只要大纲/人设/设定的请求（"帮我构思…先给我故事大纲"、"创建两个主角人设"、"给我几个爽点思路，先不用写"）被路由成 planner + `standard`（或 hook_designer + `hook_focus`），`WORKFLOW_AGENTS` 无条件排上 writer，planner 结束后计划交接 `todo` 为空，writer 只看到"按照工作流计划自动交接"，于是按大纲往下写正文；浏览器里一次"大纲 + 只写第1章"写出 6 章 + 2 轮审查。
- 计划交接（`writing_graph.py` 的 `elif workflow_agents: next_planned = workflow_agents.pop(0)`）不看 agent 怎么收尾：planner 以文字问用户"这个方向可以吗？"结束，writer 仍按没确认的方向开写；用户明说"不要改文件/只分析"也照样交接。
- writer 写完后自动质检门把 quality_reviewer 拉进来，审稿人又按提示词"发现问题必须交接 writer"，多出若干轮。
- planner 留下空文件时，空文件纠偏轮硬编码由 writer 补写：只要大纲的请求因此凭空多一轮 writer（并触发自动质检）；`standard` 下计划序列里的 writer 随后撞上自交接，被判"无效自动交接"整轮停掉（同样是真实复现）。
- PLANNER_PROMPT / HOOK_DESIGNER_PROMPT 要求设计完成后"必须交接 writer""禁止询问是否需要开始写作（默认需要）"，与路由层的问题叠加。

## Decision

范围判断放在路由（已有的一次 LLM 调用），图按这些信号决定交接：

- `agent/graph/router.py`：`RouterDecision` 新增三个关于用户请求的事实字段——`write_content: bool | None`（是否要求产出/改写正文；None 表示旧格式没给出）、`read_only: bool`（明确要求不改文件，只认明确的 true）、`scope: str`（交付范围摘要：ROUTER_PROMPT 要求模型写在 40 字以内，`MAX_SCOPE_CHARS`=80 是硬截断上限）。`_normalize_router_payload` 宽松解析布尔值。`plan_workflow_agents()` 按范围裁剪 `WORKFLOW_AGENTS`：`read_only` 清空整条序列，`write_content is False` 剔除 writer，None 沿用原序列。`ROUTER_PROMPT` 要求输出这三个字段并给出判定口径。
- `agent/graph/writing_graph.py`：
  - 路由后用 `_build_scope_directive()` 生成 `state["scope_directive"]`（只读约束 / 不要正文约束 + 交付范围），`agent/graph/nodes.py` 的 `run_streaming_agent` 把它追加到本轮每个 agent 系统提示末尾（`## 本轮用户范围约束`），不改写 user_message，避免会话历史里多一条重复用户消息。
  - 记录每个 agent 最后一次 TOOL_USE/TOOL_RESULT 之后的文本（`agent_tail_text`），用 `nodes.ends_with_question_to_user()` 判断是否以向用户提问收尾；是则清空计划序列，本轮不再按计划交接、自然结束等用户回答。显式 handoff 不受影响。自动质检门（`enable_graph_auto_review`，含 `generation_mode=quality`）不看这个信号：它只审已经写完的稿子、审稿人没有写权限，而 writer 收尾时常习惯性地问"需要我继续写第二章吗？"，按提问拦下会让用户选了高质量模式的请求实际不送审。这一轮若因留下空文件被纠偏打断，「以提问收尾」经 `carried_ended_with_question` 带过纠偏轮，与已有的 `carried_agent_content` / `carried_writer_*` 同一机制——纠偏轮只补正文，它的收尾文本（"已补齐"）不能冲掉原 agent 还在等回答的问题（同样只作用于计划交接）。
  - `read_only` 请求下，交接目标有写权限（`_agent_can_write_files`，按 `registry.AGENT_TOOL_NAME_MAP` 与 `registry.FILE_WRITE_TOOL_NAMES` 的交集判断）时连显式 handoff 也丢弃，并给用户一条 `WORKFLOW_STOPPED`（`reason="read_only_handoff_blocked"`、`target_agent`、中文 `message`）——前端已经渲染了 handoff_to_agent 的工具卡片，这条事件让用户知道交接为什么没发生。前端对非 `clarification_needed` 的 reason 渲染为"工作流已停止"提示卡片并随消息落库；但前端（`useAgentStream.onWorkflowStopped`）收到任何 `workflow_stopped` 都会结束流式状态（重新启用发送、隐藏取消、关闭 steering），所以拦下时只记录（每个请求只记第一次），图也不 break，同一轮的 steering 追加轮等后续逻辑照常；这条提示推迟到工作流收尾才发：紧挨在终止事件之前（`WORKFLOW_COMPLETE`、澄清/无效交接的 `WORKFLOW_STOPPED`、`tool_call` 层的 `ITERATION_EXHAUSTED`、agent 的 `ERROR`、协作轮数耗尽的 `ITERATION_EXHAUSTED`、图内异常的 `ERROR`），或在没有终止事件的自然结束时作为图的最后一个事件。`reason` 常量 `READ_ONLY_HANDOFF_BLOCKED_REASON` 与 `NON_TERMINAL_WORKFLOW_STOPPED_REASONS` 定义在 `agent/core/events.py`；`api/agent.py` 判定"是否收到终止事件"（决定异常时是否补发兜底 `INTERNAL_ERROR` 帧、以及计费分类）时跳过这类 `workflow_stopped`，提示发出后若 `process_stream` 再抛异常，仍补发兜底 error 帧并按内部错误退款。交给 quality_reviewer 这类只读 agent 照常。steering 追加轮在只读请求下用只读文案。
  - `read_only` 在工具层强制：graph 置 `state["read_only"] = True`，`openai_agents/runner.py` 读它传给 `_build_agent(read_only=)`。`tools_adapter.build_agent_function_tools` 对 `agent/tools/registry.py` 的 `FILE_WRITE_TOOL_NAMES`（create_file / edit_file / delete_file / parallel_execute——后者的子任务类型含 write_chapter / edit_file / delete_file，且整次调用一律拒绝，即使这一批只有 query_files / hybrid_search 这类只读子任务）仍然构建 FunctionTool，但描述前加 `READ_ONLY_TOOL_DESCRIPTION_PREFIX`（"本轮不可用"），调用一律不执行，返回 `error_type="read_only_request"` 的可恢复错误（`read_only_refusal_text`：用户要求不改文件、不要重试、直接回答）。拒绝经请求级 `ToolFailureBreaker` 记账，模型坚持乱试时由"同一调用连续失败 3 次 / 累计 10 次"兜住。`update_project` 不在 `FILE_WRITE_TOOL_NAMES` 里，只读请求下照常执行——它改的是项目信息与任务计划（`tasks`），不是文件；工作流收尾时 `_auto_finalize_task_board_on_completion` 补发的 `update_project(tasks)` 同样照常。另外 runner 的 `RunConfig` 设 `tool_not_found_behavior="return_error_to_model"` 并以 `tool_error_formatter` 把"工具不存在"改写成 `error_type="tool_not_found"` 的结构化错误、同样经熔断器记账（同名不存在的工具连续 3 次时熔断原因是 `repeated_unavailable_tool_call`，文案说"连续调用本轮不可用的工具"，不说"以相同参数失败"）：审稿人照着共享历史调 `edit_file`、或模型幻觉出的工具名，不再让 SDK 抛 `ModelBehaviorError` 以致命 ERROR 结束整轮。空文件纠偏分支在只读请求下不安排补写（`can_schedule_correction` 带 `not read_only_request`），万一出现空文件直接走回滚分支。
  - 计划交接的 handoff_context 与 `handoff_packet.todo` 带上用户的 `scope`。
  - 空文件纠偏轮由建了空文件的那个 agent 自己补写（`correction_agent`），不再硬编码 writer；暂存的显式 handoff 在纠偏后照常恢复。
- `ends_with_question_to_user()` 的判定是保守的：最后一个非列表行以 `?`/`？` 结尾（剥掉行尾空白、markdown 强调符和 emoji，不剥引号——台词里的问句不算），或末尾列表每一条都是问句、或引出列表的那一行是问句；尾部在 `</file>` 之后截取，仍有未闭合 `<file>` 时一律不算。"每一条都是问句"只在列表至少两条时成立：单独一条恰好以问号结尾（"大纲如下：\n- 悬念：谁在暗中观察？"）多半是要点里的设问，不算提问。列表项识别（`_LIST_ITEM_RE`）对数字编号后的 ASCII `.` 要求后面不是数字（"1.甜宠路线"算列表项，"1.5万字…"不算），对字母编号后的 ASCII `.` 要求跟空白（"e.g. …""e.g.…"不算）；`、` `)` `）` 后都不要求空白。
- 提示词：PLANNER_PROMPT / HOOK_DESIGNER_PROMPT 的协作指南改为"用户同时要求正文才交接 writer，并在 context 写明范围；只要规划/思路就直接结束，不在结尾追问是否开始写作"；WRITER_PROMPT 增加范围约束（用户要第1章就只写第1章）。
- `agent/CLAUDE.md` 同步上述交接规则。

## Alternatives considered

- **新增 `plan_only` 之类的 workflow 标签，而不是加布尔字段。** 最强理由：沿用现有"标签决定序列"的设计，只多一个枚举值。被否：hook_designer"只要思路"还得再加一个标签，而"明确不改文件"可以落在任何初始 agent 上，标签表达不了；把"要不要正文""能不能改文件"作为与路径形状正交的事实来问，模型更容易答对（12 条探针消息全部判对）。
- **对用户原话做关键词匹配（"只要大纲""不要改"）。** 最强理由：确定性、零额外 token。被否：措辞和语言变化太多，漏判就回到老问题；路由本来就有一次 LLM 调用，多问三个字段几乎不增加成本。
- **去掉计划交接，只靠 agent 显式 handoff。** 最强理由：最简单，交接完全由 agent 决定。被否：计划交接是"用户要了正文、planner 却忘了交接"时的兜底，"规划后写第1章"必须仍能走到 writer。
- **只读请求把写工具从工具集里拿掉。** 最强理由：模型在工具清单里根本看不到写工具，最不容易被诱导去调用，工具描述也省 token。被否：writer/planner 的提示词和共享会话历史里到处是 create_file，模型照样会调；openai-agents 0.17.6 默认 `tool_not_found_behavior="raise_error"`，调用不存在的工具抛 `ModelBehaviorError`，整轮以致命 ERROR 结束、原始异常文本直接展示给用户。只开 `tool_not_found_behavior="return_error_to_model"` 也不够：SDK 默认文案只说"工具不存在"，模型不知道是用户要求只读；留着工具、拒绝执行能给出明确原因，且拒绝走 FunctionTool 回调、天然经熔断器记账。`return_error_to_model` 仍然打开，作为其他"工具不存在"情况的兜底。
- **提问收尾也拦自动质检门。** 最强理由：agent 在等用户回答时，任何自动续跑都是替用户做决定。被否：自动质检只审已写完的稿子、审稿人没有写权限；writer 收尾的"需要我继续写第二章吗？"几乎是习惯性客套，按提问拦下会让 `generation_mode=quality` 的请求实际不送审，用户开了的质检被一句客套话关掉。
- **只读请求只靠交接拦截 + 系统提示约束，不动工具集。** 最强理由：不碰 runner / tools_adapter，改动面小，交接拦截已经挡住"交给 writer 去改"这条主路径。被否：本轮第一个 agent（常常就是 writer）仍持有 create_file / edit_file / delete_file，只要模型不听系统提示就会改文件；空文件纠偏分支还会主动要求它 `edit_file` 补正文。用户明说"不要改文件"需要硬保证。
- **纠偏轮继续交给 writer。** 最强理由：原设计里 writer 是"负责把内容写进文件"的 agent。被否：真实运行中它让只要大纲的请求多出 writer 轮和自动质检，还和计划序列里的 writer 撞成无效自交接；空文件是谁建的就该谁补，它必然有写权限。

## Consequences

- 收益：只要大纲/人设/设定/思路的请求只跑 planner（或 hook_designer）；"规划后只写第1章"仍是 planner → writer，且 writer 系统提示和交接包都带着范围；明说别改文件时不会被交接去改写；agent 用文字提问时本轮停下等用户，而不是替用户做决定继续写。真实 deepseek-flash 复跑：大纲请求只产出 1 个 outline、无 writer/审稿轮；"大纲+只写第1章"只产出 1 份第1章正文；爽点思路请求零写入。
- 代价：问句识别是启发式。模型用不带问号的方式提问（"请告诉我你的选择："+ 选项）时识别不到，退回原来的计划交接；planner 在对话里直接贴出以悬念问句结尾的大纲时可能被误判为提问，本轮提前停下，用户回一句"继续"即可。
- 代价：`write_content=false` 只裁剪计划序列，planner 若无视提示词仍显式交接 writer，图会放行（路由也可能判错，显式交接是另一侧的兜底），因此"只要大纲不写正文"同时依赖 planner 提示词的遵从。
- 代价：只读请求整轮的写工具调用都被拒绝，路由把普通请求误判成 `read_only=true` 时，这一轮什么都写不了，用户需要再说一次；同一轮里用户中途 steering 说"那就改吧"也不会生效，要等下一轮。模型无视拒绝原样重试同一个写调用 3 次时，熔断器以 ERROR 结束本轮（额度退还）——比 SDK 的致命 `ModelBehaviorError` 晚、但仍是错误结束；写工具仍在工具清单里，每次只读请求都多带这几个工具的 schema。
- 代价：只读请求拦下写交接时出现一张"工作流已停止"提示卡片，原因显示为 `read_only_handoff_blocked` 原码（前端对非澄清 reason 统一这样展示）；随消息落库后，下一轮的会话历史摘要里也会带上这条状态。提示推迟到收尾才发，用户在拦截发生后到收尾之间看不到原因；前端收到它即结束流式状态，此后到 `done` 之间（落库历史）的表现与收到 `WORKFLOW_COMPLETE` 后相同。前端只把最后一张终止状态卡片随消息保存（之后到达的任何终止状态卡片——轮数耗尽、澄清、无效交接、工具调用耗尽——都会在实时视图里顶掉它），刷新后以服务端落库的卡片列表为准，两张都在。
- 代价：writer 以提问收尾（"需要我继续写第二章吗？"）且开启了自动质检时，质检照常跑，审稿结论排在 writer 的提问之后，用户要往上翻才看到那句提问；审稿人发现问题交回 writer 修改时，这一轮的协作也会比"提问即停"更长。
- 代价：ROUTER_PROMPT 多约 10 行，每轮路由多几十个输入 token；路由输出多三个字段。

## Verification

`cd apps/server && venv/bin/pytest tests/test_agent/test_graph_router.py tests/test_agent/test_graph_nodes.py tests/test_agent/test_writing_graph.py tests/test_agent/test_round3_graph.py -q`。`TestWritingGraphUserScope` 走真实 `router_node`（只 mock 路由 LLM 调用）覆盖：只要大纲不交接、规划+第1章交接且带范围、提问收尾不交接、工具调用前的自问不算、显式交接优先于提问、只读拦截交接 writer、只读放行交接审稿人、提问收尾仍触发自动质检（环境开关与 `generation_mode=quality` 两种开法）、正常收尾仍触发质检、只读拦截时发出 `read_only_handoff_blocked` 提示且它是收尾前最后一个事件（`test_read_only_notice_waits_for_steering_followup_round`：拦截后还跑一轮 steering 追加轮，提示仍在追加轮之后、紧挨收尾；`test_read_only_notice_precedes_error_when_followup_round_raises`：后续轮次抛异常时提示在 `ERROR` 之前）；其中 5 条在改动前的代码上失败。`tests/test_api/test_agent.py` 的 `test_agent_stream_exception_after_read_only_notice_still_sends_fallback_error` / `test_agent_stream_read_only_notice_alone_is_not_terminal` 覆盖提示不算终止事件（把 `api/agent.py` 的判定改回"任何 workflow_stopped 都算终止"后这两条失败），`test_agent_stream_read_only_notice_then_done_is_charged` 覆盖正常收尾照常计费。`test_round3_graph.py::TestCorrectionRoundKeepsUserScope` 覆盖提问跨纠偏轮保留（planner 不再被计划交接给 writer；writer 的计划交接被拦但自动质检照常）与只读请求回滚而不补写。`test_graph_nodes.py::TestQuestionToUserDetection` 覆盖"1.5万字…""e.g. …""e.g.…"不算列表项、"1.甜宠路线\n2.虐恋路线"仍算选项列表、单条问句列表项不算提问。工具层：`test_tool_failure_breaker.py::test_read_only_tool_set_keeps_write_tools_but_marks_them_unavailable` / `test_read_only_write_tools_refuse_without_executing` / `test_runner_uses_request_breaker_and_read_only_flag_from_state`，以及用本地 OpenAI 兼容端点驱动真实 SDK run-loop 的 `test_sdk_read_only_request_write_attempt_recovers_with_answer`（只读请求下模型调 create_file → 拿到 `read_only_request` 错误 → 第二次模型调用直接回答，无 ERROR、写工具未执行）与 `test_sdk_unknown_tool_is_returned_to_model_not_fatal`（审稿人调 edit_file 不再致命）；`test_unknown_tool_trip_has_its_own_reason_and_message` 覆盖不存在的工具熔断时的原因与文案。
