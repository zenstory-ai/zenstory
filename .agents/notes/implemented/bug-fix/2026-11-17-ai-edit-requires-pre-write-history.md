# Agent Note: edit_file 与整章替换在原稿进不了历史时拒绝写入

Status: implemented

相关：部分推翻 `2026-10-09-ai-write-backs-up-unversioned-text.md` 中被否决的备选「备份失败就中止 AI 写入」，只限 `edit_file` 与 `write_chapter` 的整章替换（两者共用 `FileEditor._commit_ai_content`）。旧笔记不改写。

## Problem

作者保存过的正文因版本额度已满等原因没有进历史时，AI 写入前会先在 savepoint 里备份一份 system 版本。备份失败时旧规则只回滚 savepoint、记 WARNING，然后照常提交 AI 修改。撤销锚点只在历史头逐字等于原稿时才给，这时也拿不到。

结果是：`edit_file` 局部删除或改写了作者新加的一段，工具返回成功，但这段文字既不在当前正文里，也不在任何历史版本里，作者找不回来。旧笔记的理由「原稿至少还在最近一次手动保存的版本附近」不成立：`edit_file` 可以删掉恰好不在最近版本里的那一段。

## Decision

`agent/tools/file_ops/edit.py` 的 `FileEditor._commit_ai_content` 在备份和撤销锚点之后、改正文之前加一条规则：原稿去掉空白后非空，且没有拿到撤销锚点（`before_version_number is None`），就 `session.rollback()`（释放行锁，正文与 `updated_at` 不变），抛 `PreWriteHistoryUnavailableError`。

- 判断用的是现有的结构化状态，不加新标志：非空原稿经过 `backup_unversioned_content` 之后，要么历史头本来就等于它，要么刚建了一份等于它的备份。所以拿不到锚点只可能是备份失败或锚点读取失败。
- 空白原稿没有可丢的字，照常写入（锚点照旧尽力补一个空白基线）。
- `PreWriteHistoryUnavailableError` 定义在 `agent/tools/author_edit_guard.py`，是 `AuthorContentProtectionError` 的子类，`error_type = "pre_write_history_unavailable"`，文案「原文没能先存进历史版本，这次改动没有写入，原文保持不变；可以稍后重试。」
- `edit_file` 的 MCP 层把它渲染成 `mutation_applied: false`、`edits_applied: 0` 的类型化错误；模型可在同一轮重试，不另计费。
- 整章替换：`StreamAdapter._save_chapter_replacement` 本来就把 `AuthorContentProtectionError` 当作「拒绝」，卡片 `body_saved=false`、正文保持原样，计费看不到写入。
- `FileCRUD.update_file`（流式整篇新建写入等）的失败策略不变：备份失败不阻断。它的覆盖还受作者正文保护拦截。

## Alternatives considered

- **维持「备份失败、正文照存」**。最强的理由：可用性。版本服务短暂抖动时 AI 修改照样成功，作者不用重试，而且这种失败本来就罕见。没选的原因：失败的代价不对称。拒绝一次只是让模型重试一次；照存则可能让作者的一段原文永久消失，而工具还报告成功。
- **让 `stage_pre_ai_write_backup` 返回三态（已备份 / 无需备份 / 失败），调用方按失败中止**。最强的理由：直接表达「备份失败」，不依赖锚点的推论。没选的原因：锚点读取失败同样会让原稿无处恢复，三态覆盖不到；用「非空原稿没有锚点」一条规则同时覆盖两种失败，也不用改 `update_file` 的调用方。

## Consequences

- 收益：AI 局部修改或整章替换提交时，非空原稿一定在历史里，并且一定有撤销锚点。
- 代价：版本表读写出错时，这两种写入会失败，而不是照存。`test_edit_undo.py` 中「两次历史读取都失败仍提交」的旧用例改为断言拒绝；PostgreSQL 的 provenance 用例改成只让第一次读取失败（仍验证 savepoint 不污染外层事务），另加一条备份失败时拒绝并释放行锁的用例。
- 前端暂按通用失败提示显示这个 `error_type`。
