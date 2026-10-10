# Agent Note: 手机上去AI味/段落审阅可以滚到「接受/拒绝」，定位修改不再把审阅区拉回顶部

Status: implemented

关联：[2026-10-09-workspace-mobile-and-chat-small-ux](2026-10-09-workspace-mobile-and-chat-small-ux.md)。证据：r4 生产审计 r4short（`shots/r4short-16*`、`r4short-17d`，390 宽实测按钮在视口外、外层 overflow:hidden）。

## Problem

- `DiffReviewSplitView` 根容器是 `flex-col overflow-hidden`（桌面 `md:flex-row`）。手机上两栏上下排列：正文对比区 `flex-1` 被压到 0 高；修改卡片栏带着为桌面横排写的 `shrink-0`，在纵向上撑到内容高度（约 725px，大于可视的约 569px）。卡片列表本该自己滚动，但它的父级已经撑到内容高度，列表永远不溢出；唯一溢出的是根容器，而它是 overflow-hidden，所以每张卡片底部的「接受/拒绝/撤销」被裁掉，滚不到。
- 改成可滚动后又暴露一处：`InlineDiffEditor.scrollToEdit` 在打开审阅、点卡片、定位、上一个/下一个、撤销时对正文对比区里的元素调用 `scrollIntoView`。手机上这个区域高 0，`scrollIntoView` 只能去滚外层审阅容器，把作者刚滑到的按钮拉回顶部（mocked E2E 实测 189 → 0），打开审阅后 0.1–0.5 秒内的滑动也会被抵消。

## Decision

- `DiffReviewSplitView.tsx` 根容器：`overflow-hidden` → `overflow-y-auto md:overflow-hidden`。手机上审阅区整体成为唯一的滚动区域（队列标题和卡片一起滚）；768px 及以上仍是 overflow-hidden，桌面不变。
- `InlineDiffEditor.tsx` `scrollToEdit`：正文对比区（容器的父级）高度为 0 时直接返回，不调用 `scrollIntoView`。桌面上该区域有高度，行为不变。
- 回归：`e2e/editor-review-mocked.spec.ts` 新增 390×844 用例：长段落建议让卡片高于可视区；用真实滚轮滑动后「接受」成为该位置的最上层元素并能点中；点卡片定位后审阅区滚动位置不变；最终保存为 `ai_edit`。红绿：恢复 overflow-hidden 时「按钮可点」失败；去掉守卫时「定位后位置不变」失败。桌面 1440 用例断言根容器仍是 overflow hidden。

## Alternatives considered

- **卡片栏手机上去掉 `shrink-0`（`md:shrink-0`）**：标题固定、列表自己滚、虚拟列表的 scrollToIndex 也能工作，390×844 下列表约 397px。但根容器矮于约 190px（例如 667×375 横屏）时列表高度为 0，按钮又不可达；根容器滚动在任何高度都成立，所以作为备选，不与主方案同时用。
- **只改 E2E 等待自动滚动结束、不加守卫**：能让测试稳定，但真实用户每次接受或点卡片后都会被拉回顶部，等于没解决「可操作」。

## Consequences

- 手机上能逐段接受/拒绝；桌面布局与滚动不变。
- 剩余（本轮不改）：手机上队列标题（筛选、上一个/下一个）随卡片滚走，不固定；审阅按钮约 32px 高，低于 44px 触控目标；最后一张待处理卡被接受后列表切到「全部」重建，审阅区回到顶部（布局重排，不是 scrollIntoView）；手机上虚拟列表的 scrollToIndex 仍是空操作（与改前相同）。
