# Agent 模块架构文档

本文档描述 zenstory Agent 系统的架构、流程和关键组件。

## 目录结构

```
agent/
├── service.py              # 主服务入口
├── suggest_service.py      # 智能建议生成服务
├── stream_adapter.py       # LangGraph 事件适配器
├── context/                # 上下文组装模块
│   ├── assembler.py        # 上下文组装器
│   ├── budget.py           # Token 预算管理（含请求级 prompt 台账）
│   └── prioritizer.py      # 优先级管理
│                           # 注：没有会话压缩/总结模块，历史只按 token 预算取最近窗口
├── core/                   # 核心基础设施
│   ├── events.py           # SSE 事件定义
│   ├── llm_client.py       # OpenAI 兼容 LLM 客户端
│   ├── message_manager.py  # 消息和系统提示管理
│   ├── session_loader.py   # 会话加载器（历史窗口 + 面包屑 + 跨轮工作集）
│   └── stream_processor.py # 文件流处理器
├── graph/                  # LangGraph 工作流
│   ├── state.py            # 工作流状态定义
│   ├── writing_graph.py    # 图执行入口
│   ├── nodes.py            # 流式节点实现
│   └── router.py           # 意图路由
├── llm/                    # LLM 集成
│   └── openai_agents/     # openai-agents-python / DeepSeek 写作 Agent 适配层
├── prompts/                # 共享协议与角色提示（项目配置仅存数据库）
│   ├── base.py             # 基础提示
│   ├── subagents.py        # 子代理提示 (planner/writer/quality_reviewer)
│   └── suggestions.py      # 建议生成提示
├── schemas/                # 数据模型
│   ├── context.py          # 上下文数据模型
├── skills/                 # 技能系统（标准 SKILL.md，渐进式加载，永不执行脚本）
│   ├── active_skills.py    # 当前用户启用中的技能视图（目录/工具/显式选择共用）
│   ├── context_injector.py # L1 技能目录（只含名称 + 用途）
│   └── package.py          # SKILL.md 解析、zip 导入安全检查、导出打包
└── tools/                  # 工具实现
    ├── tool_schemas.py     # provider-neutral 工具 schema 定义
    ├── file_executor.py    # 文件操作执行器
    ├── mcp_tools.py        # MCP 格式工具函数
    └── permissions.py      # 权限检查
```

## 核心流程

### 1. 请求处理流程

```
用户消息
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  AgentService.process_stream() [service.py]             │
│  - 设置 ToolContext                                      │
│  - 组装上下文 (ContextAssembler)                         │
│  - 加载会话历史 (SessionLoader)                          │
│  - 构建系统提示 (MessageManager)                         │
│  - 调用工作流                                            │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  run_writing_workflow_streaming() [writing_graph.py]    │
│  - 路由策略选择初始 agent（默认 llm，可配置 off）           │
│  - 循环执行 agent 直到完成或达到最大迭代次数              │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  router (llm / off) [router.py]                         │
│  - llm: 调用 router_node()（DeepSeek Chat Completions）   │
│  - off: 固定从 writer 开始                               │
│  - 返回: initial_agent + workflow_plan + workflow_agents │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  run_streaming_agent() [nodes.py]                       │
│  - 组合基础提示 + 专业 agent 提示                        │
│  - 调用 openai_agents.runner                            │
│  - 通过 openai-agents-python 处理工具调用和 handoff       │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  StreamAdapter.adapt_langgraph_events() [stream_adapter]│
│  - 转换 LangGraph 事件为 SSE 事件                        │
│  - 处理文件流式写入 (<file>...</file>)                   │
│  - 发送事件到前端                                        │
└─────────────────────────────────────────────────────────┘
```

### 2. 多 Agent 协作流程

