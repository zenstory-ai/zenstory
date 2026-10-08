# Agent Note: 路由结果跨轮保存——「继续」恢复原来的 agent 和范围，简短回答沿用上一轮路由

Status: implemented

## Problem

路由结果只活在一次请求里，下一轮全靠 LLM 路由看用户本条原话重新判断：

- 「继续」直达（`2026-10-08-agent-no-progress-guard-and-soft-cap.md`）只能从状态卡合成文本里取 `last_agent`，而合成文本只在正文为空时才拼进历史。软着陆让被截停的轮次都有正文，于是取不到，回落到 writer，`read_only` / `scope` 也丢了。用户说「检查前 60 章一致性，不要改文件」，审稿人撞上限后用户点「继续」，恢复的是拿着写工具、没有只读约束的 writer。
- 可直达的 stop_reason 只有 `max_turns_exceeded` / `no_progress`。请求级调用预算用完（`model_call_budget_exhausted`）后说「继续」会重新规划；墙钟时限截停时，取消路径补存的部分历史根本不写 stop_reason。
- 回答澄清或确认上一轮提问时（「可以」「B」「主角叫陈默」），路由只看这一句：常被分给别的 agent，或把 `write_content` 判成 false，随后以 [最高优先级] 下发「不要写正文」，用户白花一条消息。
- 「检查并改掉错别字」被规则 1 路由成 quality_reviewer + review_only，审稿人按提示词只报告不改。

## Decision

**路由落库**

- `service.py`：从 `ROUTER_DECIDED` 事件取 `initial_agent` / `workflow_plan` / `routing_metadata`，用 `routing_from_router_decided()` 规整成 `{initial_agent, workflow_type, read_only, write_content, scope, last_agent}`；之后每个 `AGENT_SELECTED` 更新 `last_agent`。正常保存与部分保存都把它写进 assistant 消息的 `message_metadata.routing`（`MessageManager.save_messages` / `_save_messages_with_session` / `_serialize_message_metadata` 新增可选参数 `assistant_routing` / `routing`）。
- 取消路径的部分保存写 stop_reason：`partial_save_stop_reason(elapsed, AGENT_RUN_WALL_CLOCK_TIMEOUT_S)` 在取消到达时判定，已用时长达到墙钟时限为 `run_deadline_exceeded`，否则 `cancelled`。`process_stream` 比 SSE 泵先开始计时，泵按时限取消时这里的时长一定不小于时限。失败路径的补偿保存写入已知的 `assistant_stop_reason`。
- `core/session_loader.py` 的历史 dict 新增 `routing`（落库路由）、`last_agent`（最后一张带 `lastAgent` 的状态卡）、`clarification_pending`（最后一张状态卡是 `workflow_stopped` + `clarification_needed`）。只供路由读，不进回放文本，token 估算也只看 content。

**「继续」直达（`router.resume_route_after_exhaustion`）**

- `RESUMABLE_STOP_REASONS` = `max_turns_exceeded` / `no_progress` / `model_call_budget_exhausted` / `run_deadline_exceeded`。用户主动停止（`cancelled`）不在其中，「继续」照常走路由。
- agent 依次取：`history["last_agent"]` → 合成文本里的 `last_agent:`（旧数据）→ `routing.last_agent` → `routing.initial_agent` → writer。审稿人对应 review_only，其余 quick。
- 返回的 `routing_metadata` 带上一轮的 `read_only` / `scope` / `write_content`，writing_graph 照常据此置 `state["read_only"]` 和范围约束。
- 「继续」集合同时认新按钮文案「继续」/「continue」和旧的长句（前端 Vercel 先上线、Railway 还没跟上时，旧按钮仍会发长句）。

**回答提问时沿用上一轮路由（`router.inherit_routing_after_clarification`）**

只在 `router_strategy == "llm"`、且「继续」直达没命中时调用，替代 LLM 路由那一步；快速模式（路由关闭）不受影响。条件全部满足才沿用：

- 上一条 assistant 消息有落库的 `routing`；
- 它以澄清卡收尾，或模型自己的话（剥掉 `[此前的工具操作]` 等合成段落后）按 `nodes.ends_with_question_to_user` 以问句收尾；
- 用户回复不超过 `CLARIFICATION_REPLY_MAX_CHARS`=40 字，且不含「先别写 / 别写正文 / 不要正文 / 不写正文 / 只要大纲 / 不用写 / 先不写」及几个英文等价说法。
- 用户回复不含只读 / 审查意图（`_READ_ONLY_INTENT_MARKERS`：不改 / 不要改 / 别改 / 不用改 / 只看 / 看看 / 审 / 检查 / 评估 / 有没有问题 / 挑毛病 / review / check / proofread / don't change 等）。澄清卡和问句收尾都适用：writer 常以「要不要接着写第二章？」收尾，用户回「只审不改，看看第一章」若沿用 writer 路由，writer 带着 create/edit/delete 工具接手且没有只读护栏，会在用户说了不改之后照样改稿；交给 LLM 路由才能判成 quality_reviewer + review_only。
- 只以问句收尾时，回复也不含转去规划的说法（`_PLANNING_INTENT_MARKERS`：大纲 / 细纲 / 规划 / 人设 / 设定 / 世界观 / outline / planning 等）。澄清卡不检查这一组：planner 问「人设按哪版来？」时，回答里带「人设」是正常作答。

沿用规则：

