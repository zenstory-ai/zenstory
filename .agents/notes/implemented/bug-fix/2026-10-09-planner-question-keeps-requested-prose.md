# Agent Note: 作者已经要正文时，规划师结尾的提问不截停计划交接

Status: implemented

修改：`bug-fix/2026-10-03-agent-handoff-respects-user-scope.md` 中「agent 以向用户提问收尾时清空计划序列」一条。那条规则仍然成立，但对「规划类角色 + 作者本轮已经明确要正文」改为不清空，属于部分反转，单独记这一条。

## Problem

2026-10-09 新用户审计 P2-21（代码推断，浏览器 0/6 未复现，其中 1/4 次规划师没有显式交接、靠计划交接兜底）：

- `PLANNER_PROMPT` 要求「项目里还没有任何正文、这一轮交付的是故事框架或大纲时，最后单独一行只问一句：要我按这份大纲开始写第一章吗？」，不看作者有没有要正文。
- 新项目首条「帮我构思…大纲，然后直接写第一章」被路由成 planner + standard、`write_content=true`。规划师照提示词收尾问一句，`ends_with_question_to_user` 判定为在等作者回答，图清空计划序列里的 writer。
- 作者只拿到大纲和一句提问，要再发一条「写吧」，多花一条额度（免费一天 10 条）。`_build_scope_directive` 也只在 `write_content=false` 时给规划师约束，作者要正文时没有任何提示。

## Decision

- `agent/graph/author_scope.planning_question_keeps_writer`：当前 agent 是 planner / hook_designer、路由的 `write_content is True`、计划序列里还有 writer 时，以提问收尾不清空计划序列。`write_content` 为 false 或 None（旧格式路由）时照旧停下等作者。显式 handoff、自动质检门不受影响。
- 这种情况下计划交接给 writer 时，交接说明追加 `PLANNED_WRITER_AFTER_QUESTION_NOTE`：规划师问过，但作者已经要正文，不要再问，按推荐（没有推荐就按第一个）方案写，回复里一句话说明按哪个方案写的。
- `_build_scope_directive`：`write_content=true` 且初始 agent 是规划类角色时，系统提示加「规划 / 设计完成后直接交接 writer 写正文，结尾不要问作者要不要开始写」。
- `PLANNER_PROMPT`：「要我按这份大纲开始写第一章吗？」只在「作者这一轮只要了故事框架或大纲（没要求写正文）」时问；作者同时要正文时直接交接，不在结尾问。

## Alternatives considered

- **只认「要我开始写…吗」这一类提议式问句，其余问题（「你更倾向哪个方向？」）仍然停下。** 最强理由：真正的方向分歧还能留给作者选，writer 不会按没确认的方向写。被否：问句措辞千变万化，识别「提议式」又是一套启发式；作者在同一句话里已经要了第一章，多数情况下更想先看到正文再调整，而不是再花一条额度回答。writer 按推荐方案写并说明，作者想换一句话就能改。
- **只改提示词，不改图。** 最强理由：规划师不问，图就不会截停。被否：提示词遵从是概率性的，审计里 1/4 次规划师没有显式交接；计划交接是兜底，兜底本身不能被一句客套话关掉。
- **对所有角色（包括 writer）都不因提问截停。** 最强理由：规则统一。被否：writer 之后的计划序列里通常没有 agent；审稿人提问时往往确实在等作者决定（改不改已确认的事实），不在本次范围。

## Consequences

- 收益：首条「大纲 + 直接写第一章」稳定拿到正文，不再多花一条额度；只要框架时规划师仍会问一句「要我开始写吗」。
- 代价：作者要正文、规划师又真心提了方向问题（多个方案待选）时，writer 会按推荐 / 第一个方案直接写，作者不满意要再说一次。`write_content` 判错（把只要大纲判成要正文）时，规划师的提问也拦不住 writer，与 2026-10-03 note 里「路由判错」的代价相同。
- `test_writing_graph.py::test_question_to_user_stops_planned_handoff` 改为不带 `write_content` 的路由（旧格式仍然停下），新行为见 `test_author_scope_graph.py`。

## Verification

`cd apps/server && venv/bin/python -m pytest tests/test_agent/test_author_scope_graph.py tests/test_agent/test_writing_graph.py -q --no-cov -n 0`：`write_content=true` 时规划师以「要我按这份大纲开始写第一章吗？」收尾仍交给 writer，writer 的交接信息带着「不要再问」的说明，规划师的系统提示带着「不要问要不要开始写」；`write_content=false` 时照旧只跑规划师。
