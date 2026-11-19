# Bug-Fix Note: 清理 `</file>` 后纯空白展示段与隐藏本轮已存章节的重复 `write_chapter` 守卫卡（Issue #413）

Status: implemented

关联决策：补充 [单循环 Agent 生产复盘跟进修复](2026-11-18-loop-prod-review-followups.md) 与 [单循环 Agent 二次生产复盘修复](2026-11-19-loop-prod-audit-round2-fixes.md)。旧决策记录本身不改写。

## Problem

基于 `main`（`1981d14b`）在 Staging 环境的实测核查（Issue #413），评估确认以下两项确属需要收敛的有界缺陷（其余两项中，强制中文思考不改；偶发 `U+FFFD` 因尚缺原始 SSE 字节流证据且不应引入自动模型返工循环，暂不改业务逻辑）：

1. **`</file>` 闭合后的纯换行块记入 `display_events` 与 `ChatMessage.content` 首部，并击穿空助手消息不落库守卫（#413 第 2 项）**：
   - 连续流式创建多份文件时，每次 `</file>` 之后的换行被转为 `content` 事件，在 `assistant_display_events` 中留下孤立的 `{"type": "content", "content": "\n\n"}`，并在 `ChatMessage.content` 首部累积成 `"\n\n\n\n\n\n…"`。
   - `AgentService._has_assistant_payload()` 与 `MessageManager._save_messages_with_session` 此前将任意非 `history_compacted` 的 `display_events` 视为有效产出，导致仅含纯空白 `content` 事件的空文件回滚或中断轮次仍落库空白 `assistant` 行并虚增 `ChatSession.message_count`；前端 `hasSubstantiveDisplayItems` 及完成态实质内容判定亦未排除纯空白文本段。
2. **写后 `self_check` 续跑中重复调用 `write_chapter` 触发内部守卫 `chapter_written_this_turn`，前端展示为「写章节失败」卡（#413 第 4 项）**：
   - `mcp_tools._write_chapter_replace` 在 `ToolContext.chapter_body_saved_this_request(file_id)` 为真（即该章完整正文已在本轮成功落库）时返回 `error_type="chapter_written_this_turn"`、`mutation_applied=False`。
   - 前端 `INTERNAL_ORDERING_ERROR_TYPES` 仅包含 `file_body_pending` 与 `pending_empty_file_unwritten`，漏掉 `chapter_written_this_turn`，使已成功落库的章节卡下方出现一张误导性的「写章节失败 — 这一步没做成」卡片；同时 `_BODY_SAVED_CONTEXT` / `_BODY_SAVED_REPLIED_CONTEXT` 仅笼统写“不要用 `write_chapter` 整章重写”，未点明服务端硬拦截规则。

## Decision

1. **仅在 `file_content_end` 邻接协议边界与首部空消息守卫处清理纯空白 `content` 事件，普通内部文本段逐字拼接不改写**：
   - `AgentService.process_stream` 跟踪 `file_content_end` 后的邻接空白窗口：若 `file_content_end` 之后紧邻的 `content` 段在遇到下一个工具/思考/终态边界前全为空白，则直接丢弃该协议尾随空白；若紧邻 `file_content_end` 进入正文文本，则仅剥离该展示段首部 `\r\n`（当本轮尚无前序正文时同步剥离 `assistant_response` 首部 `\r\n`）；其余普通 `content` 事件（含被非 `content` 事件切开的内部 `\n`、`\t`、CRLF 与多空行）一律逐字原样拼接，不做跨段空白合成。
   - `agent/core/message_manager.py` 提供 `normalize_assistant_content`、`normalize_assistant_display_events` 与 `has_substantive_display_events`：纯空白消息归一为空串，非空正文仅裁去全文首部 `\r\n`（保留首行空格/Tab/全角 `　` 缩进与内部全部原文）；展示事件剔除孤立纯空白文本块并仅在首个非空 `content` 段剥离首部 `\r\n`，内部文本段逐字保留；`AgentService` 与 `MessageManager` 统一通过 `has_substantive_display_events` 守卫空助手行落库。
   - 前端 `chatRoundEnd.hasSubstantiveDisplayItems` 与 `ChatPanel` 完成态实质产出判定忽略纯空白 `content` / `thinking_content` 段（`MessageList` 渲染层原本即跳过空白 `content` 段，保持 `dropProgressOnlyItems` 仅过滤 `transient` 进度项的既有契约）。