```
┌─────────────┐
│   Router    │ ─── 分析意图，确定工作流
└─────────────┘
       │
       ▼
   ┌───────────────────────────────────────┐
   │         工作流类型 (workflow_plan)      │
   ├───────────────────────────────────────┤
   │ quick       : writer（必要时再 review）  │
   │ standard    : planner → writer（必要时再 review）│
   │ full        : planner → hook_designer → writer（必要时再 review）│
   │ hook_focus  : hook_designer → writer（必要时再 review）│
   │ review_only : quality_reviewer          │
   └───────────────────────────────────────┘
   上表是计划序列的上限：用户没要正文（write_content=false）时剔除 writer，
   用户明确只读（read_only=true）时整条序列清空（router.plan_workflow_agents）。
       │
       ▼
┌─────────────┐     handoff      ┌─────────────┐     handoff      ┌─────────────┐
│   Planner   │ ───────────────► │   Writer    │ ───────────────► │ Quality Reviewer │
│  大纲规划师  │                  │  内容创作者  │                  │   质量审稿人     │
└─────────────┘                  └─────────────┘                  └─────────────┘
```

### 3. Agent 交接机制

Agent 可以通过两种方式交接：

1. **显式 handoff**: Agent 调用 `handoff_to_agent` 工具
2. **工作流自动交接**: 按照 router 规划的 workflow_agents 顺序执行
3. **自动质检门**: writer 写出超过阈值的正文后自动交给 quality_reviewer

用户范围优先于交接（writing_graph.py）：

- agent 以向用户提问收尾（最后一次工具调用之后的文本以问号结尾，见
  `nodes.ends_with_question_to_user`）时，计划交接不触发，本轮停下等用户回答；
  结构化的 `request_clarification` 仍是首选信号。显式 handoff 不受影响。自动质检门不看这个
  信号（writer 收尾"需要我继续写第二章吗？"时，开启了自动质检/高质量模式仍照常送审）。
- 只读请求（router `read_only=true`）不交接给有写权限的 agent，显式 handoff 也拦下，并给一张
  `WORKFLOW_STOPPED(reason="read_only_handoff_blocked")` 提示卡片；交接给 quality_reviewer 这类
  只读 agent 照常。前端收到任何 workflow_stopped 都会结束流式状态，所以拦下时只记录、不 break，
  提示推迟到收尾才发：紧挨在终止事件（WORKFLOW_COMPLETE / 澄清或无效交接 / 轮数耗尽 / ERROR）
  之前，或自然结束时作为图的最后一个事件。`api/agent.py` 判定终止事件时跳过这个 reason
  （`core/events.NON_TERMINAL_WORKFLOW_STOPPED_REASONS`），之后若异常仍补发兜底 error 帧并退款。
  graph 同时置 `state["read_only"]`，`tools_adapter` 对
  `registry.FILE_WRITE_TOOL_NAMES`（create/edit/delete_file、parallel_execute）的调用一律拒绝执行、
  返回 `error_type="read_only_request"` 的可恢复错误（工具仍在清单里，描述标明本轮不可用），
  拒绝经熔断器记账；`parallel_execute` 整次拒绝，即使这一批只有 query_files / hybrid_search
  子任务；`update_project`（项目信息/任务计划，不是文件）不在其中，只读请求下照常执行。
  只读请求万一留下空文件，直接回滚、不安排补写。
- runner 的 `RunConfig` 设 `tool_not_found_behavior="return_error_to_model"`：模型调用工具集里
  没有的工具（如审稿人调 edit_file）时得到 `error_type="tool_not_found"` 的结构化错误并经熔断器
  记账（同名连续 3 次的熔断原因为 `repeated_unavailable_tool_call`），而不是 SDK 抛
  `ModelBehaviorError` 以致命 ERROR 结束。
- 路由的范围判断转成 `state["scope_directive"]`，由 `nodes.run_streaming_agent` 追加到本轮每个
  agent 的系统提示末尾；计划交接的 handoff_packet.todo 也带上用户的 `scope`。
- 空文件纠偏轮由建了空文件的 agent 自己补写（不再硬编码 writer）；被纠偏打断的那一轮若以提问
  收尾，「等用户回答」会带过纠偏轮，纠偏完成后照样停下。
- 自动质检门只认「至少一次写工具成功」：writer 的 create_file/edit_file 全部失败时不送审。

## 关键组件详解

### AgentService (service.py)

主服务类，处理用户消息的流式响应。

```python
async def process_stream(
    session: Session,
    project_id: str,
    user_message: str,
    ...
) -> AsyncIterator[str]:
    # 1. 设置工具上下文
    ToolContext.set_context(session, user_id, project_id, session_id)

    # 2. 组装项目上下文
    context_data = session_loader.assemble_context(...)

    # 3. 构建系统提示
    system_prompt = message_manager.build_system_prompt(...)

    # 4. 执行工作流
    async for event in run_writing_workflow_streaming(state):
        yield stream_adapter.adapt_event(event)

    # 5. 保存消息历史
    await message_manager.save_messages(...)
```

