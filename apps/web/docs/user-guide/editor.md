# 在 ZenStory 中编辑正文与审阅修改

ZenStory 编辑器支持直接手写、把选中文字精确引用到对话、对局部文字执行“去AI味”、按段落审阅差异、保存当前文件和查看已保留的版本历史。最重要的边界是：**保存当前内容**与**生成一个历史版本快照**有关联，但不是同一件事。

想跟着完整情境练习，先读[用 ZenStory 写第一篇短篇：从创意到可修改的正文](https://zenstory.ai/docs/getting-started/first-project)；需要了解对话范围、上下文与文件工具，再看 [AI 创作助手](https://zenstory.ai/docs/user-guide/ai-assistant)。

## 直接手写并确认保存

在项目树中打开一个文件，而不是文件夹。编辑器提供标题输入框和纯文本内容区；输入或粘贴修改后，切换文件前先检查底部状态。

- 内容变化后，停止输入约三秒会安排一次自动保存。
- 需要立即提交时，点击“保存”，或按 `Ctrl/Command + S`。
- “未保存”“正在保存”和上次保存时间描述的是当前文件状态，不代表每次按键都有可恢复版本。
- AI 正在编辑同一文件时，编辑器会暂缓自动保存，避免较旧的本地整篇内容覆盖 AI 更新。

一次保存会写入当前标题和内容；版本则是另一份历史快照。较小的修改可能保存成功但不生成快照；版本历史额度用满时，内容也可能已经保存，只是跳过本次快照。做大幅改写前，先把不可替代的段落另存一份，并等界面显示已保存。不要把编辑器理解成离线恢复工具。

状态栏“字数”是工作台统计，不等于投稿平台口径。当前函数把支持范围内的中文字符逐字计数，把连续拉丁字母视为一个词，不计数字和符号；投稿前仍应按目标平台规则复核。

## 引用原文，提出局部修改

要改一小段，先在编辑器选中准确文本，再点浮动的“添加到对话”，或按 `Ctrl/Command + Shift + Q`。引用会携带来源文件进入对话。随后同时写清允许修改什么、哪些信息必须保持不变。

根据你希望的交付方式选择请求：

- **只要建议**：“只在对话中给一版替换段落，不要修改文件。”你审阅后再手动粘贴。
- **直接改文件**：明确文件名，并要求 Agent 只替换已引用段落。Agent 具备项目文件操作能力，不要假设每次写入前都会等待确认框。

焦点文件可以让后端在上下文预算内读取已保存内容，但引用能把具体目标明确交给助手。若编辑器里还有未保存修改，先保存或另行保留，再依赖焦点文件内容。

## 把“去AI味”与对话改稿分开理解

选中文字后，点击“去AI味”，或按 `Ctrl/Command + Shift + R`。这会调用专门的选区改写路径，然后以原文和候选替换进入差异审阅；它不同于在对话中讨论某段文字，也不是作者身份鉴定或检测器分数保证。本页只核对了源码流程，没有调用 AI 服务，所以下例是原创编辑教学，不是模型实测输出。

本例的既定事实是：内袋物件描述不符、林禾不知道原因、包未交付。

**修改前：**

```text
沈敏说完内袋里的物件，林禾立刻知道她在撒谎。
```

**修改后：**

```text
林禾没有把灰色帆布包递过去。
“内袋里放的什么，您再想一想。”
```

**修改理由：**不把错误答案升级为“撒谎”的结论，用未交包与再次核对推进场景，保留原因待定。追问是本例的措辞选择，没有新增人物背景或替作者决定真相。

## 逐处接受或拒绝差异

差异审阅可能由三条不同路径触发：

1. “去AI味”为选区产生候选替换；
2. 对话中的 `edit_file` 流式编辑在同时取得修改前后内容时进入审阅；
3. 保存冲突把服务端较新内容与尚未保存的本地候选放在一起审阅。

因此，审阅并不是所有 AI 写入之前统一出现的批准关卡。新建文件、其他工具结果，或没有成对原文与新文的编辑，可能走不同路径。

审阅队列按段落块展示修改。逐项比较旧文和新文，再选择“接受”“拒绝”，或重置决定。请特别注意：**待审项在完成审阅时默认采用**；不想保留的改动必须明确拒绝。确认批量方向时可用“全部接受”或“全部拒绝”，最后点击“完成审阅”或“应用更改”写入组合后的结果。

下面只列源码明确实现的快捷键，并且需要让审阅区域获得焦点：`Y` 接受当前项，`N` 拒绝，`U` 或 `R` 重置；方向键或 `J`/`K` 切换项目，`L` 在差异中定位。`Shift + Y` 全部接受，`Shift + N` 全部拒绝，`Enter` 完成审阅。要放弃候选改动，使用“全部拒绝”，确认各项状态后再完成审阅；不要把关闭面板当作已撤销。

## 保存冲突时先保住本地内容

如果 AI 或另一个标签页在你打开文件后又更新了它，本次保存可能因内容过期而被拒绝。ZenStory 会把本地输入保留在编辑器中，以服务端较新内容为基线、本地内容为候选打开差异审阅。先另存任何不可重写的本地段落；不要把刷新页面或清理浏览器数据当成第一步。比较两边，再决定逐项保留什么。

如果应用审阅结果时又发生新的并发写入，编辑器会基于最新服务端内容重新打开审阅，而不是静默丢掉刚整理的定稿。“内容已保存，但未生成版本快照”的额度提示则不是保存冲突：当前文件已经写入，只是本次保存没有新增一条保留历史。

## 已核对源码

源码核对日期：2026-09-12。以下说明针对固定版本，未进行真实账号操作或模型生成。

- [选中文字并把精确引用加入对话](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/SimpleEditor.tsx#L310-L354)
- [选区“去AI味”请求与进入差异审阅](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/SimpleEditor.tsx#L363-L445)
- [防抖自动保存、手动保存与编辑/审阅快捷键](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/SimpleEditor.tsx#L509-L675)
- [把焦点文件及相关文件载入上下文](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/agent/context/assembler.py#L168-L177)
- [对话文件编辑结束后在条件满足时进入审阅](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/hooks/useChatStreaming.ts#L1200-L1297)
- [待审、接受、拒绝状态，以及待审默认应用](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/contexts/ProjectContext.tsx#L221-L315)
- [逐项审阅的导航与快捷键](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/DiffReviewSplitView.tsx#L209-L244)
- [手动保存结果、过期写入冲突与版本额度提示](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/Editor.tsx#L297-L393)
- [应用审阅内容以及二次冲突后重新审阅](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/Editor.tsx#L397-L475)
- [中英混合内容的工作台字数统计函数](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/lib/documentChunker.ts#L26-L29)
