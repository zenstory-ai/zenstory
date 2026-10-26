# Agent Note: 接近请求墙钟时限时收尾

Status: implemented

关联：[运行预算与心跳](../feature/2026-10-05-agent-stream-runtime-budget-and-heartbeat.md)、[平台时限与终止帧](2026-10-13-agent-stream-platform-deadline-and-terminal-guard.md)。

## Problem

单次请求有 780 秒墙钟时限（`AGENT_RUN_WALL_CLOCK_TIMEOUT_S`，须早于 Railway 900 秒断连）。已有的软着陆只按模型调用次数触发，工作流也只按协作轮数停止，没有任何东西随时间收尾。2026-10-20 生产 103 轮里有 2 轮（50、54 次模型调用）跑满 780 秒被截断：可能停在 <file> 正文中间，作者拿到的是超时卡片而不是已保存内容的总结。模型变慢时（供应商切换后实测慢 2–5 倍）这类情况会更多。

## Decision

1. `AgentRunMeter` 记录请求开始时刻；`AGENT_RUN_WRAP_UP_AFTER_S`（默认时限减 180 秒，即 600 秒，可用同名环境变量覆盖）之后为「该收尾了」。
2. runner 的 `_ModelCallCountingFilter`：过了收尾时刻，每次模型调用前在输入末尾追加时间收尾提醒（写完正在写的 <file>、不再开始新文件或大段改写、写阶段总结）。模型照做后按已有的耗尽路径收尾：状态卡说明时间快用完、回复「继续」接着做，stop_reason 为 `run_deadline_exceeded`（路由已把它当作「没做完」续做）。
3. writing_graph：过了收尾时刻不再开始新的 agent run（交接、自动送审、续写、纠偏、插话追加轮），给作者一句「这一轮的时间快用完了，先停在这里；回复「继续」我接着做」后正常结束。
4. （2026-10-25 补）交接点上判断「下一个 agent 会不会跑」时也算上收尾时刻（含发布交接，`past_wrap_up` 同时看 `is_draining()`）：不会再跑时不发 `CHAT_HOLD(discard)`、不发 HANDOFF，也不构建审稿载荷，writer 扣着的总结照常放出。writer 交给审稿人的交接里有【要告诉作者】时，按快速模式丢掉交接的做法转述给作者，然后由循环顶部发收尾说明。此前收尾只在循环顶部检查，writer 交给审稿人时总结先被丢掉、又发出 HANDOFF，下一轮才停下，结果作者既看不到总结，也等不到审稿人。回归测试：`test_wrap_up_keeps_the_writer_summary_when_review_will_not_run`。

## Alternatives considered

- **提高墙钟时限**。最强理由：长任务能做完。被否：Railway 900 秒断开公网连接是硬限制，780 秒已经留了取消、退款和终止帧的余量。
- **只在图上停止新 run，不提醒正在跑的 run**。最强理由：实现更小。被否：生产里超时的两轮都是单个 run 里连续几十次调用，图层面的判断在 run 结束前根本不会执行。

## Consequences

- 收益：长请求以阶段总结和「继续」入口结束，而不是在墙钟时限被从中间截断。
- 代价：600 秒之后的长请求会提前收尾，作者需要多发一次「继续」；提醒追加在输入末尾，不影响前缀缓存。
