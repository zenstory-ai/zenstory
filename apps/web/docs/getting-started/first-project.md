# 用 ZenStory 写第一篇短篇：从创意到可修改的正文

在 ZenStory 在线工作台中，先把故事事实与待定想法分开，再用项目文件保存人物和场景提纲，按场起草、引用原文做局部修改，最后导出正文。这篇教程用一个失物招领故事示范完整路径，而不是让 AI 一次写完一本书。

使用 [app.zenstory.ai 工作台](https://app.zenstory.ai/dashboard)，不需要先安装 Oh Story 或编程 Agent。想先了解按钮位置，可看 [快速入门](https://zenstory.ai/docs/getting-started/quick-start)。下方人物、文件名和对话均为原创教学示例，不是一次真实模型运行的记录。

## 1. 创建一个空的短篇项目

1. 登录后打开 Dashboard，在可用类型中选择「短篇小说」。
2. **这次先不填灵感**，点击「开始创作」。这样先建立项目，再自己发送范围明确的写作请求。快速创建使用默认项目名；进入后可从项目切换器的「编辑项目名称」改为「关窗之前」。
3. 创建成功后进入项目页面：左侧管理文件，中间编辑选中的文件，右侧与 AI 对话；窄屏需要切换对应区域。

若创建时填写了灵感，页面可能在进入项目后自动发送构思请求。它不是“自动完成大纲和第一章”的承诺；不要一边等待它，一边重复发送同样的任务。空灵感创建不会触发这条灵感自动发送路径。

中文默认模板如下。类型主要帮助组织文件，不是固定产出字数或成稿质量保证；已有项目的文件夹可能已经调整。

| 类型 | 默认文件夹 | 这次怎样用 |
| --- | --- | --- |
| 长篇小说 | 设定、角色、素材、大纲、正文 | 多章作品按大纲逐章推进 |
| 短篇小说 | 人物、构思、素材、正文 | 本例使用这一类型，先做三个场景 |
| 短剧剧本 | 角色、设定、素材、分集大纲、剧本 | 按分集和场景组织剧本，不等于生成视频 |

## 2. 先给故事边界，不把猜想写成设定

本例的起点是一份简短的事实卡。你可以把它换成自己的题材，但保留“确定了什么、还没决定什么”。

| 已确定 | 暂不确定 |
| --- | --- |
| 车站失物招领窗口晚上六点关闭，现在五点四十分 | 沈敏为什么报错物件 |
| 林禾负责窗口；沈敏来认领灰色帆布包 | 她与真正失主是什么关系 |
| 沈敏说对外观，却说错内袋中的物件 | 包最终交给谁 |
| 林禾不能只凭口头认领交付物品 | 结局可以讨论，但不能擅自写成既定事实 |

在右侧对话框发送：

```text
我想写现实题材短篇《关窗之前》，用林禾的有限视角。
车站失物招领窗口六点关闭，现在五点四十分。沈敏来认领灰色帆布包，
外观说对了，却说错内袋物件。林禾不能仅凭口头认领把包交出去。

先只在对话中给出三个场景的提纲，每场写：人物目标、阻力、发生的变化。
把已确定事实与建议新增的设定分开。暂不确定沈敏报错的原因和最终归属；
不要擅自认定她偷窃，不加入超自然解释。先不要写正文，也不要修改文件。
```

“先不改文件”是给 Agent 的任务要求，不是产品提供的只读权限开关。ZenStory 的 Agent 有文件操作能力；发送后既看回复，也留意文件树和操作结果。若你希望完全自己控制正文，先让它只给建议，再手动采用。

## 3. 确认提纲，再留下可继续写的项目文件

阅读提纲时，先做创作选择：每场是否真的改变了局面？林禾知道的事情是否超出当前证据？最后一场是否回答了你想讨论的问题？把你选定的原因和结局补充给 AI，而不是让下一轮把所有备选都当成事实。

确认后，可以请求 AI **只创建下面这些文件**，或通过文件树的新建入口自己建文件、粘贴已确认内容：

| 文件夹 / 示例标题 | 文件类型 | 应放什么 |
| --- | --- | --- |
| 人物 / 林禾 | 角色 `character` | 目标、职责、能知道什么，避免写入其他人物的秘密 |
| 人物 / 沈敏 | 角色 `character` | 已确认背景；尚未确定的动机明确标注待定 |
| 构思 / 三场景提纲 | 大纲 `outline` | 已选方案、各场变化、读者何时知道关键事实 |
| 正文 / 第1场 认领 | 正文 `draft` | 实际叙事文字，不把分析和改稿意见混进去 |

这些标题是你要创建的文件，不是系统自动生成的文件。**文件类型也重要**：仅把文件命名为“正文”，不等于它就是可导出的正文类型。素材文件夹可以放你有权使用的参考笔记；这个小练习不需要上传整部参考小说。

## 4. 打开目标文件，明确这次用哪些上下文

点击「第1场 认领」打开编辑器。发送消息时，工作台会带上焦点文件的身份，服务端据此读取已保存内容，但上下文仍受预算约束，不等于每次都装入整本书。素材可以附加到对话；普通人物与提纲文件可通过选段引用或在请求中明确命名来提供依据，不能假定每种文件都有素材的附加按钮。

先保存刚改过的人物与提纲，引用这次需要的关键设定，并要求先读取相关文件。然后发送：

```text
先读取已确认的《三场景提纲》和人物文件，只写《第1场 认领》。
约600—900个中文字，以林禾有限视角写到沈敏报错内袋物件为止。
用问答和动作表现紧迫感；不要提前揭露报错原因，不替其他人物解释内心。
将正文写入已打开的正文文件，不新建同名副本，不继续写第2场。
缺少关键设定时先列出问题，不把猜测写进人物档案。
```

字数是创作目标，不是精确输出保证。回复里出现了一段文字，也不等于已经写入目标文件；打开文件看实际结果。如果只是建议文本，你可以手动采用，或明确请求写入指定文件。

## 5. 引用问题段落，而不是让 AI 重写整篇

假设初稿出现“林禾知道沈敏一定在撒谎”，而你希望暂时保留歧义：

1. 在编辑器选中这个句子及必要上下文，用选区工具栏的引用入口加入对话。
2. 说明它违背哪条已确认事实，以及允许改动的范围。
3. 如果进入差异审阅，逐项接受或拒绝，再完成审阅；其他文件操作不一定都经过同样的逐项确认界面。

```text
只修改我引用的段落。林禾只知道物件描述不符，还不知道沈敏为何报错。
删掉对“撒谎”的断定，改为能观察到的动作或追问；保留包未交付这一结果。
先在对话中给出替换建议，不改文件，不改人物动机，不动其他场景。
```

满意后把建议应用到选定段落，再检查前后衔接。如果这次实际改变了人物动机或结局，另行更新人物文件与提纲；不要假定正文改动会自动同步成所有设定。

## 6. 保存、保留重要内容，再导出正文

普通编辑支持自动保存和「保存」入口；离开前查看保存状态。AI 正在操作同一文件时，先等任务结束，避免同时进行互相覆盖的修改。发生冲突时比较两份内容，不要直接刷新丢掉未保存的本地文字。

历史快照可以帮助比较和回滚，但受快照策略与版本额度影响，不是每次按键的永久备份。遇到“正文已保存但未生成版本”一类提示，要区分正文保存与历史快照；重要原稿请另留副本，也不要用删除文件来测试恢复能力。

在项目页顶部的「导出」入口下载 TXT；移动端从菜单进入。当前导出合并未删除的正文 `draft` 和剧本 `script` 文件，包含标题与正文，排序参考章节/分集序号及文件排序信息，**不是只按创建时间**。下载后检查场景顺序、结尾和缺段。

导出不包含整套人物、素材、聊天与版本历史，不能当作完整项目备份。没有可导出的文件、权限或当前套餐格式限制时，以页面错误提示为准；本文不承诺 DOCX、EPUB 或自动投稿。

更详细的焦点文件、素材附件与文本引用区别，见[AI 创作助手](https://zenstory.ai/docs/user-guide/ai-assistant)。

## 接下来写什么

拿到一场可用正文后，再继续第二场：复用已确认文件，写清本场要发生的变化和停止位置。你也可以先自行写作，只在卡住或修订时使用 AI。

- 想判断该用网页工作台还是本地技能：[写作工作流对照](https://zenstory.ai/compare/writing-workflows)。
- 想看局部改稿的前后对照：[减少套路化表达](https://zenstory.ai/oh-story/revise-ai-prose)。其中的编辑原则可以借鉴，但 Oh Story 的文件流程并非在线工作台按钮。
- 账号与配置入口：[注册和登录](https://zenstory.ai/docs/getting-started/installation)。账号、配额与模型服务可用性以实际部署为准。

## 实现依据

源码核对于 2026-09-12。以下固定版本解释页面操作；不代表线上账号、模型输出或当前套餐已经实测。

- [空灵感快速创建](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/pages/DashboardHome.tsx#L511-L547)
- [类型与创建按钮](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/pages/DashboardHome.tsx#L653-L720)
- [中英文默认项目模板](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/config/project_templates.py#L15-L135)
- [焦点文件、附件与引用](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/ChatPanel.tsx#L1151-L1177)
- [初始灵感发送条件](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/ChatPanel.tsx#L1238-L1287)
- [选区引用](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/SimpleEditor.tsx#L348-L354)
- [自动保存与快照意图](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/SimpleEditor.tsx#L509-L554)
- [冲突与保存成功边界](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/Editor.tsx#L327-L394)
- [差异审阅写回](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/Editor.tsx#L397-L446)
- [用户版本额度](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/file_version_service.py#L536-L555)
- [项目导出入口](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/Header.tsx#L228-L241)
- [导出文件类型与排序](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/export_service.py#L110-L143)
- [TXT 合并内容](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/services/features/export_service.py#L197-L203)
- [导出限制与下载响应](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/api/export.py#L87-L143)
- [服务端读取焦点文件与附加资料](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/server/agent/context/assembler.py#L145-L177)
- [项目素材的附加入口](https://github.com/zenstory-ai/zenstory/blob/0cb3d51c8f92a1856b99ef974b94fa81b7da6cc3/apps/web/src/components/sidebar/FileTreePane.tsx#L552-L575)
