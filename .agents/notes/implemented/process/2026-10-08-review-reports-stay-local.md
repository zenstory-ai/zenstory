# Agent Note: 工作期审查报告不进 Git

Status: implemented

## Problem

`docs/reviews/2026-10-06-modules/` 提交了 157 份模块审查过程报告（plan、worker、integration 等中间产物）。它们是一次审查任务的工作记录，内容很快过时，又和 `.agents/notes/` 的决策记录重复；owner 认为不应该进 Git。

## Decision

`docs/reviews/` 从版本库移除（`git rm --cached`，本地文件保留）并加入 `.gitignore`。审查过程报告只留在本地；需要长期保留的结论按决策记录规范写进 `.agents/notes/`。`docs/plans/2026-10-06-functional-module-audit.md` 中对报告位置的描述同步改为"本地、git-ignored"。

## Alternatives considered

- **保留在 Git，但移到 `docs/archive/`**。最强理由：审查证据可追溯，换机器也能看到。被否：这些报告的结论已经落在代码、测试和 `.agents/notes/` 里，过程文件只会增加仓库噪音和误读风险；需要追溯时 Git 历史里仍有这些文件。

## Consequences

- 收益：仓库只保留长期有效的文档与决策。
- 代价：其他机器或 agent 拿不到这批过程报告，只能从 Git 历史（提交 a4dd64b 及之前）恢复。
