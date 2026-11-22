# 缩短新建/替换章节后的首字等待并消除空白画布假死感

## Problem

生产取证（517 次 `Pending file write set → Found <file> marker`，含 228 次新建章节与 160 次整章替换）显示，用户在 `write_chapter` 建章或定位整章替换后感知的「等待很久才开始流式写正文」呈强长尾分布（p50 = 2.82s，但 p90 = 21.64s，15.8% ≥ 10s，最长 107.48s），根因集中在三点：

1. **正文轮二次长思考与违规调工具（占慢尾 76%+）**：模型调用 `write_chapter` 前往往已完成全章构思，但 `write_chapter` / `create_file` 返回的 `next_step` 未给出短思考与禁调其他工具的明确提醒，工具描述也未提醒「先读完资料再建章」，导致正文轮反复陷入 12s–103s 的二次长思考，或先调 `query_files` / `write_chapter` / `edit_file` / `update_project` 触发 `file_body_pending` 作废整轮。
2. **`WAITING_START` 泄漏 `<tool_call>` 协议头的 20–96s 静默黑洞**：少数请求中模型在待写正文阶段不输出 `<file>`，而是以 `]<]minimax[>[<tool_call>` 或 `<tool_call>` 开头连续输出 3.6k–20k 字符，旧版 `StreamProcessor` 在 `WAITING_START` 一直静默缓冲直到流结束才在 EOF 整段丢弃再进入纠偏轮，白白耗掉 20–96 秒和上万无效 output tokens。
3. **前端等待首字阶段的「假死」体验**：收到 `file_created` 后编辑器立即切到空白章节，画布里仍显示作者态占位符「开始你的创作」、无光标也无等待秒数；聊天底部的 `StreamActivityLine` 在 0 字时就宣告「正在写入正文」，放大了空白等待的焦虑。

## Decision

落地三项零架构风险、零会话内前缀缓存回归的快赢优化：

1. **前移短思考与禁调工具引导（`mcp_tools.py`、`tool_inputs.py`、`tool_schemas.py`）**：
   - 在 `_awaiting_body_data` 与 `_write_chapter_replace` 的 `next_step` 中，复刻生产纠偏轮已验证有效的约束：明确构思已在调用前完成、本次思考只留一两行或直接开始（不重新推演或预写全文）、回复第一行直接输出 `<file>`、写完前不调用 `query_files` / `write_chapter` / `edit_file` / `update_project`。该文案随工具结果追加在对话末尾，零前缀缓存破坏。
   - 在 `WriteChapterInput`（`tool_inputs.py`）与 `WRITE_CHAPTER_TOOL`（`tool_schemas.py`）描述中补充「调用前先读完所需大纲、前一章和角色卡，调用后直接输出 `<file>` 正文，写完前不再调用其他工具」。
2. **`WAITING_START` 泄漏 `<tool_call>` 协议头立即早停并转入 `FILE_BODY_RESUME`（`stream_processor.py`、`stream_adapter.py`、`runner.py`、`loop/engine.py`）**：
   - `stream_processor.py` 提供 `starts_with_leaked_tool_call_marker` 与 `could_be_leaked_tool_call_prefix`：只在尚未打开 `<file>` 且不在代码围栏/行内代码内、去掉前导空白后完整匹配泄漏 `tool_call` 协议头（如 `]<]minimax[>[<tool_call>`、`]<]minimax[>[tool_call>`、`<tool_call>`、`<<tool_call>`）时命中；裸 `]<]minimax[>[`、合法 `]<]minimax[>[file>`、`<file>` 正文与代码围栏内的字面量绝不误触发。
   - `runner.py` 在存在待写正文（`req_ctx.overdue_file_bodies()`）且当前回复文本前缀命中上述完整协议头时，立即调用 `_safe_cancel(result, "immediate")` 中止本次无效生成，不发致命 `ERROR`，先以 `stop_reason = LEAKED_TOOL_CALL_STOP_REASON` 发出 `MESSAGE_END` 结算已完成调用的用量并保留校验过的 `completed_items`（剔除任何末尾悬空 `reasoning` 并核对 `function_call` 成对，保持 `wire_prefix_cache` 干净）。
   - `stream_adapter.py` 在收到该 `stop_reason` 时丢弃 `WAITING_START` 悬置协议残片并复位处理器（保留 `_pending_file_write` 与待写守卫）；`loop/engine.py` 直接将其路由进既有 `FILE_BODY_RESUME` 纠偏轮（不误判为 `body_cut` 或重复发 `unexecuted_tool_text` 卡）。
3. **前端明确区分「等待首字构思」与「逐字写入」状态（`EditorStreamingWaiting.tsx`、`SimpleEditor.tsx`、`StreamActivityLine.tsx` 及中英 locale）**：
   - 当 `isStreaming && !displayContent` 时，`SimpleEditor` 隐藏作者态占位符「开始你的创作」，在顶部横幅与编辑器画布中展示轻量的「正在构思《第N章》开篇，即将逐字写入…」与独立计时的已等待秒数（遵守 `motion-reduce`），收到首字节 `displayContent` 后无缝切换为正文流式写入与跟随光标。
   - `StreamActivityLine` 在 `words === 0` 时展示「正在构思《…》开篇…」（`chat:activity.preparingBody`），`words > 0` 后展示「正在写入《…》正文 · 已写 N 字」。

## Alternatives considered

- **为正文轮重新加回 `tool_choice="none"`**：可从协议层彻底禁止违规调工具，但 PR #430 已用生产数据证明修改 `tool_choice` 会击穿 MiniMax 模板前缀缓存导致缓存命中率大跌，成本与 TTFT 反而恶化，因此不采用。
- **直接对正文轮降档 `reasoning_effort="low"`**：可强行封顶二次长思考，但尚未实测按请求切换 `reasoning_effort` 是否同样破坏 MiniMax 前缀缓存，且存在文质波动风险，留作后续单独授权实测，不混入本次零风险快赢批次。
- **结构改造成流式 `write_chapter(content=...)` 参数**：可省去第二次往返并把 2.8s 基线等待降到接近 0s，但需改造流式 JSON 字符串解码、中断补存、版本并发与计费追踪，改造面与风险显著大于本批次，留待 Tier 1 线上指标验证后再评估。
- **在 `StreamProcessor` 遇到泄漏 `<tool_call>` 时直接发致命 `ERROR` 终止整轮**：实现比接入 `FILE_BODY_RESUME` 更简单，但会让作者白白收到失败报错卡并手动重试；复用 `FILE_BODY_RESUME` 可在 3–5 秒内自动纠偏写完正文，对作者完全无感。

## Consequences

- **收益**：直击二次长思考（44% 慢尾）与违规调工具/泄漏 `<tool_call>` 黑洞（43% 慢尾），零会话内前缀缓存回归，单次泄漏早停可节省 15–90 秒等待与数千到上万无效 output tokens；同时彻底消除建章后空白画布的「假死」观感。
- **代价与权衡**：工具 schema 新增约 77 字符的 `write_chapter` 读前提醒（仅部署生效一次，会话内完全稳定）；`runner.py` 需维护每条模型回复的首部快速剪枝状态，但首字符确定非 `<`/`]` 后即停止检查，对正常长正文几乎零开销。
