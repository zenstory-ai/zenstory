# Agent Note: 快速模式不送审、返工稿不再自动送审，以及交接与收尾的几处细节

Status: implemented

## Problem

- 快速模式只关掉了 LLM 路由和自动质检门。WRITER_PROMPT 是常量，仍要求「创作稿件超过阈值就 handoff_to_agent quality_reviewer」，图对显式送审只受 `MAX_REVIEW_ROUNDS` 约束。快速模式写 800 字的打斗，照样跑审稿人甚至返工，用户选的「更快出结果」没有兑现。
- 自动质检门不看上一个 agent。审稿人交回 writer 返工后，返工稿只要够长、动过写工具，就再被送审一次，最后还附一句「请回复「按审稿意见修改」」，用户多花一条消息。WRITER_PROMPT 写的是「返工回来……完成后直接结束，不再送审」，图和提示词说法相反。
- 工具调用轮数耗尽（`tool_call_exhausted`）时空文件纠偏分支被跳过，`create_file` 建了、还没写 `<file>` 正文的文件留在文件树里，后续上下文会把空章节当成已写过。
- 交接包里的 `todo` / `evidence` 只进了 HANDOFF 事件，没有渲染进下一个 agent 的交接信息。审稿人通过 `handoff_to_agent` 列出的具体修改项，writer 看不到。
- 英文界面的语言区段要求「respond ENTIRELY in English」，同时约束了 `<file>` 正文：中文稿件在英文界面下会被续写成英文。
- 读取守卫拦下 parallel_execute 里的重复读取后，实际执行的子任务比落库的 arguments 少。会话历史的面包屑按下标配对，把后面子任务的结果（标题、失败状态）安到前一个子任务上：失败的编辑被记成成功，读过的文件标错标题。
- `update_project` 对 summary / writing_style / notes 是整字段覆盖，提示词示例却是 `update_project(notes='女主第5章才出场')`，只有一句模糊的「考虑追加 vs 覆盖」。按示例调用会把原有的禁忌（如「反派身份第20章前不得揭露」）抹掉，没有版本历史。

## Decision

- **快速模式**：`nodes.run_streaming_agent` 在 `generation_mode == "fast"` 时给非审稿人 agent 的系统提示末尾追加 `## 本轮生成模式`：`FAST_MODE_DIRECTIVE`（写完直接结束，不要交接给 quality_reviewer）。`writing_graph` 在快速模式下丢弃 writer → quality_reviewer 的显式交接并记日志。判断依据是 `generation_mode`，不是 `enable_graph_auto_review`（环境变量关掉自动质检的普通模式不受影响）。
- **返工稿不再自动送审**：`writing_graph` 新增 `rework_from_reviewer`，在交接决策落地时置为「上一个 agent 是 quality_reviewer 且还有下一个 agent」。空文件纠偏轮和 steering 追加轮不切换 agent，标志保持。自动质检门加 `not rework_from_reviewer`。没用 `previous_agent`，因为纠偏轮、追加轮会把它改成当前 agent。writer 无视提示词显式送审时，仍受 `MAX_REVIEW_ROUNDS` 约束。
- **轮数耗尽时回滚空文件**：`tool_call_exhausted` 时若 `ToolContext.has_pending_empty_file()`，`_roll_back_empty_files_after_exhaustion` 逐个落库核验（`_probe_pending_file_body`）。已写入或已删除的清掉标记，确认为空的走 `_rollback_unfinished_empty_files` 软删除，不安排补写轮。然后在 assistant 正文末尾追加一段说明：已撤销的是「《X》还没写入正文，已撤销这个空文件。回复「继续」会重新写。」，回滚不了的是「……也没能自动撤销。回复「继续」补完，或手动删除。」
- **交接包渲染**：交接决策落地时记下 `incoming_handoff_packet`（纠偏轮、追加轮清空）。下一个 agent 的交接信息在 context 之后追加 `[待办]` / `[依据]` 列表（`_format_handoff_packet_items`）。审稿人与非审稿人两种格式都追加。图自己写进 evidence 的内部标记（`workflow_plan=standard`、`content_length=812`，匹配 `^[a-z_]+=\S*$`）不渲染。
- **英文界面语言规则**：`MessageManager._build_language_section` 只约束聊天回复。文件内容（`<file>` 块、edit_file 文本、create_file 内容）保持稿件原有语言；新文件用作者要求的语言或已有稿件的语言。
- **parallel 面包屑配对**：`SessionLoader._extract_parallel_file_actions` 用游标顺序配对。被剔除的名额 = arguments 子任务数 − 结果子任务数。还有名额、且结果里找不到该文件 id 的 query_files 子任务视为被守卫拦下，不记面包屑，之前的读取已经记过。其余子任务只在结果 `type` 一致时配对，配不上的按「没有结果」处理。
- **update_project 整字段覆盖**：`prompts/base.py` 的 update_project 段新增「整字段覆盖」说明。要求先看「项目状态」里的现有内容，把原有条目和新条目合并后整段传入；现有内容显示不全（被截断）时不改写该字段，在回复里告诉作者。notes 示例改成合并写法（`'反派身份第20章前不得揭露；女主第5章才出场'`）。`tool_schemas.UPDATE_PROJECT_TOOL` 的工具描述和 summary / writing_style / notes 字段描述同样写明整体替换、先合并。

