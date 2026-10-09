# Agent Note: 读取计数按 agent run 清零、预算软着陆、无进展用专用错误码

Status: implemented

本 note 推翻了 [`2026-10-08-agent-no-progress-guard-and-soft-cap.md`](2026-10-08-agent-no-progress-guard-and-soft-cap.md) 里的两条决定：

- 守卫「按一次请求记账……交接不清零」；
- 「为无进展新增错误码 `ERR_AGENT_NO_PROGRESS`」被否、沿用 `ERR_AGENT_TOOL_FAILURE_LIMIT`。

那篇 note 的其余决定（拦截阈值、写入清零、无进展判定与退款、max_turns 软着陆、审查轮数上限、「继续」直达）不变。

## Problem

10-08 的读取守卫按整个请求计数，但 runner 回放历史时丢掉工具结果（`runner.normalize_messages_for_openai_agents`），上一个 agent 读到的正文不在下一个 agent 的上下文里。质量模式下的多 agent 流程因此会被误判：

- full 流程 planner → hook_designer → writer → quality_reviewer 各自读一次卷纲、上一章、主角卡。第 2 个 agent 收到「请直接使用上文内容」，可它的上下文里并没有这份内容；第 3 个收到「下一次重复读取将被拒绝」；第 4 个（审稿人）第一次读就被拦下，拦满 3 次触发无进展停止。返工丢失，第 5 章已经写入，所以照常扣费。
- 交接摘要一边说「每个文件读一次即可」，一边由同一个守卫拦下这一次读取，两条规则互相矛盾。
- `edit_file` 返回 `status=partial`（部分 edit 生效）时守卫不算写入：之后再读仍提示「之后未被修改」，这是错的；无进展停止时还会按「没有写入」标 `refundable=true`，而计费把 partial 当作写入、照常扣费，两边口径不一致。
- 系统提示和工作集每个请求只组装一次，本请求内被修改过的文件，那里的 [全文] 仍是改之前的版本，`prompts/base.py` 却说标注[全文]的就是最新内容。
- 软着陆只看单个 run 的 `max_turns`。前面的 agent 用掉大半请求级预算（`AGENT_RUN_MAX_MODEL_CALLS`=120）后，后面的 agent 撞到预算是硬停：ERROR，没有阶段总结。
- 软着陆提醒要求「立即停止调用任何工具」，没有提到刚 `create_file` 还没写 `<file>` 正文的文件，模型照做就会留下空章节。
- 无进展停止沿用 `ERR_AGENT_TOOL_FAILURE_LIMIT`，前端按错误码显示「AI 多次操作失败……已完成的内容已保存」。这次停止既不是工具失败，也不一定保存了什么，而且回复「继续」就能接着做。
- 被拦下的读取结果只有写给模型的 `error`（带 id 和「不要再读取它」之类的指令），工具卡片能拿来给作者看的只有这段文字。

## Decision

**读取计数按 agent run 隔离（`agent/openai_agents/repeat_read_guard.RepeatReadGuard`）**

- 新增 `begin_agent_run()`：清空 `_read_counts`；`files_read`、`files_written`、`write_succeeded`、`titles` 和请求级的 `blocked_reads` / `duplicate_reads` / `trip` 保留。
- `runner.run_openai_agents_streaming_agent` 取到守卫（`state["repeat_read_guard"]`，没有时自建）后立即调用一次。writing_graph 仍为整个请求建一个守卫，每次进入 runner（交接、计划交接、自动质检、steering 追加轮、纠偏轮）都会清零。
- 同一 run 内的阈值不变：第 2 次读附提示，第 3 次附警告，第 4 次起拦下。被拦下的次数仍按请求累计，跨 run 累计满 `MAX_BLOCKED_READS_PER_REQUEST`（3）次就判定无进展。
- 提示和拦截文案只说「本次运行中已读取过」，内容在「本次运行中之前的工具结果」里，不再说跨 agent 的「上文」。

**部分成功的编辑算写入（`_observe_direct_write`）**

- `status=="success"`（`data.all_failed` 除外），或 `status=="partial"` 且 `mutation_applied is True`，都清零该文件的读取计数，并记入 `files_written` / `write_succeeded`。`partial` 而 `mutation_applied` 不为 true 时不算写入。

**[全文] 过期提示**

