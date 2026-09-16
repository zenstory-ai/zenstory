# Agent Note: 聊天消息列表不做虚拟化，完成后的自动滚动合并进单个 RAF

Status: implemented

## Problem

长对话里 Agent 回复结束后，消息列表持续闪动。根因有两层：消息列表用估算高度做虚拟化，富文本的助手消息落地后虚拟器反复修正行高与偏移；同时完成事件触发多次间隔极近的滚动到底请求，把抖动放大成肉眼可见的闪烁。

## Decision

`MessageList` 把过滤后的消息按稳定 DOM 顺序直接渲染，不使用 `@tanstack/react-virtual`（该依赖仅用于 `DiffReviewSplitView` 和 `useVirtualizedTree`）；`Row` 组件与 `scrollToBottom` 的 ref API 保持原样，滚动直接作用于容器。`ChatPanel` 的自动滚动通过 `autoScrollFrameRef` 在单个 `requestAnimationFrame` 内合并执行；触发信号限定为 `messages.length`、累积流式内容、思考内容、冲突计数、流式状态标志和 `streamRenderItems.length`。仅元数据变化的消息更新（如后端 hydration）never 触发滚动。

来源：3ef311e（初始提交；设计稿 `docs/plans/2026-04-04-chat-message-flicker-fix-design.md`，已落地）

## Alternatives considered

- **保留虚拟化、改进高度测量**（设计稿所替换的原实现）。最强理由：超长对话下 DOM 节点数受控，是长列表的标准解法。被否：富文本助手消息的高度无法预估，测量-修正循环本身就是闪烁来源；网文对话的消息数量级尚未到需要虚拟化的程度。

## Consequences

- 收益：完成后列表稳定；滚动只在有意义的信号上发生一次。
- 代价与已知上限：DOM 节点随消息数线性增长。若单会话消息数达到让渲染明显变慢的量级（当前无实测阈值），需重访虚拟化，届时 must 先解决富文本高度预估问题。
- `MessageList.tsx` 中仍有一句提到 virtualizer 的注释（约第 207 行），是历史残留，不代表虚拟化存在。

## Verification

`cd apps/web && pnpm exec vitest run src/components/__tests__/MessageList.test.tsx`：测试按非虚拟化渲染断言。
