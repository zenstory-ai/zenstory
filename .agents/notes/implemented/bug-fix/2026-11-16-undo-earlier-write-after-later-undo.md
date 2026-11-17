# 后一次 AI 修改撤销后，前一次修改仍可撤销

Status: implemented

关联：[撤销 AI 修改后修改卡显示已撤销](2026-10-24-undone-edit-card-state.md)、[AI 写入前备份未入史的正文](2026-10-09-ai-write-backs-up-unversioned-text.md)、[单循环 agent](../architecture/2026-11-16-single-loop-agent.md)。

## Problem

同一轮里先用 write_chapter 整章替换一章，再对同一文件 edit_file。作者先撤销后面那张 edit_file 卡：回滚到替换后的版本，正文与替换结果逐字相同，但这是一次新的写入，`updated_at` 前进了。之后再撤销整章替换那张卡，请求带的是替换刚提交时的令牌 `expected_after_updated_at`，与当前令牌永远对不上，服务端一律返回 409 stale_write。按钮一直可点、点了一直失败。

## Decision

- `FileEditor._commit_ai_content`（edit_file 与 write_chapter 整章替换共用）返回的撤销锚点多一个 `expected_after_content_sha256`：这次提交写进 `file.content` 的正文的 UTF-8 SHA-256（`services.features.file_version_service.content_sha256`）。
- `RollbackRequest` 多一个可选字段 `expected_after_content_sha256`（64 位小写十六进制，格式不对 422）。`FileVersionService.rollback_to_version` 在文件写锁内核对：令牌一致照旧通过；令牌不一致时，只有请求带了摘要、且当前正文的摘要与它逐字相同才放行，否则照旧 409 stale_write。这是确定性的内容相等检查，不做任何语义判断。
- 没带 `expected_updated_at` 的历史恢复不看摘要；不带摘要的旧客户端行为不变。卡片标记（`record_edit_card_undone`）不变：客户端仍带这张卡自己的令牌，与落库锚点一致。
- 前端把卡片锚点里的摘要随撤销请求发出；较早的卡在同一文件有更晚、未撤销的 AI 修改卡时先禁用撤销，提示先撤销后面的修改（前端分支另行提交）。

## Alternatives considered

- **只比令牌，让作者去历史面板恢复。** 最强理由：规则最简单，不多一个字段。被否：撤销后一张卡之后正文已经逐字回到前一次修改的结果，再拒绝前一张卡没有任何保护价值，只会让按钮永远失败。
- **锚点记修改后的版本号，服务端用 `get_content_at_version` 比较。** 最强理由：不在工具结果里多 64 个字符。被否：AI 版本快照是可选的（失败只记日志），也可能被清理；摘要自带、不依赖历史是否完整。
- **令牌不一致时直接按「正文没变就放行」比较，不需要客户端给摘要。** 最强理由：客户端不用改。被否：服务端不知道那次修改写出了什么，只能和历史比，回到上一条的问题。

## Consequences

- 收益：先撤销后一次、再撤销前一次能逐级退回原稿；正文只要和那次修改的结果有一个字不同（作者改过、后一次修改还在），仍然 409，不覆盖新稿。
- 代价：撤销锚点（随 edit_file / write_chapter 的工具结果给到模型）每次多约 90 个字符。客户端提交的摘要只是前置条件：调用方本就可以不带令牌直接恢复自己的文件，摘要不增加权限。

## Verification

`apps/server/tests/test_services/test_edit_undo.py`：替换后编辑、撤销编辑、再带摘要撤销替换返回 200、正文回到原稿、卡片标成已撤销；后一次修改仍在、撤销后作者又改过、不带摘要三种情况仍 409 且正文、令牌、版本数不变；摘要格式不对 422。既有锚点断言补上新字段。
