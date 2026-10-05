/**
 * Agent 请求体的长度上限，必须与后端 api/agent.py 的 AgentRequest 保持一致。
 * 后端用 Pydantic Field(max_length=...) 拒绝超限请求（422），
 * 前端在发送前先拦下并提示，避免用户把整章正文贴进输入框后才看到报错。
 */
export const MAX_AGENT_MESSAGE_CHARS = 20000;
