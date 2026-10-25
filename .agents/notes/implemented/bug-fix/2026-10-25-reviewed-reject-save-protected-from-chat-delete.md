# Agent Note: 作者审阅时拒掉 AI 修改后存下的版本，聊天不能删除或整份覆盖

## Problem

2026-10-12 的 [作者改动以事实展示](../architecture/2026-10-12-author-edits-shown-not-guessed.md) 保留了删除守卫的旧口径：`latest_text_is_authors` 只要最新版本是 `change_type=ai_edit` 就算 AI 写的，不受保护（测试 `test_protection_of_deletes_keeps_the_old_reviewed_save_rule` 锁定）。

但编辑器的审阅保存就是 `ai_edit`：AI 改完一个文件后，作者在接受 / 拒绝审阅里拒掉 AI 的改动、恢复自己的写法并存档，版本记作 `source=user, type=ai_edit, summary="AI edit (reviewed)"`（`api/files.py` 把来源钉成 user）。在作者下一次普通存档之前，应用内对话仍可以软删除这个文件，或用同名剧集的 `create_file` 整份覆盖它（2026-10-25 审计 B-BUG-013 / F-34，离线复现：守卫返回 False，删除放行）。差异计算（`author_changes.version_actor` 加上「紧跟 AI 写入」的条件）早已把这个版本算作作者的，两边口径不一致。2026-10-14 的 [权限不从展示文字推导](../architecture/2026-10-14-typed-authority-and-delivery-evidence.md) 也说明保护的是作者保存过的正文。

## Decision

只读版本元组，不看正文、不看作者说了什么：

- 最新版本 `change_source=ai`：AI 写的，不保护（不变）。
- 最新版本不是 `ai_edit`：作者写的，保护（不变）。
- 最新版本是 `ai_edit`：只有 `version_actor` 判为作者（`source=user` 且 summary 以 `AI edit (reviewed)` 开头）**并且**上一个版本（版本号减一）被 `version_actor` 判为 AI 时，才算作者的正文、受保护。其余 `ai_edit` 照旧不保护：去AI味审阅（`AI polish (reviewed)`）、外部 Agent API 的 `ai_edit`、不紧跟 AI 写入的旧版前端「AI edit (reviewed)」（那是作者接受的 AI 润色）。
- 正文与最新版本不一致时仍保护；版本查询出错仍按「保护状态未知」拒绝（`author_edit_protection_unavailable`）。

旧测试替换为三条：紧跟 AI 写入的审阅保存受保护；跟在作者保存之后的审阅保存不受保护；其他 `ai_edit` 元组不受保护。另加一条经由真实 `update_file` 产生审阅版本、再在聊天里删除被拒的回归。这是 2026-10-12 note 中「删除守卫仍按 change_type=ai_edit 判 AI」那一条的关联修订，旧 note 不改写。

## Alternatives considered

- **维持 2026-10-12 的口径**：那是有意的决定且有测试锁定；窗口只到作者下一次普通存档；删除是软删除，可从快照还原；整份覆盖还需要同名剧集的 `create_file`。但作者在审阅里拒掉 AI 改动并按下存档，就是在说「这是我的文字」，而保护作者保存的正文正是守卫存在的理由；可还原不等于作者会发现被删了。
- **完整复用 `author_changes` 的版本链回放**：口径最一致，能处理更长的链。但删除守卫在写锁内执行，回放要读最多 40 个版本，且回放失败会把删除变成「保护状态未知」；只看前一版的元组已经覆盖审阅保存这条路径。
- **把所有 `user` 来源的 `ai_edit` 都算作者**：最简单。但会把作者接受的去AI味润色和旧前端的润色审阅也锁住，扩大了保护面。

## Consequences

- 作者审阅时拒掉 AI 修改后存下的文件，聊天删除、递归删除和同名剧集整份覆盖都会被拒绝，提示作者用文件菜单或编辑器。
- 作者在审阅里全部接受 AI 修改再存档，版本元组相同，也会受保护；这时聊天要删除需作者用文件菜单。这是只读元组、不比对正文的代价，接受它。
- 守卫每次多一次按版本号的主键查询，只在最新版本是 `ai_edit` 时发生。
- 普通 `edit_file`、作者差异计算、陈旧写入拒绝都不变。
