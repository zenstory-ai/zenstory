# Bug-fix Note: 范围读取有自己的重复读取键，局部读取不记成读过全文

Status: implemented

关联：[读取计数按 agent run 清零](2026-10-08-agent-read-guard-per-run-counts.md)、[query_files 按章节或范围读取](../feature/2026-10-19-query-files-range-reads.md)。审计编号 F-11（B-BUG-010）。

## Problem

10-19 加入范围读取（`section` / `tail_chars` / `start_char` + `max_chars`）时，`RepeatReadGuard` 的键没有跟着改：范围读取一律按全文取再切片，守卫算出的模式是 `full`，于是同一文件的不同窗口共用 `(query_files, id, "full")` 一个键。

- 按 100 字一窗顺序读 4 段，第 2、3 段收到「该文件全文已读取过」的错误提示，第 4 个新窗口在执行前被拦下，还计入请求级的 `blocked_reads`，可能推向无进展停止；
- 读过全文 3 次之后，再读结尾 `tail_chars` 会被拦；
- 只读了结尾也被记进 `files_read`，交接摘要对下一个 agent 说「本请求已读取全文」，与范围读取 note「不算看过全文」的决定矛盾。

## Decision

- `_read_key` 在按范围读取时（`content_range.wants_range` 判定，与 query_files 同一组参数）把归一化范围放进模式：`full:section:<标题>`、`full:tail:<N>`、`full:chars:<start>+<max_chars>`（未给 `max_chars` 时按默认 6000）。数字参数 `100` 与 `"100"` 视为同一窗口。不是范围读取时键不变。
- 直接调用与 `parallel_execute` 子任务走同一个 `_read_key`，口径一致。
- 记账时结果第一条 `content_partial is True` 就不记进 `files_read`；范围恰好覆盖全文（`content_partial=False`，例如 `tail_chars` 大于全文）照常记为读过全文，这与 query_files 在这种情况下记全文快照一致。
- 同一窗口重复读取的阈值不变（第 2 次提示、第 3 次警告、第 4 次起拦下），提示文案改为「该文件的这一段内容……」，拦截文案说「这一段内容」。写入、`stale_content` 清零按文件 id 进行，同样清掉该文件所有范围键。`MAX_BLOCKED_READS_PER_REQUEST`、按 run 清零、无进展停止都不变。

## Alternatives considered

- **范围读取完全不进守卫**。最强理由：最简单，局部读取本来就便宜，不会误伤。未采用：同一窗口反复读同样是 10-08 事故里的无进展循环，只是换了参数，守卫应当照样拦。
- **按文件 id 统一计数，不管范围**（现状）。最强理由：键少，模型无法靠换窗口绕过拦截。未采用：顺序分段阅读长合集是范围读取设计的用法，第 4 段就被拦、提示内容还是错的；绕过的风险由请求级 `blocked_reads`、请求模型调用预算和单 run `max_turns` 兜底。
- **把范围按字符覆盖合并，读过的区间再读就提示**。最强理由：能识别「换个起点读同一段」。未采用：需要区间记账，section 还要先解析标题才知道区间，复杂度不值得；owner 偏好轻量规则。

## Consequences

- 收益：分段读长文件的每一段都能执行，提示不再误说「全文已读过」，交接摘要只列真正读过全文的文件。
- 代价：模型可以用略有差异的窗口反复读同一内容而不被拦；上限仍是请求预算、`max_turns` 和工具说明里的读取预算规则。

## Verification

`tests/test_agent/test_repeat_read_guard.py` 新增 5 条：4 个不同窗口经 tools_adapter 全部执行、无提示、不进 `files_read`；同一窗口（含字符串数字写法）第 2 次提示、第 4 次拦下；全文与 tail 互不计数、tail 不算全文；覆盖全文的 tail 记为全文；并行子任务的 section / tail 不受全文计数影响、局部结果不进 `files_read`。改动前 4 条失败、1 条（覆盖全文）通过。
