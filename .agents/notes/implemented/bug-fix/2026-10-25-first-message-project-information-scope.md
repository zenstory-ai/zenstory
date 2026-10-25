# Agent Note: First-message project information stays metadata-only

关联：[作者第一条消息先写开头](../feature/2026-10-19-first-turn-writes-before-asking.md)、[移除 AI 记忆提议](../feature/2026-10-25-remove-memory-proposals.md)、[语义路由与交付证据](2026-10-24-agent-semantic-routing-and-evidence.md)。

## Problem

main `572464fd` 的真实 MiniMax 探针中，新作品第一条原话只有“记住：女主要到第五章才出场；主角左手有旧伤，阴天会疼。”。Router 把人物和剧情事实落进“其余情况”的默认开篇，输出 `writer + quick`、`write_content=true`、`requested_file_types=["draft"]`；writer 在追加 notes 后又创建并写入 877 字第一章。此前 #314 的 version-1 配置探针正确保存记忆，不能覆盖这次使用 version-3 配置与 main 源码的失败。

根因是第一条消息的两个提示面都把“其余情况”写得过宽：`ROUTER_PROMPT` 把没有正文范围的消息一律落到默认开篇，`service.first_message_hint` 也用 `Otherwise / 其余情况` 要求补主角、背景、冲突和开头。项目资料里出现人物或剧情事实因此被误当成创作意图。

## Decision

- 项目资料持久化是独立交付物。新作品第一条只要求保存或更新项目资料时，由现有 Router 语义判断为 `writer + quick`、`write_content=false`、`read_only=true`、`requested_file_types=[]`；scope 只描述本轮要保存的资料。这里的 `read_only` 表示本轮没有写作文件修改授权：资料涉及人物或剧情不扩张成正文、人设、设定文件或大纲授权。
- 复用现有只读工具守卫：`create_file`、`edit_file`、`delete_file`、`rename_file`、`parallel_execute` 在 SDK 回调层拒绝执行，`update_project` 不属于写作文件工具，仍可由初始 writer 执行。范围提示和工具拒绝提示只允许它更新作者明确点名的项目资料字段，不附带改名、摘要、风格、阶段或任务更新；没有资料更新请求时仍直接回答。
- 第一条消息的默认开篇只用于作者表达了创作意图、但没有限定交付范围的情况。普通“一句话题材”“写个……”仍直接交付开头，不恢复首轮追问。
- 中英文 `first_message_hint` 同步说明：只保存项目资料时完成该资料更新，不附送任何写作文件，也不额外发起作者没要求的资料更新；已有正文、明确大纲/设定交付、只问问题、只读、明确正文范围等原有分支不变。
- 不使用关键词、正则或额外分类器；仍由既有 Router 模型输出结构化范围。记忆的追加、撤销、原话保留和 `update_project` 合同不变。

## Alternatives considered

- **把所有包含人物或剧情设定的首条消息都当作资料保存。** 最强理由是能确定性避免这次误写。未采用：人物和剧情设定也经常就是作者的一句话创作起点，会破坏“首轮先写”的主路径；按文本类别推断意图也违反语义边界。
- **在服务端用“记住/记下”等词识别资料保存。** 最强理由是改动小且这条样本可稳定命中。未采用：同义表达和跨语言无法穷举，否定或引用也会误判，并形成新的自然语言关键词权限判断。
- **第一条消息一律先问作者要保存还是创作。** 最强理由是不会猜错。未采用：每天三条免费额度下会重新引入首轮空转，且破坏已有普通创作首条直接写的产品决定。
- **新增一次分类模型调用或 UI 标志。** 最强理由是可以把资料保存做成独立通道。未采用：当前 Router 已有完整结构化合同，额外调用增加延迟和成本；新 UI 协议超出这次真实事故的最小修复。

## Consequences

任务板-only handler 依据已有正文单调前推 current_phase 是现有派生行为，本次保留；提示限制的是模型提交哪些更新参数，不虚称任何资料请求都使其他持久化字段逐字不变。纯 notes 更新不经过该任务板分支，回归核对 notes 真正保存且 name/summary/writing_style/current_phase 原值不变。

- 新作品第一条只要求记忆资料时，Router 有明确的 metadata-only 结构合同；writer 可以调用 `update_project(notes=...)`，所有写作文件工具都在执行前被拒绝，不会再从默认首轮提示获得开篇授权。
- 普通创作第一条仍按作品类型直接写开头；既有跨轮续接、明确大纲/设定文件及历史范围继承不变。
- Router 对“只保存资料”的识别和 `update_project` 只传作者要求的字段仍是模型遵循的语义合同，目标真实模型探针是发布证据；文件修改边界由既有 `read_only` 工具守卫机械执行。`read_only=true` 会清空后续计划、禁止文件纠偏与自动审稿，正符合单次 metadata-only 交付。明确要求 outline/lore/正文或文件重命名、删除时 Router 必须输出 `read_only=false`，原有文件交付路径不变。
