# Agent 模块架构指南

ZenStory 写作 agent 的服务端：由**单循环 agent**（`loop/`）跑完每一轮——一个模型、写作工具集、
一份只追加的对话 → openai-agents-python 运行时（主备两个供应商，见 `core/llm_providers.py` /
`core/llm_failover.py`：MiniMax 默认走 OpenAI `/v1/responses` + `store=False` + 会话确定性 `prompt_cache_key`，可由 `MINIMAX_API_SURFACE=chat_completions` 环境变量即时回滚；DeepSeek 严格走 `/v1/chat/completions`；[minimax-responses-api][n-minimax-responses]）→ SSE。决策见 [single-loop-agent][n-loop]。原来的多角色工作流（路由 → planner / hook_designer / writer /
quality_reviewer）已删除（[remove-graph-agent][n-remove-graph]）：它写下的旧会话行只读回放（`loop/history.py` 的 `_legacy_row_items` 退回纯文本 `row.content`），前端照常展示；回滚 = 从 git 检出删除前的提交重新部署。

MiniMax 可配置双凭证：`MINIMAX_API_KEY` 是主凭证，
`MINIMAX_SECONDARY_API_KEY` 是次凭证，
`MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT` 控制次凭证承担的任务比例（默认 20）；活跃会话通过 Redis 空闲滑动 TTL 键 `llm:key_route:{session_id}`（`LLM_KEY_ROUTING_WINDOW_S`，默认 1800 秒，每次新 run 命中续期、空闲超时后重抽、`SET NX` 防并发竞态；Redis 不可用时 fail-open 退回确定性 `session_id` HMAC，权重为 0 时零延迟直切主 Key）固定留在一把 Key、避免跨整点切 Key 击穿热缓存，同一 `agent_run_id` 内记在内存零额外 Redis IO；一把 Key 限流或账号出错只冷却自己 `LLM_KEY_COOLDOWN_S`（不覆盖粘性路由键），两把都冷却才算供应商失败。
同一服务端 `agent_run_id` 的全部 MiniMax 调用固定到同一凭证；没有 run 上下文的素材调用按每次真实模型调用轮转分配。
双凭证仍属于同一个 provider 槽位，不互相 failover；client cache 按真实凭证隔离，日志只记录
`key_slot=primary|secondary`，不得记录 Key 或分桶值。该比例是任务数比例，不保证 token 或费用精确同比分摊。

路径约定：不带前缀的路径相对 `apps/server/agent/`；模块外的文件写全路径（例如 `apps/server/utils/logger.py`，
不要和 `agent/utils/` 混淆）。行为细节与取舍以 `.agents/notes/` 里链接的决策记录为准，改行为前先读对应记录。

## 语义判断边界

