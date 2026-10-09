# Agent Note: AI 起的项目名可以再改；作者明确要求时 AI 可以改项目名

Status: implemented

相关：作品名自动命名（#35）的原规则见 `feature/2026-10-09-agent-writes-normalize-cjk-quotes.md` 的「作品名自动命名」一节。本 note 在它的基础上补两条路径，「作者起的名字 AI 不自动改」这条规则不变。

## Problem

2026-10-09 第二轮审计 N4：#35 的修法只认「项目还叫模板默认名」。AI 第一次定名以后，项目名就不在 `DEFAULT_PROJECT_NAMES` 里了，`_apply_ai_title` 把它当成作者起的名字锁死：

- 之后 AI 再给作品定名（比如作者改了方向，大纲换了书名），项目名不会跟着变。
- 作者找不到改名入口（项目切换器里的铅笔平时 `opacity-0`，悬停才显示），于是让 AI 改名。`update_project` 没有「作者明确要求」的路径，结果是 `title_skipped=author_named`，AI 回复「系统判定这个名字是你自己起的……去项目设置里改」：入口是模型编的，这条消息还计入了当天额度。

## Decision

**记下名字是谁起的**（`agent/tools/file_ops/project.py`）

- 项目表没有元数据字段，本次不改表结构。每次 `update_project` 改了项目名，`_apply_ai_title` 在同一次提交里往 `agent_artifact_ledger` 写一行：`action="project_title"`、`tool_name="update_project"`、`artifact_ref="project:{id}"`、`payload={"project_name": 新名字, "source": "ai_auto" | "author_requested"}`。`project_title` 不在 `STANDARD_HANDOFF_ARTIFACT_ACTIONS` 里，不会进交接包的 artifact_refs。
- `_is_ai_auto_named` 取这个项目最近一条 `project_title` 记录：`source == "ai_auto"` 且 `project_name` 等于项目现在的名字，才算 AI 起的名。作者之后在界面里改过名（名字对不上），或者那次改名是作者要求的（`author_requested`），都按作者起的名字处理。读记录出错时也按作者起的名字处理（宁可不改）。
- 改名条件变为：项目名属于 `DEFAULT_PROJECT_NAMES`，或是 AI 自动命名留下的名字，或本次带了 `author_requested=true`。作品名的规范化（去书名号、1–30 字）对三种情况都一样，`author_requested` 不跳过校验。

**作者明确要求改名**

- `update_project` 新增布尔参数 `author_requested`。mcp 层只认 `true` 和字符串 `"true"` / `"1"` / `"yes"`，只在同时传了 `title` 时转给 `ProjectOperations`。
- `tool_schemas.UPDATE_PROJECT_TOOL` 的工具描述和 `author_requested` 字段描述写明：只在作者本人在对话里明确要求改项目名时传 true；AI 自己想换名、或只是大纲里写了书名，不传。`prompts/base.py` 的 update_project 注意事项加一行同样的说明。

**拒绝改名时给出真实入口**

- `title_skipped == "author_named"` 时结果里多一个 `title_note`（mcp 层提到结果顶层）：「项目名「X」是作者自己起的，这次没改。作者在对话里明确要求改项目名时，带上 author_requested=true 再调用一次；作者想自己改的话，告诉他：点顶部的项目名打开项目切换器，再点项目名旁的铅笔（「编辑项目名称」）。」入口和 `ProjectSwitcher` 里铅笔按钮的 `editor:projectSwitcher.editName` 一致。`title` 字段描述要求模型把 `title_note` 里的入口原样告诉作者。

## Alternatives considered

- **在 Project 表加一列 `name_source`。** 最强的理由：一眼就能看出名字来源，不用查另一张表。没选：本批不改表结构（生产迁移要手动跑，见 AGENTS.md 与项目记忆），台账表已经按 `project_id` 建了索引，查最近一条的成本很低。
- **AI 每次定名都覆盖，不管名字是谁起的。** 最强的理由：最简单，AI 最新的书名总能生效。没选：作者自己起的名字会被 AI 改掉，#35 当初就是为此只改默认名。
- **作者要求改名时让模型直接回复手动入口，不加 `author_requested`。** 最强的理由：AI 永远动不了作者起的名字。没选：作者已经明确说了「把项目名改成 X」，再让他自己去找入口是白扣一条消息；而且入口目前在触屏上看不到（前端另一个包处理铅笔常显）。
- **把「名字是 AI 起的」记在 `project.notes` 里。** 最强的理由：不需要新的记录行。没选：notes 是作者也能看到、模型会整段改写的备注，会被覆盖，也会让作者看到内部标记。

## Consequences

- 收益：
  - AI 起的名字之后还能跟着作品名变；作者在界面里改过的名字照样保留。
  - 作者在对话里要求改名，AI 一次就能改好；改不了时模型拿到的是界面上真实存在的入口。
- 代价：
  - 本改动上线前 AI 已经起好的名字没有台账记录，仍按作者起的名字处理，AI 自动定名不会再改它们；作者明确要求时可以用 `author_requested` 改。
  - 是否「作者明确要求」由模型判断，模型误传 `author_requested=true` 会改掉作者起的名字；schema 和提示词都限定了只在作者原话要求时传。
  - 作者在界面里把名字改回和 AI 起的完全一样的名字时，会被当成 AI 起的名字。
  - 每次 AI 定名多一次台账查询（只取一行）和一行台账写入。

## Verification

`cd apps/server && venv/bin/python -m pytest tests/test_agent/test_update_project_title.py -q --no-cov -n 0` 通过，覆盖：AI 起的名字被下一次 AI 定名替换（旧实现返回 `author_named`）；作者在界面里改过名后 AI 不能再改；拒绝时 `title_note` 写明项目切换器、铅笔和「编辑项目名称」，不出现「项目设置」；`author_requested` 为 `true` / `"true"` 时改掉作者起的名字；作者要求改成的名字不会被之后的 AI 定名覆盖；`author_requested` 不跳过长度校验；schema 写明了新参数。`pytest tests/test_agent -n 8` 全部通过。本包改了一行提示词，没有做真实模型验证。
