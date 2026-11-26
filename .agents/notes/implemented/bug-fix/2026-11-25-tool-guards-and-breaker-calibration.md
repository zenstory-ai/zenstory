# 待写正文守卫放行非冲突工具、RepeatEditGuard 批量优先与软跳过、熔断器跨文件校准

Status: implemented

关联：[2026-11-23-repeat-edit-guard-and-thresholds](../feature/2026-11-23-repeat-edit-guard-and-thresholds.md)、[2026-10-11-repeat-create-of-pending-empty-file](2026-10-11-repeat-create-of-pending-empty-file.md)、[2026-10-03-agent-tool-failure-circuit-breaker](2026-10-03-agent-tool-failure-circuit-breaker.md)。

## Problem

48 小时生产工具调用失败治理审计（1,082 轮 / 7,687 次计量工具调用 / 7,795 张落库工具卡片）定位到三类运行期守卫误伤与遥测失真：

1. **待写 `<file>` 前置守卫误拆轮（`file_body_pending`，88 次 / 70 轮）**：`tools_adapter.py` 对上一次回复建好、尚未写 `<file>` 的文件一刀切拦截所有非 `delete_file` 工具并返回 `file_body_pending`，触发 `runner._stop_run_after_tools` 强行拆断 SDK run 并安排 `FILE_BODY_RESUME` 纠偏轮。其中 `30/88`（34.1%）是严格不碰 `<file>` 槽位的只读/元数据工具（`query_files`、`update_project`、`load_skill`、`read_skill_resource`），另有大量 `write_chapter -> write_chapter` / `create_file -> create_file` 同目标重复调用在 `mcp_tools.py` 已有幂等吸收，却在执行前就被前置守卫挡回并拆轮。
2. **`RepeatEditGuard` 批量优先与软跳过状态污染（`edit_repeat_limit`，80 张错误卡）**：`repeat_edit_guard.py` 先判断 `written_this_turn` 再判断 `is_bulk`，导致新写章节上 `len(edits) >= 6` 的批量自查修订被 `post_write_limit` 误拦（5/80）；且软跳过回执发出 `"status": "error"`，在 `chat_message.tool_calls` 中落库为 80 张 `status="error"` 卡片，拉高表观 `edit_file` 失败率。
3. **`ToolFailureBreaker` 跨文件批量误熔断（`tool_failure_circuit_open`，15 张错误卡）**：`tool_failure_breaker.py` 的 `total_failures` 严格单调累加，24 章批量格式化任务在前 23 章成功写入后，后续轮次在另外 9 章上遇到 `anchor_not_found`（累计达 10）即触发 `TRIP_REASON_TOTAL` 致命熔断，中断整轮任务。

## Decision

1. **`tools_adapter.py` 待写 `<file>` 守卫分级放行**：
   - 严格不冲突的只读/元数据工具（`query_files`、`update_project`、`load_skill`、`read_skill_resource`）以及不占用 `<file>` 槽位的 `create_file`（`file_type="folder"`、`empty=True` 或非空 `source_text`）在存在待写文件时正常执行，不记 `file_body_pending`、不触发 `runner` 拆轮，并在其 JSON 结果顶层附带一行 `pending_file_reminder`（提醒完成本步后以 `<file>` 开头写完待写正文）；若已进入墙钟收尾提醒，仍优先按 `wall_clock_wrap_up` 拦截。
   - 指向当前同一待写目标的重复 `create_file`（标题匹配）与 `write_chapter`（已武装整章替换的章号匹配，或按有界标题序号 `extract_sequence_number` / `prefixed_chapter_heading` 与标题匹配）放行至 `mcp_tools.py` 既有的同目标吸收路径，不触发 `file_body_pending` 拆轮。
   - 真正冲突的写操作（`edit_file`、非精确取消的 `delete_file`、`rename_file`、不同章号 `write_chapter`、新占槽 `create_file`）继续保持硬 `file_body_pending` 拦截与 `FILE_BODY_RESUME` 纠偏。
