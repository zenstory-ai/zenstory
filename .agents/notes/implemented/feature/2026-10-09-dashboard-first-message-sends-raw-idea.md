# Agent Note: 工作台首条消息发作者原话，聊天面板跟住作者刚发的内容

Status: implemented

本 note 推翻了 `process/2026-10-08-dashboard-home-copy.md` 中「首条自动消息用 `chat:message.createProject` 模板」的做法（那份 note 里的其余文案决定不变）。服务端在模型上下文里加首条消息提示的部分放在 P2（`apps/server/agent/service.py`），不在本 note 范围内。

## Problem

2026-10-08 新用户审计（三个 persona 都遇到）：

1. 工作台填了想法后，ChatPanel 自动发出的第一条消息被套进 `chat:message.createProject` 模板（「我想写一部短篇小说。我的想法是：……请先帮我理一理……」）。这段模板既显示在作者的气泡里，也作为作者的原话落库，作者看到的是一段自己没写过的话（审计 #5）。
2. 作者往上翻看前文后再发消息，聊天区不会回到底部，新回复在视野外，只出现「跳到最新内容」按钮（#11）。
3. 步骤提示写「步骤 2/12」，看起来像这一轮只做了六分之一（#12）。
4. 回复里的中文控制标记 `[任务完成]`、`[需要澄清]` 原样显示（#3 的前端部分，服务端把它们当作 `[TASK_COMPLETE]` 的别名由 P1 处理）。
5. 免费额度用到 10/10 后输入框照常可发，发出去才收到额度卡片；顶栏额度胶囊只按 60 秒轮询刷新，一轮扣费或退还后要等一会儿才变（#30）。
6. 右栏宽度约 300px 时，头部的「AI 创作助手」和「今日 AI 消息 3/10 条」会折成两行；撤销 AI 修改用浏览器原生 `confirm`（#31）。
7. 每次 AI 改完文件自动生成的项目快照都叫「AI 对话完成 - 文件已修改」，快照列表里分不出哪次是哪次（#22 的快照说明部分）。
8. 项目还叫默认名「我的短篇」时，AI 在这一轮里给项目起了名字，顶栏仍显示旧名字，要刷新页面才变（#35 的「项目名不跟随」）。

## Decision

**首条消息发原话（`ChatPanel.tsx`）**

- 工作台想法自动发送改为 `handleSendMessage(content, undefined, { entry: "dashboard_idea" })`。`handleSendMessage` 新增可选的第三个参数 `extraMetadata`，展开合并进 `request.metadata`；普通发送不带 `entry`。
- 气泡里显示的、发给服务端的 `message`（也就是落库的 `ChatMessage.content`）都是作者的原始想法。
- 删除 `chat:message.createProject`（中英）、按 `projectType` 拼类型名的代码，以及因此不再被引用的 `chat:projectType.*`（工作台和项目页用的是 `dashboard` 命名空间里的同名 key，不受影响）。
- 服务端看到 `metadata.entry === "dashboard_idea"` 时，把「想法具体就给框架、模糊就先问两三个问题」的提示加进模型上下文且不落库，由 P2 实现。前后端谁先上线都安全：前端先上时，旧服务端忽略 `entry`，原话照常路由，只是少了这段提示；服务端先上时，旧前端发的仍是模板，不带 `entry`，服务端不加提示。

**发送后跟随（`ChatPanel.followLatestMessage`）**

- `handleSendMessage` 乐观插入用户消息后，以及「重试」重新发起同一请求时，设 `shouldAutoScrollRef.current = true`、隐藏「跳到最新内容」，在下一帧（复用 `autoScrollFrameRef`，与已有的自动滚动合并）调用 `scrollToBottom(false)`。输入框发送、点建议芯片后发送（芯片只填入输入框，发送仍走 `onSend`）、工作台想法自动发送都经过 `handleSendMessage`。这补充了 `bug-fix/2026-04-29-chat-message-list-without-virtualization.md` 里的自动滚动规则：作者主动发送时不看当前是否在底部。

