# 「去AI味」统一至单循环写作 Agent（结构化 `ui_action="natural_polish"`）

## Problem

编辑器底栏的「去AI味」按钮曾维护着一条独立的单轮非流式管线（`POST /api/v1/editor/natural-polish`、`services/features/natural_polish_service.py`、自研「原：/改：」行级 diff 解析协议、前端 `NaturalPolishCandidate` 未保存候选预览与重试状态机，共 ~4,500 LOC）。

在单循环 Agent、直接改稿落库（[direct-ai-edits](../feature/2026-11-18-direct-ai-edits.md)）和基于 `FileVersion` 的可恢复编辑（[simplify-recoverable-agent-edits](../simplification/2026-11-19-simplify-recoverable-agent-edits.md)）落地后，保留双管线带来四重问题：

1. **心智与恢复机制割裂**：全站其余 AI 改稿均已统一为「直接落库 + 聊天卡一键撤销 + 文件历史版本」，唯有「去AI味」停留在未保存候选预览特例。
2. **能力天花板与硬限制**：独立接口强依赖选区且硬限 6,000 字，只能看到选区本身（“蒙眼编辑”），无法结合全书写作约定、作者手改（`<author_edits>`）或支持多轮追改，更不支持无选区整章去AI味。
3. **高维护成本与脆弱文本协议**：1,400+ 行自研行级协议解析器频繁因 `invalid_marker` / `missing_anchor` 产生 502 失败，且独立 Prompt 与官方「去AI味」技能形成双份漂移。
4. **重复的安全与配额实现**：独立路由重复实现了鉴权、项目归属、按用户限流、免费网络共享池和失败退款。

## Decision

彻底删除独立的 `/api/v1/editor/natural-polish` 管线与 `NaturalPolishCandidate` 预览层，将「去AI味」按钮与快捷键（`Ctrl/Cmd+Shift+R`）统一到单循环写作 Agent（`POST /api/v1/agent/stream`），由服务端校验的结构化 UI 动作合同驱动：

1. **严格校验的当轮结构化动作（零自然语言关键词启发式）**：
   - `AgentRequest` 新增 `ui_action: Literal["natural_polish"] | None = None`，仅作用于当前单轮请求，不写回会话粘性状态，避免后续普通消息误继承。
   - `POST /api/v1/agent/stream` 在预扣额度前核验 `metadata.current_file_id` 存在、归属当前 `project_id`、未删除且非文件夹，杜绝越权或无效目标消耗额度。
2. **单一数据源方法注入 `<turn_context>`（不污染静态 system prompt 前缀缓存）**：
   - 新增 `agent/loop/ui_actions.py`。当本轮携带 `ui_action="natural_polish"` 时，直接查询数据库中已发布的官方 `PublicSkill`（「去AI味」），不要求用户预先在技能库手动添加（`UserAddedSkill`）；在未预置官方技能的本地/测试库中回退到模块内唯一共享方法常量。
   - 仅将权威去AI味方法、短剧剧本格式保护（保留 `△` 动作行、场号/人物行、`角色：台词` / `角色（神态）：台词` 前缀）以及明确执行约束（优先单次批量 `edit_file` 逐段局部替换；有 `selected_text` 时仅改选区内文字、选区外一字不动；禁止 `write_chapter` 整章重写；禁止非必要 `query_files` / `load_skill`；无具体阅读问题时不调用 `edit_file`）注入本轮 `<turn_context>`。静态 `system_prompt` 跨轮逐字节不变，保持提示词前缀缓存。
3. **基于产物证据的零改动退额**：
   - 在 `StreamBillingTracker.decide(..., ui_action=...)` 中，当 `ui_action == "natural_polish"` 正常结束且本轮 `file_written == False`（未产生真实文件写入，不解析模型自然语言自报）时，返回 `("natural_polish_no_change", True)`，退还本次 AI 消息额度（同时释放免费 IP 共享池名额），并下发 `quota_refunded(kind="no_change")`。真实模型调用的 `llm_usage_event` 台账与内部免费每日 ¥15 成本兜底照常保留，从不向作者展示兜底金额。