- `handoff_summary()` 对 `files_written` 里的每个文件追加：`系统上下文/工作集中《X》(id=…)的[全文]是本请求修改前的版本，已过期；需要时用 query_files(id=…) 读取最新内容。`
- 摘要的收尾句改为「这些文件的正文不在你的上下文里：只读取完成你的任务确实需要的文件，在你这次运行里每个文件读一次即可，不要重复读取同一文件。」
- `prompts/base.py` 读取预算一节的 [全文] 规则加限定「（截至本请求开始；本请求内被修改过的文件除外）」。`context/assembler.py` 的 `FULL_TEXT_CONTEXT_NOTICE` 由上下文组装那边的改动负责，不在本 note 范围内。

**软着陆也看请求级预算（`runner._ModelCallCountingFilter`）**

- 剩余调用数取 `min(max_turns - calls, run_meter.max_model_calls - run_meter.model_calls)`，小于 `SOFT_LANDING_TURNS`（3）时追加收尾提醒。`max_turns` 为空时只看预算。
- 过滤器记下最近一次提醒是哪个上限触发的（`landing_limit`：`max_turns` / `model_call_budget`）。
- 提醒由预算触发、没有撞上 `max_turns`，且本次 run 没有以交接或澄清结束（模型照提醒写了总结）时：照常发 `ITERATION_EXHAUSTED(layer="tool_call")`，`iterations_used` / `max_iterations` 换成请求级的 `model_calls` / `max_model_calls`，`reason` 写明是这一轮的模型调用次数快到上限；`MESSAGE_END.stop_reason` 为 `model_call_budget_exhausted`。工作流、计费（`tool_call` 层耗尽，没有写入就退还）和前端「继续」按钮都沿用轮数耗尽的路径。
- 模型无视提醒、继续调用工具直到预算用尽：仍走原来的硬停（`MESSAGE_END(model_call_budget_exhausted)` + `ERR_AGENT_MODEL_CALL_LIMIT`）。
- `SOFT_LANDING_PROMPT` 增加一句：如果刚用 `create_file` 建了文件但还没写 `<file>` 正文，先在本次回复里写完该文件的 `<file>` 正文，再做总结。

**无进展停止的对外信息**

- `NoProgressTrip.as_event_data` 的 `code` 改为新增的 `ErrorCode.AGENT_NO_PROGRESS`（`ERR_AGENT_NO_PROGRESS`，`core/error_codes.py` 补中英文案）。`error` 为固定文案 `NO_PROGRESS_USER_MESSAGE`：「AI 一直在翻看同样的资料，迟迟没动笔，这一轮先停下了。可以直接告诉它改哪一章、改什么，或者回复「继续」让它接着写。」
- `reason="no_progress"`、`refundable` = 本请求没有任何写入成功、`retryable=false` 都不变，计费照旧按 `reason` 识别失控停止。事件里另带 `last_file`。
- `NoProgressTrip.user_message()` 不再接收 `wrote_files`，也不再在文案里写是否扣费；退款与否由 `refundable` 和计费帧表达。
- `StreamAdapter` 对非熔断错误码用 `ERROR_MESSAGES["zh"]` 的固定文案，所以 SSE error 帧的 `message` 就是上面这句。

**被拦下的读取带 `user_message`**

- 直接 `query_files` 被拦下，以及 `parallel_execute` 整批被拦下时，工具结果 JSON 在 `error`（写给模型，保留文件《标题》(id) 和指令）之外加 `user_message`：「《X》刚才已经读过，AI 直接用已读内容继续。」多个文件用顿号连接；拿不到标题时写「这个文件刚才已经读过……」。`parallel_execute` 只拦下部分子任务时（整体 status=success）不加。

## Alternatives considered

- **保持请求级计数，只把提示文案改成不说「上文」**。最强理由：改动最小，还能挡住跨 agent 来回读同一批文件的循环（例如 writer ↔ 审稿人每轮都把同一章读 3 遍）。被否：第 4 个 agent 的第一次读仍会被拦下，full 流程里的审稿人第一轮就读不到要审的稿子，拦满 3 次还会中止并丢掉返工。跨 agent 的循环已经有别的上限：协作轮数 12、审查轮数 2、请求级预算 120 次调用，以及按请求累计的 `blocked_reads`。
- **像 DeepSeek Harness 那样只统计连续的相同调用，只提醒不拦截**。最强理由：不会误伤任何正常读取，上下文变化（插话、交接）自然清零。被否：10-08 的事故里模型是穿插着读几份文件、反复绕圈，连续计数抓不到；只提醒不拦截，模型拿着同样的内容会继续同样的循环，这一点上一篇 note 已经验证过。
- **预算快用完时维持硬停 ERROR**。最强理由：预算是成本的硬边界，ERROR 一定终止整条工作流，不会出现「提醒了还多跑」。被否：收尾提醒只在剩余调用里生效，不额外花调用；硬停什么总结也不留下，用户不知道做到了哪一步。模型无视提醒时仍然硬停，硬边界没有放松。
- **继续沿用 `ERR_AGENT_TOOL_FAILURE_LIMIT`（上一篇 note 的选择）**。最强理由：不用新增错误码，前后端不用同步改。被否：前端按错误码显示的「AI 多次操作失败……已完成的内容已保存」对无进展停止是错的：没有工具失败，也不一定保存了东西，而且没告诉用户可以「继续」。
- **只改 `error` 文案，让它同时适合模型和作者**。最强理由：一个字段，工具卡片不用改。被否：模型需要精确的文件 id、读了几次、还剩几次就会被终止这些信息来纠偏；作者不该看到 id 和「不要再读取它」这种指令。

