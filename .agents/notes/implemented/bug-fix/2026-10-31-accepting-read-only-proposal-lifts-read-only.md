# Agent Note: 接受只读回合提出的方案时解除只读

## Problem

生产中出现过以下四类问题：

- 只读回合核对完后，用补问卡（`request_clarification`）提出几处具体改法，问作者现在要不要改。作者回复同意按方案修改，路由判为 `answer_pending_clarification`，服务端按 [语义路由与证据](2026-10-24-agent-semantic-routing-and-evidence.md) 的规则“read_only 取旧/新 OR”继承了只读，writer 回复不能改动文件，作者只好再要求一次执行。补问作答只能收窄、不能解除只读。
- 连续只读推演后，作者纠正 AI 对某条说明的理解，这句纠错被判为 hook_focus、可写，hook_designer 交给 writer 新建了一章。
- 新作品第一条消息里作者只贴了故事简介、没给指令时，writer 直接开写第一章，作者往往要叫停。
- 补问卡有正文时，问题和选项只给路由器看、不进模型回放，作者回复选其中一项时模型看不到选项。

## Decision

- 路由输出新增可选严格布尔 `accepts_previous_proposal`（缺失为 None，显式非布尔按技术失败处理，与 `clarify_first` 一致）。ROUTER_PROMPT 写明：上一轮路由为只读、上一轮在只读状态下提出了具体修改方案（包括用补问卡问要不要改），作者本轮接受该方案并要求执行时为 true，此时 agent / write_content / scope / requested_file_types 按方案那一步填写；纠错、补充说明、追问、继续讨论或又说别改时为 false。判断完全来自路由模型，服务端不看原话。
- `_resolve_turn_decision`：只有同时满足 `answer_pending_clarification`、可信旧状态匹配且旧 `read_only=true`、`accepts_previous_proposal=true`、本轮 `read_only=false` 时，按本轮路由执行（与“确认上一轮提议的下一步”同一语义）：不沿用只读回合的角色、只读 / 禁写正文和已交付范围，`scope_constraints` 只含本轮 scope，`followup_resolved` 不置位（大纲补写检查照常）。其他情况（旧状态可写、截停续接、本轮新增只读）保持原有继承与收窄不变。路由同时给出 `read_only=true` 与 `accepts_previous_proposal=true` 时不算接受方案：`_normalize_router_payload` 在把「不改文件又要写正文」纠正成可写之前先取消接受，接续时照常继承只读（接续回合的 `write_content=true` 只表示没有新增禁写正文限制，不能借这条单轮纠正绕过作者本轮的「先别改」）。
- ROUTER_PROMPT 补充正面规则：只读推演后的纠错或补充说明沿用只读；只贴材料没给指令时（新作品第一条消息也一样）走 planner + standard、write_content=false、read_only=true，scope 写“先问作者要用这些材料做什么”；hook_focus 只有作者本轮明确要写或改正文时 write_content=true。HOOK_DESIGNER_PROMPT 同步：只在作者本轮原话明确要正文时交给 writer，纠错和讨论只回答。
- 只贴材料没给指令的第一条消息：这是对 [作者的第一条消息：先写开头](../feature/2026-10-19-first-turn-writes-before-asking.md) 的局部例外（上面只贴简介的例子正是新作品第一条消息）。ROUTER_PROMPT「新作品的第一条消息」一节加一条指向选择规则 7；`service.first_message_hint` 中英文同步加一句：只贴了简介、梗概、设定或章节列表而没说要做什么时，读懂材料后用一句话问作者想怎么用，本轮不写正文。其余第一条消息仍是“不先提问、本轮交付”。选择规则 7 的“先问材料用途”和内容红线的说明回合都不是待续的原任务：作者下一句说出要做什么（如“按它写第一章”“那就按合规的写”）时，ROUTER_PROMPT 要求选 `new_or_changed_request`、按本轮交付物路由，即使上一轮留下了补问卡；路由器输入里的 [上一轮路由] 带着“先问”范围，模型据此分辨。否则补问作答会继承那一轮的只读、禁写正文和“先问”范围，作者第二条消息仍看不到正文。第一条消息的默认交付篇幅（“约800字”）和内容红线由同日并行分支 `fix/agent-prompts-20261031` 处理，本 note 不涉及。ROUTER_PROMPT 的红线一节只在整个交付物都越线时判 read_only=true；交付物只有一部分越线（如写第一章、其中要一段露骨描写）时照常路由，scope 末尾加“越线部分改为合规写法”，与 `CONTENT_RED_LINES`“说明不能写、给出合规写法、继续完成其余部分”一致，免费版的这一条消息不至于什么都没交付。
- `SessionLoader`：补问卡（`clarification_needed`）的 question / context / details 不论本轮有没有正文都追加进回放；其他状态卡摘要仍只在没有正文时追加。有正文时追加的补问卡与路由器 `pending_clarification` 共用同一组上限（问题与说明各 400 字、选项最多 8 条每条 160 字），模型和路由器看到同样的选项文本；只有状态卡、没有正文的轮次和 `status_card_text` 仍是完整卡片，与改动前一致。路由器读上一轮结尾时仍在 `[workflow_stopped]` 处截断，不把卡片文本当作助手结尾。