- 澄清卡：任务还没开始做，agent（`routing.last_agent` → `initial_agent`）、workflow、`write_content`、`read_only`、`scope` 全部沿用，计划序列仍经 `plan_workflow_agents` 按范围裁剪。
- 只以问句收尾：上一轮若 `read_only=true` 或 `write_content=false`，返回 None 交给 LLM 路由；否则沿用 agent、workflow、`write_content`，`scope` 清空。
- agent 与上一轮 `initial_agent` 相同时沿用其 workflow，否则按 agent 推断（`_infer_workflow_from_agent`）。`routing_metadata.reason` 为 `inherit_after_clarification` / `inherit_after_question`。

**提示词（F10）**

- ROUTER_PROMPT 选择规则新增第 1 条：「检查并修改 / 有问题就改 / 改掉错别字 / 顺便改了」→ writer + quick；原规则 1 改为只审不改（含「只审不改 / 只帮我看看」）→ quality_reviewer + review_only。
- QUALITY_REVIEWER_PROMPT：用户原话明确要求修改时，审完把全部问题（包括错别字/标点）一次交给 writer，context 逐条列出位置、原文和改法；自动审查仍只为阻断问题返工，审稿人不动手。

## Alternatives considered

- **把上一轮助手结尾和路由结果喂给 LLM 路由（评审报告第 5 步的原方案）。** 最强理由：路由器能综合理解回答的语义，「可以，但第二章换个视角」这类混合回复也能判对。没采用：改路由输入要用真实模型探测验证分类效果，这一批只能用 mock 测试；确定性的沿用规则可以完整测到，覆盖了最常见的「可以」「B」「主角叫陈默」。路由输入的改动后来单独实现（`2026-10-08-agent-router-previous-turn-context.md`），与这里的规则并存：规则命中时直接跳过路由，没命中时路由器看得到上一轮结尾和落库路由。
- **只以问句收尾时也沿用全部字段，包括 scope 和只读。** 最强理由：规则最简单，与评审报告的字面描述一致。没采用：问句收尾多半是做完之后问下一步。writer 写完「只写第1章」后问「要继续写第二章吗？」，沿用 scope 会让 writer 被要求「只交付第1章」；审稿人只读审查后问「要我帮你改吗？」，沿用 read_only 会把用户的「可以」变成禁止改文件。这两种情况交回 LLM 路由，或清空已交付的 scope。
- **从状态卡合成文本里取 last_agent，正文非空时也拼进历史。** 最强理由：不用新增落库字段。没采用：合成文本会进回放，正文后面再挂一段状态卡摘要对模型是噪音；而且状态卡里没有 read_only / scope，F5 的核心问题（只读审查恢复成 writer 去改文件）解决不了。
- **在 sse_pump 里用 `task.cancel(msg)` 标明时限取消。** 最强理由：不依赖计时比较，语义最直接。没采用：取消消息要穿过 `_primed_stream` 和生成器关闭才能到 `process_stream`，GeneratorExit 路径上拿不到；按已用时长判定只在时限边界上有毫秒级歧义，而那时两种归类都说得通。

## Consequences

- 收益：只读审查、规划任务被截停后说「继续」，恢复的是原来的 agent 和范围；调用预算用完、墙钟时限截停也能直达；澄清后的简短回答不再被路由重新分类，开书阶段不会因一句「主角叫陈默」丢掉第 1 章；「检查并改掉错别字」一条消息就能改完。
- 代价：沿用规则是启发式。模型不带问号地提问时不会沿用，退回 LLM 路由的老行为；40 字以内带新要求、又没命中上述意图词的回复（「可以，第二章换成女主视角」）会沿用上一轮的 agent，新要求只靠历史里的原话传达。意图词按子串匹配、宁宽勿窄：「写完我看看」这类回复也会交回 LLM 路由，代价只是少一次沿用，回到改动前的行为。
- 代价：`message_metadata` 每条 assistant 消息多约 150 字节；这个字段上线前的旧消息没有 routing，正文非空时「继续」仍回落到 writer，问句回答仍走 LLM 路由。
- 代价：「检查并修改」改由 writer 处理，不再经过审稿人的分析视角；路由分类效果（「检查并修改第三章」「只帮我检查不要改」「先别写正文」等）需要真实模型探测确认，本批只有 mock 测试。
- 代价：取消路径按已用时长区分用户停止和时限截停，恰好在时限前一刻点停止会被记成 `run_deadline_exceeded`，下一轮「继续」会直达。

## Verification

`cd apps/server && venv/bin/python -m pytest tests/test_agent/test_agent_routing_continuity.py -q --no-cov -n 0`：正文非空的只读审查以审稿人 + read_only + scope 恢复；回落顺序；预算 / 时限可直达、用户停止不直达；新旧「继续」文案；澄清卡沿用全部字段；问句收尾沿用 writer 且清空 scope（面包屑追加在正文后也能识别）；长回复、收窄说法、writer 提问后回复「只审不改」「帮我审查一下第一章」「列个大纲」等只读 / 审查 / 规划意图（澄清卡后的只读 / 审查回复同样交回，规划词在澄清卡回答里仍沿用）、上一轮只读或不要正文、上一轮没提问、没有落库路由时都交回路由；graph 沿用时不调用 LLM 路由；历史 dict 暴露 routing / last_agent / clarification_pending；`process_stream` 落库路由后经 `load_chat_session` 读回、「继续」恢复为只读审稿人；取消路径按时限写 `cancelled` / `run_deadline_exceeded` 并带路由。改动前的代码上这些用例全部失败（新旧「继续」文案与用户停止两条除外）。另跑 `tests/test_agent` 全量与 `tests/test_api/test_agent.py`、`test_agent_stream_hardening.py`、`test_round3_agent_api.py`。
