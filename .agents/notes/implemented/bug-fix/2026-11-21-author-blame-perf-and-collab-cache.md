# Bug-fix Note: 消除 `author_changes` 二次退化与僵尸线程，并放行干净协作上限暂停卡写入 `wire_prefix_cache`

Status: implemented

关联：[作者手改差异给模型看](../architecture/2026-10-12-author-edits-shown-not-guessed.md)、[空闲滑动 TTL 会话粘性 Key 路由与软着陆 `wire_prefix_cache` 写入/失效](../architecture/2026-11-21-cache-soft-land-and-session-key-sticky.md)。

## Problem

生产取证（2026-11-21，commit `3f6b33fd`）定位到两个独立的高影响缺陷：

1. **`_prepare_loop_turn_sync` 耗时 7–8 分钟与僵尸 GIL 线程堆积**：
   - 在含 70 个长章节（均长 ~1,737 行、最长 ~3,496 行）的项目中，焦点文件（3,064 行，其中 1 条 2 字符短行重复 594 次）虽 12 个版本全部由 AI 写入（0 次作者手改），每轮启动仍对最多 30 个文件逐版调用 `difflib.SequenceMatcher(..., autojunk=False)`。关闭 `autojunk` 使高频非空短行触发 $O(N^2)$/$O(N^3)$ 退化（单文件 12 版 Apple Silicon 实测 `37.1s`，Railway vCPU 上 ~65–80s；单轮 `prepare_context` 达 `444s–502s`）。
   - 更糟的是，客户端提前断连后 `asyncio.to_thread(_prepare_loop_turn_sync)` 无法中止，残留工作线程继续占满 CPU/GIL，与后续新请求重叠后把单轮 `prepare_context` 拉长至 `780.8s` 触发墙钟硬超时（0 次模型调用）。
2. **干净的协作轮数上限暂停卡被误判为阻塞卡片，导致 `wire_prefix_cache` 跳过写入且缺乏观测**：
   - `loop/engine.py` 达到 `AGENT_COLLABORATION_MAX_ITERATIONS`（12 次 run）正常停下时发出的 `iteration_exhausted` 卡片为 `layer="collaboration"` 且不带 `limit`（落库后 `limit=None`），但 `service._has_blocking_wire_prefix_status_cards` 仅放行 `limit in {"wall_clock", "max_turns"}`，导致多轮超大上下文（~150k–195k token）干净结束却跳过写入 `wire_prefix_cache` 并清掉旧键，相邻「继续」轮次白白损失 238k+ 热缓存 token。
   - 同时，`service.py` 跳过调用 `astore_wire_prefix()` 时无任何 `INFO` 日志（只有 `wire_prefix_cache.py` 在超 2 MiB 时写日志），排障时难以直接区分上游守卫拦截原因。

## Decision

1. **修复 `author_changes` 性能退化并加装总预算与协同取消**：
   - `apps/server/agent/tools/author_changes.py`：
     - `line_blame()` 先用低开销的 `_apply_diff` 沿版本链重建正文；在窗口内首次遇到有效 `effective == AUTHOR` 版本之前，所有连续非作者版本都不会产生 `AUTHOR` 标注或 `AuthorDeletion`，直接推进最新非作者基线、跳过无意义的 `SequenceMatcher` 比对（纯 AI 文件与纯原稿文件 0 次 `SequenceMatcher` 调用；混合链跳过首次作者编辑前的全部非作者 diff；未进版本的作者手改只在最后做 1 次比对）。
     - `_matcher()` 将 `autojunk=False` 改为 `autojunk=True`（Python 标准库仅在 `len(b) >= 200` 时忽略出现率 `> 1%` 的高频行，同时保留空行 `isjunk`，对短文件零影响、消除长文重复短行的二次退化）。
     - `author_hunks()` 在 `baseline == blame.lines` 时跳过冗余 `_matcher`；`touched_author_text()` 在 `AUTHOR not in blame.labels` 时跳过 `_matcher` 仅核对 `_restored_deletions`。
   - `apps/server/agent/core/session_loader.py`、`apps/server/agent/loop/turn_context.py`、`apps/server/agent/service.py`：
     - `SessionLoader.author_edits_for_files()` 增加总墙钟预算 `AUTHOR_EDITS_TIME_BUDGET_S = 2.0`（支持参数覆盖，默认检查完当前文件后若超预算，其余文件体面归入既有的 `unchecked_titles` 提示「未核对」）。
     - `AgentService.process_stream()` 为 `_prepare_loop_turn` / `_prepare_loop_turn_sync` 传入协同 `threading.Event`（`cancel_event`），透传至 `build_loop_turn_context` → `session_written_file_ids` / `author_edits_for_files`；流取消/超时及 `finally` 立即 `set()`，让后台线程在分页/文件边界快速退出，不再堆积僵尸 GIL 线程。
