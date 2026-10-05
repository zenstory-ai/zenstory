# Agent Note: Agent 请求体设长度与数量上限，前后端同值

Status: implemented

## Problem

`AgentRequest.message` / `selected_text` 是没有上限的 `str`，`metadata` 是任意 dict。`service.py` 把 message 与 selected_text 原样拼成本轮用户消息，router 和 writer 的每一轮模型调用都会重发；`SessionLoader._trim_history_to_token_budget` 无论多大都至少保留最新一条，所以超大消息落库后下一轮还会整段带上。一次 300KB 的请求只扣 1 次额度，却能让每一轮都多发约 10 万 token；超出上下文时上游 400 又会触发退款，等于白嫖一次 router 调用。前端输入框也没有任何长度限制。

## Decision

- `api/agent.py`：`message` 用 `Field(max_length=AGENT_MESSAGE_MAX_CHARS)`（20000 字），`selected_text` 用 `AGENT_SELECTED_TEXT_MAX_CHARS`（50000 字）。`metadata` 由 `field_validator` 校验：`attached_file_ids`、`attached_library_materials`、`text_quotes` 每类最多 `AGENT_METADATA_MAX_LIST_ITEMS`（20）条，整体 JSON 序列化后不超过 `AGENT_METADATA_MAX_CHARS`（100000 字）。超限在请求解析阶段返回 422 `ERR_VALIDATION_ERROR`，不会进入配额预扣与 LLM 调用。
- 校验失败抛 `PydanticCustomError` 而不是 `ValueError`：后者会把异常对象放进 422 响应的 `errors[].ctx`，`JSONResponse` 序列化失败后变成 500。
- 前端 `src/lib/agentLimits.ts` 的 `MAX_AGENT_MESSAGE_CHARS`（20000）与后端同值；`MessageInput` 输入超限时显示 `chat:input.tooLong` 提示（中英文都有），发送按钮与追加指令按钮禁用，Enter 也不会发送。

## Alternatives considered

- **在 ASGI 层或 Cloudflare/Railway 设请求体字节上限**。最强理由：一处配置覆盖所有端点，连 JSON 解析都不用做。被否：字节上限无法区分「正文」与「附件元数据」，也给不出字段级的错误；它适合作为额外的外层防线，不能代替字段级上限。
- **超限时截断而不是拒绝**（steering 的做法）。最强理由：用户不用手动删减。被否：静默截断一段写作要求会让模型只看到半句话，结果与用户期望不符却没有任何提示；提前在输入框拦下并说明原因更可预期。

## Consequences

- 收益：单次请求能塞进模型的用户文本有明确上界；超限请求在入口被拒，不扣额度、不产生 LLM 成本；前端在发送前就给出原因。
- 代价：确实需要贴整章正文的用户必须改用「附加文件」或文本引用；引用片段整体受 10 万字约束。前端按 UTF-16 码元计数，emoji 会被算成 2 个字符，比后端略严。

## Verification

`cd apps/server && venv/bin/pytest tests/test_api/test_agent_request_limits.py -q`（边界值通过、5 类超限被拒、`/stream` 超长与超量都返回 422 且未调用 `consume_ai_conversation`）；`cd apps/web && pnpm exec vitest run src/components/__tests__/MessageInput.test.tsx`（超限提示、按钮禁用、Enter 不发送）。
