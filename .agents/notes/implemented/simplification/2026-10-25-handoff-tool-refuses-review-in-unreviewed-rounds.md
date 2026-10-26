# Agent Note: 不送审的一轮由 handoff_to_agent 直接拒绝送审，删掉图里的补总结重跑

关联：[作品第一条消息不自动送审](../feature/2026-10-20-first-turn-skips-auto-review.md)、[快速模式保留 writer 总结](../bug-fix/2026-10-21-fast-mode-summary-and-file-body-turn.md)。本 note 修订这两条里「图丢弃交接、必要时补跑一轮只写总结」的做法，旧 note 不改写。

## Problem

快速模式和作品第一条消息这两种轮次不送审。writer 仍按送审协议调用 `handoff_to_agent(quality_reviewer)` 时，原来的处理在 `writing_graph` 里：

- 工具照常返回 `handoff`，runner 结束这次 run；
- 图丢弃交接，再按「交接前写了多少字」判断作者有没有看到总结：快速模式不足 80 字（`PROGRESS_ONLY_MAX_CHARS`）、第一条消息完全没写，就让 writer 再跑一轮只写总结（`FAST_MODE_SUMMARY_CONTEXT` / `FIRST_ROUND_SUMMARY_CONTEXT`，带上「[要告诉作者]」的话）；写够了就把那些话直接追加给作者；中途有作者引导时不补跑。

2026-10-25 审计（A-S27）指出：这是用字数猜模型有没有写总结，为此要追踪「交接那一批工具调用之前的文字」，还多了一个只此一次的重跑分支和一处与 chat-hold、steering 的时序交织。补跑本身也要多开一次 run。

## Decision

- `ToolContext` 增加请求级的 `review_handoff_refusal`。`writing_graph` 在确定生成模式和首条消息后设置：第一条消息用 `FIRST_ROUND_REVIEW_REFUSAL`，快速模式用 `FAST_MODE_REVIEW_REFUSAL`，其余为 None；收尾时清掉。
- `handoff_to_agent` 在当前角色是 writer、目标是 quality_reviewer 且设置了拒绝说明时，返回 `status=error`，错误文字就是这段说明。runner 只在 `status=handoff` 时停下，所以 writer 在同一次 run 里看到说明、接着给作者写总结。说明沿用原来补总结轮的要求（第一条消息：写了什么、主角背景冲突、两个用「」括起来的方向；快速模式：写了什么、各多少字、不说送审），并要求把「[要告诉作者]」的话写在总结最后；交接前已经写过总结的只补这些话。
- 删除图里的丢弃 + 补跑分支、`PROGRESS_ONLY_MAX_CHARS`、`AUTHOR_NOTES_RELAY_HEADER`、`first_round_summary_rerun_used`，以及只为它服务的「交接前文字」追踪。`_author_notes_from_handoff` 仍由收尾门槛（F-07）使用，保留。
- 不变：快速模式的 writer 指令（`FAST_MODE_DIRECTIVE`）照旧先告诉模型不要送审；第一条消息与快速模式都关自动质检门；writer 交给其他角色、规划角色的交接、路由显式安排的审稿（不经过这个工具）都不受影响。前端本来就不显示控制流工具卡片，不需要改。

## Alternatives considered

- **维持原状（图丢弃 + 按字数补跑）**：现有行为稳定，没有事故；写够总结时不用多一次模型调用。但 80 字是在猜模型写的是进度还是总结，猜错就多跑一轮或漏掉总结；分支和 chat-hold、steering 时序缠在一起，每次改动交接逻辑都要绕开它。
- **只在提示词里强调这一轮不要送审**：最轻。但 2026-10-20 生产样本里，指令关掉了送审，writer 仍调用了交接；需要一个确定的拦截点。
- **在 runner 的工具执行前拦截**：和只读请求、角色权限的拒绝放在一起。但那一层只认工具名，判断目标角色还要解析参数；交接工具本身已经在校验目标（非法目标、自我交接），拒绝放在同一处最直接。

## Consequences

- 不送审的一轮里，writer 的交接被拒后在同一次 run 里写总结；作者读到的仍是 writer 自己的交付说明，「[要告诉作者]」的话由模型写进总结，不再由图拼接。
- 模型调用次数基本不变：原来补跑一次 run，现在是同一次 run 里多一次模型调用；不再有「写够 80 字就不补」的分支。
- 被拒的交接按一次工具失败记入熔断器的累计计数（上限 10）；模型原样重试 3 次才会熔断，概率很低。
- 总结写得是否到位依赖模型照说明执行，和原来的补跑轮一样没有用真实模型验证；作者中途发来的引导仍由原有 steering 追加轮处理。

## Verification

- `tests/test_agent/test_agent_routing_continuity.py`：快速模式下 writer 调用真实 `handoff_to_agent(quality_reviewer)`，工具返回带「快速模式」「[要告诉作者]」的拒绝，没有 HANDOFF、没有第二次 run，同一 run 的总结是作者看到的最后文字；高质量模式送审、快速模式交给 planner 照常交接。
- `tests/test_agent/test_first_message_hint.py`：第一条消息的同一场景工具拒绝（说明含「第一条消息」），非第一条照常送审。
- 两条拒绝用例在改动前失败（旧代码返回 handoff、图补跑出第二次 writer run）。
