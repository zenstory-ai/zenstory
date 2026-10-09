# Agent Note: 已结束的回合不再显示进行时状态；退还后徽标立即回退；去AI味不再用看不见的旧选区

Status: implemented

相关：停止与无产出退还的协议见 `architecture/2026-10-09-agent-graceful-stop-and-no-output-refund.md`；停止说明与重发见 `feature/2026-10-09-stop-guard-progress-and-leave-prompt.md`；去AI味的计费与「不计入」提示见 `bug-fix/2026-10-09-natural-polish-cost-noop-and-format.md`。本 note 只修第四轮审计里四处状态错误，不改文案风格、思考展示和计费规则。

## Problem

2026-10-09 第四轮审计（r4）：

- **P3-N18**：已完成的回复（有点赞和时间戳）上方仍挂着「正在组装上下文… / 正在思考… / 高质量模式：正在规划工作流…」，重新登录后历史里 router_thinking 那条还在。原因：`useChatStreaming.onThinking` 和 `onRouterThinking` 产生的条目在完成时被 `ChatPanel` 原样存进 `message.displayItems`；服务端把 `router_thinking` 写进 `display_events`，`parseChatDisplayEvents` 回放时照样渲染。交接说明也用 `thinking_status` 类型，所以不能按类型整类过滤。
- **P3-N17**：停止时 `edit_file` 还没有 `tool_result`，完成后的消息里这张卡一直转「处理中…」。`MessageList` 的 `isPending` 只看 `toolCall.status === 'pending'`，不看这条消息是否还在流式中。
- **P3-N13**：无产出停止后，灰字已写「不计入」，额度徽标却短暂显示预扣后的 2/10。服务端先发 `done`（触发一次重拉，读到退还前的值），再结算退还，再发 `quota_refunded`；前端收到退还帧后只再 invalidate 一次，徽标要等第二次 GET 返回。
- **P3-18**：手机切到 AI 页再切回编辑页、或「引用到对话」后，编辑器还留着看不见的选区（三个面板常驻挂载，只用 opacity/inert 隐藏），作者没选字点「去AI味」时按旧选区发出了会预扣额度的请求。

## Decision

- **进行时状态**：`StreamRenderItem` 加可选 `transient`，只有 `onThinking`（服务端只发「正在组装上下文」「正在思考」两种）设为 true，交接条目不设。`chatDisplayEvents.ts` 新增 `dropProgressOnlyItems`（去掉 `transient` 和 `router_thinking`），`ChatPanel` 完成时用它生成保存进消息的 `displayItems`；提前结束的空气泡判断仍用过滤前的快照和 `hasSubstantiveDisplayItems`，行为不变。历史回放 `parseChatDisplayEvents` 跳过 `router_thinking`（不再当作未知事件），紧随其后的 `router_decided`「创作计划：…」保留。服务端仍持久化 `router_thinking`，不改。
- **未完成的工具卡**：`MessageList` 两处（`OrderedMessageItems` 和 legacy 分支）改为 `isPending = pending && isStreaming`，`interrupted = pending && !isStreaming`。`ToolResultCard` 加 `interrupted` prop：不转圈，在原位置显示中性灰字 `chat:tool.interrupted`「未完成」/ "Not finished"（报错、断线提前结束时同样适用，所以不写「已停止」）。
- **退还后徽标**：`startRound` 发起时记下缓存里的 `ai_conversations.used`（`roundQuotaBaselineRef`）。`onQuotaRefunded` 先对 `quota()` 和 `quotaLite()` 做 `setQueryData`，把 used 设为 `min(当前值, 开轮前值)`（无上限套餐、缓存为空、没有基线时不动），再 invalidate 拉真值。invalidate 会取消还在路上的退还前重拉，不会被它覆盖。不做乐观 +1，`done` 时的重拉保持不变。
- **残留选区**：`SimpleEditor` 把文件切换时的选区清理抽成 `clearSelectionState`；新增 `dropSelection`（同时把 textarea 的选区折叠到末尾）。`useMobileLayout()` 为手机且 `activePanel !== 'editor'` 时调用 `dropSelection`；`handleAddQuote` 引用成功后也调用。桌面/平板 `isMobile` 为 false，不受影响。toast 文案不改。

## Alternatives considered

- 按类型过滤所有 `thinking_status`：会把交接说明「接下来由内容创作者继续：…」一起去掉，否决。
- 服务端停止持久化 `router_thinking`：前端回放过滤已覆盖新旧消息，不动服务端避免和其他区块冲突。
- 「已停止生成」延迟约 20 秒：audit 推测是服务端 `_cancel_producer` 等生成器收尾，需要 Railway 日志里停止与结算的时间差才能确认。本轮没有日志证据，不改停止协议和 `useAgentStream` 的回退。
- 读文件记录「找到 1 个文件 第1/2/3集」重复两组：drama r3 日志 `logs/drama-chat-msg1.txt` 里两组之间隔着一段「AI 思考过程」，是两次独立的模型工具调用（创建后又读、再读一遍），前端没有重复渲染路径（有 `displayItems` 时不再渲染 legacy toolCalls）。属于模型行为，本轮不在展示层合并卡片。
- 在 `blur` 时清选区：桌面 Safari 点按钮不聚焦按钮、`relatedTarget` 为 null，会误清，否决。

## Consequences

- 完成的回复只保留计划、交接、工具、正文；流式中仍照常显示进行时状态。旧消息重新加载后也不再显示「正在规划工作流…」。
- 提前结束（停止、报错或断线）后没拿到结果的工具卡显示「未完成」，不再误导为仍在运行。
- 收到退还帧时徽标与「不计入」说明同时回到开轮前的值；若其他设备同时用了额度，随后的重拉纠正。只出现过 1/3 次，上线后审计里建议复测「停止 B」场景。
- 手机切走编辑页或引用到对话后，再点「去AI味」会提示先选中文字，不会对看不见的选区发起会预扣额度的请求。
- 测试：`MessageList.test.tsx`（结束后的 pending 工具卡不转圈、显示未完成；流式中仍转圈）、`chatDisplayEvents.test.ts`（回放跳过 router_thinking，`dropProgressOnlyItems` 保留交接与计划）、`useChatStreaming.test.ts`（onThinking 条目 transient、交接不 transient）、`ChatPanel.mount.test.tsx`（完成消息去掉三条进行时状态；退还帧立即回退缓存且重复帧不再减、仍重拉）、`SimpleEditor.test.tsx`（手机切面板后不按旧选区去AI味；面板不切时照常；引用后清选区）。
