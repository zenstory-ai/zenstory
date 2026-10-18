# Agent Note: chunk recovery 不把合法离页取消当成旧 chunk

Status: implemented。延续 [退出前草稿恢复](2026-10-07-editor-draft-recovery-on-chunk-reload.md)，只补充 document navigation 与 chunk 恢复重叠时的边界。

## Problem

Firefox 在新 document navigation 接管页面时，会取消旧页面仍在加载的动态模块。Vite 将这种取消同样报告为 `vite:preloadError`；原实现把该事件当作权威旧 chunk 信号，立即刷新旧 URL。结果是用户已经发起的目标导航被第二次 reload 中断，Playwright 暴露为 `NS_BINDING_ABORTED`，真实浏览器也可能被送回刚准备离开的页面。仅在测试里等待页面完全 ready 可以修正测试前置条件，但不能消除用户快速离开懒加载页面的产品风险。

## Decision

`chunkRecovery` 在 `pagehide` 确认当前 document 已经离开后设置标记；该状态下，权威 `vite:preloadError` 与 `lazyRoute` 随后的同一 rejection 都不再触发 chunk reload。普通旧 chunk 失败仍先同步派发可取消的草稿捕获事件，再写一次性 guard 并刷新；没有关闭既有恢复，也没有绕过存储失败时的草稿保护。

Firefox 取消原生离页确认时只派发 `beforeunload`，不派发 `pagehide`，因此从未进入抑制状态，随后真实旧 chunk 仍可立即恢复。从 back-forward cache 返回时由 `pageshow` 清除标记。这个边界不依赖 `focus` 猜测或无条件延时复位。

## Alternatives considered

- **只按错误文案过滤取消。** 改动表面最小，但 Firefox 的合法取消与真实模块加载失败使用相同的动态 import 错误文案，不能可靠区分。
- **取消所有自动 chunk reload。** 不会抢导航，但发布后旧页面无法自动取得新 chunk，也破坏已实现的同步草稿保护合同。
- **只让 E2E 等待项目页 ready。** 这是正确的测试前置条件，能避免测试主动制造竞争；但真实用户仍可在懒加载完成前离开，不能替代产品边界修复。

## Consequences

合法 document navigation 优先完成，离页时取消的旧模块不会再把浏览器拉回旧 URL。真实旧 chunk 的一次恢复、草稿同步捕获、捕获失败阻止 reload、持续失败不循环等既有合同不变。新增单测先复现导航取消仍派发 chunk 草稿事件并 reload，修复后 chunk recovery 全组 15 项通过；Linux Firefox 也保留快速连续 document navigation 与取消原生离页确认后的回归证据。
