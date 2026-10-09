# Agent Note: AI 覆盖正文之前，先把还没进历史的当前正文备份成系统版本

Status: implemented

相关：单文件恢复前的备份见同批的 `2026-10-09-file-restore-backs-up-current-content.md`。本笔记只管 agent 写入路径，恢复路径不在这里。

## Problem

2026-10-08 新用户审计 #9、#33：「第三章原稿不在历史里」。编辑器里长度变化不超过 10 字的保存会带 `skip_version`，正文变了，历史里却没有这一版；作者手动改完以后，最新版本和正文也可能对不上。之后 AI 用 `update_file`（流式 `<file>` 整篇落库）或 `edit_file` 改写这个文件，写入前的那份正文就既不在正文里，也不在历史里，作者找不回来。

edit_file 的撤销锚点（`undo.before_version_number`）只在「最新版本内容 == 写入前正文」时才给，所以碰到这种文件，聊天卡片上也没有撤销入口。

## Decision

备份规则只有一份：`services/features/file_version_service.py` 的 `FileVersionService.backup_unversioned_content(session, file_id, current_content, *, change_summary, unless_equal_to=None)`。恢复历史版本前的备份（`2026-10-09-file-restore-backs-up-current-content.md`）调用的也是它，两条路径只在失败策略上不同。规则：

- 当前正文去掉空白后为空时不备份；传了 `unless_equal_to` 且当前正文等于它时不备份。
- 当前正文等于最新版本内容（没有版本时按空串算）时不备份。它已经在历史里了，所以同一份正文不会被 AI 写入前、恢复前各备份一次。
- 否则调用 `create_version`，参数为 `change_type=edit`、`change_source=system`、`force_base=True`、`skip_quota=True`、`commit=False`。备份存成 base 全文版本，自成一份，恢复它不用重放 diff 链。
- 失败时直接抛异常，由调用方决定怎么处理。

agent 侧的入口是 `agent/tools/file_ops/edit.py` 的 `stage_pre_ai_write_backup(session, file_id, current_content)`，`FileCRUD._update_file_impl` 和 `FileEditor._edit_file_impl` 在改正文之前调用它。调用时调用方已经持有文件锁（PostgreSQL 行锁，或 SQLite 的进程内条带锁），并且处在同一个事务里。它只管 AI 写入路径的失败策略：

- 在 savepoint（`begin_nested`）里调用上面的规则，`change_summary` 为 `BEFORE_AI_EDIT_SUMMARY = "Before AI edit"`。这个常量定义在 `file_version_service.py`，是固定字符串，由前端 `versionSummary.ts` 映射成本地化文案。
- 任何异常只回滚这个 savepoint，记 WARNING，不阻断 AI 写入。这沿用「版本失败、正文照存」的语义。PostgreSQL 上读历史失败也不会污染外层事务（`test_agent_file_preconditions_postgres` 覆盖）。

触发条件：

- `update_file` 只在 `content` 真的变化时备份。比较用的是引号规范化之后的内容；只改标题、改位置时不备份。
- `edit_file` 只在编辑后的内容和原文不同时备份。

和时间窗合并（`2026-10-09-user-versions-coalesce-by-time-window.md`）合起来以后，`skip_version` 不再产生没进历史的正文：窗口内的手动保存会改写最新用户版本，正文始终等于历史头，AI 写入前不需要备份。正文和历史头对不上的情况现在只剩这几种：最新版本不能合并（AI/恢复/系统版本、窗口外、之后拍过快照）并且用户版本额度已满；版本写入失败（正文照存）；以及绕过版本的写入路径。备份兜住的就是这些情况。

备份在 AI 版本之前写入。edit_file 的撤销锚点检查排在备份之后，所以这类文件也能拿到 `before_version_number`。它指向刚建的备份，撤销就回到作者手改的原稿。撤销走的是恢复路径，这时正文等于 AI 版本（历史头），不会再生成「Before restoring」备份。