2. **`repeat_edit_guard.py` 批量优先与非错误软跳过**：
   - 本轮新写章节在 `post_write_limit` 拦截前给予 `POST_WRITE_BULK_ALLOWANCE = 1` 次批量自查修改额度（`len(edits) >= BULK_EDIT_MIN_ITEMS`），既避免误拦 6+ 处合法自查修订，又防止新章陷入无限批量循环。
   - 软跳过回执改为 `"status": "skipped"`、`"guard_skipped": True`、`"reason": reason`、`"message": ...`、`"mutation_applied": False`、`"edits_applied": 0`，保留 `"error_type": "edit_repeat_limit"` 与 `"user_message"`，移除 `"error"` 键以避免 `StreamAdapter` 落库为 `status="error"` 或透传内部提示文案（前端 `MessageList.tsx` 需在后续配套按 `guard_skipped` / `isInternalOrderingRefusal` 不限 `status==='error'` 隐藏该内部卡片）。
3. **`tool_failure_breaker.py` 跨文件有效写入解耦与绝对上限**：
   - 保留 `MAX_IDENTICAL_TOOL_FAILURES = 3` 与 `file_not_found` 恢复族 3 次熔断不变，保留 `total_failures` 单调计数用于观测。
   - 新增 `consecutive_failures`：每次真实落盘且跨不同文件的有效写入（`edit_file` / `create_file` / `write_chapter` / `rename_file` / `delete_file`，非 `awaiting_body`、非 `mutation_applied=False`、非 `guard_skipped`、且 `file_id != _last_mutated_file_id`）将其归零；只读查询、元数据更新、空占位、同文件重复编辑和 `RepeatEditGuard` 软跳过均不重置。
   - `TRIP_REASON_TOTAL` 在 `consecutive_failures >= max_failures_per_request`（默认 10）或 `total_failures >= lifetime_failure_cap`（默认 `3 * max_failures_per_request = 30`）时熔断，兼顾多章批处理容错与防多文件交替空转兜底。

## Alternatives considered

1. **完全撤掉 `tools_adapter.py` 的 `file_body_pending` 前置守卫，全交给 `mcp_tools.py` 事后报错**：
   - 最强论据：逻辑集中在一处，减少适配层判断。
   - 拒绝原因：`edit_file` / `rename_file` / 误删别的文件在 `mcp_tools.py` 里没有待写正文拦截，会直接落库污染正文或破坏其他文件（2026-10-25 事故）；保留硬守卫仅放行严格非冲突工具与同目标重复更安全。
2. **对新写章节彻底豁免所有 `is_bulk` 修改（无次数上限）**：
   - 最强论据：与既有文件的 `is_bulk` 规则完全一致。
   - 拒绝原因：新写章节如果模型把每次 6 处微调打成批量循环，仍可能反复重写刚写完的章节；允许 1 次批量自查修订已覆盖真实自查需求并保持有界收敛。
3. **直接把 `MAX_TOOL_FAILURES_PER_REQUEST` 调大到 30**：
   - 最强论据：改动最小，一行常量。
   - 拒绝原因：在零有效写入的真故障/乱试场景下，会把单轮空转时间与 token 浪费放大 3 倍；按跨文件有效写入重置连续失败计数 + 30 次绝对上限，既保住零进展 10 次即停，又放行多章批处理。

## Consequences

- **收益**：消除只读/元数据工具与同目标重复写触发的 `file_body_pending` 误拆轮；允许新写章节完成 1 次 6+ 处批量自查修订，并使 `RepeatEditGuard` 软跳过不再以 `status="error"` 污染持久化遥测；24 章跨文件批量处理不再因历史零星 miss 在累计第 10 次被致命熔断，同时保持同参数 3 次、无效 UUID 3 次、零进展 10 次与多文件空转 30 次的四重安全防线。
- **成本与配套**：`RepeatEditGuard` 改为 `status="skipped"` 后，`round_outcome.tool_result_status` 会映射为非错误 `status="success"`；后端 `stream_adapter.py` 已跳过 `guard_skipped=True` 的 `file_edit_*` 事件，前端 `apps/web/src/components/MessageList.tsx` 也已在 #483 同步过滤 `guard_skipped === true` 与 `edit_repeat_limit`。