**步骤与标记**

- `MessageList` 的步骤提示只显示序号「步骤 2」，不再带 `/{maxIterations}`；快到上限时的「还剩 N」和 `lowTurnWarning` 不变。
- `lib/utils.ts` 的 `AGENT_CONTROL_MARKERS` 增加 `[任务完成]`、`[需要澄清]`，并导出 `stripAgentControlMarkers` 供思考面板复用（见 `feature/2026-10-09-thinking-collapsed-and-sanitized.md`）。

**额度用完（#30）**

- ChatPanel 读取与 `QuotaBadge` 同一个 query（`subscriptionQueryKeys.quota()`，共用缓存，轮询仍由胶囊负责），`quotaExhausted = limit !== -1 && used >= limit`。用完时输入框仍可起草，`sendDisabled = true`；不在生成中时 placeholder 用新 key `chat:input.placeholderQuotaExhausted`「今天的 {{limit}} 条 AI 消息用完了，北京时间明天 00:00 恢复。可以先把想法写下来，到时再发。」。不新增任何关于成本兜底的文案，兜底仍走现有的错误码卡片（`feature/2026-10-08-agent-stop-reasons-for-authors.md`）。
- 胶囊刷新：`onSessionStarted`（服务端已预扣）、一轮完成、出错、`quota_refunded` 时 invalidate `quota()` 和 `quotaLite()`。

**头部不折行、撤销用应用内确认框（#31）**

- 标题 `shrink-0 whitespace-nowrap`，标题组和右侧容器 `min-w-0`，「处理中」等状态词放不下时省略。
- `QuotaBadge` 新增可选 prop `compact`：ChatPanel 传 true，只显示「{{used}}/{{limit}}」（Pro 显示「∞」），完整文案放进 `title` 和 `aria-label`；升级按钮在 compact 下是带 `aria-label` 的图标按钮。其他调用点（设置弹窗等）不传，行为不变。
- 撤销 AI 修改改用 `ui/ConfirmDialog`：标题 `chat:tool.undo_edit`，正文沿用 `editor:versionHistory.confirmUndoAIEdit`（本包只读不改），确认 `chat:actions.undo`，取消 `common:cancel`；回滚进行中按钮禁用，结束后关闭。

**快照说明（#22）**

- `useChatStreaming` 自动拍快照时，说明改用新 key `chat:message.snapshotAfterAiEdit`「AI 修改后：{{request}}」/「After AI edit: {{request}}」。`request` 取本轮作者原话（ChatPanel 通过 `getLatestUserRequest` 提供，切换项目时清空）：去掉换行（两侧都是英文字母或数字时补一个空格），按字符截取前 24 个，超出加「…」。拿不到原话时退回 `chat:message.aiDone`。删除不再使用的 `chat:message.aiDoneFilesModified`；已经落库的旧说明由 P4 的 `versionSummary` 映射。

**自动命名后刷新项目名（#35）**

- 一轮完成后，如果当前项目名是默认名（`config/project_templates.py` 的中英默认名，与 `dashboard:defaults.*` 一致，外加服务端兜底「我的项目」），调用一次 `refreshProjects()`；作者已经起了名字就不调用。

## Alternatives considered

