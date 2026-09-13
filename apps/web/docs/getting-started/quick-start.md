# ZenStory 工作台快速开始：从空项目到第一份可修改提纲

本页只带你走通第一轮：进入 Dashboard、创建项目、认识文件与对话区，再让 AI 先交付一份可审阅的提纲。不要把“创建项目”理解成自动生成第一章，也不要把版本历史当成无限备份。

托管写作工作台入口：[app.zenstory.ai](https://app.zenstory.ai)。

> 本页依据文末固定版本源码说明操作路径，没有创建账户或调用 AI。注册是否开放、邀请码是否必填等，以当前页面为准；详见[账号注册与登录](https://zenstory.ai/docs/getting-started/installation)。

## 1. 登录并创建项目

1. 打开 [登录页](https://app.zenstory.ai/login)，登录后进入 [Dashboard](https://app.zenstory.ai/dashboard)。
2. 选择项目类型。它决定项目的基础文件结构，之后仍可在项目里继续整理文件。
3. 灵感输入框可以留空：直接按 Enter（不要按 Shift）或点击“开始创作”，也能建立项目。想先熟悉界面、再自己控制第一条请求时，留空更合适。
4. 如果填写灵感，不要预设系统必然生成大纲或正文；进入项目后查看实际对话与文件结果。

## 2. 先认识三个工作区

- **左侧文件区**：浏览项目文件树，点击文件后在编辑器打开。
- **中间编辑器**：阅读和修改当前文件，留意保存状态。
- **右侧对话区**：向 AI 提要求，查看回复和工具执行结果。

AI 具备查询、创建、编辑和删除项目文件的工具。某些任务可能追问或要求确认，但不要假设每次写入前都会出现确认框。只想讨论时，把边界写进请求，例如“暂不创建或修改文件”；这不是强制只读开关。准备落盘时，再明确目标文件、允许的改动和停止点。

## 3. 用一条窄请求开始

下面是原创示例，不是模型运行记录：

```text
我想写一篇现实悬疑短篇。先只给三场景提纲，暂不写正文，也不要创建或修改文件。

现在五点四十分，车站失物招领窗口六点关闭。值班员林禾处理沈敏对灰色帆布包的认领：
她准确说出包的外观，却说错了内袋里的一件物品。不要直接认定她偷窃，也不要替我决定真相。林禾不能仅凭口头认领交付物品。

每个场景写：地点、人物当下目标、可见行动、信息变化、场景结束时留下的问题。
最后列出两个必须由我决定的故事选择，然后停止。
```

这条请求把交付物、证据边界和停止点写清楚。先判断三场是否有递进，再决定沈敏为什么说错、林禾是否隐瞒信息，以及下一步要不要写正文。

## 4. 打开目标文件，再补相关上下文

准备把提纲写入项目时，先在文件树新建或打开目标大纲文件，再说“把刚确认的三场提纲写入当前文件”。当前焦点会把文件 ID、类型和标题带给请求，但不要假设整个项目会自动、完整进入上下文。

如果任务还依赖角色卡、旧章节或素材，请主动附加相关文件；只改一段时，引用具体文本，并在请求里写明文件名和段落。上下文越精确，越容易避免 AI 改错文件或扩大范围。

## 5. 保存、历史与导出边界

编辑器会提交保存；检查界面上的保存或冲突提示。版本历史可以用于查看或恢复已保留的版本，但快照生成与版本额度限制适用。版本额度用完时，正文仍可能保存成功，只是不再产生新的版本快照，因此重要节点应另做外部备份。

项目顶部的下载入口当前导出一个合并的 TXT，内容来自未删除的正文 `draft` 与剧本 `script` 文件，排序参考章节/分集序号和文件排序信息，并以创建时间等信息兜底。它**不是整项目备份**：大纲、角色、设定、聊天与全部历史不在这份正文 TXT 中。

## 下一步

- 跟着[用 ZenStory 写第一篇短篇：从创意到可修改的正文](https://zenstory.ai/docs/getting-started/first-project)完成一个更完整的例子。
- 查看[托管工作台说明](https://zenstory.ai/workbench)，确认它与本地 Agent 工作流的边界。
- 用[写作工作流比较](https://zenstory.ai/compare/writing-workflows)选择 Hosted ZenStory、Oh Story 或 Oh Story DSH。

## 已核对源码

源码核对日期：2026-09-12。

- [Dashboard 空灵感快速创建与跳转](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/pages/DashboardHome.tsx#L511-L547)
- [焦点文件、附件、素材与引用进入请求的方式](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/ChatPanel.tsx#L1151-L1177)
- [AI 可用的项目文件工具](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/agent/tools/registry.py#L17-L42)
- [版本额度满时保存正文但跳过快照](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/api/files.py#L899-L927)
- [桌面项目页的导出入口与版本历史入口](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/Header.tsx#L228-L249)
- [TXT 导出的文件类型、排序与合并内容](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/export_service.py#L110-L203)
