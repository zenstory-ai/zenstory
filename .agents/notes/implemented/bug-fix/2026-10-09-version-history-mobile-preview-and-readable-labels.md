# Agent Note: 文件历史在手机上整屏预览；审阅后保存的版本不再标「AI 编辑」；AI 修改说明改为处数

Status: implemented

本 note 修改 `bug-fix/2026-10-09-file-restore-backs-up-current-content.md` 中「agent edit_file 写的 `AI 编辑: 替换, 追加 等 N 处修改` →『AI 修改：替换、追加 等 N 处』（操作名逐个翻译，有认不出的操作名时不显示）」这一条：改为只说改了几处。其余说明文字的映射不变。

## Problem

PR #163 新用户审计（2026-10-09，`shots/shortstory-35-history.png`、`shortstory-36-history-view.png`）：

1. P2-17：390 宽的手机上点「查看内容」，历史弹窗左右分栏（列表 1/3、预览 2/3），版本列表被挤成一字一行，作者没法在手机上挑要恢复的版本。
2. P3-21：作者在编辑器里逐段审阅 AI 改动后点「完成审阅」，保存的版本 `change_type=ai_edit`、`change_source=user`，徽标仍写「AI 编辑」（灰色），和「AI 修改（已审阅）」放在一起，看不出这是作者自己定的稿；AI 改动的说明写成「AI 修改：替换、替换、替换 等 4 处」，「替换」是编辑工具的操作名，对作者没有信息量。

## Decision

- **手机整屏预览**：`FileVersionHistory` 打开预览或对比时，版本列表在 768px 以下隐藏（`hidden md:block md:w-1/3`），预览/对比占满弹窗（`w-full md:w-2/3`）；关掉预览回到列表。768px 及以上保持左右分栏。预览头部加「恢复到此版本」，看完内容可以直接恢复，仍走原来的应用内确认框。
- **审阅后保存**：`change_type=ai_edit` 且 `change_source=user` 的版本，徽标显示「审阅后保存」（`versions:types.reviewed`）；AI 自己写入的（`change_source=ai`）仍是「AI 编辑」。服务端写入的类型与说明不变，只改显示。
- **说明改为处数**：`AI 编辑: <操作列表>[ 等 N 处修改]` 显示为「AI 改了 N 处」：有「等 N 处」用 N，否则数列出的操作个数。认不出的操作名也照样计数，不再整行隐藏。`versions:summary.aiEditOps/aiEditOpsMore/opsSeparator/ops.*` 删除，新增 `versions:summary.aiEditCount`。
- 顺带把该组件里几个英文 `defaultValue` 改成与 zh 文案一致。

## Alternatives considered

- **手机上把列表和预览上下叠放。** 最强理由：同时看到列表和内容。被否：弹窗最高 80vh，两块各占一半时列表只剩两三行、预览也只有几行正文，都不够用；整屏预览加「恢复到此版本」能完成「看内容→决定恢复」这一步。
- **按操作类型分组说明（「改写 3 处、续写 1 段」）。** 最强理由：信息更多。被否：服务端只在说明里列前几个操作再写「等 N 处」，分组后的数字会不完整；行数增减已在同一行显示，处数加增减行数足够判断改动大小。
- **在服务端改写版本说明。** 最强理由：所有客户端一致。被否：历史数据里已存了大量旧格式说明，前端仍要兼容；显示层翻译是现有约定（见上面链接的 note）。

## Consequences

- 收益：手机上能看清每个版本的内容并从预览直接恢复；作者审阅定稿的版本有了自己的标签；说明从操作名改成作者能理解的「改了几处」。
- 代价：手机上预览时看不到列表，要关掉预览才能切换版本；「AI 改了 N 处」不再区分替换、追加、删除。

## Verification

- `pnpm --dir apps/web exec vitest run src/components/__tests__/FileVersionHistory.test.tsx src/lib/__tests__/versionSummary.test.ts src/components/__tests__/VersionHistoryPanel.test.tsx`：预览打开时列表在窄屏隐藏、预览占满、从预览发起恢复弹出确认、关闭后列表回来；审阅后保存的版本显示「审阅后保存」而 AI 写入的仍是「AI 编辑」；「替换、替换、替换」显示为「AI 改了 3 处」，中英文 locale 一致。