### Router (router.py)

意图分类器，决定使用哪个 agent 和工作流。

**输入**: 用户消息 + 上下文
**输出**:
- `current_agent`: 初始 agent (planner/writer/quality_reviewer)
- `workflow_plan`: 工作流类型 (quick/standard/full/hook_focus/review_only)
- `workflow_agents`: 后续要执行的 agent 列表（已按用户范围裁剪）
- `routing_metadata`: RouterDecision 全量，含用户范围字段 `write_content`（是否要正文，
  None=旧格式未给出）、`read_only`（明确不改文件）、`scope`（交付范围摘要：ROUTER_PROMPT 要求 <=40 字，`MAX_SCOPE_CHARS`=80 为硬截断上限）

### OpenAI Agents SDK Runner (openai_agents/runner.py)

写作 agent 的模型循环由 `openai-agents-python` 承担，项目侧只保留图节点入口、工具适配和事件映射。

```python
async def run_openai_agents_streaming_agent(...):
    # 1. 归一化会话历史和当前用户消息
    api_messages = build_history_messages(state)

    # 2. 构建 SDK Agent（DeepSeek deepseek-flash + 项目工具）
    #    熔断器取 state["tool_failure_breaker"]（writing_graph 每个请求建一个，跨 agent run 共享）；
    #    state["read_only"] 为 True 时写文件工具仍在工具集里，但调用一律拒绝执行（read_only_request）
    sdk_agent = _build_agent(agent_type, system_prompt, failure_breaker=..., read_only=...)

    # 3. 运行 Runner.run_streamed 并映射 SDK 事件
    async for sdk_event in result.stream_events():
        # response.output_text.delta -> TEXT
        # reasoning delta -> THINKING
        # tool_called -> TOOL_USE
        # tool_output -> TOOL_RESULT / HANDOFF / WORKFLOW_STOPPED
        #               （工具失败熔断后：MESSAGE_END(stop_reason=tool_failure_circuit_open) + ERROR）
        yield mapped_event

    # 4. 写回 assistant/tool turn，供后续 graph agent 使用
    state["messages"] = updated_messages
```

### StreamAdapter (stream_adapter.py)

事件适配器，处理文件流式写入。

**核心功能**:
- 转换 LangGraph 事件为 SSE 格式
- 处理 `<file>...</file>` 标记的文件内容流
- 在文件写入完成后更新数据库

### ContextAssembler (context/assembler.py)

上下文组装器，收集项目相关内容。

**收集内容**:
- 焦点文件 (当前编辑的文件)
- 附加文件 (用户选择的参考文件)
- 用户引用文本
- 相关大纲、角色、设定

**优先级** (prioritizer.py):
1. CRITICAL - 焦点文件、用户引用
2. CONSTRAINT - 角色设定、高重要度设定、前一章（档内角色卡与设定排在章节之前）
3. RELEVANT - 兄弟章节、中重要度设定、检索片段
4. INSPIRATION - 其他参考内容

非 CRITICAL 档截断后不足 500 字且不到原文一半的残片直接丢弃（仍在文件清单里）；
检索只排除最终入选的条目；角色/设定名作为子串出现在查询里时加分。

**预算与渲染**：
- 组装上下文预算 `AGENT_CONTEXT_TOKEN_BUDGET`（默认 32000），历史窗口
  `AGENT_CHAT_HISTORY_TOKEN_BUDGET`（默认 32000），两者与系统提示、工作集共用
  `budget.DEFAULT_PROMPT_TOKEN_LEDGER_CEILING`（160000）台账，台账只收缩历史。
- 每个条目标题行渲染为 `标题 (id=…) [全文]` 或
  `[已截断：显示 x/y 字；全文用 query_files(id="…")]`（检索片段标 `[检索片段，非全文…]`），
  「相关内容详情」段头声明「标注[全文]的条目就是该文件当前完整内容（截至本请求开始；本请求内被修改过的文件除外）」。