## Alternatives considered

- **快速模式只靠提示词（nodes 指令），图不拦。** 最强理由：显式交接是 agent 的明确决定，图一律放行是现有原则（`2026-10-03-agent-handoff-respects-user-scope.md`）。没采用：提示词遵从是概率性的，而快速模式是用户在界面上做的明确选择，写完再审一轮直接违背它；图里拦截只针对 writer → quality_reviewer 这一条，交给 planner 等其他交接不受影响。
- **自动质检门直接比较 `previous_agent != "quality_reviewer"`（评审报告的原写法）。** 最强理由：一行改动。没采用：返工 writer 留下空文件时，纠偏分支会把 `previous_agent` 改成 writer，纠偏轮结束后返工稿又被送审（`test_rework_stays_unreviewed_across_empty_file_correction_round` 覆盖）。
- **自动质检门只统计 `<file>` 正文和写工具文本的长度（评审报告 F9 第 3 项）。** 最强理由：小改动附带长篇说明时也不会被误送审。本批没做：需要改 writer 文本的统计口径，涉及纠偏轮的接续逻辑；返工稿的重复送审已经由上面的标志解决。
- **轮数耗尽时也安排一轮补写。** 最强理由：用户不用说「继续」就能拿到正文。没采用：轮数耗尽本来就是要停下，再跑一轮会突破刚触发的上限，计费和成本口径都要重算；回滚加文字说明后，「继续」会直达上一个 agent 重新写。
- **update_project 服务端加 `notes_append` 参数。** 最强理由：从根上杜绝覆盖。本批没做：这是工具契约变更，需要单独设计（追加长度上限、去重、旧模型调用的兼容），评审报告把它列在下一批。

## Consequences

- 收益：快速模式下 writer 写完就结束；返工稿不会被二次送审，用户不再收到「请回复按审稿意见修改」的多余提示；轮数耗尽不再在文件树里留空章节；审稿人列的具体修改项能到 writer 手里；英文界面不会把中文稿件续写成英文；守卫拦截后的面包屑不再张冠李戴；按新示例调用 update_project 不会抹掉原有约定。
- 代价：快速模式完全不审稿，长篇正文的质量把关只能靠用户自己或切到高质量模式；writer 在快速模式下仍可能先调用一次 handoff_to_agent（被图丢弃），这次调用的 token 白花。
- 代价：返工稿不再送审，返工引入的新问题本轮不会被发现；writer 显式送审时仍会进入第 2 轮审查。
- 代价：轮数耗尽时被撤销的空文件需要「继续」后重新创建；这段说明以普通正文追加，排在轮数耗尽卡片之后。
- 代价：交接信息多了 `[待办]` / `[依据]` 两段，审稿人的 todo 与 context 内容重复时会多占一些输入 token。
- 代价：parallel 面包屑在「被剔除名额」内识别不到 id 的读取（例如读取失败、结果被截断到没有 id）会被当成拦下而不记；update_project 的合并依赖模型遵从提示词，服务端仍是整字段覆盖。

## Verification

`cd apps/server && venv/bin/python -m pytest tests/test_agent/test_agent_routing_continuity.py -q --no-cov -n 0`：快速模式丢弃 writer 送审、非审稿人系统提示带快速模式指令；高质量模式首稿自动送审、返工稿不送审（含返工后经过空文件纠偏轮）；审稿人交接包的 todo / evidence 出现在 writer 的交接信息里、内部标记不渲染；轮数耗尽时回滚确认为空的文件并追加说明、已写入的不回滚；守卫改写 3→2 的 parallel 批次面包屑按类型配对。`tests/test_agent/test_repeat_read_guard.py::test_review_rounds_are_capped_and_reviewer_notes_are_surfaced` 改为 writer 显式送审后才有第 2 轮审查。update_project 与语言区段是提示词文案改动，没有加镜像文案的测试。
