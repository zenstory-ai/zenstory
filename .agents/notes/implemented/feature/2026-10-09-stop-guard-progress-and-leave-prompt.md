# Agent Note: 停止按钮防误触、等待时显示进度、生成中离开先确认

Status: implemented

计费与停止协议见 `architecture/2026-10-09-agent-graceful-stop-and-no-output-refund.md`；本 note 只记界面上的四处改动。

## Problem

空回复复盘（同上 note）里，作者侧的三种中断都和界面有关：

1. 发送按钮在消息发出的同一刻原地变成停止按钮，位置、大小不变；约 10 次停止发生在发送后 3 秒内，多为双击误触。
2. 正常一轮的中位耗时约 66 秒，开头十几秒屏幕上只有路由芯片和折叠的思考过程，作者以为卡住而停掉重发（约 15 次）。
3. 生成中跳到首页、定价页、技能页会卸载聊天面板并中断这一轮（10 次），作者事先并不知道。

另外，停掉之后聊天里只剩一个空白气泡，作者不知道这一轮扣没扣额度，也没有顺手重发的入口。

## Decision

- **防误触**：`MessageInput` 的停止按钮出现后 `STOP_ARM_DELAY_MS`（1.5 秒）内禁用（半透明、不响应）；`isStopping` 为真时显示并朗读「正在停止…」，不再响应。手机与桌面是同一个组件（`MobileChatInput` 未被引用，未改）。
- **停止后的说明**：服务端的 `workflow_stopped(reason=user_stopped)` 在消息列表里渲染为灰色「已停止生成」（`UserStoppedNote`），不再用「AI 已暂停」警告卡片。收到 `quota_refunded(kind=stopped)` 后，消息区下方灰字说明「已停止，这一轮还没有写出内容，不计入今日 AI 消息。」并附「重新发送」，点击重发最近一条用户消息。其余两种退还说明不带重发。
- **等待时的进度**：消息上方已有路由、agent、工具的步骤芯片说明 AI 在做什么；`StreamActivityLine` 只在生成期间补一行「AI 正在处理 · N 秒」，满 20 秒（`LONG_WAIT_HINT_AFTER_S`）后加一句「写长内容通常要一两分钟，请稍等。」。秒数自己计时，不让 ChatPanel 每秒重渲染；秒数不朗读，只朗读提示。
- **离开先确认**：`useLeaveWhileGenerating(isStreaming && !isStopping)` 在生成期间拦截路由的 `push/replace`（应用用的是 `<BrowserRouter>`，没有数据路由的 blocker），跳到别的页面前弹应用内确认框（「AI 还在写 / 离开会中断这一轮，还没写出内容的话不计入今日 AI 消息」，按钮「继续等 / 离开」）；同一页面只改 query/hash 不拦。关闭或刷新标签页走浏览器自带的 `beforeunload` 提示。路径从路由 context 读取，聊天面板在没有路由的环境里照常渲染。
- **遥测**：作者停止单独记为 `ai_chat_stopped`（首字等待只在服务端计费日志里记 `first_output_ms`）。

## Alternatives considered

- **手机上把停止按钮挪到别处（例如消息气泡旁）。** 最强理由：从位置上根本消除双击误触。被否：作者想停时最先找的就是原来那个按钮，挪走会让主动停止变难；短暂禁用已经覆盖误触窗口，改动也小。
- **用 `createBrowserRouter` + `useBlocker` 做离开拦截。** 最强理由：官方 API，覆盖浏览器后退按钮。被否：要把整个应用迁到数据路由，影响所有页面与测试，超出本批范围；拦截 `push/replace` 覆盖了站内链接和按钮跳转，后退按钮暂不拦。
- **把流事件翻成作者口吻的当前动作（「正在读取《总纲》」「正在写《第一章》」）。** 最强理由：比步骤芯片更好懂。被否：本地实测时这一行和上方的步骤芯片、工具卡片说的是同一件事；真正缺的是「已经多久、还要多久」，秒数加安抚提示就够了。

## Consequences

- 收益：双击发送不再停掉这一轮；停止后作者知道没扣额度并能一键重发；等待期间看得到已经多久；误点导航不再悄悄中断生成。
- 代价：真想立刻停止的作者要等 1.5 秒；离开确认只拦站内导航，浏览器后退仍会直接中断；`beforeunload` 的文案由浏览器决定。拦截 `push/replace` 依赖路由 navigator 对象的结构，升级 react-router 时要复查。离开后后台退还的额度要刷新后才在额度标签上体现。

## Verification

`cd apps/web && pnpm exec vitest run src/components/__tests__/MessageInput.test.tsx src/components/__tests__/ChatPanel.mount.test.tsx src/hooks/__tests__/useLeaveWhileGenerating.test.tsx src/components/__tests__/StreamActivityLine.test.tsx`：停止按钮出现后先忽略点击、到时后可停、停止中不重复触发；无产出停止的说明与重发（重发最近一条用户消息），其他退还不带重发；离开拦截在确认后才跳转、同页不拦、不生成时不拦；进度从理解需求走到写具名文件，满 20 秒出现安抚提示。
