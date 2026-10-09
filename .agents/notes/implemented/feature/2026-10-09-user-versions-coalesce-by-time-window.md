# Agent Note: 连续的手动保存按时间窗合并成一个版本

Status: implemented

## Problem

新用户审计 #23：编辑器（`SimpleEditor.tsx`）对长度变化 ≤10 字的保存发 `skip_version: true`，服务端 `update_file` 照做，这些改动永远不进历史。作者把「三秒」改成「两秒」、改一个错字、换一个人名，历史里都找不到；配合「恢复前不备份」（见 `2026-10-09-file-restore-backs-up-current-content.md`），一次恢复就把它们冲掉。

反过来，超过 10 字的每次防抖保存（约 3 秒一次）都新建一个用户版本。免费版每个文件只有 10 个用户版本，连续写几分钟就把额度刷光，之后的保存只能「正文照常保存，不再生成新版本」。审计决策表因此担心「按时间合并会更快用完额度」——实际上现状本身就在高速消耗额度，只是把小改动排除在外。

## Decision

- `api/files.py` 的 `update_file` 在持有文件写锁（PG 行锁 / SQLite 条带锁）的同一事务里决定版本：正文有变化且本次 `change_type == edit` 时，先调用 `file_version_service.find_coalescible_user_version`。最新版本 L 同时满足以下条件就合并：
  - `L.change_source == user` 且 `L.change_type == edit`；
  - `now - L.created_at < USER_VERSION_COALESCE_WINDOW_SECONDS`（默认 600 秒；环境变量可覆盖，`0` 关闭合并，非法值回落默认）；
  - `L.snapshot_id` 为空，且本项目没有 `Snapshot.created_at >= L.created_at` 的快照（快照按 `version_id` 引用版本，改写被引用的版本会悄悄改变快照内容）。
- 合并由 `amend_latest_user_version` 完成：只允许改写文件的最新版本；base 版本存新全文，delta 版本相对 `version_number - 1` 的内容重新算 diff；`word_count`、`char_count`、`lines_added`、`lines_removed` 按「上一个版本 → 新正文」重算；`created_at` 保持窗口起点，所以一直在写也不会无限续期同一个版本；不检查额度，额度已满也照样合并。合并在 savepoint 里执行，失败时记 warning 并退回原来的「新建版本」路径。
- 不满足条件就按原逻辑新建版本，受用户额度约束，额度满时 `version_quota_exceeded=true`、正文照常保存。`ai_edit`、`restore`、`auto_save` 等其他 `change_type` 不参与合并，照旧新建。中间夹着 AI 版本、恢复版本、系统备份或快照基线时，最新版本不是用户 edit，自然不合并。
- `FileUpdate.skip_version` 保留在线路上以兼容旧客户端，但对正文变化不再跳过版本，统一走「合并或新建」。前端不改；在所有工作包合并前，仍发 `skip_version` 的旧前端也是安全的：它的小改动会被并进窗口内的版本，或在窗口外新建一个。

## Alternatives considered

- **维持 `skip_version`，只在恢复前备份**（审计决策表把 #23 列为 next 的理由）。最强理由：#9 已经堵住恢复丢稿的主路径，版本写入逻辑不用动，也不会改变额度消耗。被否：小改动仍然不在历史里，作者想找回「两秒」那一版只能靠恰好发生过一次恢复；而大改动按 3 秒一次刷版本的问题也没解决。
- **按字数阈值合并（例如改动 <50 字就并入上一版）**。最强理由：保留每次「大改」作为独立版本，粒度更贴近内容。被否：阈值需要猜，一段一段地写会被拆成很多版本；时间窗对应作者的「一次写作」，10 分钟内的连续修改本来就是同一件事。
- **合并时也扣额度，或额度满时不合并**。最强理由：口径简单，「每次写入历史都算一次」。被否：合并不新增版本行，额度限制的是历史条数；额度满时拒绝合并，又会让最近的改动掉出历史，回到原来的问题。
- **快照之后也允许合并，但给快照存一份全文**。最强理由：作者在拍快照后继续写，依然只占一个版本。被否：要改快照存储格式，超出本次不改表结构的范围；拍快照本身是一个时间点，在它之后另起一个版本也符合直觉。

## Consequences

- 收益：每次保存都会进入历史，包括一个字的改动；连续写作 10 分钟只占一个用户版本，免费额度不再被防抖保存刷光；版本链（base/delta）在改写后仍能完整还原。
- 代价：同一个窗口内的中间状态不再单独保存，只能找回窗口的最终内容；窗口起点固定，作者在 10 分钟边界附近的改动会落到新版本。每次保存多一次「最新版本 + 快照存在性」查询，delta 版本改写时还要重放上一版内容。`test_files.py`、`test_round3_version_quota.py` 中依赖「`skip_version` 不建版本」或「窗口内第二次保存撞额度」的断言随之更新（把首个版本挪出时间窗）；`test_versions.py` 中窗口内的同内容保存不再多出一个版本号。

## Verification

- pytest `tests/test_api/test_file_version_coalescing.py`：10 分钟内连续 5 次保存（含 1 字改动）只产生 1 个用户版本，内容为最后一次，v1 系统基线不变；超过窗口新建版本；中间有 `ai_edit` 或 `restore` 版本时不合并；版本之后拍过快照时不合并且被引用版本内容不变；额度已满（1/1）时仍能合并且 `version_quota_exceeded=false`；在 delta 版本（v9）和 base 版本（v10）上合并后整条链 v1–v10 都能还原；`skip_version=true` 的保存也进入合并。改动前其中 4 个用例失败（其余 4 个是「不合并」的守卫用例）。
- 相关回归：`test_files.py`、`test_round3_version_quota.py`、`test_versions.py`、`test_file_version_service.py`、`test_snapshots.py`、`test_agent_api_versions.py` 等 31 个测试文件 446 passed；另有 10 个失败都是本机 Python 3.14 下已知会失败的 worker-thread 用例（`test_agent_file_precondition` / `test_export_worker`），与本改动无关。PostgreSQL 专用用例（如 `test_canonical_folder_repair_postgres.py` 里带 `skip_version=True` 的并发写）本机没有跑。
