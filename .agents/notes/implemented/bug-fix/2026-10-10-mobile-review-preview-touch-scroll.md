# Agent Note: 手机去AI味审阅，从「原段落/建议段落」框起滑要能滚动审阅面板

Status: implemented

证据：r5 审计 `audit-runs/20261009-r5-zenstory-production/journal-r5target.md` 20:18、20:31 两条（390 宽，单卡 2/2、12 卡 6/6 从预览框起滑面板 scrollTop 停在 0，从卡片其他位置起滑可到 248/501/750/999；`logs/r5target-mobile-review-swipe-scan.txt`），以及 Chromium 145 独立页面复测（`overscroll-behavior: contain` 的 `overflow:auto` 框即使内容放得下或已滚到边，也会截断滚动链）。

## Problem

手机（< 768px）上去AI味审阅时，`DiffReviewSplitView` 根节点是唯一的纵向滚动区，队列列不限高。每张卡里的「原段落/建议段落」预览框（`DiffReviewEditPreview`）是 `max-h-36 overflow-y-auto overscroll-contain`。手指从这些框上开始纵向滑动时，滚动链在预览框处被 `overscroll-contain` 截断，外层面板不动；预览框在 390 宽占视口 25–47%，作者很容易「滑不动」。不是触摸事件处理问题：这条路径上没有 `touch-action`，`useGestures` 只在横滑结束和双指缩放时 `preventDefault`。React 层的 `onWheel/onMouseDown` 阻止冒泡不影响原生滚动链。

`overscroll-contain` 来自初始提交，没有相关决策依赖它。

## Decision

预览框只在 `md` 及以上保留 `overscroll-contain`（`md:overscroll-contain`），与 `DiffReviewSplitView` 切换为桌面左右布局、队列独立限高滚动的断点一致。桌面行为不变：在预览框内滚轮滚到头不带动队列。文字选择、`onMouseDown/onWheel` 阻止冒泡、接受/拒绝、上一处/下一处都不改。

回归：`e2e/editor-review-mocked.spec.ts`「narrow review touch scrolling」，390x844、`hasTouch`，用 CDP `Input.dispatchTouchEvent` 从第一张卡「Original paragraph」框中心向上滑 240px（不用滚轮、不写 scrollTop），断言面板 scrollTop > 100；再切到 1440 断言预览框计算样式 `overscroll-behavior-y` 仍为 `contain`。修复前失败（scrollTop 0），修复后通过。用 describe 级 `test.skip` 限定 Chromium、不设 `isMobile`，避免 Firefox 全量通道在建上下文时报错而不是跳过。

## Alternatives considered

- **`pointer-fine:overscroll-contain`（按输入设备而非宽度）。** 最强理由：768px 及以上的触屏平板（如 iPad 竖屏）也能修好。暂不采用：桌面守卫需要另起非触摸上下文验证，且平板上队列本身是限高滚动区、行为与桌面一致，不是本次复现的问题；记为残留。
- **给预览框加 `touch-action` 或 JS 触摸转发。** 被否：改动更大且可能影响框内选字，问题根因只是一条 CSS。
- **去掉预览框自身的 `max-h`/滚动。** 被否：长段落卡片会过高，改变既有审阅布局。

## Consequences

- 手机上从预览框起滑可以滚动审阅面板；长段落预览框仍先滚自身，到边后再带动面板（正常嵌套滚动）。手机上滚轮/触控板在预览框边缘也会带动面板，这是预期的。
- 残留：768px 及以上的触屏平板仍保持 contain；iOS WebKit 行为按规范推断，未在 WebKit 实测。审阅里「拒绝」偶需点两次、触摸上一处/下一处未确认，不在本次范围。