2. **放行干净协作上限暂停卡写入 `wire_prefix_cache` 并补齐 `skip_reason` 日志**：
   - `apps/server/agent/service.py`：
     - `_has_blocking_wire_prefix_status_cards()` 在保留 `unfinishedFiles` / `rolledBackFiles` 严格拦截的前提下，允许 `type == "iteration_exhausted"` 且 `layer == "collaboration"` 且 `limit is None` 的干净暂停卡写入缓存；`has_pending_file_state`（含未保存的整章替换与待写空文件）保持严格阻塞不变。
     - 当已完成且无错误、未取消的一轮（`history_saved and not had_stream_error and not request_failed and not stream_cancelled`）因上游守卫跳过调用 `astore_wire_prefix()` 时，输出单条简洁 `INFO` 日志 `"Agent wire prefix cache store skipped"`（附 `session_id`、`skip_reason`、`stop_reason`，不含任何正文内容）；调用了 `astore_wire_prefix()` 但其内部返回 `False`（如超 2 MiB 或序列化失败）时不重复打日志。

## Alternatives considered

- **直接降低 `MAX_BLAME_LINES` 或限制版本窗口替代快速路径**。最强理由：改一行常量即可。未采用：截断阈值会让正常长章频繁出现「手改没算出来」，降低模型对作者手改的可见性；而跳过首次作者编辑前的非作者 diff + `autojunk=True` 在不牺牲作者手改准确性的前提下直接消除 99%+ 冗余计算。
- **给 `engine.py` 的协作上限暂停卡新增 `"limit": "max_runs"` 字段**。最强理由：与 `wall_clock` / `max_turns` 命名对称。未采用：会改变 `events.py:409` 承诺的既有落库卡片形状（且前后端已有卡片与历史记录均为 `limit=None`）；在白名单中显式放行 `layer == "collaboration" and limit is None` 更小、向后兼容且无需改动卡片协议。
- **放宽 `has_pending_file_state` 以挽救未保存替换轮次的缓存**。最强理由：减少一次 cache miss。未采用：取证已证实 `734e42c9` 正是因为未保存的 `write_chapter` 替换被 `has_pending_file_state` 拦下，下一轮才走 DB 回放注入 `_REPLACE_NOT_SAVED_NOTE` 告知模型原文未动；这是必要的正确性守卫，绝不能放宽。

## Consequences

- **收益**：
  - 纯 AI 长文（3,000 行 × 12 版，含 600 条重复短行）手改核算从 ~37s 降至毫秒级（0 次 `SequenceMatcher` 调用）；带作者手改的长文在 `autojunk=True` + 跳过前置 AI diff 后同样在百毫秒级完成。
  - 2 秒总预算与协同 `cancel_event` 为 `_prepare_loop_turn_sync` 加上硬顶，断连或超时的流不再留下长时间占满 GIL 的僵尸线程。
  - 达到协作上限（12 次 run）干净收尾的长对话不再无故丢弃 `wire_prefix_cache`，后续「继续」与轮前压缩可复用热前缀；所有跳过写入的干净轮次均有明确 `skip_reason` `INFO` 日志可查。
- **代价与风险**：
  - `autojunk=True` 在超过 200 行的文件中将出现率 >1% 的相同行视作 `bpopular`（不作为最长公共子串的锚点，但仍通过邻接扩展参与匹配），对常规小说段落无负面影响；极端重复文本中位置对齐粒度略有折中，换取彻底杜绝平方级卡死。
  - 若同一轮内极端多文件同时包含复杂作者改动且总耗时超过 2 秒，超出预算的文件会落入 `unchecked_titles`，提示模型修改前用 `query_files` 核对（复用既有降级协议）。
