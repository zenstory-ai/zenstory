# Agent Note: 安装命令在卡片与表格内换行显示，不裁切不横滚

Status: implemented

## Problem

首屏要回答"怎么开始"，安装命令是首屏最重要的可复制对象。它同时出现在 hero、项目卡片和"What you need"表格里，在 390px 手机宽度下比容器宽。一条被裁切或需要横向滚动才能看全的命令，读者会当成页面坏了，而不是当成一条可以复制的命令。

## Decision

卡片和表格内的安装命令 may 换到第二行；只在空格处折行，一个 token（包名、flag、URL）never 被拆开。Copy 按钮占独立一列，不与命令文本争宽度。等宽字体是 JetBrains Mono，样式集中在 `apps/web/scripts/org-pages.css`。页面 body never 横向滚动；允许横滚的只有代码块和表格各自的容器。

来源：2685721 (#58)

## Alternatives considered

- **nowrap + 容器内横向滚动**（重设计所替换的组织页面规则）。最强理由：命令视觉上保持一行，和终端里的样子一致，也是多数文档站的默认。被否：手机上滚动条不可见，读者看到的是被截断的命令；390px 溢出检查把它计为破版。

## Consequences

- 收益：任何宽度下命令完整可见、可整段复制；390px 溢出检查在 en/zh/no-JS/dark 四种模式下通过。
- 代价：长命令在窄屏上占两行，卡片高度不再整齐。后续任何含命令的新组件 must 沿用同一规则（只在空格处折行 + Copy 独立列），否则又回到裁切。

## Verification

`cd apps/web && pnpm test:geo` 的 390px 溢出检查针对生成器输出的命令段落；DESIGN.md「Responsive behavior」记录同一约束。
