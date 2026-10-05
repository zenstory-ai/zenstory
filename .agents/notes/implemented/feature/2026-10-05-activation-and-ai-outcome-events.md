# Agent Note: 激活漏斗补记 Google 注册与 Agent 写入，前端补 AI 对话结果与事件量控制

Status: implemented

## Problem

开放注册前的打点审查发现几处数字系统性失真：

- Google OAuth 新用户不写 `signup_success`，前端也只发不带属性的 `oauth_callback_success`。以 Google 为主要入口时，后台激活漏斗的分母偏小，`conversion_from_previous` 可能超过 100%，PostHog 里数不出 OAuth 注册。
- `first_ai_action_accepted` 只在 `PUT /files/{id}` 带 `change_type=ai_edit` 且内容变化时记录。Agent 的 `edit_file` / `create_file` 在审阅前就已写库，用户全部接受时 PUT 回去的内容与库里相同，事件不会写入；首日引导里「完成首次 AI 操作」对多数人一直显示未完成。
- 前端只有 `ai_chat_submitted`，没有完成、失败、断线结果；`STREAM_CLOSED`（代理掐断 SSE）只有前端看得到，团队却无从知道。
- 自动保存每次停顿都发 `file_saved`，web vitals 每个页面加载发 5 个，事件量随写作时长线性增长。
- 升级漏斗的 localStorage 待发队列登出不清，会用下一个账号的 token 发出；token 过期后每 5 秒重试一次 401。

## Decision

- **Google 注册**：`api/oauth.py` 新用户分支在 referral 之后调用 `activation_event_service.record_once(signup_success, event_metadata={source: "google_oauth", invite_code_provided, invite_policy_*})`，`try/except` 内失败只记 WARNING 并 `session.rollback()`，不影响登录。回调 hash 增加 `new_user=1`（只对新用户）；hash 在前端启动时被清掉、不进分析 URL。`OAuthCallback` 把它作为 `handleOAuthCallback(access, refresh, { isNewUser })` 传入，`AuthContext` 对新用户发 `register_success { method: "google", email_verified: true }`，并给 `oauth_callback_success` 带 `is_new_user`；密码注册的 `register_success` 增加 `method: "password"`。
- **Agent 写入即「首次 AI 操作」**：`ActivationEventService.record_ai_write_accepted()` 在 Agent 文件工具提交成功后记录 `first_ai_action_accepted`（`event_metadata.via="agent_tool"`、`tool`、`file_id`、`file_type`）。调用点只在 `agent/tools/file_ops/`：`edit_file` 内容有变化并 commit 后、`create_file` 带非空内容且不是文件夹时、`update_file` 内容有变化时。`user_id` 为空直接跳过；异常 rollback 后记 WARNING 吞掉（内容已提交）。REST `PUT /files` 原有的 `ai_edit` 记录保留。
- **AI 对话结果**：`lib/agentStreamTelemetry.ts` 为每次 `streamAgentRequest` 建一个结算器，`agentApi.ts` 用 `withOutcomeTelemetry` 包住回调，每个流只结算一次：`onDone` → `ai_chat_completed`，`onError` → `ai_chat_failed { error_code, retryable }`，中止 → `ai_chat_cancelled`。属性只有 `project_id`、`duration_ms`、`http_status`、`events_received`、`tool_calls`、`request_id`、`agent_run_id`；不带提示词、回复或错误文本。`STREAM_CLOSED`、`STREAM_ERROR`、`NO_BODY` 和 HTTP 5xx 另外 `captureException(new AgentStreamError(code))`；配额等业务错误码不报异常。`ChatPanel.tsx` 不改。
- **事件量**：`fileApi.update` 对同一文件 5 分钟内最多发一次 `file_saved`（模块内 Map 记时间）。`initWebVitalsMonitoring` 每个页面加载按 `WEB_VITALS_SAMPLE_RATE = 0.1` 抽样一次，抽中才注册 reporter，事件带 `sample_rate`。
- **升级漏斗队列**：`clearPendingUpgradeFunnelEvents()` 清空内存队列、localStorage 键与重试计时器，`AuthContext.clearAuthState`（登出与强制登出共用）调用它。后端返回 401 时记下当时的 token 并停止重试，`getAccessToken()` 换成另一个 token（重新登录）后才恢复发送。

## Alternatives considered

- **在 `handleFinishReview` 只要 `accepted_edit_count > 0` 就记录**。最强理由：语义上更接近「接受」，不把用户事后全部拒绝的写入算进去。被否：`create_file` 生成大纲、新章节完全不经过审阅流程，仍会漏记；Agent 写入本身就是用户发起、默认保留的结果，用工具写入作为信号覆盖面最全。
- **在 `mcp_tools.py` 或 `stream_adapter.py` 里记录**。最强理由：那里能拿到完整的工具调用上下文。被否：这些文件正由另一个 PR 修改；`file_ops` 是两条 Agent 路径共同的落库点，放在这里不会漏也不会冲突。
- **前端用 `/me` 判断是否新用户**。最强理由：不用改回调 URL。被否：`/me` 没有「刚创建」的信息，要么加字段要么按 `created_at` 猜时间窗；回调本来就在新用户分支里，直接带旗标最准。
- **`ai_chat_*` 打在 `useAgentStream` / `ChatPanel`**。最强理由：那里有 UI 状态（是否切换项目、是否手动停止）。被否：`ChatPanel` 不允许改动；`agentApi` 是所有流的唯一出口，能拿到 HTTP 状态与关联 id，且一次请求只结算一次。
- **`file_saved` 改成每个会话只发一次**。最强理由：事件量最小。被否：长时间写作的会话只留一个点，看不出持续写作；5 分钟窗口在量和信号之间更平衡。

## Consequences

- 收益：Google 注册进入后台漏斗和 PostHog；全部接受 AI 写入的用户也会完成激活步骤；AI 对话成功率、断线率、5xx 可见并能用 `request_id` / `agent_run_id` 对日志；`file_saved` 与 web vitals 事件量大幅下降。
- 代价：`first_ai_action_accepted` 现在在 Agent 写入时就记录，包含用户随后全部拒绝的情况，比字面意义的「接受」宽松。每次 Agent 写入多一次按 `(user_id, event_name)` 的查询。`file_saved` 不再等于保存次数，web vital 只代表约 10% 的页面加载，分析时需要按 `sample_rate` 换算。上线前已注册的 Google 用户不会补记 `signup_success`。