- **保留模板，只在气泡里显示原话。** 最强理由：前端单独就能改，不依赖服务端上线顺序。被否：落库的仍是模板，刷新后气泡又变回模板，AI 记忆和路由看到的也是模板；提示应该只进模型上下文，这只能在服务端做。
- **服务端从首条消息里识别模板再剥掉。** 最强理由：不用新增字段。被否：要做字符串匹配，中英文和后续改文案都会让匹配失效；`metadata.entry` 是显式信号，旧服务端会直接忽略。
- **额度用完时把输入框整个禁用。** 最强理由：最直观，作者不会误以为还能发。被否：作者此时常常还有想法要记，禁用输入框会把草稿也挡住；只禁用发送、在 placeholder 里写清恢复时间，第二天打开就能直接发。
- **额度胶囊只靠缩短轮询间隔来刷新。** 最强理由：不用在流事件里插调用。被否：缩短间隔会让所有打开的页面都更频繁地请求，而扣费和退还的时间点在流事件里是确定的，按事件刷新更准，也更省请求。
- **快照说明写改了哪些文件。** 最强理由：比作者原话更精确。被否：一轮可能改很多文件，`onComplete` 时拿到的只是确认有改动的信号，拼文件名要另外汇总，名字也可能很长；作者认得出自己说过的话，24 字足以分辨。
- **撤销确认继续用原生 `confirm`。** 最强理由：零代码、天然阻塞。被否：样式和应用不一致，移动端和部分浏览器里体验差，审计工具里还会卡住页面；仓库里已有 `ConfirmDialog`，其他删除和恢复确认都在用。

## Consequences

- 收益：作者在气泡和历史里看到的是自己写的话；发送后视图回到最新回复；步骤提示不再像没做完；额度用完时提前说明何时恢复，草稿不丢；胶囊在扣费和退还后及时更新；窄右栏头部不折行；快照列表能分辨每一轮；AI 起的项目名马上出现在顶栏。
- 代价：在 P2 上线前，工作台首条消息没有任何引导提示，模糊想法时 AI 的第一轮回复可能不够有针对性。ChatPanel 依赖 React Query，渲染它的测试（`round3_frontend_chatpanel.test.tsx`、`ChatPanel.projectSwitch.test.tsx`）要包一层 `QueryClientProvider` 并 mock `subscriptionApi`；`round3` 的撤销用例改为点确认框按钮。额度判断基于缓存，过了北京时间零点到胶囊下一次轮询（最多 60 秒）之间，发送按钮可能还是灰的。紧凑胶囊只显示数字，含义要靠悬停提示或读屏的完整文案。默认名列表写死在前端，服务端模板改默认名时要同步。快照说明在创建时按当时界面语言写入，切换语言后旧快照不跟着变。

## Verification

- `NODE_OPTIONS='--max-old-space-size=8192' npx vitest run src/components/__tests__/ChatPanel.mount.test.tsx src/components/__tests__/round3_frontend_chatpanel.test.tsx src/components/__tests__/ChatPanel.projectSwitch.test.tsx src/components/__tests__/ChatPanel.newSessionLifetime.test.tsx src/components/__tests__/MessageList.test.tsx src/hooks/__tests__/useChatStreaming.test.ts src/components/__tests__/QuotaBadge.test.tsx src/components/__tests__/QuotaBadge.compact.test.tsx src/components/__tests__/SettingsDialog.test.tsx`：自动发送时 `message` 等于原话、`metadata.entry === "dashboard_idea"`、气泡为原话，普通发送不带 `entry`；往上滚动后发送会调用 `scrollToBottom(false)` 且不显示跳转按钮；10/10 时 `sendDisabled`、新 placeholder、输入框可编辑，9/10 与 Pro 不受影响；`session_started`、完成、出错、退还时 invalidate 两个额度 query；头部 class 与 compact 胶囊；撤销走 ConfirmDialog（不调用原生 `confirm`，取消不回滚，确认按原参数回滚）；默认名时完成后调用 `refreshProjects`，自定义名时不调用；步骤提示不出现「/12」；中文标记被过滤；快照说明使用截断后的原话。把这些新用例放到改动前的源码上跑，除「普通发送不带 entry」「默认不传 compact 时文案不变」这类对照用例外都会失败。
- `npx tsc --noEmit -p tsconfig.app.json`、`pnpm lint`、`pnpm lint:tokens`、`pnpm lint:i18n-keys`、`pnpm lint:i18n`（中英 key 对齐）、`pnpm run build:typecheck` 均通过。
