# 额度用完的一轮不再叫作者「继续」、撤销冲突指向历史版本、去AI味写明用量

Status: implemented

关联：[出错/超时的一轮不给下一步建议](2026-10-24-error-round-ends-without-followup-chips.md)（该记录写「『继续』照常可恢复」，没考虑额度已用完的组合；本记录补上，不改它的结论）、[撤销卡状态](2026-10-24-undone-edit-card-state.md)、[去AI味无改动退还](2026-10-09-natural-polish-cost-noop-and-format.md)、[慢请求阶段耗时](2026-10-24-agent-stage-timing.md)。

## Problem

2026-10-24 生产新用户审计（`prod-v2026.10.24.2`）与随后的根因排查，本批独立核对源码与截图后确认：

1. 免费用户第 3 条消息撞上 780 秒时限：同一屏上黄框写「这一轮写得太久，先停下了。回复「继续」就能接着写。」，下面红框写「今天的 3 条免费 AI 消息用完了」。错误卡只对额度错误码隐藏，正文是 `ERR_AGENT_RUN_TIMEOUT` 等固定文案，重试按钮也不看额度；`iteration_exhausted` 暂停卡（实时与刷新后两处）固定给「继续完成 / 拆成小步骤 / 只要修改建议」，摘要写「点「继续完成」接着做」。刷新后错误卡不持久化、改由 `stop_outcome` 显示「已中断」，所以「刷新后才对」只是偶然一致。另一条路径：过了收尾时刻、图不再开新的 agent run 时，`writing_graph` 在回复末尾追加固定的 `WRAP_UP_TEXT`（「……回复「继续」我接着做」）并落库，同样不看额度（独立审查指出）。
2. AI 修改后作者手改一处，再点那张卡的「撤销」：服务端按 `expected_updated_at` 正确返回 409 `ERR_RESOURCE_CONFLICT`（`detail.reason="stale_write"`），前端显示通用的「操作与现有数据冲突，请刷新后重试」。卡片的版本锚点是那次修改后的不可变令牌，刷新后重试必然再失败（实测 2/2）。
3. 编辑器「去AI味 · 1 条」：免费用户点一次先预扣 1 条今日 AI 消息、失败或无改动退还（`api/editor.py`），但可见标签只有「· 1 条」，触屏看不到 tooltip，审计者读成「1 条建议」。
4. 路由决策（`router_decided`）之后，「快速模式：正在理解你的要求…」芯片仍留在实时列表里闪烁，看起来一直卡在理解要求；刷新后这条已被 `dropProgressOnlyItems` 去掉。
5. 短篇「继续完成」两次没压到目标：编辑合同（old 逐字照抄、批量默认整批回滚）与「收尾提醒只在下一次模型调用前追加」属实，但每次调用耗时与工具业务失败没有证据；结算日志的 `tool` 阶段 `error_count` 只计异常，`status=error` 的业务失败（如 old 没找到、陈旧写入）完全看不到。

## Decision

- **额度用完（既有 `aiSendBlocked`：缓存条数用完，或服务端今天已拒绝过；Pro 永不为真）时不给下一步**：ChatPanel 错误卡保留标题，正文换成 `chat:panel.streamErrorQuotaPaused`（这一轮没做完、已保存的不受影响、额度恢复后再接着写），不显示重试；恢复时间与升级照旧由 `ChatQuotaCard` 给。MessageList 新增 `nextStepsBlocked`，实时与已保存的暂停卡共用 `IterationExhaustedCard`：保留标题与事实，摘要改用不带「点继续完成」的 `*Paused` 文案，隐藏「接下来你可以」与三个按钮。额度恢复（北京零点、升级）后 `aiSendBlocked` 变假，同一张卡重新给按钮。只读结构状态，不解析错误文案，不持久化错误卡。建议 chips 只把文字填进输入框（额度恢复后再发），不改。
- **服务端收尾说明**：图在收尾时刻停下时，用独立短会话（`asyncio.to_thread`）读一次 `quota_service.check_ai_conversation_quota`（每日条数，本轮已预扣）；确知今天不能再发时追加 `WRAP_UP_TEXT_NO_MESSAGES_LEFT`（停在这里、已写好的都保留），不提「继续」；读不到额度时保持原说明（不改变无法判断时的既有行为）。只在这个少见时刻多一次读取，不影响计费。
- **撤销冲突**：`handleConfirmUndo` 识别 `ApiError.errorCode === "ERR_RESOURCE_CONFLICT"` 且 `details.reason === "stale_write"`，toast `versions:undoStaleWrite`：文件在这次修改后又改过，不能直接撤销以免覆盖后来的内容，到编辑器下方「历史版本」里选版本恢复。服务端版本守卫不动；卡片不标「已撤销」，正文不刷新。
- **去AI味**：中文可见标签改「· 用 1 条」，中英 tooltip 补「没有改动或失败不计」。英文标签维持「· 1 msg」（窄屏页脚布局的 e2e 已按此长度验收）。
- **路由芯片**：`useChatStreaming.onRouterDecided` 追加决策时移除实时列表里的 `router_thinking`，与刷新后一致。
- **无内容观测**：`AgentRunMeter.record_tool_failure` 只在工具真正执行、正常返回 `status=error`（含 `parallel_execute` 子任务失败，判定复用 `tool_failure_breaker._parse_error_payload`）或 `status=partial`（`continue_on_error` 下部分失败，error_type 记 `partial`）时计数；`timing_report()["tool_failures"]` 按工具名给 `count`、`rolled_back_batches`（`edits` 超过 1 项且没开 `continue_on_error` 的失败，整批回滚）与 `error_types`（只收 `[a-z0-9_]{1,48}` 的程序标识符，每个工具最多 8 种，其余归 `other`），随既有 `Agent stream billing evaluated` 行以 `run_tool_failures` 输出。守卫拒绝、参数解析失败不算执行；异常仍只计在 `tool.error_count`。