## Alternatives considered

- **只改提示词，让路由把“接受补问卡里的方案”判成 new_or_changed_request。** 最强理由：不加字段，服务端零改动，复用已有的“确认下一步按那一步路由”规则。没采用：上一轮有可信的 pending_clarification 时，提示词要求选 follow-up disposition；把同一状态时而判成补问作答、时而判成新请求，会让真正的补问作答（选 A/B、补人名）也有机会丢掉只读与范围继承。用独立字段把“接受方案”与“回答问题”分开，服务端可以只在旧状态只读时放行，其他继承不受影响。
- **补问作答时 read_only 只取本轮值（完全不继承旧只读）。** 最强理由：实现最简单，接受方案的情形直接通过。没采用：只读审查中途补问“看第几章？”，作者回“第3章”就会被放成可写，正是 [routing-continuity](2026-10-08-agent-routing-continuity.md) 要防的情况。
- **在补问卡上加“同意并执行”按钮，按钮点击才解除只读。** 最强理由：授权最明确，不依赖模型分类。没采用：需要新的前端交互与 wire 契约，[typed-authority](../architecture/2026-10-14-typed-authority-and-delivery-evidence.md) 已把确认卡列为另行处理；本批只做路由与提示词的局部修正。
- **第一条消息不设“只贴材料先问”的例外，仍按 10-19 直接写开头。** 最强理由：免费版每天 3 条，追问这一轮照样计 1 条，作者要到第 2 条才看到正文，这正是 10-19 推翻“先问再写”的原因。没采用：贴了完整梗概或章节列表的作者已经有了自己的方向，常见意图是按它写某一章或先整理大纲，直接写一段开头往往不是作者要的；作者为此叫停的轮次同样计次，比问一句更费额度。只在作者给了材料却没给指令时例外，一句题材、一个想法仍直接写。
- **补问卡选项只给路由器（维持原状），靠正文自己写出选项。** 最强理由：不改变回放内容、前缀缓存不受影响。没采用：生产中正文经常只写“请选一个方向”，选项只在卡片里；作者回复选其中一项时模型无从对照。

## Consequences

- 收益：只读回合提出方案后，作者一句接受即可执行，不必再要求一次执行；作者同时说别改时仍只读；纠错与只贴材料不再触发写正文；补问选项在后续轮次可见。
- 代价：解除只读依赖路由模型对“接受方案”的语义判断，误判为 true 会让本应只读的补问作答可写（仅限上一轮只读且本轮未说别改的情形，文件写入仍受版本并发与受保护稿件规则约束）；误判为 false 时退回原行为。合成回归只证明服务端组合规则，不能证明真实模型的分类质量，需要后续用合规样本观测 `followup_resolution=accepted_read_only_proposal` 的日志与作者后续纠正率。
- 额度取舍：只贴材料的第一条消息以一句提问结束，照样计 1 条（每天 3 条里的 1 条），作者第 2 条才看到正文。路由把“贴材料+一句题材”误判成只贴材料时，新作者首轮看不到正文。
- 自动化覆盖：`test_passthrough_guard_*` 两条只证明服务端照单执行 mock 决策；“纠错沿用只读”“只贴材料先问”“hook_designer 只在明确要正文时交接”三条提示词规则以及 first_message_hint 的新句子没有自动化覆盖。上线前需要另行授权用合规（合成）样本跑一次真实路由评测，覆盖：只读回合里的普通问题卡（如“看第几章？”答“第3章”）不得给出 accepts_previous_proposal=true、接受方案、纠错、只贴材料的第一条消息、只给一句题材的第一条消息；上线后观察 `followup_resolution=accepted_read_only_proposal` 的日志与作者随后的纠正/叫停。
- 有正文的补问轮次回放多出一段有上限的卡片文本，旧会话第一次加载时历史前缀变化一次，之后稳定。
- 本 note 局部替换 [2026-10-24 语义路由](2026-10-24-agent-semantic-routing-and-evidence.md) 中“补问作答 read_only 取旧/新 OR”的规则，以及 [2026-10-08 路由延续](2026-10-08-agent-routing-continuity.md) / [上一轮上下文](2026-10-08-agent-router-previous-turn-context.md) 中“正文非空时状态卡摘要不进回放”的取舍；旧 note 不改写。
