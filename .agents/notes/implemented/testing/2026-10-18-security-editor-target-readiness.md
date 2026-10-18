# 安全正文用例等待真实编辑器

## Problem

[安全证据断言](2026-10-18-security-chat-evidence-contracts.md)修正后，最终 `e241d139` 的 full E2E 仍有一次 Firefox 正文 XSS 用例在 `fill()` 后立即读到空字符串，重试通过。需要先区分真实输入丢失与错误控件定位，不能将产品覆盖输入当作测试等待问题掩盖。

## Decision

在安装好的 Chromium 中挂起新文件的首次 GET，按原用例创建并选择文件。此时编辑器显示 loading，没有正文 textarea；原 `textarea.first()` 的可见断言却通过，实际目标是 `chat-input`。填入的完整 XSS 文本保留在聊天输入框。释放 GET 后，正文 textarea 在 DOM 中排到聊天输入框之前，同一 locator 的下一次读取因此得到新编辑器的空字符串。定位在两次调用间改变，未发生正文输入被产品覆盖。

正文 XSS 与特殊字符用例的首次输入及重载读取均定位 `[data-editor-scroll-container="true"] textarea`，等待该实际正文控件可见。保留原始输入、真实 PUT、独立 GET、重载正文、对话框和 CORS 断言，不增加固定延迟或放宽空值。

## Alternatives considered

- **在原泛化 locator 上重试读值。** 最强理由是改动小，但填入聊天框后目标会永久改变，等待不能证明正文输入，故不采用。
- **把输入后清空当作产品 bug 修改编辑器。** 最强理由是先保护作者文字；但受控响应实验确认文字从未进入正文，真实正文输入的即时值、PUT、独立 GET 和重载全部逐字保持，故不修改产品。
- **仅等待请求结束但仍用页面第一 textarea。** 最强理由是可以消除当前窗口；但控件定位仍依赖无关 DOM 顺序，故直接使用已有正文容器语义。

## Consequences

仅修复测试定位，保留现有五秒控件等待、自动保存时序和所有文本保真断言。受控复现和截图存于本地 `runtime-security-race` 输出目录，包含红色目标切换证据及真实正文控件的保存／重载正例，不包含生产用户或服务数据。该 SQLite 实验不是 PostgreSQL 或 staging 证明。

## Verification

受控 Chromium 实验稳定复现旧 locator 填聊天框、随后读到空正文；限定正文容器后的即时输入、真实 PUT、独立 GET 和重载均保持原文。修复后的整份 security spec 在本地 Chromium 的 16 项场景及一次认证 setup 全部通过，零失败、零重试。定向 ESLint 与 diff 检查通过。尝试的 16 项 Firefox 均在启动浏览器前因本机缺少 revision 1509 可执行文件失败，不是产品回归；未安装或替换运行时，不将其称为本地通过。最终双引擎证据由精确合并 SHA 的 GitHub full 提供。