## Alternatives considered

- **额度用完时完全隐藏错误卡，只留额度卡。** 最强理由：一屏只有一张卡，最干净。被否：作者看不到这一轮是中断的（实时时 RoundEndNote 还没出现），会以为是正常结束；保留标题、换正文即可消除误导。
- **按错误码给每种中断写一份「额度用完版」原因文案（写得太久 / 步骤太多…）。** 最强理由：保留具体原因。被否：要为 8 个以上错误码各加一份中英文案，原因本身对「今天不能再发」的作者没有可操作差别；一份通用文案加既有标题已足够，刷新后 RoundEndNote 仍写「已中断」。
- **撤销冲突时直接打开那个文件的历史版本面板。** 最强理由：一步到位。被否：历史面板是编辑器内部状态，需要跨组件新通道并先切换文件；提示里指明入口已经可操作，范围更小。
- **短篇不收敛直接做整篇替换 / 批量只重发失败项 / 截停进行中的调用。** 最强理由：根因报告认为能显著减少输出量。被否：都改变编辑合同的原子性或作者内容安全边界，需要单独设计与评测；本批只补能定位慢点与失败的计数，配合模型指标里的逐次调用耗时、TTFT 与输出 token。

## Consequences

- 收益：最后一条免费消息中断或到收尾时刻后，错误卡、暂停卡与服务端收尾说明都不再叫作者「继续」（模型自己写在总结里的「下一步」措辞不受程序控制，不做文字检测）；撤销冲突给出能走通的路；去AI味的耗用一眼可见；路由完成后不再像卡住；结算行能看出 edit_file 失败与整批回滚的次数。
- 代价：额度用完时暂停卡的具体下一步不显示，作者要等额度恢复后从输入框或卡片重新开始；旧的暂停卡在额度恢复后会重新出现按钮（与原行为一致）。`run_tool_failures` 每请求多一个小字典。
- 不处理：短篇压缩的工具成本、进行中调用跨收尾、工具参数进度事件、总结措辞与文学质量；这些需要另行授权的设计与真实模型评测。

## Verification

- vitest：`ChatPanel.mount.test.tsx` 新增额度 3/3 + `ERR_AGENT_RUN_TIMEOUT`（无「继续」、无重试、`nextStepsBlocked=true`）与额度未用完对照、撤销 409 stale_write 专用提示，修改前 3 项失败；`MessageList.test.tsx` 新增实时 / 已保存 / 流式三处暂停卡在额度用完时无按钮、恢复后有按钮；`useChatStreaming.test.ts` 新增决策后无 `router_thinking`，修改前失败。
- pytest：`test_stage_timing_boundaries.py` 新增业务失败计数（整批回滚、单次、成功、非法/空 error_type、`continue_on_error` 的 partial 与 error，报告不含参数/错误原文）与 error_type 上限；`test_agent_routing_continuity.py` 新增收尾说明在额度用完 / 读不到额度两种情况下的文字。
- 独立审查（另起 Claude Code 只读会话）结论 APPROVE，提出的收尾说明、partial 计数、整批回滚口径、memo 覆盖（改用 rerender，删掉 memo 比较行时测试失败）均已在本批处理。
