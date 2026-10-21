# Agent Note: 页面翻译改写 DOM 后不再让编辑器或对话面板崩溃

相关：[前端 ErrorBoundary](2026-10-05-frontend-error-boundaries.md)。

Status: implemented

## Problem

PostHog 近 30 天 `NotFoundError: Failed to execute 'removeChild' / 'insertBefore' on 'Node'` 共 62 次 / 8 人，落在编辑器面板、对话面板，偶尔整页。按 prod-v2026.10.17.1 重建的 sourcemap 映射：编辑器「去 AI 味」按钮的图标切换（图标旁是裸文本标签）、保存状态三分支复用同一个 span、对话里工具卡片的标签 span（裸文本 `{label}` 旁有条件 span）。我们的代码没有直接改写这些 DOM；浏览器页面翻译（Chrome / Edge / 翻译插件）会把文本节点替换成 `<font>` 并可能移动行内元素，React 之后按旧位置删除或插入，DOM 抛错，ErrorBoundary 把整个面板替换成兜底页。happy-dom 下把文本节点替换为 `<font>` 后切换状态即可复现。

## Decision

- `lib/domMutationGuard.ts` 在应用启动时（`main.tsx`，渲染之前）包装 `Node.prototype.removeChild` 与 `insertBefore`：要删除的节点已不在该父节点下时跳过；参照节点已不在该父节点下时改为追加到末尾。正常 DOM 操作不变。
- 每种冲突每个页面只记一次 `dom_mutation_conflict`（`operation`，以及页面是否带翻译标记或 `<font>` 的 `translated`），并写 warn 日志，保留可观测性。
- 不逐处改写组件：翻译插件改写的位置不止被观测到的三处，逐处包 span 只能覆盖已知位置。

## Alternatives considered

- **只把已定位的裸文本包进 span、给保存状态分支加 key。** 最强理由：不碰全局原型，改动局部。被否：翻译还会移动行内元素，包 span 不能覆盖所有情况，其他位置的同类结构仍会崩溃。
- **在应用根节点加 `translate="no"`。** 最强理由：彻底避免冲突。被否：需要翻译界面的用户（错误中有 en-GB、vi-VN）会失去翻译能力。
- **保持现状，依赖 ErrorBoundary。** 最强理由：不掩盖任何 DOM 不一致。被否：用户每次都要重载面板，写作流程被打断；冲突本身来自浏览器而非我们的数据。

## Consequences

- 收益：开启页面翻译的作者不再因面板崩溃中断写作；冲突以事件形式继续可见，可按 `translated` 验证来源。
- 代价：被跳过的删除可能让翻译后的旧文本暂留，追加到末尾的插入可能出现顺序偏差，直到该区域重新渲染；真实的我方 DOM 不一致也会被同样处理，只能从 `dom_mutation_conflict` 事件发现。