system 来源的版本本来就不计入用户的版本额度（`get_version_count(change_source=user)`），`skip_quota=True` 又保证额度满的时候照样备份。

## Alternatives considered

- **取消前端 ≤10 字跳过版本的规则，让每次保存都进历史**。最强的理由：在源头堵住，历史里不会再有缺口，也不用在 AI 写入路径上多一步。没选的原因：免费版每个文件只有 10 个用户版本，小改动全部入历史会很快耗尽额度（审计决策 #23 列为 next，需要单独权衡）；而且这条规则改的是前端，不在本包范围。
- **每次 AI 写入前都无条件备份一次**。最强的理由：逻辑最简单，不用读历史头做比较。没选的原因：正文本来就和最新版本一致时（这是常见情况），每次 AI 修改都会多出一条一模一样的备份，历史面板被噪音填满。
- **备份失败就中止 AI 写入**。最强的理由：保证「AI 改写之前，原稿一定能找回」。没选的原因：AI 写入路径的约定一直是「版本失败、正文照存」，版本服务一抖动就让整轮写作失败，代价更大；这时原稿至少还在最近一次手动保存的版本附近。恢复路径（WP4）是作者主动的破坏性操作，那里选择了失败即中止，两种场景不一样。

## Consequences

- 收益：
  - 作者手动改过、没进历史的正文，在 AI 覆盖之前会留下一个可恢复的版本。
  - 这类文件的 edit_file 也有撤销入口了，撤销回到的就是作者的原稿。
- 代价：
  - 符合条件的 AI 写入多一次历史头读取，可能多一条 system 版本，历史里会出现「Before AI edit」条目（需要前端映射成中文）。
  - 「小改动不进历史」的缺口只在 AI 写入前补上，作者自己连续手改时仍然可能丢失中间稿。
  - 备份是 base 全文版本，比 delta 多占存储。合并上线后备份只在上面那几种「正文和历史头对不上」的情况下出现，数量很少。
  - `test_agent_write_backup_version.py` 原来用 `skip_version=True` 的保存制造「没进历史的正文」。时间窗合并之后这条路径会生成用户版本，所以夹具改成「用户额度已满时的手动保存」（断言 `version_quota_exceeded`），也就是现在真实存在的那条路径。
  - 有三个测试的断言本来是写给「没有锚点」这一旧行为的，随行为一起更新：`test_edit_undo.py` 里没有版本、正文没进历史这两种情况，现在断言撤销锚点落在备份上；`test_file_ops_regressions.py` 的并发写入用例，现在断言先备份、后 AI 版本；PostgreSQL 的 provenance 失败用例，现在让前两次历史读取都失败。

## Verification

`cd apps/server && venv/bin/python -m pytest tests/test_agent/test_agent_write_backup_version.py tests/test_services/test_edit_undo.py tests/test_services/test_file_version_rollback_backup.py -q --no-cov -n 0` 全部通过，覆盖：

- 额度已满时的手动保存没进历史，之后 AI 用 update_file 或 edit_file 覆盖：历史里有一个 system 来源的「Before AI edit」版本，内容是手改后的正文，后面才是 AI 版本；
- 正文和最新版本相同时不产生备份，包括窗口内多次手动保存合并之后再做 AI 写入；
- 额度已满时仍会备份，用户版本计数为 0；
- 备份失败时 AI 写入照常落库；
- 撤销锚点落在备份上；带 `expected_updated_at` 撤销这次 AI 修改时，「Before AI edit」只有一条，也没有「Before restoring」备份，回滚返回的版本号就是新建的 restore 版本。

本地 PostgreSQL 14（一次性库 `zenstory_p1b_pgtests`）上跑了 CI 的 PostgreSQL 串行测试清单，全部通过。新增的 `test_ai_write_backs_up_unversioned_body_inside_the_locked_postgres_transaction` 在行锁事务里验证了 update_file 和 edit_file 两条路径。改成共用备份规则之后，又在本机 PostgreSQL 14 上把 CI 的 PostgreSQL 串行清单整份重跑了一遍，306 passed。
