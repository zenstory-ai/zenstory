# Agent Note: 离开编辑器后作品卡片等最后一次保存落库再取进度

Status: implemented

Related: [作品卡片显示写作进度](../feature/2026-10-09-project-card-progress-and-word-count-refresh.md)

## Problem

新手审计（PR163 审计 P2-8，浏览器 2/2）：在章节里加或删约 200 字，1 秒内点「返回工作台」，卡片仍显示修改前的字数（数据库已经是新值），之后不会再刷新，作者以为没保存。

原因：编辑器的最后一次修改在卸载时才保存（防抖还没到 3 秒），工作台在同一次提交里挂载并立刻请求 `GET /projects/progress`。两个请求并发，进度先返回就拿到旧字数；`useProjectsProgress` 只在挂载和作品数变化时请求，保存完成后不会再刷新。

## Decision

- 新增 `lib/editorSaveTracker.ts`：`SimpleEditor.handleSave` 把每次保存（包括卸载时的冲刷）登记为进行中的保存；`Editor` 在内容保存成功（普通保存和对比审阅写回）后派发 `zenstory:editor-content-saved`。
- `useProjectsProgress`：每次请求进度前先等进行中的编辑器保存结束（最多 5 秒，超时照常请求）；收到保存完成事件时重新请求。工作台首页和「我的项目」两处卡片都用这个 hook。
- 回归：`hooks/__tests__/useProjectsProgress.test.tsx` 用真实 SimpleEditor：打字后立即切到卡片页，断言进度请求在保存落库之后才发出、卡片显示新字数；以及卡片页打开时收到保存完成事件会重新请求。

## Alternatives considered

- **点返回时先 await 保存再跳转。** 最强理由：顺序最直观。被否：离开编辑器的路径很多（顶栏返回、浏览器后退、侧栏链接、登出），每处都要拦截导航；网络慢时按钮会卡住不跳转。
- **工作台在窗口 focus / visibilitychange 时重新请求。** 最强理由：顺带覆盖其他标签页里的修改。被否：同一标签页内跳转不会触发这些事件，修不了这个问题。
- **把进度改成 react-query 并在保存后 invalidate。** 最强理由：与额度等数据的缓存方式一致。被否：只为一个 hook 引入新的查询键和缓存语义，等待「保存先落库」仍需要额外的协调；事件 + 等待已经足够。

## Consequences

- 收益：编辑后立刻回到工作台，卡片显示保存后的字数；工作台打开期间如果有保存完成，卡片会跟着更新。
- 代价：有进行中的保存时，卡片进度最多晚一次保存往返（含写作统计请求）才出现；保存超过 5 秒未完成时仍可能先显示旧值，保存完成后由事件纠正。
