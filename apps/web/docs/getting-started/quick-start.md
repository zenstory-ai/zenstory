# ZenStory 工作台快速开始：从空项目到第一份可修改提纲

本页带你走完第一轮：登录工作台、建好项目、认识文件区和对话区，再请 AI 先给一份可以改的提纲。

在线入口：[app.zenstory.ai](https://app.zenstory.ai)。

## 1. 登录并创建项目

1. 打开[登录页](https://app.zenstory.ai/login)，登录后进入[工作台](https://app.zenstory.ai/dashboard)。还没有账号，见[账号注册与登录](https://zenstory.ai/docs/getting-started/installation)。
2. 选择作品类型：长篇小说、短篇小说或短剧剧本。它决定项目的基础文件结构，之后仍可在项目里继续整理文件。
3. 想法框可以留空，直接按回车或点「开始创作」就能建好项目。想先熟悉界面、再自己发第一条请求时，留空更合适。
4. 写了想法的话，进入项目后看看对话和文件里实际生成了什么。

## 2. 先认识三个工作区

- **左侧文件区**：浏览项目文件树，点击文件后在编辑器打开。
- **中间编辑器**：阅读和修改当前文件，留意保存状态。
- **右侧对话区**：向 AI 提要求，查看回复和工具执行结果。

AI 可以查询、创建、编辑和删除项目文件，写入前不一定会先问你。只想讨论时，在请求里写明「暂不创建或修改文件」；这是给 AI 的要求，不是只读开关。准备写进文件时，再说清目标文件、允许改动的范围和停在哪里。

## 3. 用一条窄请求开始

下面是一个原创示例：

```text
我想写一篇现实悬疑短篇。先只给三场景提纲，暂不写正文，也不要创建或修改文件。

现在五点四十分，车站失物招领窗口六点关闭。值班员林禾处理沈敏对灰色帆布包的认领：
她准确说出包的外观，却说错了内袋里的一件物品。不要直接认定她偷窃，也不要替我决定真相。林禾不能仅凭口头认领交付物品。

每个场景写：地点、人物当下目标、可见行动、信息变化、场景结束时留下的问题。
最后列出两个必须由我决定的故事选择，然后停止。
```

这条请求把交付物、证据边界和停止点写清楚。先判断三场是否有递进，再决定沈敏为什么说错、林禾是否隐瞒信息，以及下一步要不要写正文。

## 4. 打开目标文件，再补相关上下文

准备把提纲写入项目时，先在文件树新建或打开目标大纲文件，再说「把刚确认的三场提纲写入当前文件」。请求会带上这个文件已保存的内容；AI 每轮能读的上下文有上限，不会自动带入整个项目。

素材条目可以附加到对话；需要角色卡或旧章节时，引用关键文字或点名让 AI 读取。只改一段时，引用原文并写明文件名和段落。上下文越精确，AI 越不容易改错文件或扩大范围。

## 5. 保存、历史版本与导出

编辑器会自动保存，留意界面上的保存或冲突提示。历史版本可以用来查看和恢复；版本数到了套餐上限后，正文照常保存，只是不再记新版本，重要节点请另存一份。

项目顶部的下载入口会把正文和剧本文件按章节顺序合成一个 TXT。它不是整个项目的备份：大纲、角色卡、设定、对话和历史版本都不在里面。

焦点文件、素材附件与文本引用的区别，见[AI 创作助手](https://zenstory.ai/docs/user-guide/ai-assistant)。

## 下一步

- 跟着[用 ZenStory 写第一篇短篇：从创意到可修改的正文](https://zenstory.ai/docs/getting-started/first-project)完成一个更完整的例子。
- 看看 [ZenStory 工作台介绍](https://zenstory.ai/workbench)，了解它和在 Agent 里用 skill 包有什么不同。
- 用[写作环境比较](https://zenstory.ai/compare/writing-workflows)在 ZenStory 工作台、Oh Story 和 Oh Story DSH 之间选择。

## 已核对源码

源码核对日期：2026-09-12。

- [Dashboard 空灵感快速创建与跳转](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/pages/DashboardHome.tsx#L511-L547)
- [焦点文件、附件、素材与引用进入请求的方式](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/ChatPanel.tsx#L1151-L1177)
- [AI 可用的项目文件工具](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/agent/tools/registry.py#L17-L42)
- [版本额度满时保存正文但跳过快照](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/api/files.py#L899-L927)
- [桌面项目页的导出入口与版本历史入口](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/Header.tsx#L228-L249)
- [TXT 导出的文件类型、排序与合并内容](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/export_service.py#L110-L203)
- [服务端读取焦点文件与附加资料](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/agent/context/assembler.py#L145-L177)
- [项目素材的附加入口](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/sidebar/FileTreePane.tsx#L552-L575)