4. **前端发送前强制落盘脏稿 + 选区/整章双范围 + 移动端切换**：
   - 删除 `NaturalPolishCandidate`、`naturalPolishApi.ts`、`naturalPolish.ts` 及 `Editor.tsx` 内的 `naturalPolishReviewRef` / `naturalPolishServerCopyRef`。
   - 按钮有选区时显示「去AI味（选区）」并发送 `selected_text`；无选区时直接对当前打开整章去AI味（取消 6,000 字硬墙）；空文件或流式生成中禁用；免费用户保留 `· 用 1 条` 提示。
   - 点击按钮或快捷键后、发起 `/api/v1/agent/stream` 前**必须先 `await flushOpenEditor()`** 保存未保存草稿；若保存失败或触发 `409 stale_write` 冲突则立即中止发送并展示错误/冲突对比，绝不让 Agent 基于旧库正文改稿撞出 `aiEditedWhileDirty`。
   - 移动端（`<768px`）保存并派发动作后自动切至 `AI` 标签页展示实时 SSE 进度、修改卡一键撤销与追改入口，可随时切回 `编辑` 标签页。
5. **下线旧路由与边缘规则**：
   - 删除 `apps/server/api/editor.py`、`apps/server/services/features/natural_polish_service.py` 及其专属单测/E2E；移除 `apps/server/main.py` 路由注册、旧 `ERR_NATURAL_POLISH_*` 错误码，以及 `apps/web/content/site-routing.json` / `apps/web/vercel.json` 的 `v1/editor/natural-polish` 排除项（保留 `v1/agent/stream` 与 `v1/materials/upload`）。`agent/core/llm_client.py` 与 `LLM_USAGE_SOURCE_POLISH` 常量继续保留（分别供 `/agent/suggest` 与历史用量台账读取）。

## Alternatives considered

1. **保留独立管线（仅在独立接口里继续修补协议与支持整章）**：
   - 最强论据：单次调用无需 11k+ token 的 Agent 静态前缀，且可对 DeepSeek 关闭思考，单次润色边际成本更低（约 ¥0.007–0.02 vs Agent 约 ¥0.03–0.05），首字延迟也更短。
   - 拒绝原因：免费用户每日仅 3 条、Pro 订阅可完全覆盖几分钱单次差价；但独立管线持续付出 4,500+ LOC 维护负担、行级协议 502 故障率、与官方技能 Prompt 脱节，以及无法参考项目上下文和多轮追改的体验上限。
2. **双轨混合（选区继续走 `/editor/natural-polish`，整章走 Agent）**：
   - 最强论据：兼顾短选区低成本与长章节能力。
   - 拒绝原因：4,500 LOC 一行无法删除，同时引入“选区先预览再采用、整章直接落库可撤销”的交互割裂，工程与体验 ROI 最差。
3. **直接往聊天输入框塞一段「帮我去AI味」自然语言**：
   - 最强论据：前端改动极小，无需扩展 `/agent/stream` 请求结构。
   - 拒绝原因：违反 `AGENTS.md` 语义判断边界（无法可靠区分按钮动作与普通聊天，也不能在不解析自然语言的前提下做零改动退额），且新用户未手动添加官方「去AI味」技能时会退化为通用润色。

## Consequences

- **Benefits**：
  - 净削减数千行脆弱的独立协议、路由、候选预览状态机与专属测试代码。
  - 全站 AI 改稿体验彻底统一为「直接落库 + 聊天卡一键撤销 + 版本历史兜底」，支持选区与无 6,000 字限制的整章去AI味及多轮追改。
  - 权威「去AI味」方法归于单一数据源，对所有用户即时生效且不破坏 Agent 提示词前缀缓存；零改动退额保持免费用户信任承诺。
- **Costs & Trade-offs**：
  - 单次去AI味会写入当前会话历史并占用同会话串行槽 / 账号 2 并发槽，模型 token 成本略高于独立非思考接口（通过约束单次批量 `edit_file`、禁止 `write_chapter` 和非必要 `query_files` 控制轮次开销）。
  - 下线旧服务里的代码级 `△`/台词后处理守卫后，依赖本轮 `<turn_context>` 格式保护规则与现有 `edit_file` 锚点校验和 `FileVersion` 一键撤销共同兜底。
