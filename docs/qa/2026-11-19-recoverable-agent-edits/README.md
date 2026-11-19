# 可恢复编辑与回收站：PR 截图

此分支仅保存合成 QA 截图，不是产品发布来源。新产品行为为按当前请求直接写作、使用操作记录撤销，并通过回收站恢复删除；它取代旧的作者来源拒绝策略。

| 文件 | 来源 | 证明范围 |
| --- | --- | --- |
| before-author-protection-real-staging.png | staging `19e08e7b4f373ad14229d0416d4212739490c837`，真实浏览器 + MiniMax | 旧策略下出现保护提示，后续工具仍改了正文 |
| after-compact-card-local.png | 本地 mocked Chromium，当前实现 | 整章替换只显示紧凑结果、字数和撤销入口，无强制大量 diff |
| after-undo-conflict-mobile-local.png | 本地 mocked Chromium，390×844 | 仅冲突时显示当前/恢复内容、实际标题与位置，默认保留当前 |
| after-delete-confirmation-local.png | 本地 mocked Chromium | 删除确认显示精确目标路径、类型和数量 |

本地截图使用合成响应，不能作为真实模型或 staging 已通过的证明。当前实现绑定产品提交 `1a63aab5b3861b318e40c43a27d56f6020f6229e` 与审查 manifest v5；后续真实 staging 截图将补充到此页。截图均经过人工查看，未包含账号邮箱、凭据或 token。