2. **前端隐藏 `chapter_written_this_turn` 内部防重守卫卡，续跑提示仅陈述既有调用限制**：
   - `apps/web/src/lib/agentRoundProgress.ts` 将 `'chapter_written_this_turn'` 加入 `INTERNAL_ORDERING_ERROR_TYPES`（该错误唯一触发前提是同轮完整章节正文已确认落库且成功卡已存在；后端 `ChatMessage.tool_calls` 与 `ToolFailureBreaker` 记账保持不变）。
   - **不隐藏 `chapter_replace_pending`**：核查代码发现 `chapter_replace_pending` 同时用于整章替换待写窗口内拦截 `edit_file`、`rename_file` 与 `delete_file`（含作者插话要求删除已有章节但替换尚未完成、删除确未执行的场景），不能一概当作无害内部顺序守卫静默隐藏。
   - `agent/loop/engine.py` 在 `_BODY_SAVED_CONTEXT` / `_BODY_SAVED_REPLIED_CONTEXT`（中英文）中如实陈述“本轮已保存的这一章不能再调用 `write_chapter` 整章重写，系统会直接拒绝”，不将重大字数违约写成一律忽略，也不增加要求模型例行反问作者是否下一轮压缩/扩写的提示。

## Alternatives considered

- **在 `StreamProcessor` 流式逐 chunk 阶段直接 `strip()` 或吞掉 `</file>` 后的全部空白字符。** 最强理由：源头拦截最彻底，连流式中间帧也不发出 `\n\n`。未采用：流式阶段无法预知后续 chunk 是 `tool_call` 还是紧邻的普通对话段落，逐 chunk 吞空白会破坏跨 delta 的 Markdown 段落空行、首行代码缩进以及 `StreamProcessor` 既有的逐字符无损回退契约；按已完成文本段与落库边界归一即可零副作用解决。
- **将 `chapter_replace_pending` 与 `chapter_written_this_turn` 一并加入 `INTERNAL_ORDERING_ERROR_TYPES` 隐藏。** 最强理由：同一回复并列调用 `write_chapter` + `rename_file` 时也能少显示一张拒绝卡。未采用：`chapter_replace_pending` 发生时新正文尚未落库，且覆盖作者插话后对已有章节调用 `delete_file` 被拒（`tools_adapter._cancels_overdue_file` 放行后由 `_delete_file_sync` 拦截）的真实未执行操作，盲目隐藏会掩盖真实未生效请求。
- **按 Issue 建议在自查提示中写明“字数超出或少于作者要求时一律不改，直接询问作者是否需要下一轮压缩或扩写”。** 最强理由：避免模型在自查轮因篇幅偏差误调工具。未采用：免费账号每日仅 3 条 AI 消息额度，把篇幅偏差固定转嫁为反问用户消耗下一轮额度既损害免费体验，也违背“无实质冲突直接收尾、不追加多余追问”的回复协议。

## Consequences

- **收益**：消除多文件流式创建后的孤立纯空白 `display_events` 与首部空行堆积，修复空文件回滚/中断轮次因纯换行绕过 `_has_assistant_payload` 写入空助手行的问题；消除写后自查误调 `write_chapter` 时紧跟在已存章节卡下方的假「写章节失败」提示。
- **代价**：若模型在同一条回复内对尚未写出 `<file>` 的替换目标并列误调 `rename_file` / `edit_file` 触发 `chapter_replace_pending`，聊天区仍会如实保留该步未改动提示卡。