遵循仓库的 [Agent semantic decisions](../../../AGENTS.md#agent-semantic-decisions)：禁止用关键词表、子串/整句匹配、正则、字数或标点启发式判断自然语言里的意图、确认、澄清、续接、角色、读写范围及是否完成。语义由模型和结构化事实表达，权限和交付由程序核验真实状态与产物；不能只因模型自报就授权或结算完成。

协议标记、JSON/枚举、文件路径、精确编辑锚点与版本并发校验不属于自然语言语义猜测，保护仍须保留。下文是现有实现的架构说明，不豁免其中尚待迁移的字符串语义分支；不能为遵守本规则而直接删除只读、内容、终态或计费保护，也不默认新增一轮分类模型调用。

## 目录结构

```
agent/
├── service.py              # AgentService.process_stream：一次 /agent/stream 的主入口
├── stream_adapter.py       # StreamAdapter：workflow 事件 → SSE 帧，<file> 正文捕获与落库
├── suggest_service.py      # /agent/suggest 的下一步建议
├── constants.py            # 子系统共享常量
├── context/                # 项目上下文组装
│   ├── assembler.py        # ContextAssembler
│   ├── budget.py           # token 预算与截断
│   └── prioritizer.py      # 优先级分档
├── core/                   # 基础设施
│   ├── events.py           # SSE 事件（EventType）与帧构造
│   ├── workflow_events.py  # 循环 → StreamAdapter 的内部事件（StreamEventType / StreamEvent）
│   ├── writing_state.py    # WritingState：一轮请求交给单循环 agent 与 runner 的状态
│   ├── empty_files.py      # 空文件的落库核验、恢复写入目标与回滚
│   ├── sse_pump.py         # SSEStreamPump：心跳、请求墙钟时限、作者停止
│   ├── account_run_limit.py# 账号任务名额（每账号最多 1 个）：原子获取、续租、失效回收
│   ├── stream_billing.py   # StreamBillingTracker：按结构化执行事实判定计费 / 退还与产出回执标记
│   ├── stream_errors.py    # 错误码、固定文案、retryable / refundable
│   ├── round_outcome.py    # 停止 / 断线的一轮怎样收尾（终态落库、空白占位文件）
│   ├── run_meter.py        # 请求级模型调用计数与耗时
│   ├── steering.py         # 运行中插话（steering）队列
│   ├── output_truncation.py# 输出截断后的续写提示
│   ├── author_facing_text.py # 作者可见文字整理（AgentTextShaper）
│   ├── session_loader.py   # 状态卡回放、作者手改提醒、会话读取快照与本会话写过的文件
│   ├── message_manager.py  # 系统提示构建（含多语言回复规则）、本轮上下文的共用分段、历史保存
│   ├── protocol_filter.py  # 代码块/行内代码外的泄漏工具调用协议过滤与检测（单源共享）
│   ├── stream_processor.py # <file>…</file> 捕获状态机
│   ├── usage.py            # 统一的 LLM token 用量提取、键名归一与累加（runner / compaction / StreamAdapter 共用）
│   ├── llm_providers.py    # 主 / 备供应商配置与 OpenAI 兼容客户端
│   ├── llm_failover.py     # 主备切换：会话固定、熔断、还没出正文/工具参数时换供应商
│   └── llm_client.py       # 输入建议 / 润色的单次调用
├── loop/                   # 单循环 agent
│   ├── engine.py           # run_loop_agent_streaming：一轮的运行循环（纠偏 / 续写 / 插话再跑）
│   ├── history.py          # 按会话消息行重建只追加的历史（工具调用成对回放、压缩点、旧行折叠）
│   ├── wire_prefix_cache.py # 活跃会话短 TTL Redis wire-prefix 缓存（跨轮逐字节前缀复用，fail-open）
│   ├── compaction.py       # 轮与轮之间的交接摘要压缩
│   ├── turn_context.py     # 每轮附在作者消息前的结构化上下文
│   ├── ui_actions.py       # 经 Pydantic 校验的当轮结构化 UI 动作（如 ui_action="natural_polish"）
│   ├── prompts.py          # 工作方式、输入布局、压缩提示
│   ├── run_support.py      # 收尾说明、插话追加轮提示、正文字数、事件文字整理
│   └── completion_evidence.py # 回复停在 <file> 正文上、文字里没执行的工具调用（协议结构识别）
├── openai_agents/          # openai-agents-python 适配层
│   ├── runner.py           # run_openai_agents_streaming_agent：Runner.run_streamed + 事件映射
│   ├── model.py            # FailoverChatModel：每次模型调用按主备选择供应商
│   ├── tools_adapter.py    # 项目工具 → SDK FunctionTool（熔断、待写正文与重复读取守卫）
│   ├── tool_failure_breaker.py # 请求级工具失败熔断
│   ├── repeat_read_guard.py    # 重复读取无进展守卫
│   ├── usage_hooks.py      # 每次模型调用的用量记账
│   ├── intra_run_trimmer.py# run 内上下文安全阀
│   └── events.py
├── prompts/                # 共享写作协议（项目写作配置只存数据库）
│   ├── base.py             # get_base_prompt、UNIFIED_EXECUTION_PROTOCOL、回复与工具协议
│   └── suggestions.py      # 建议生成提示
├── schemas/context.py      # 上下文数据模型
├── skills/                 # 技能系统（标准 SKILL.md，渐进式加载，永不执行脚本）
│   ├── active_skills.py    # 当前用户启用中的技能视图
│   ├── context_injector.py # L1 技能目录（名称 + 用途）
│   ├── content_budget.py   # 技能正文 token 预算
│   └── package.py          # SKILL.md 解析、zip 导入安全检查、导出
├── tools/                  # 工具
│   ├── tool_inputs.py      # Pydantic v2 工具入参模型（TOOL_INPUT_MODELS）、紧凑 schema 导出与统一中文校验错误
│   ├── tool_schemas.py     # provider-neutral 工具 schema（由 tool_inputs.py 导出 TOOL_SCHEMAS）
│   ├── tool_context.py     # AgentRequestContext（强类型请求上下文、短生命周期 session_scope、待写正文/快照/UI 侧通道）
│   ├── mcp_tools.py        # 工具实现（MCP_TOOL_HANDLERS）
│   ├── registry.py         # schema / handler / 单循环 agent 工具集的唯一接线处
│   ├── file_ops/           # 文件 CRUD、编辑、项目操作（FileToolExecutor 组合三者）
│   ├── author_changes.py   # 作者在 AI 写过之后的净改动
│   └── permissions.py
└── utils/                  # token_utils
```

## 单循环 agent

- **接入点**：`AgentService.process_stream` 用 `_prepare_loop_turn` 准备输入，
  把 `loop.engine.run_loop_agent_streaming` 交给 `StreamAdapter.process_workflow_events`。SSE 外壳、计费 / 退还、停止 / 插话、
  残稿补存、`round_outcome`、文件版本并发与原稿恢复记录都在这条路径上。请求 `metadata.generation_mode` 照旧接受、不读取。
- **输入**：`[系统提示][重建的历史][<turn_context> + 作者消息]`。系统提示 = 项目类型配置（`prompts.get_prompt_for_project_type`：
  `LOOP_AUTHOR_REPLY_PROTOCOL`、`LOOP_TOOL_USAGE_PROTOCOL`）+ 回复语言 + `loop/prompts.py` 的输入布局与
  工作方式（含写后连贯自查、按真实字数汇报）+ 技能目录，跨轮不变。`<turn_context>`（`loop/turn_context.py`）只放这一轮请求带来的东西：
  项目信息与写作约定、焦点文件、附加文件、引用、素材库条目、作者选的技能、经服务端校验的当轮结构化 UI 动作（`ui_action="natural_polish"` 时经 `loop/ui_actions.py` 直接注入官方「去AI味」权威方法、剧本 `△`/台词保护及单次批量 `edit_file` 范围约束，不改动静态系统提示、也不依赖 `UserAddedSkill`；见 [deai-via-agent][n-deai-agent]）（回复语言完全交由模型按作者最新消息在系统提示规则下自主判断，不再经正则启发式注入 `<turn_context>`；沿用 ContextAssembler 的解析器与渲染，不做
  优先级裁剪、检索和文件清单；超出 `AGENT_LOOP_TURN_CONTEXT_MAX_TOKENS` 120000 供应商 token 时按 `TokenBudget` 优先保留引用与焦点文件全文、截断超大附加稿并标注 `read_files`），以及 `<author_edits>`：本会话 AI 写过但不在本轮全文上下文中的正文文件（`SessionLoader.session_written_file_ids`，
  按页读 assistant 行，先按本项目、未删除的 draft / script 筛再计名额，至多 `AUTHOR_EDITS_MAX_FILES` 个，凑够就停）里作者在 AI 写过之后的手改提醒（`SessionLoader.author_edits_for_files`，单次批量 `FileVersion` 元数据查询判定是否晚于最近 AI 版本被手改，仅在极少数等长等字数候选上批量还原 AI 版本原文核验，零逐行 `SequenceMatcher` 归属计算；受 `service.process_stream` 传入的协同 `cancel_event` 约束）；热缓存轮次还按 `wire_prefix_cache` 携带的 `context_fingerprints` 对未变更的 `<project_files>`、焦点文件与 `<author_edits>` 输出精简 unchanged 标记（不改前面轮次前缀字节、仍保留 `read_snapshots[file_id]`，本轮发生文件变更时失效对应指纹、Stage 2 压缩或滑窗截断时回退完整 `full_text`）。选中文字和首条消息提示仍拼在作者消息后。以全文给出的文件与本会话此前轮次读/写过且库内未变更的文件（`SessionLoader.session_read_snapshots`，至多 `SESSION_SNAPSHOT_MAX_FILES` 个、单次批量 `SELECT` 核验、遇压缩点停止；若本轮开始前轮间压缩成功则经 `AgentRequestContext.drop_initial_read_snapshots` 撤销）作为 edit_file 陈旧检查的起点（[batch-tool-calls-and-session-snapshots][n-batch-snapshots]）。
- **历史**（`loop/history.py`、`loop/wire_prefix_cache.py`）：不开窗，按显式 6 列投影 + keyset 分页（不读取未用的 `reasoning_content` 列）。单循环写下的 assistant 行（`message_metadata.engine == "loop"`）按 `display_events` 顺序还原成
  assistant 文字和成对的 function_call / function_call_output（id 为 `h{行id}_{序号}`，每个调用都有输出）；create_file / write_chapter 结果去掉
  `awaiting_body` / `next_step`，带 `body_saved`（write_chapter 的整章替换只认落库后补发的结果，只有定位结果时 `body_saved=false` 并说明没保存；
  新建写入看 service 落库前按保存回执写进卡片的 `body_saved`（`service._stamp_body_receipts`），为 false 时附说明，修复前没有这个键的行照旧当作已保存）。成功的历史文件写入回执另带 `write_target_active=false`：不恢复上一轮捕获目标，本轮仍需按作者要求重新定位，改为只读时不恢复写入；load_skill / read_skill_resource 与失败调用的结果附 `note`，说明正文 / 给模型的原因没有落库、
  需要时重新调用。活跃会话在 `AGENT_WIRE_PREFIX_CACHE_TTL_SECONDS`（默认 1200s，匹配 MiniMax 20 分钟缓存窗口；`<=0` 关闭）与 `AGENT_WIRE_PREFIX_CACHE_MAX_BYTES`（默认 2 MiB，按压缩后实际入库载荷大小核验）内额外将干净结束或干净软着陆（`end_turn` / `run_deadline_exceeded` / `max_turns_exceeded`，含无未完成/回滚空文件的 `layer="collaboration", limit=None` 协作上限暂停卡，无未配对工具调用尾部、无错误/取消/待写文件/阻塞状态卡/保存前迟到插话）的一轮的 SDK `completed_items` 以 gzip+base64（`zsgz1:` 前缀，适配全局 `decode_responses=True` 连接池，读取端同时无缝兼容滚动发布期间 Redis 残留的旧纯文本 JSON）缓存到 Redis（`agent:wire_prefix:{session_id}`），落库了新消息却跳过写入时记一条带 `skip_reason` 的 `INFO` 日志（仅无错未取消轮次，不与 `wire_prefix_cache` 内部拒写日志重复）并主动 fail-open 删除旧键；下一轮先以 3 列轻量索引查询（`id, role, created_at LIMIT 1`）尝试命中热缓存，按数据库最新消息 id、归属、`system_prompt` SHA-256、`provider:model:key_slot`、`tools_sha256` 与 `reasoning_effort` 严格校验命中后直接复用 `items` + `history_meta` + `context_fingerprints` 并跳过 `load_loop_history` 全表 TOAST 扫描（任何不匹配、会话清空/新建、卡片撤销/恢复、迟到插话或 Redis 异常均 fail-open 回退到 `load_loop_history`）[prompt-cache-stability][n-cache-stability]、[cache-soft-land-and-key-sticky][n-cache-followup]、[author-blame-perf-and-collab-cache][n-blame-perf-collab-cache]、[tier0-minimax-quota-safety][n-tier0-quota]。图写下的旧行回退为纯文本 `row.content` 摘要。首次 run 由 `runner.build_run_input` 把 `state["history_items"]` 排在本轮消息之前；同轮续跑（自查 / 纠偏 / 插话）直接从 `state["run_items"]`（上一次正常结束 run 的 `result.to_input_list()`）只追加续接，待消费插话记在 `state["pending_steering"]`（干净结束时 `service.py` 也直接把 `state["run_items"]` 写入 `wire_prefix_cache`）。
- **压缩**（`loop/compaction.py`）：上一条单循环 assistant 行的 `prompt_tokens`（最后一次模型调用的实测输入，含缓存；没有时用供应商同量纲估算 `estimate_provider_items_tokens`）加上本轮新增输入（`<turn_context>` + 用户消息的 `estimate_provider_text_tokens` 估算）
  超过 `AGENT_LOOP_COMPACT_AT_TOKENS`（300000）时，本轮开始前用会话固定的供应商写交接摘要（`COMPACT_PROMPT`，工具清单经 `Converter.tool_to_openai(build_agent_function_tools())` 生成并保持默认 `tool_choice` 以保住前缀缓存、`should_replay_reasoning_content` 与主循环一致、`max_retries=0`；Responses 供应商走 `acreate_response(..., stream_collect=True)` 流式收集终态 `Response` 以规避长摘要非流式 120s 读超时，并传入 `request_context` 写入 `llm_usage_event` 与免费成本预算；见 [deepseek-cost-reduction][n-deepseek-cost]），
  空、被截断或混入未执行的工具调用机器协议（结构化 `tool_calls` 或代码外泄漏 `<tool_call>` / `<invoke>` / `<parameter>` 标签）时保持默认思考强度附带精炼重试说明再试一次（不降低 `reasoning_effort`），仍不行就不压缩。成功时写进本轮 assistant 行的 `message_metadata.compaction`
  （`summary`、`covers_through_message_id`、`kept_author_messages`、`created_at`），之后的历史 = 压缩点之前最近的作者原话（`AGENT_LOOP_COMPACT_KEEP_USER_TOKENS`
  预算内原文）+ 「【上下文已压缩】」摘要消息 + 压缩点之后的行。保留的作者原话按 `kept_author_messages`（压缩那一轮实际发出的文字，含截断后缀）原样回放，
  不按行重新截断，前缀跨轮逐字不变（再次压缩时被截断过的原话再截一次，按行重算会挪动截断点、让之后整段历史缓存 miss）；没有这个字段的旧记录照旧按行重算。`kept_author_messages` 只给模型回放，聊天历史接口（`client_message_metadata`）不把它发给浏览器。压缩用量以一条 MESSAGE_END 汇入本轮用量；从不在工具循环中途压缩。
  没压缩成时记 `message_metadata.compaction_skipped`（`reason`、`at_prompt_tokens`）：仅模型输出不可用（如 `empty_or_truncated_summary`）触发 `AGENT_LOOP_COMPACT_RETRY_GROWTH_TOKENS`（+100k）退避，瞬时 `provider_error` 不退避、下一轮立即重试（防止单次 5xx 把长会话锁死拖到 950k）；
  本轮没有 assistant 行时两种记录写在本轮作者 user 行上（「对话已压缩」显示事件本身不算回复，压缩后没有任何产出的一轮也只落作者行）；
  重试（`retry_unanswered`）删掉这条没得到回复的作者行前接过它上面的记录，本轮历史照它重建，本轮没有新记录时随本轮的行落库，不重付摘要。
  作者看得到这一步：摘要调用前发 `thinking`（`step=history_compaction`，「正在压缩对话…」）；成功后发 `history_compacted`（`data.summary`），
  并在本轮 `display_events` 开头记 `{"type": "history_compacted", "data": {}}`，刷新后前端在同一位置回放「对话已压缩」标记，摘要读本行的
  `compaction.summary`；没压缩成时只发 `thinking`（`step=history_compaction_skipped`）收起进度，不落库任何显示事件（退避目标封顶 950k，避免逼近 1M 窗口的存量老会话死锁；平台 `APIException` 如免费成本上限原样上抛、不记退避）。`kept_author_messages` 与旧压缩点分页统一用 `estimate_provider_text_tokens` 计 20k 预算，`COMPACT_PROMPT` 不设硬字数上限（由 `AGENT_LOOP_COMPACT_MAX_OUTPUT_TOKENS` 兜底）；若摘要首次请求仍遇 400/413/422 超窗则自动减半输入预算并重折旧工具载荷重试一次，裁剪后为空则直接跳过不落空摘要（`AGENT_LOOP_RUN_HISTORY_MAX_TOKENS` 750000 / `AGENT_LOOP_COMPACT_INPUT_MAX_TOKENS` 700000，`bound_history_for_provider`），常态绝不因 `cl100k_base` 虚高而提前砍掉旧消息。[loop-post-launch-fixes][n-loop-fixes]、[cross-review-fixes][n-xreview]
- **运行循环**（`loop/engine.py`）：每次 run 都是 `runner.run_openai_agents_streaming_agent(agent_type="loop")`。同一轮里只因结构化事实再跑
  （从上一次完整的 SDK 输入接着来）：空文件纠偏（落库核验 + `FILE_BODY_RESUME`，补不成就回滚并发 `empty_file_correction` 卡；write_chapter 定下的整章替换没收到正文时也经 `FILE_BODY_RESUME` 恢复捕获、
  补不成就作废定位，原文不动）、
  保存后自查的续写（本请求有确认落库、还没自查过的完整章节 / 分集正文就续跑一次——`AgentRequestContext.saved_content_body_ids()`，由 StreamAdapter 的保存点
  `_note_body_saved` 连同文件类型记下，不看 `</file>` 之后有没有一句总结；大纲、人物等资料文件不在内；另外停在写完的 `</file>` 或新正文 EOF 上的 run 照旧续跑。写正文的回复到 `</file>` 就结束；续跑说明附落库字数，并要求先自查、用 edit_file 改正再回复，同一个文件只要求一次，见 `_BODY_SAVED_CONTEXT` / `_BODY_RESAVED_CONTEXT`；正文之后已经回复过作者（这次 run 没停在 `</file>` 上）时改用 `_BODY_SAVED_REPLIED_CONTEXT`，只自查、不重复总结；整章替换的新正文没落库时——`AgentRequestContext.unsaved_chapter_replacement_ids()`，
  StreamAdapter 提交 `replace_body` 成功才 `mark_chapter_replacement_saved`——不说已保存、不列它的原稿字数，改发 `_REPLACE_UNSAVED_CONTEXT` 如实说明原文没动；同一章每次重新定位（结构化结果 `action=replaced`）算一次新的替换，
  没保存就再说一次，没有任何正文保存过时也不退回已保存的说明；新正文被输出上限截断时见下）、运行中插话；最多 `AGENT_COLLABORATION_MAX_ITERATIONS` 次。
  `previous_output_truncations` 由 `LoopHistory.trailing_output_truncations` 给出；它大于 0 时本轮第一次 run 的 `agent_directive` 带
  「## 截断后的本轮交付」+ `OUTPUT_TRUNCATED_RESUME`（只进这次 SDK 输入，不写回历史，续跑不再追加）。
  正常结束的 run 最后一次响应的 output_tokens 也到了上限（`OUTPUT_CAP_RATIO`；SDK 不给 finish_reason）时，runner 照常 `end_turn`，MESSAGE_END 多带
  `output_capped=True`（`runner.OUTPUT_CAPPED_KEY`）。这次 run 里又有没等到 `</file>`、按流结束补全保存的新正文（StreamAdapter `_save_new_body` 记进
  `AgentRequestContext.mark_cut_body_saved`，engine 每个 run 边界 `take_cut_bodies()` 取走）时，正文照样保存，但不发 `_BODY_SAVED_CONTEXT`：作者看到固定的
  「《…》写到了单次回复的输出上限，正文已保存到中断处。」（`author_facing_text.body_cut_at_output_cap_notice`），续跑说明 `_BODY_CUT_CONTEXT` 附落库正文末尾 200 字，
  要求用 edit_file `op=append` 从断点续写、不整章重写，写完再自查；没撞上限的未闭合正文（漏写 `</file>`）照旧按已保存续跑。整章替换的新正文被截断时
  仍是原文不动、在纠偏次数内重试，重试说明换成 `_CHAPTER_REPLACE_CUT_RETRY_CONTEXT`（说明被截断、思考尽量简短）。到收尾时间不再开新 run：
  若结构化 `pending_self_check` 为真（或正在跑的 `in_self_check` 通过 `runner.RunExecutionPolicy(defer_wall_clock_soft_landing=True)` 将内层 `limit=wall_clock` 软着陆延迟交回外层循环），且已有确认落库正文、无待写空文件、无截断残稿、无未保存替换、无未执行工具与插话，直接按 `end_turn` 正常收尾（上一轮停在 `</file>` 尚未写总结时补一句确定性的已保存说明），不发暂停卡；其余未完成续跑照旧补发 `wall_clock` 暂停卡（`StreamAdapter` 随之将 `stop_reason` 记为 `run_deadline_exceeded`）；工具轮数耗尽、熔断、无进展、重复、截断都由 runner 原样处理（API 墙钟硬超时补发暂停卡时 `iterations_used` 取 `RunOutcome.runs_started`；单循环不再透传死字段 `last_agent`）。[prod-review-followups][n-prod-review]、[prod-audit-round2][n-prod-audit2]、[tools-simplification][n-tools-simplification]
  收尾提醒（`runner._ModelCallCountingFilter`）用 `LOOP_SOFT_LANDING_PROMPT` / `LOOP_TIME_LANDING_PROMPT`
  （刚调用 write_chapter 或 create_file 还没写 `<file>` 的先写完）。空文件的核验与回滚在 `core/empty_files.py`，收尾说明、插话追加轮提示与续跑说明里的字数在 `loop/run_support.py`。
- **工具与权限**：`registry.LOOP_TOOL_NAMES` 是唯一的工具集（顺序即请求里工具清单的顺序，前缀缓存依赖它），请求里直接从 `tool_schemas.TOOL_SCHEMAS` 按序取出（`get_exposed_tools()`；`CREATE_FILE_TOOL` 只列资料文件与文件夹，不列 draft / script）。没有路由的 read_only / scope_constraints：改不改文件由模型按提示判断，写入直接落库并保留修改卡撤销、历史版本及草稿冲突与版本并发保护（[direct-ai-edits][n-direct-edits]）；
  写作约定追加许可放开（只追加、`expected_notes` 冲突保护不变）。
  create_file 的 `empty=true` 在单循环轮次里直接生效（`AgentRequestContext.author_wants_no_body`，由模型判断，不从原话推断）。
- **改稿恢复**：`FileEditor._commit_ai_content` 在文件写锁内为 edit_file 与 write_chapter(replaced) 先用 `stage_pre_ai_write_backup` 备份未进历史的原稿（非空原稿拿不到历史锚点时抛 `PreWriteHistoryUnavailableError` 拒绝覆盖），在同一事务内创建 AI `FileVersion` 并返回轻量 `undo` 锚点（`before_version_number`、`expected_after_updated_at`、`expected_after_content_sha256`；write_chapter 整章替换顺带改名时另记 `before_title`）。聊天修改卡复用既有的条件版本回滚（`file_version_service.rollback_to_version` + `record_edit_card_undone`；整章替换改名卡在标题未被作者改写且无同级重名时同事务恢复 `before_title`），后续又有编辑时由文件版本历史面板查看或恢复任意旧稿；不再维护独立的 `FileEditOperation` 账本或三方合并撤销接口。正文来源只通过 `author_changes` 帮助模型理解手改，不决定能否改写。
- **可恢复删除**：`delete_file(id, recursive=false)` 对目标文件（及 `recursive=true` 时的存活子树）执行软删除并统一打上同一 `deleted_at` 时间戳，结果卡返回 `id`、`title`、`file_type`、`restored=false`；作者点击聊天删除卡上的「恢复」调用 `POST /api/v1/files/{file_id}/restore`，在项目排他锁与文件写锁下校验父目录存活及同级重名，按同一 `deleted_at` 恢复该次删除的文件或目录子树（不复活更早单独删除的子项），并通过 `record_delete_card_restored` 将 `restored=true` 写回 `ChatMessage.tool_calls` 供刷新展示与后续轮次历史回放。工作台不单独设侧栏回收站。
- **正文按章号写入**（`mcp_tools.write_chapter`）：`write_chapter(chapter, title, file_type=draft|script, parent_id?, empty?)` 在本项目未删除的
  draft / script 里按标题开头的章号（draft）或集号（script）解析目标（`utils.title_sequence.extract_sequence_number(unit=…)`，无前缀章号时按 `file_type` 回退读取 `file_metadata` 的 `chapter_number` / `episode_number` 以识别导入拆章，有界的标识符解析；
  给了 parent_id 只看那个目录，先按 `file_ops.crud.resolve_create_target` 与 create_file 同样归一父节点；正文分在多个目录而没给 parent_id 时
  不把唯一的同号章节当替换目标（`chapter_target_ambiguous`）；标题里的编号与 chapter 不一致时拒绝，没有编号时补上）。没有 → 走 `_create_file_sync` 的新建路径（没给 parent_id 时放进同类型章节所在的唯一目录，没有则放项目默认正文目录 `-draft-folder` / `-script-folder`，不落在项目根）
  （`action=created`，两次回复协议、待写守卫、空文件纠偏 / 回滚、退还时移除空白占位都照旧）；恰好一个 → `action=replaced`，定位时不改数据库，
  记下 `updated_at` 与原长度、绑定待写守卫（传入了非裸章号的新章名时记为 `new_title`，在 `</file>` 落库同一事务内一并更新标题；不记建好时的版本：补不成不回滚、不算可取消的空文件；记下替换的定位结果，
  `<file>` 落库前对同一文件再调 `write_chapter`、`rename_file` 或 `edit_file` 一律返回 `chapter_replace_pending` 且 `mutation_applied=false`，不推进 `updated_at`）；不止一个 → `chapter_target_ambiguous`
  列出候选，不写。作者保存过或没有来源记录的原稿同样可按本轮请求重写，不再做来源权限判断。
  `empty=true` 只建缺的空章节（已有的 `action=exists` 不动）。StreamAdapter 在 `</file>` 后把整章替换交给 `FileEditor.replace_body`
  （与 edit_file 共用 `_commit_ai_content`：写锁内重读、版本核对、原稿备份、AI 版本「AI 整章替换」、同事务更新新标题与 `FileVersion` 撤销锚点），
  然后发 `file_content_end`、`file_edit_start/applied/end`（end 带 `original_content` / `new_content`，前端直接刷新展示最新正文），
  并以同一个 tool_use_id 补发一条 write_chapter 的 tool_result（`body_saved`、`new_word_count`、`previous_word_count`、`undo`；落库卡片随之更新，
  `services/chat_tool_cards` 的撤销标记接受 write_chapter 卡）。版本变了或恢复记录无法建立时不覆盖、发「这一章的整章替换没有保存，原文保持不变」、
  补发 `body_saved=false`，然后结束这一轮（`_stop_after_refused_replace`：之后的模型文字不放出，正常 done；`AgentRequestContext.close_pending_body_files`
  撤销模型跑在前面建好的空文件，之后才建好的由 `_create_file_sync` 自己撤销）。产出与保存回执记录（`StreamBillingTracker._write_effect`，供 `stop_outcome.saved_output`、暂停卡与前端建议门槛使用；消息额度计费见下节 [model-error-only-refund][n-model-error-refund]）：write_chapter
  新建同 create_file 只看 content，替换只认 `body_saved` 且 `mutation_applied` 的补发结果（替换目标即结果 `action=replaced` 且原文非空的 id 记进
  `replace_target_ids`，它的流式 file_content 不算已保存写入：正文流到一半停止 / 断线 / 到时，原文保持不变，但因本轮已进入模型执行仍照常计费；
  原文为空的章节没有覆盖保护，中途停下的正文照常落库；写到 `</file>` 后提交还在途中时被截停，
  StreamAdapter 不随取消丢掉提交，`settle_aborted_capture` 等它的结果写进保存回执，收尾按回执 `note_saved_receipts` 补记已保存写入）；本轮新建文件的回执同样补记（短正文只在解析器尾缓冲里、截停时才补存，没有 file_content 帧），
  API 在本轮还有等正文的文件（`tracker.file_body_pending()`）时都先等回执（有上限）。`</file>` 已解析、还没开始保存就被截停的正文
  （例如 SSE 队列满时停在 yield 上）在发帧前登记为 `_completed_unsaved`，`settle_aborted_capture` 按正常完成的规则补存；正常路径的新正文保存
  在 shield 的任务里跑、版本令牌在任务创建前定下，取消落在保存中途时由 settle 等它的结果；没写完的 `<file>`（截断补全、中途停止）按既有覆盖保护不覆盖原文；run 边界的截断补全被拒时
  待写绑定与守卫留着，engine 在纠偏次数内经 `FILE_BODY_RESUME` 为这一章恢复捕获重试，补不成就作废定位。
- **create_file / edit_file 的正文边界**：单循环轮次里 create_file 不建 draft / script（按 `resolve_create_target` 归一后的类型，剧本分集目录下带集号的 document 同样拒绝；
  `chapter_body_requires_write_chapter`，指向 write_chapter）；
  edit_file 改 draft / script 时 `FileEditor.edit_file(body_guards=True)` 在提交前做两道逐字检查（`file_ops.edit.check_body_edit_guards`，不判断语义）：
  一处 replace 的 old 就是整份原文、或只有一处 replace 且原文两段以上而改完一段都没留下 → `whole_body_replace`（几处各自定位的小改动不算）；改完后有一段（≥ 80 字）逐字出现两次以上而改之前没有
  重复 → `duplicated_paragraph`。整批不落库。资料文件不受影响。
- **落库**：assistant 行 `routing` 为空，`display_events` 只有 content / thinking_content / tool_call / iteration_exhausted / workflow_stopped / history_compacted（`file_content_end` 邻接纯空白协议换行丢弃、移除孤立纯空白文本段并仅裁去首个非空 `content` 段首部换行，普通内部文本段逐字保留，纯空白文本段不触发空 assistant 行落库；本轮已保存正文的章节在自查中重调 `write_chapter` 返回的 `chapter_written_this_turn` 以及墙钟收尾提醒后被拦截的 `wall_clock_wrap_up` 属内部守卫，前端 `INTERNAL_ORDERING_ERROR_TYPES` 不渲染为失败卡，见 [issue-413][n-issue-413]），额外 metadata
  `engine`、`prompt_tokens`、`compaction`（`MessageManager.save_messages(assistant_extra_metadata=…)`，取消 / 失败补存也带）。
- **测试**：AgentService 外壳用例（test_service、test_round3_service、test_user_message_sent_at、test_steering_live_run_enqueue、
  test_agent_service_session_id、test_first_message_hint、test_prompt_cache_prefix、e2e/test_agent_stream_e2e 与 test_api/test_agent、
  test_account_run_limit 里走 AgentService 的用例）替换 `agent.service.run_loop_agent_streaming` 产出工作流事件，外壳照常跑单循环的输入准备
  与落库；`shell_engine` 夹具让替身 ContextAssembler 不往本轮上下文传。引擎本身见 `tests/test_agent/test_loop_agent.py`、
  `test_loop_write_chapter.py`、`test_loop_round_endings.py`、`test_file_body_resume.py`、`tests/e2e/test_loop_agent_e2e.py`、
  `tests/e2e/test_loop_agent_shell_e2e.py`（真实工具 + 脚本模型跑整章替换与撤销、edit_file 撤销元数据、停止回执与计费、时限、轮数耗尽、插话）；
  图写下的旧行的回放见 `test_loop_legacy_graph_rows.py`。

## 请求与流

1. `apps/server/api/agent.py` 的 `POST /api/v1/agent/stream`：鉴权、项目权限（`verify_project_access`）、按用户限流、
   会话忙预检、额度预检、原子获取账号任务名额（`core/account_run_limit.py`，每账号最多 1 个，见下）、
   预扣一次 AI 对话额度（免费账号同时占用客户端网络的共享额度，同一事务，运行 id 即预约 id，单轮至多退还一次；网络额度用完时 402 `ERR_QUOTA_FREE_NETWORK_DAILY_EXCEEDED`）；用 `SSEStreamPump` 包住 `AgentService.process_stream`，`StreamBillingTracker` 记账每一帧。
   同文件还有 `/stop`（作者停止）、`/steer`（运行中插话）、`/suggest`。新增会触发 LLM 的端点必须同时有
   鉴权 + 项目权限 + 成本上限 + 按用户限流（见该文件模块 docstring）。
2. `AgentService.process_stream`（`service.py`）：按本轮已解析的会话 id（`chat_session_id`，不是重新挑最新活跃会话）准备单循环 agent 的输入
   （见上一节），构造单份强类型 `AgentRequestContext`（`tools/tool_context.py`；生产路径下工具每次调用经 `req_ctx.session_scope()` 独立开关短生命周期 DB Session，不再使用全局 `ContextVar` 服务定位器或跨线程复用的懒建 Session），经 `WritingState["request_context"]`、`StreamAdapterConfig.request_context` 与 SDK 原生 `Runner.run_streamed(..., context=req_ctx)`（`RunContextWrapper[AgentRequestContext]`）贯通 `FunctionTool.on_invoke_tool`、`RunHooks`、`call_model_input_filter` 与 `_stop_run_after_tools`（[agent-run-context-wrapper][n-run-context-wrapper]），然后
   `StreamAdapter.process_workflow_events(run_loop_agent_streaming(...))`，结束时保存历史并清理请求级上下文。
3. `loop/engine.run_loop_agent_streaming` 每次 run 调用 `openai_agents/runner.run_openai_agents_streaming_agent`。[cache-stable-prompt-layout][n-cache-layout]
   runner 用 `Runner.run_streamed` 跑 SDK，事件映射：`response.output_text.delta` → TEXT，reasoning delta → THINKING，
   `tool_called` → TOOL_USE，`tool_output` → TOOL_RESULT（熔断后为 MESSAGE_END(stop_reason=tool_failure_circuit_open) + ERROR）。
   熔断、无进展、预算用尽、待写正文被前置守卫挡回时由 `runner._stop_run_after_tools`（SDK 的 tool_use_behavior）同步结束这次 run。
4. `StreamAdapter.process_workflow_events`：`core/workflow_events.StreamEvent` → SSE 帧（`core/events.EventType`），
   用 `StreamProcessor` 捕获 `<file>` 正文并落库。

预算（`apps/server/config/agent_runtime.py`，均可用同名环境变量覆盖）：`AGENT_COLLABORATION_MAX_ITERATIONS`（12，一轮里单循环 agent 最多跑几次；名字沿用旧称）、
`AGENT_TOOL_CALL_MAX_ITERATIONS`（60，单次 run 的 SDK max_turns，最后 `runner.SOFT_LANDING_TURNS` 轮提示收尾）、
`AGENT_RUN_MAX_MODEL_CALLS`（120，请求级）、`AGENT_RUN_WALL_CLOCK_TIMEOUT_S`（780，须早于 Railway 900 秒断连）、
`AGENT_RUN_WRAP_UP_AFTER_S`（默认时限减 240 秒：之后每次模型调用都提醒收尾、不再开新的 run，且已注入墙钟收尾提醒的调用里模型继续发出的工具由 `tools_adapter` 以 `wall_clock_wrap_up` 同步拦截、达上限由 `_stop_run_after_tools` 同步结束 run，避免拖到 780s 硬超时击穿 `wire_prefix_cache`；两种收尾和已有写入的墙钟硬超时都发 `limit=wall_clock` 暂停卡，这一轮记 `run_deadline_exceeded`，见 [pause-card][n-pause-card]）、
`AGENT_SSE_HEARTBEAT_INTERVAL_S`（15）、
`AGENT_LOOP_COMPACT_AT_TOKENS`（300000）、`AGENT_LOOP_COMPACT_KEEP_USER_TOKENS`（20000）、`AGENT_LOOP_COMPACT_MAX_OUTPUT_TOKENS`（24000）、
`AGENT_LOOP_COMPACT_INPUT_MAX_TOKENS`（700000）、`AGENT_LOOP_RUN_HISTORY_MAX_TOKENS`（750000）、`AGENT_LOOP_TURN_CONTEXT_MAX_TOKENS`（120000）。见 [runtime-budget-and-heartbeat][n-budget]、
[no-progress-guard-and-soft-cap][n-noprogress]、[platform-deadline-and-terminal-guard][n-deadline]、[wall-clock-wrap-up][n-wrap-up]、[loop-post-launch-fixes][n-loop-fixes]、[cross-review-fixes][n-xreview]。

## 流的终止、停止与计费

- 每个 `/agent/stream` 恰好以一个终止帧结束（`done` / `workflow_stopped` / `error`）；逃逸异常先补发 error 帧。
  错误码与固定文案在 `core/stream_errors.py`。[terminal-frame][n-terminal]、[error-and-refund][n-refund]
- 计费规则以 `core/stream_billing.py` 与 [model-error-only-refund][n-model-error-refund] 为准（已推翻早期“无 60 字回复或无文件写入即退还”的规则）：
  唯一计费边界是请求是否真正进入供应商模型调用（`services/usage/model_call_guard.py` 在 `reserve_model_call` 通过后、紧贴 `await transport` 之前通过 `services/usage/cost_budget.py` 回调置 `RunOutcome.provider_attempted=True`，含轮前压缩与主循环调用，零反向依赖）以及是否因未恢复的上游模型服务错误终止整轮（`core/llm_failover.py:is_upstream_provider_error` 统一判据 + `FailoverChatModel.unrecovered_provider_error` 单一终态出口 → `runner` `error_source="model"` → `StreamAdapter` 致命终止置 `RunOutcome.terminal_model_failure=True`；`/v1/responses` 的 `response.failed` SSE 事件在 `openai_agents/model.py` 先转换为流内 `openai.APIError` 再抛出，复用既有未出正文自动 failover、会话固定与 `upstream_model_error` 退费链路，不再落入 SDK `ModelBehaviorError`）。
  正常完成（`done` 且无 error）直接计费（特例：经校验的 `ui_action="natural_polish"` 正常完成且真实文件写入证据 `file_written == False` 时，按 `natural_polish_no_change` 退还本次 AI 消息额度并补发 `quota_refunded(kind="no_change")`，真实 `llm_usage` 成本台账与内部 ¥15 每日成本兜底不变，见 [deai-via-agent][n-deai-agent]）；仅 `provider_attempted == False`（进入模型前停止、断线、超时、预检或首次调用成本预检拦截）或 `terminal_model_failure == True`（即使此前已流出部分正文）时退还本轮 AI 消息额度；一旦 `provider_attempted == True` 且非上述上游模型错误终止，纯读取核对、只思考、主动停止、客户端断线、600s 软着陆、780s 墙钟硬超时、发布交接、工具熔断 / `no_progress` / 重复循环 / 模型调用上限 / 输出截断、第 2 次及以后调用撞成本兜底、SDK/本地异常均计 1 轮 AI 消息额度。`produced_output` / `write_succeeded` 仍保留用于 `stop_outcome.saved_output`、时限暂停卡与前端建议门槛。退还实际落库后才发 `quota_refunded`，单轮至多结算一次。
  历史背景见 [error-and-refund][n-refund]、[stop-output-definition][n-output]。
- 作者停止走 `POST /agent/stop` → `SSEStreamPump.request_stop()`，连接上先发 `workflow_stopped(user_stopped)`、等保存回执与迟到写工具结果补齐后真实结算计费（若实际退还额度且满足版本/作者保留安全条件则清理本轮空白占位文件，异常记日志不阻塞 `done`），再以实际 `charged=not refund_applied` 与真实移除集过滤后的回执发出 `done`（退还生效时随后发 `quota_refunded`）；未移除的新建空白占位保留在库并在 `done.unwritten_files` 与 `stop_outcome.unwritten_files` 如实列出。停止 / 断线 / 超时的取消路径会把进行中的 `<file>` 捕获补存，并由 `core/round_outcome.py` 写终态。
  [graceful-stop][n-stop]、[abort-flushes-capture][n-flush]、[stop-output-definition][n-output]、[model-error-only-refund][n-model-error-refund]
- 墙钟时限由 `SSEStreamPump` 执行：到点先发真实超时终态再等生产者收尾。[platform-deadline][n-deadline]、[deadline-terminal][n-deadline2]
- 发布交接：SIGTERM 后 `core/server_drain.py` 让所有运行立即按时限收尾提醒，`api/agent.py` 把进行中的泵的时限提前到 `AGENT_DRAIN_STOP_AFTER_S`（240 秒），到点以 `ERR_AGENT_SERVER_RESTARTING` 收尾、计费同墙钟超时（`billing_reason=server_restarting`）；lifespan 关闭前等后台退还与终态任务。[deploy-drain][n-drain]
- 运行中插话：`/agent/steer` 写入 `core/steering.py` 的队列，runner 在边界处消费。入队与「存在未封口的存活 run」原子判定；工作流结束后、保存历史前的那次 drain 起就封口（本 run 不再把引导喂给模型），释放前的兜底 drain 再封一次；之后的 steer 回 404，前端改为下一条普通消息发送。[steering-busy-409][n-steer]
- 账号任务名额：同一账号所有写作任务（`/agent/stream`）合计最多 1 个，跨项目、会话、设备和进程共享；内部模型调用（含压缩）、工具和 `/steer` 不另占。超限 409 `ERR_ACCOUNT_RUNS_BUSY`，不排队、不自动重试、不扣额；前端把这句话和技能退回输入框。名额在预扣额度前原子获取，每 20 秒续租，90 秒没心跳即失效；`process_stream` 接管后在真正结束时释放（取消路径在残稿补存和会话释放之后），接管前的失败由路由释放。Redis 故障或 staging / production 没配 Redis 时放行，不用进程内计数拒绝请求；首次初始化和数据丢失没有等待窗口。只续租当前任务，不批量写回其他任务，避免已释放任务被旧快照复活。故障恢复和首次混合版本发布期间可能暂时超出 1 个，这是优先保障写作可用性的取舍。[account-two-tasks][n-account-tasks]
- SSE 事件类型见 `core/events.py` 的 `EventType`。

## 上下文与跨轮记忆

单循环 agent 的本轮上下文、历史与压缩见「单循环 agent」一节。ContextAssembler 的解析器与渲染被 `loop/turn_context.py` 沿用，
完整组装（分档与预算）仍被 `/suggest` 和外部 API 使用。

- `ContextAssembler`（`context/assembler.py`）收集焦点文件、附加文件、用户引用、相关大纲 / 角色 / 设定。分档（`context/prioritizer.py`）：
  CRITICAL（焦点文件、用户引用）→ CONSTRAINT（角色设定、高重要度设定、前一章，角色卡与设定排在章节之前）→ RELEVANT（兄弟章节、
  中重要度设定、检索片段）→ INSPIRATION。非 CRITICAL 档截断后不足 500 字且不到原文一半的残片丢弃（仍在文件清单里）；检索只排除最终
  入选的条目；角色 / 设定名作为子串出现在查询里时加分。预算 `AGENT_CONTEXT_TOKEN_BUDGET`（32000）。
- 前一章（`relation="previous"` 的正文条目）标题下多一行 `PREVIOUS_CHAPTER_NOTE`：前一章用于承接情节；文风以作者的 writing_style、样文和作者自己写或改过的章节为准（模型不知道前一章是谁写的，说明不以此为条件）。[prose-craft][n-prose-craft]
- 渲染：条目标题行为 `标题 (id=…) [全文]` 或 `[已截断：显示 x/y 字；全文用 read_files(ids=["…"])]`（检索片段标 `[检索片段，非全文…]`）；
  标注 [全文] 的条目就是该文件截至本请求开始的完整内容（本请求内被修改过的文件除外）。
- 图写下的旧行（`core/session_loader.py`，只读回放）：正文 + 状态卡摘要 + 「[此前的工具操作]」面包屑（创建 / 编辑 / 删除和按 id 精确读取的文件，
  含 parallel_execute 的子任务）；补问卡的问题与选项附在正文后。图里有写工具的角色冻结为 `_LEGACY_GRAPH_WRITE_ROLES`。
- 决策记录：[full-text-and-working-set][n-context]、[characters-and-core-lore][n-lore]、[cache-stable-prompt-layout][n-cache-layout]。

## 工具

工具清单以 `tools/registry.LOOP_TOOL_NAMES` 为准，每一轮在请求里看到的都是同一份（前缀缓存不分裂）；各工具的 JSON Schema 与运行时参数校验统一以 `tools/tool_inputs.py` 的 Pydantic v2 入参模型（`TOOL_INPUT_MODELS`）为单一真相源，由 `export_tool_schema` 剔除 `title`/`$defs`/`anyOf[..., null]` 冗余并保持 `strict_json_schema=False` + `json_repair` + 结构化中文错误 [pydantic-tool-inputs][n-pydantic-tool-inputs]。旧会话历史里调用已删除的工具
（handoff_to_agent / request_clarification / parallel_execute）时，SDK 按 `tool_not_found_behavior="return_error_to_model"` 把错误交还模型。
create_file 录入本轮作者资料时可传 `source_text`：只接受原始作者消息中的非空连续原文（兼容 CRLF、行尾多余空白与连续空行个数差异，唯一命中时截取对应原始切片保存，禁止改写、跳段拼接或补字段），直接沿用创建路径保存；不与 content/empty 混用、不建正文或文件夹，不覆盖同名旧文件。原文复制不进入待写正文状态；模型仍负责选择资料范围，程序不从自然语言判断意图。
普通 create_file 建好空文件后返回 `awaiting_body` 与 `next_step`（同条模型回复里并列发出的后续流式 `create_file` 以 `pending_empty_file_unwritten` 顺序化挡回，不计入请求级失败熔断上限）；下一次回复没先以 <file> 写完正文就调用的工具
（按精确 id 非递归删除本请求新建的这份待写文件除外，删成功后守卫随之清除；此外每轮待写正文逾期后至多允许穿插 2 次 `read_files`/`search_files`/`update_project` 等非冲突工具并附 `pending_file_reminder`，第 3 次起一律拒绝）以 `file_body_pending` 拒绝（`tools_adapter`，按 on_llm_start 记的回复序号判断；单次工具调用对入参与出参各只做一次 JSON 解析并透传 `edit_plan.arguments`），
拒绝后 runner 同步结束当前 run，由单循环 agent 核验并经 `FILE_BODY_RESUME` 安排纠偏轮；这类拒绝不参与同参数熔断，仍计入请求累计上限。
这次回复保持与普通步骤相同的工具清单与默认 `tool_choice`（不设为 `"none"`）：生产实测 MiniMax 在 `tool_choice="none"` 时会剥离 ~4.3k 工具段、击穿前缀缓存并在轮内反复切换两棵缓存树，因此改由同步 `file_body_pending` 守卫 + `_stop_run_after_tools` + `FILE_BODY_RESUME` 兜底 [prompt-cache-stability][n-cache-stability]（反转 [loop-body-reply-without-tools][n-body-no-tools]）。SDK tracing 每次 run 都关掉（`RunConfig(tracing_disabled=True)`），不向 OpenAI 导出内容。
[fast-mode-summary-and-file-body-turn][n-body-turn]、[pending-refusal-returns-to-correction][n-pending-refusal]

| 工具 | 用途 |
|---|---|
| `create_file` | 新建资料文件；source_text 校验并直接保存本轮作者原文，生成资料则先建空文件、随后用 `<file>` 写入；不建 draft / script |
| `write_chapter` | 按章号 / 集号写一章正文，不存在就新建、已存在就整章替换这一章（正文同样随后用 `<file>` 写入，传入新章名时落库一并更新标题，支持撤销与历史版本恢复） |
| `edit_file` | 精确编辑（对外 4 核心操作 `replace` / `append` / `insert_after` / `insert_before`，`replace(new="")` 删除原文且同批配对 `insert_*`/`append` 视作耦合移动；运行时兼容旧 `delete`/`prepend` 与隐藏参数）；逐字、引号等价、模糊都找不到时近似匹配兜底，长章节用种子定位（[approx-seeds][n-approx-seeds]）；范围读取后在已读区间内同样允许模糊/近似匹配、未读区间仍只逐字匹配，陈旧检查始终核对全文；LLM 输出经 `slim_edit_file_result_for_llm` 省略 UI 专用 `undo`、精确匹配回显预览与空样板字段，`StreamAdapter` 经 `AgentRequestContext` 侧通道原样还给前端卡片与落库（[payload-slimming][n-payload-slimming]）；同一文件找不到原文只附恢复提示、不提前熔断，无效文件 id 三次即熔断（[anchor-no-early-trip][n-anchor-no-early-trip]） |
| `batch_replace_files` | 跨文件批量字面替换（支持 `dry_run` 预览与 `file_ids` / `file_types` 范围过滤），逐文件加锁提交、生成独立 `FileVersion` 撤销锚点，并全链路接入计费、`RoundWrittenFiles`、`chat_tool_cards` 撤销标记、热缓存指纹失效、`SessionLoader` 快照与 `RepeatEditGuard` |
| `delete_file` | 删除文件 |
| `rename_file` | 只改文件标题，id 与正文不变（同文件夹重名拒绝；[rename-file][n-rename]） |
| `read_files` | 按 `ids` 批量（去重最多 5 个、单条 `IN` SQL，兼容单 `id` 别名）精确读取完整正文，或对单文件按 `section` / `tail_chars` / `start_char`+`max_chars` 范围读取并支持 `show_paragraph_stats`；逐 ID 记全文陈旧快照（范围读取同时记已读字符区间）与重复读取守卫（[batch-tool-calls-and-session-snapshots][n-batch-snapshots]、[range-reads][n-range]） |
| `search_files` | 按关键词在项目文件正文与标题中检索片段，或按 `file_types` / `parent_id` / `status` 列出文件摘要，永不返回大正文；引导需要全文时再用 `read_files(ids=[…])`（旧 `query_files` 仅保留在后端处理器与历史回放兼容层，不暴露给模型；[tools-simplification][n-tools-simplification]） |
| `update_project` | 项目信息与持久化写作约定（`title`、`summary`、`writing_style`、`current_phase`、`notes`、`expected_notes`；旧 Redis 任务板已退役删除） |
| `load_skill` / `read_skill_resource` | 技能 L2（方法与资源清单）/ L3（参考文件或 `path="SKILL.md"`，支持 `offset` 续读）；受每请求技能 token 预算限制，每请求最多读 `skills/content_budget.MAX_LOADED_SKILLS_PER_REQUEST`（3）个不同技能 |

生成内容的文件流式写入分两次回复：这次回复以 `create_file`（写正文时是 `write_chapter`）工具调用结束；下一次回复以 `<file>` 开头写正文、`</file>` 结束，StreamAdapter 捕获后落库。create_file 的 source_text 原文复制已经直接保存，不再输出文件流。
一次只流式写一个文件。[two-replies][n-two-replies]

## 提示词

- 真实失败稿用于回归和正确反例，不把样例人物、账目、物件或情节关键词写成提示词/Agent 的特判。先定位通用的范围、输入、执行证据或权限缺口；提示策略变更须验证跨题材效果与误改，不能只证明已知样例修好了。
- `prompts/base.py`：`get_base_prompt` 与 `UNIFIED_EXECUTION_PROTOCOL` 等共享协议，回复规则 `LOOP_AUTHOR_REPLY_PROTOCOL` 与工具规则
  `LOOP_TOOL_USAGE_PROTOCOL`（`_LOOP_TOOL_USAGE_HEAD` + 文件 ID / 失败重试 / update_project 通用段 + `_LOOP_TOOL_USAGE_FILE_WRITES` + 思考段：
  写正文用 write_chapter，改已有文件用 edit_file 且不整份替换正文，资料文件用 create_file）。
- `loop/prompts.py`：单循环 agent 的输入布局、工作方式（`LOOP_WORKING_RULES`：三类项目目录规划与 `分章大纲`/`分集大纲` 边界、正文按章写、作者的稿子直接写进对应章节不另存副本、大纲是节奏的上限也是底线、写后快速自查最多一次 `edit_file` 且不重读刚写完的章节、只报工具结果给出的真实字数、以及从已删除多角色 `WRITER_PROMPT` 补回的「行文与成稿质量」六条正面原则；见 [single-loop-prompt-tuning][n-loop-prompt-tuning]）和压缩提示。
- `prompts/suggestions.py`：建议生成。
- 项目写作配置只存数据库（`prompts/__init__.py`）；项目类型的默认模板在 `apps/server/config/project_templates.py`。

## 开发清单

### 添加工具

1. 在 `tools/tool_inputs.py` 定义 Pydantic v2 入参模型并注册进 `TOOL_INPUT_MODELS`，在 `tools/tool_schemas.py` 用 `build_tool_schema` 导出进 `TOOL_SCHEMAS`。
2. 实现加进 `tools/mcp_tools.py` 的 `MCP_TOOL_HANDLERS`（`registry.TOOL_FUNCTIONS` 由它生成）。
3. 在 `tools/registry.py` 把它加进 `LOOP_TOOL_NAMES`（加在末尾：顺序变了会让请求的前缀缓存失效）。
4. 会产生作者可见产出的工具：在 `core/stream_billing.py` 里定义它怎样算产出（`WRITE_TOOL_NAMES` 等），供 `done.produced_output`、`stop_outcome.saved_output` 与时限暂停卡准确记录。
5. 前端：`apps/web/src/components/ToolResultCard.tsx` 的卡片、`apps/web/public/locales/{zh,en}/chat.json` 的 `chat:tool.<name>` 文案、
   `apps/web/src/lib/thinkingDisplay.ts` 的 `UNDERSCORE_IDENTIFIER_KEYS`。
6. 测试：`apps/server/tests/test_agent/test_tool_registry.py`（`validate_registry_alignment`）加上工具自己的用例。

## 调试

- 日志：`apps/server/utils/logger.py` 的 `log_with_context`（自动带 `apps/server/utils/request_context.py` 里的 `request_id` / `agent_run_id`）。每个请求结束时 `apps/server/api/agent.py`
  写一条 `Agent stream billing evaluated`，含真实 `charged`、`billing_reason`、`provider_attempted`、`terminal_model_failure`、`produced_output` 和 `run_*` 运行摘要（模型调用数、阶段耗时、`chapter_first_prose_ms`、`leaked_tool_call_count` 等，来自 `core/run_meter.py`），并在旁路经独立短 session 写入 1 行窄表事实 `agent_round_metric`（零正文/零 PII；异常只记日志、绝不阻塞或影响 SSE；见 [round-metric-ledger][n-round-metric]）。
- 工具执行看 `tools/mcp_tools.py` 的返回结构（`status` / `error_type`）；事件转换看 `stream_adapter.py`；一轮里为什么再跑看 `loop/engine.py` 的日志。
- 测试在 `apps/server/tests/test_agent/` 与 `apps/server/tests/test_api/`（如 `apps/server/tests/test_api/test_agent.py`、`apps/server/tests/test_api/test_agent_stream_hardening.py`）。

[n-budget]: ../../../.agents/notes/implemented/feature/2026-10-05-agent-stream-runtime-budget-and-heartbeat.md
[n-noprogress]: ../../../.agents/notes/implemented/bug-fix/2026-10-08-agent-no-progress-guard-and-soft-cap.md
[n-deadline]: ../../../.agents/notes/implemented/bug-fix/2026-10-13-agent-stream-platform-deadline-and-terminal-guard.md
[n-deadline2]: ../../../.agents/notes/implemented/bug-fix/2026-10-14-deadline-terminal-before-cancellation-cleanup.md
[n-anchor-no-early-trip]: ../../../.agents/notes/implemented/bug-fix/2026-11-04-anchor-misses-do-not-trip-early.md
[n-approx-seeds]: ../../../.agents/notes/implemented/bug-fix/2026-11-04-approximate-match-seeds-long-chapters.md
[n-terminal]: ../../../.agents/notes/implemented/architecture/2026-07-18-sse-stream-always-ends-with-terminal-frame.md
[n-refund]: ../../../.agents/notes/implemented/architecture/2026-10-05-agent-stream-error-and-refund-contract.md
[n-output]: ../../../.agents/notes/implemented/architecture/2026-10-09-stop-output-definition-and-round-outcome.md
[n-stop]: ../../../.agents/notes/implemented/architecture/2026-10-09-agent-graceful-stop-and-no-output-refund.md
[n-flush]: ../../../.agents/notes/implemented/bug-fix/2026-10-05-agent-stream-abort-flushes-file-capture.md
[n-steer]: ../../../.agents/notes/implemented/bug-fix/2026-10-05-steering-run-heartbeat-and-busy-409.md
[n-context]: ../../../.agents/notes/implemented/bug-fix/2026-10-08-agent-context-full-text-and-working-set.md
[n-lore]: ../../../.agents/notes/implemented/bug-fix/2026-10-08-agent-context-keeps-characters-and-core-lore.md
[n-two-replies]: ../../../.agents/notes/implemented/bug-fix/2026-10-10-file-streaming-protocol-two-replies.md

[n-rename]: ../../../.agents/notes/implemented/feature/2026-10-19-agent-rename-file-tool.md
[n-range]: ../../../.agents/notes/implemented/feature/2026-10-19-query-files-range-reads.md
[n-cache-layout]: ../../../.agents/notes/implemented/bug-fix/2026-10-20-agent-cache-stable-prompt-layout.md
[n-body-turn]: ../../../.agents/notes/implemented/bug-fix/2026-10-21-fast-mode-summary-and-file-body-turn.md
[n-prose-craft]: ../../../.agents/notes/implemented/bug-fix/2026-10-31-prose-craft-and-continuity-self-check.md
[n-pending-refusal]: ../../../.agents/notes/implemented/bug-fix/2026-10-25-pending-body-refusal-returns-to-correction.md
[n-body-no-tools]: ../../../.agents/notes/implemented/bug-fix/2026-11-17-loop-body-reply-without-tools.md
[n-wrap-up]: ../../../.agents/notes/implemented/bug-fix/2026-10-21-wall-clock-wrap-up.md
[n-pause-card]: ../../../.agents/notes/implemented/bug-fix/2026-10-31-wall-clock-pause-card-and-receipt.md
[n-drain]: ../../../.agents/notes/implemented/architecture/2026-10-21-deploy-drain-ends-running-generations.md
[n-account-tasks]: ../../../.agents/notes/implemented/feature/2026-11-05-account-concurrency-availability-first.md
[n-loop]: ../../../.agents/notes/implemented/architecture/2026-11-16-single-loop-agent.md
[n-remove-graph]: ../../../.agents/notes/implemented/simplification/2026-11-17-remove-graph-agent.md
[n-loop-fixes]: ../../../.agents/notes/implemented/bug-fix/2026-11-18-loop-post-launch-fixes.md
[n-xreview]: ../../../.agents/notes/implemented/bug-fix/2026-11-18-agent-cross-review-fixes.md
[n-direct-edits]: ../../../.agents/notes/implemented/feature/2026-11-18-direct-ai-edits.md
[n-prod-review]: ../../../.agents/notes/implemented/bug-fix/2026-11-18-loop-prod-review-followups.md
[n-deai-agent]: ../../../.agents/notes/implemented/architecture/2026-11-22-deai-via-single-loop-agent.md
[n-prod-audit2]: ../../../.agents/notes/implemented/bug-fix/2026-11-19-loop-prod-audit-round2-fixes.md
[n-round-metric]: ../../../.agents/notes/implemented/architecture/2026-11-22-agent-round-metric-ledger.md
[n-cache-stability]: ../../../.agents/notes/implemented/architecture/2026-11-20-agent-prompt-cache-stability.md
[n-cache-followup]: ../../../.agents/notes/implemented/architecture/2026-11-21-cache-soft-land-and-session-key-sticky.md
[n-issue-413]: ../../../.agents/notes/implemented/bug-fix/2026-11-19-issue-413-blank-display-and-write-chapter-guard.md
[n-model-error-refund]: ../../../.agents/notes/implemented/bug-fix/2026-11-20-round-billing-model-error-only-refund.md
[n-blame-perf-collab-cache]: ../../../.agents/notes/implemented/bug-fix/2026-11-21-author-blame-perf-and-collab-cache.md
[n-payload-slimming]: ../../../.agents/notes/implemented/architecture/2026-11-21-tool-payload-and-range-slimming.md
[n-batch-snapshots]: ../../../.agents/notes/implemented/architecture/2026-11-21-batch-tool-calls-and-session-snapshots.md
[n-run-context-wrapper]: ../../../.agents/notes/implemented/architecture/2026-11-21-agent-run-context-wrapper.md
[n-pydantic-tool-inputs]: ../../../.agents/notes/implemented/simplification/2026-11-21-agent-pydantic-tool-inputs.md
[n-tier0-quota]: ../../../.agents/notes/implemented/architecture/2026-11-22-tier0-minimax-quota-safety.md
[n-loop-prompt-tuning]: ../../../.agents/notes/implemented/feature/2026-11-22-single-loop-agent-prompt-tuning.md
[n-minimax-responses]: ../../../.agents/notes/implemented/architecture/2026-11-23-minimax-responses-api-prompt-cache-key.md
[n-deepseek-cost]: ../../../.agents/notes/implemented/architecture/2026-11-25-reduce-deepseek-failover-and-summary-cost.md
[n-tools-simplification]: ../../../.agents/notes/implemented/simplification/2026-11-26-agent-tools-simplification-b1-b2-b3.md