- `MessageManager` 只对原始上下文做 strip + 兜底封顶（保留换行）；已在原始上下文
  完整出现的条目，world_model truth/surface 里只列「标题 (id=…)（全文见项目上下文）」。

### 跨轮工作集 (core/session_loader.py)

runner 回放历史时丢弃工具结果，所以新一轮靠两样东西知道上一轮做过什么：
- 面包屑：assistant 历史消息末尾的纯文本「[此前的工具操作]」，列创建/编辑/删除，
  以及按 id 精确读取（query_files(id=…)，含 parallel_execute 子任务）的文件标题 + id。
- 工作集：最近 2 条 assistant 回复里读/写过的文件，按**当前库内容**整份注入（最多
  `AGENT_WORKING_SET_MAX_FILES`=8 份、`AGENT_WORKING_SET_MAX_CHARS`=60000 字，超出只列
  标题 + id；已在组装上下文中以 [全文] 出现的只列一行）。`service.py` 把它拼在本轮用户
  消息之前（`<previous_turn_working_set>` 块 + 「【本轮用户消息】」），不进系统提示也不改
  历史消息，前缀缓存可以一直命中到上一轮。

## 工具系统

### 可用工具 (tool_schemas.py)

| 工具名 | 描述 |
|--------|------|
| `create_file` | 创建新文件 (大纲/角色/设定/草稿) |
| `edit_file` | 精确编辑 (replace/insert/append/delete) |
| `delete_file` | 删除文件 |
| `query_files` | 查询和搜索文件 |
| `hybrid_search` | 关键词+向量混合检索（语义检索可能未启用；定位文件以 `query_files(query=…)` 为准） |
| `update_project` | 更新项目状态 |
| `handoff_to_agent` | 交接给另一个 agent |
| `request_clarification` | 请求用户澄清并暂停工作流 |
| `parallel_execute` | 并行执行多个只读/工具任务 |
| `load_skill` | 按名称或 id 加载已启用技能的方法与资源清单（技能 L2，记录用量；受每请求技能内容 token 预算限制，超出截断） |
| `read_skill_resource` | 读取技能附带的一个参考文件，或 `path="SKILL.md"` 读正文；支持 `offset` 分段续读（技能 L3） |

### 文件流式写入协议

创建文件时使用 `<file>...</file>` 标记流式输出内容：

```
1. Agent 调用 create_file (不传 content)
2. StreamAdapter 检测到 create_file 结果，进入等待模式
3. Agent 输出 <file> 标记
4. StreamAdapter 开始收集文件内容
5. Agent 输出 </file> 标记
6. StreamAdapter 将内容写入数据库
```

## SSE 事件类型 (core/events.py)

| 事件类型 | 描述 |
|----------|------|
| `thinking` | 模型思考过程 |
| `content` | 文本内容 |
| `tool_call` | 工具调用开始/进行中 |
| `tool_result` | 工具执行结果 |
| `file_created` | 文件创建完成 |
| `file_updated` | 文件更新完成 |
| `file_content` | 文件内容流 |
| `agent_selected` | Agent 被选中 |
| `handoff` | Agent 交接 |
| `error` | 错误信息 |
| `done` | 流结束 |

## 提示词系统 (prompts/)

### 项目类型提示
- `novel.py` - 长篇小说
- `short_story.py` - 短篇故事
- `screenplay.py` - 剧本

### Agent 专业提示 (subagents.py)
- `ROUTER_PROMPT` - 意图分类规则
- `PLANNER_PROMPT` - 大纲规划指南
- `WRITER_PROMPT` - 内容创作规范
- `QUALITY_REVIEWER_PROMPT` - 质量检查标准

## 开发指南

### 添加新工具

1. 在 `tools/tool_schemas.py` 添加工具定义
2. 在 `tools/mcp_tools.py` 实现工具函数
3. 在 `tools/registry.py` 注册工具映射

### 添加新 Agent 类型

1. 在 `prompts/subagents.py` 添加专业提示
2. 在 `graph/router.py` 更新路由逻辑
3. 在 `graph/nodes.py` 的 `specialized_prompts` 注册

### 调试技巧

- 查看日志: `utils/logger.py` 的 `log_with_context`
- 检查工具执行: `tools/mcp_tools.py` 的返回值
- 跟踪事件流: `stream_adapter.py` 的事件转换