## Consequences

- 收益：多 agent 流程里每个 agent 都能把需要的参考文件读一次，不会收到「已读过」的假提示，也不会被误判为无进展。同一 run 内的重复读取照旧被拦下。部分成功的编辑在守卫和计费里口径一致。下一个 agent 知道哪些 [全文] 已过期。前面的 agent 用掉大半预算时，后面的 agent 会先写阶段总结再停，用户看到的是「继续」入口，而不是错误。无进展停止显示准确的说明，并提示可以「继续」。工具卡片有了可以直接展示给作者的一句话。
- 代价：同一个文件现在每个 agent run 都能读 3 次。writer ↔ 审稿人来回时，同一章可能被读 2 轮 × 2 个 agent × 3 次，只受协作轮数、审查轮数和请求预算限制，成本上限比按请求计数时高。
- 代价：无进展的 SSE 文案不再写「本轮没有修改任何文件，不扣除本次对话额度」，作者要靠计费帧上的退还标记才能看到这一点（由前端和计费的并行改动补上）。
- 代价：前端 `errors.json` 必须有 `ERR_AGENT_NO_PROGRESS`，否则前端按错误码取文案时会落到通用错误文案（并行的前端改动负责；Vercel 先于 Railway 上线，只要同一次合并带上就没有窗口期）。
- 代价：预算软着陆落库的 `stop_reason` 是 `model_call_budget_exhausted`，要等路由把它加进 `RESUMABLE_STOP_REASONS`（并行的路由改动负责），之后单说「继续」才会直达上一个 agent。在那之前，这种情况下的「继续」仍走 LLM 路由。
- 代价：进入预算收尾区间后，即使模型恰好在最后几次调用里完成了任务，也会显示耗尽卡片并停止送审，和 max_turns 软着陆的取舍一样。

## Verification

`cd apps/server && <venv>/bin/python -m pytest tests/test_agent/test_repeat_read_guard.py -q --no-cov -p no:cacheprovider -n 0` 覆盖以下情况：

- 守卫本身：4 个 agent run 各读同样 3 个文件、中间调用 `begin_agent_run()`，没有提示、拦截或无进展；同一 run 内第 4 次读取仍被拦下，被拦下的次数跨 run 累计到 3 次就停止；`partial` + `mutation_applied=true` 清零计数并记为写入，`mutation_applied=false` 不算；交接摘要对改过的文件标「已过期」并给出 `query_files(id=…)`；直接读取和并行整批被拦下时的 `user_message`。
- 软着陆过滤器：请求预算只剩 3 次时，本次 run 的第 1 次调用就追加提醒，`landing_limit=model_call_budget`。
- 用本地 OpenAI 兼容端点驱动真实 SDK run-loop：
  - 预算软着陆以 `ITERATION_EXHAUSTED` + `stop_reason=model_call_budget_exhausted` 结束，不发 ERROR，提醒里带 `<file>`；
  - 上一个 agent 已把 f1 读满 3 次，新 run 读 f1 仍会真正执行且没有提示（去掉 runner 里的 `begin_agent_run()` 调用后这条测试失败）；
  - 无进展停止的 ERROR 带 `ERR_AGENT_NO_PROGRESS` 和新文案。
- 流式适配：`StreamAdapter` 把无进展事件转成 SSE error 帧，`code`、`message`、`reason`、`refundable` 都正确。

另外跑了 `tests/test_agent`、`tests/test_core` 全量，以及 `tests/test_api/test_agent.py`、`test_agent_stream_hardening.py`、`test_round3_agent_api.py`、`test_round3_converge.py`。
