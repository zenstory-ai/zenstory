# Agent Note: 停止、断线或 LLM 中途出错时补存进行中的 `<file>` 正文

Status: implemented

## Problem

新章节的主流程是 `create_file` 先建空文件，模型再在回复里用 `<file>…</file>` 流式输出正文。`StreamProcessor` 只把正文累积在内存缓冲里，要到 `</file>`、agent 边界（`MESSAGE_END`）或流正常结束时的 `finalize_on_stream_end` 才调用 `_save_file_content` 落库。两种情况下这一步不会发生：

1. 用户按停止或客户端断线：`CancelledError` 从 `async for event in events` 抛出，跳过流结束时的收尾；`service.py` 的取消路径只保存 `assistant_response`（只含 `content` 事件，不含 `file_content`）。
2. LLM 中途异常：runner 直接发 ERROR、不发 `MESSAGE_END`，`StreamAdapter` 把 `_fatal_stream_error` 置真，流结束时的收尾条件 `not self._fatal_stream_error` 不成立。

结果是编辑器里已经串流了几千字，重新加载后文件是空的，文件树留下一个空的「第 N 章」，按停止的情况还照常扣额度。

## Decision

- `StreamAdapter.process_workflow_events` 捕获 `CancelledError` / `GeneratorExit` 时调用 `_persist_active_capture_in_background()`：同步执行 `finalize_on_stream_end()` 得到补全结果，若是完整的文件正文且不触发截断覆盖保护（`_should_refuse_truncated_overwrite`：目标文件原本已有正文时不整体覆盖），就用 `asyncio.create_task` 在后台调用 `_save_file_content`（独立 session，带 `AGENT_STREAM_FILE_SAVE_TIMEOUT_S` 超时），任务引用保存在模块级集合里直到完成，然后原样重新抛出。这里不 await：取消会再次打断 await，而 `GeneratorExit` 期间挂起会让收尾全部丢失。
- 处理 ERROR 事件时先 `_flush_active_capture()`（与 agent 边界相同的补全与落库，会发出 `file_content_end`），再发 error 帧；若补存本身失败已经发过 `ERR_AGENT_FILE_SAVE_FAILED`，不再发第二个 error 帧。
- 请求级墙钟时限到期时，`process_stream` 被取消，走的是同一条取消补存路径。

## Alternatives considered

- **在取消路径里用 `asyncio.shield` 包住落库并 await**。最强理由：调用方能确定落库完成后才结束请求。被否：Starlette 的 anyio 取消作用域是电平触发的，shield 外层的 await 仍会立即再次收到取消；`GeneratorExit` 路径上挂起则直接触发 `RuntimeError`。后台任务是这两种到达方式下都可靠的写法，`service.py` 的部分历史补存也是同样处理。
- **只把已串流的正文写进部分聊天历史，不写文件**。最强理由：不会碰用户的文件，也绕开覆盖风险。被否：用户期望的是正文出现在那个章节文件里；空文件留在文件树中本身就是错误状态，而覆盖保护已经挡住了「用残稿覆盖已有正文」的风险。
- **取消时删除空文件**。最强理由：文件树保持干净。被否：直接丢掉用户已经看到的几千字，和问题本身一样糟。

## Consequences

- 收益：停止、断线、LLM 中途出错或请求时限到期时，已经串流的正文都会写进对应文件（按截断补全的规则清理控制标记），不再只留下空文件。
- 代价：取消路径上的落库在后台进行，前端在 `finishFileStreaming` 后立即重新加载编辑器时可能先读到落库前的空内容，需要手动刷新或再次打开文件才能看到；没有 SSE 事件告诉前端补存结果。补存的是被截断的正文，章节末尾可能停在半句话。目标文件原本已有正文时（幂等复用分支）为避免覆盖不会补存。

## Verification

`cd apps/server && venv/bin/pytest tests/test_agent/test_stream_launch_hardening.py -q -k "cancelled_stream or llm_error_mid_file"`：取消时进行中的正文被落库、原本已有正文的文件不被覆盖、LLM 中途出错时 `file_content_end` 先于 error 帧且正文已落库。
