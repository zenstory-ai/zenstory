# Agent Note: 正文收尾后模型多补的空 `<file></file>` 不再外露到对话

Status: implemented

关联：[2026-10-10-same-reply-file-body-unblocks-next-create](2026-10-10-same-reply-file-body-unblocks-next-create.md)（该决策约定"不在 IDLE 状态额外剥离 `<file>`"；本条是只针对收尾窗口里精确空标签对的窄例外，不改变那条约定的其余部分）。

证据来源：`audit-runs/20261009-180227-r5-fix-release-supervisor/prod-regression/sse-loop1.txt` 事件 18015–18022（文件第 54040–54067 行）。

## Problem

生产写作回合里，正文最后一个 `file_content` chunk 之后是 `file_content_end`，随后是 `content` 事件 `"\n\n"`，然后是 `"<"`、`"file"`、`">\n"`、`"</"`、`"file"`、`">"` 六个独立的 content 事件，再接 `tool_call query_files`。也就是说，模型在真实 `</file>` 收尾后又多输出了一对空的 `<file>\n</file>`，被切成很碎的 chunk。

根因：

- `StreamProcessor` 只在捕获进行中识别标记。真实 `</file>` 由 `_handle_end_marker` 收尾后 `reset()` 回到 IDLE，IDLE 分支把每个 chunk 原样当对话返回。
- `normalize_file_markers` 逐 chunk 运行，看不到跨 chunk 的标签。
- `stream_adapter._emit_stream_result` 把这些内容直接发成 content 事件，`service.py` 又把每个 content 事件拼进 `assistant_response`，所以空标签对同时出现在直播气泡和历史消息里。

频率很低（生产该轮 1/1，本地复跑 0/2），但每次出现都是用户可见的协议噪声。

## Decision

只在 `apps/server/agent/core/stream_processor.py` 内做最小改动，不改 adapter、前端和整体解析流程：

- 新增两个字段：`after_close`（收尾窗口）和 `idle_hold`（窗口内暂扣的前缀）。`reset()` 和 `start_file_write()` 都会清掉它们。
- 只有 `_handle_end_marker`（真实 `</file>`，包括流结束复扫确认的悬置标记）会打开窗口。自动补全、DRAINING 收尾、WAITING_START 冲刷都不打开。
- 窗口内（IDLE），`_filter_after_close` 这样处理：
  - 空白原样放行。
  - 去掉空白并转小写后仍是 `<file></file>` 前缀的文字暂扣，长度上限 `2 * MAX_MARKER_LEN`。
  - 凑齐两个 `>` 后，只有 fullmatch `<\s*file\s*>\s*<\s*/\s*file\s*>`（忽略大小写）才丢弃，并记 WARNING 日志 "Dropping empty <file></file> pair emitted after a completed file"。
  - 其他任何文字（叙述、反引号、围栏、`<file/>`、非空块、其他标签）都会关闭窗口并原样输出。
  - 连续多对仅由空白隔开的空标签对都会丢弃。
- 不丢字：`is_active` 扩展为"窗口打开或有暂扣"。适配器现有的 `finalize_on_stream_end` 调用点（MESSAGE_END、ERROR、下一次 create_file 前的 `_flush_active_capture`、流结束）在 IDLE 时都会经 `_release_after_close` 交还暂扣内容并关窗。WRITING 的流结束复扫若确认收尾，也会在同一结果里交还。

## Alternatives considered

- **全局正则清洗对话里的 `<file></file>`**：会吞掉叙述和代码示例里的字面标签，用户明确不接受。
- **前端过滤**：历史消息已经存了标签，前端只能掩盖；而且前端同样分不清协议噪声和字面量。
- **重写流解析器，让 IDLE 也跨 chunk 解析标记**：范围大，风险高，对一个低频噪声不值得。
- **在 TOOL_USE 事件前也调用 finalize 交还暂扣**：需要改 `stream_adapter.py`。暂扣只发生在"空标签对写到一半就切到工具调用"的极端情况，内容会在下一段文字、MESSAGE_END 或流结束时原样交还，不会丢失，所以本轮不改 adapter。

## Consequences

- 按生产 chunk 序列构造的回放测试（收尾 chunk `"</file>\n\n"` 的边界是据 SSE 事件推断重建的，SSE 只记录了 `file_content`/`content` 事件；其后六个碎 chunk 在 IDLE 下一对一透传，与生产完全一致）（`test_prod_split_empty_pair_after_close_is_dropped`，以及 adapter 级的 `test_prod_empty_file_pair_after_close_is_not_emitted_as_chat`）修复前失败，修复后通过。守卫用例在修复前后都通过：字面标签、逐字符切分的行内代码和围栏、非空块、`<file/>`、`<--`、未写完的前缀在流结束/下一次 create_file 前交还、自动补全和 DRAINING 不开窗。
- 修复前已经存进历史的空标签对不做清理。
- 窗口内暂扣的前缀如果正好遇到工具调用，会在工具卡之后才显示；取消时暂扣内容不进对话（取消路径本来就只保存正文）；`_save_file_content` 失败触发 `_fatal_stream_error` 时流结束 finalize 被跳过，暂扣片段（至多 `2 * MAX_MARKER_LEN` 字符，且只可能是空标签对的前缀）同样不会补发，影响可忽略。
- 同一条 agent 消息里，窗口会跨工具调用保持打开，直到出现非空白文字或 agent 边界。所以工具结果之后模型的第一段文字如果恰好是裸的空 `<file></file>`，也会被丢弃。这同样是协议噪声，可以接受；只要先出现叙述、反引号或围栏，窗口就会关闭。
- 可以按上面的 WARNING 日志统计线上触发频率。由于噪声罕见，线上回归只能确认没有退化，确定性证明靠回放测试。
