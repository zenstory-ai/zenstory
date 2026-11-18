# Bug-Fix Note: 单循环 agent 上线首日故障修复（历史压缩口径对齐、NUL 字节清洗、低字符集复读与 MiniMax 分隔符）

Status: implemented

关联决策：[single-loop-agent](../architecture/2026-11-16-single-loop-agent.md)、[stop-output-definition-and-round-outcome](../architecture/2026-10-09-stop-output-definition-and-round-outcome.md)。

## Problem

单循环 agent 上线生产（`prod-v2026.11.18.2`）约 15 小时、105 轮真实请求后，日志与落库数据暴露出五处具体故障：

1. **长连载会话在 ~30 万实测 token 处被逐轮滑窗砍掉早期消息，且永远到不了轮间压缩阈值**：`should_compact` 读供应商实测 `prompt_tokens`（默认门限 `600_000`），而 `service.py` 的 `bounded_items(..., AGENT_LOOP_RUN_HISTORY_MAX_TOKENS=800_000)` 与 `compact_history` 输入上限（`900_000`）用本地 `tiktoken(cl100k_base)` 估算。`cl100k_base` 对中文按 UTF-8 字节拆成约 1.5～2.2 token/字，比 MiniMax M3.1-Flash / DeepSeek V3.2 实测（约 0.55～0.70 token/字）虚高 2.5～3 倍。结果中文长会话在实测约 28～39 万 token 时 `cl100k_base` 已破 80 万，`service.py` 每轮从开头丢掉十几到四十多项旧历史，导致：① 作者开篇设定与早期章节回执被静默截掉；② 截断使历史首部每轮移动，前缀缓存整段失效；③ 实测 `prompt_tokens` 被压在 ~39 万上下，永远跨不过 `600_000` 的 `should_compact` 门限，压缩形同死锁。同时 `IntraRunToolOutputTrimmer` 默认阈值（`600_000` 字符，约 30 万实测 token）低于轮间压缩门限，且未将顶层 `write_chapter` 列入可折叠写工具。
2. **模型输出夹带 NUL 字节（`\x00`）触发 PostgreSQL `DataError`，且未落库的流式正文仍被计为已产出**：两次请求里模型在长纲要或回复中吐出 `\x00`，PostgreSQL `TEXT` 字段拒绝含 `0x00` 的字符串（`psycopg.DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes`）。其中一次新建空文件后已流式发出 `file_content` 帧，末尾保存因 `DataError` 失败并报 `ERR_AGENT_FILE_SAVE_FAILED`，但 `StreamBillingTracker` 在收到 `file_content` 时已置 `file_written = produced_output = True`，失败未回滚该文件的流式记账，导致文件未保存却照常扣掉一次额度、空白占位文件也未回收。
3. **低字符集退化复读绕过 `find_repetition_start`**：生产出现两次单次调用输出打满 `64,000` completion token 的退化循环——一次是 `dl/dl/dl/…` 重复上万次，一次是 18 个不同字符的短句循环上千次。`find_repetition_start` 原要求 800 字窗口内去重非空白字符数 `>= 30`（本意是放行 `——`、`---` 等分隔线），把这两类真实退化复读一并放过了。
4. **MiniMax 内部特殊分隔符 `]<]minimax[>[` 泄漏与吞 `<`**：MiniMax M3.1-Flash 偶发将训练分隔符 `]<]minimax[>[` 原样吐入文本，一方面把 `<file>` / `</file>` 变成 `]<]minimax[>[file>` / `]<]minimax[>[/file>` 导致流式捕获漏识别、整篇大纲掉进聊天；另一方面将未走结构化通道的 `]<]minimax[>[<tool_call>…`（含流末未闭合的 `]<]minimax[>[<tool_call>\n<` 残片，或吞掉 `<` 的 `]<]minimax[>[tool_call>`）直接漏进作者对话气泡。
5. **写后自查对微小字数偏差反复微调**：个别长会话在章节落库后的自查续跑里，为了凑齐大纲里的约数字数目标，连续调用二十余次 `edit_file` 逐句增删，单轮耗时超过 7 分钟。

## Decision

1. **轮间压缩门限下调至 300k，统一供应商分词量纲，常态绝不滑窗砍旧消息**：
   - `AGENT_LOOP_COMPACT_AT_TOKENS` 默认从 `600_000` 下调为 `300_000`（实测 `prompt_tokens`）：在前缀缓存单轮成本失控之前及时做轮间交接摘要。
   - `agent/loop/history.py` 新增按国产 1M 模型量纲的粗估 `estimate_provider_text_tokens` / `estimate_provider_items_tokens`、会话自校准 `calibrated_tokens_per_char`、`effective_history_provider_tokens` 与 `bound_history_for_provider(history, items, max_provider_tokens)`：`FailoverChatModel` / `AgentUsageHooks` / `AgentService` 随 `prompt_tokens` 一并落库实际发出的输入项字符数 `prompt_chars`；有 `(prompt_tokens, prompt_chars)` 锚点对时按会话真实字词比做增量与折叠反推（常态未折叠前缀误差为 0，主备切换后下一轮自动按新供应商校准），无 `prompt_chars` 的旧行退回 `max(last_prompt_tokens, estimate_provider_items_tokens(items))`；不超过硬上限（运行历史 `750_000`、压缩摘要输入 `700_000`）时原样返回全部历史项，超过时按比例折算预算做最后防线降级并对齐到 `user` 边界。`kept_author_messages` 与旧压缩点数据库倒序分页统一改为 `estimate_provider_text_tokens` 计 `20,000` token 预算（对齐 Codex 保留约 3.1 万中文字，不再被 `cl100k_base` 虚算腰斩）；`COMPACT_PROMPT` 去掉 `2500 字以内` 硬上限，篇幅随项目体量伸缩并由 `AGENT_LOOP_COMPACT_MAX_OUTPUT_TOKENS`（`24,000`）兜底。`compact_history` 先按自校准密度调用 `IntraRunToolOutputTrimmer.trim_items` 折叠超预算旧工具载荷，若首次摘要请求仍遇 400/413 拒绝则将输入预算减半重试一次；`should_compact` 的退避目标封顶 `950_000`，避免存量老会话在 96 万 token 跳过后死锁。
   - `IntraRunToolOutputTrimmer` 默认阈值从 `600_000` 字符调整为 `1_500_000` 字符（高于轮间压缩门限，同时距 1M 硬顶留足约 25 万 token 缓冲），并将 `"write_chapter"` 加入 `DEFAULT_WRITE_TOOLS`。`token_utils.estimate_text_tokens` 保持原有 `cl100k_base` 语义不变，不影响其他调用方。
2. **持久化入口剥离 `\x00`，流式落库失败回滚该文件的产出记账**：
   - 在 `FileCRUD.create_file`、`FileCRUD.update_file`、`FileEditor.edit_file`、`FileEditor.replace_body`、`MessageManager._save_messages_with_session` 与 `AgentService` 的追加用户消息 / 显示事件入口剥离字符串中的 `\x00` 字节，消除 PostgreSQL `DataError`。
   - `error_event` 在 `ERR_AGENT_FILE_SAVE_FAILED` 时携带可选 `file_id`；`StreamBillingTracker` 将已确认的工具文件写入、项目信息写入与流式 `filled_file_ids` 分开记账。当某文件的正常流式完成落库失败（收到带 `file_id` 的 `ERR_AGENT_FILE_SAVE_FAILED` 平台错误帧）时，从 `filled_file_ids` 移除该文件并重算 `file_written`、`write_succeeded`、`produced_output` 与 `ended_on_reply`。若本轮无其他已保存写入或达标回复正文，照常触发额度退还并由 `remove_empty_placeholders` 回收该空白占位文件；作者主动停止或断线路径（无 `ERR_AGENT_FILE_SAVE_FAILED` 帧）的既有 `stop_outcome` 与计费口径保持不变。
3. **复读检测改为校验窗口内文字/字母/数字含量**：
   - `StreamProcessor` 的 `find_repetition_start` 去掉「去重非空白字符 >= 30」的限制，改为要求 800 字窗口内 `isalnum()` 字符数不少于 `min(64, max(2, window // 12))`。纯标点分隔线（`——`、`---`、`| --- | --- |`）与空白的 `isalnum()` 为 0 仍放行；`dl/dl/…` 与短句复读在连续第三遍出现时立即截停并只保留第一遍。
4. **有界兼容与剥离 MiniMax `]<]minimax[>[` 机器协议**：
   - `StreamProcessor` 的开始/结束标记、独占行标记与尾残片识别兼容 `]<]minimax[>[` 前缀（含吞掉 `<` 的 `]<]minimax[>[file>` / `]<]minimax[>[/file>`），跨任意流式分片均可正常捕获正文（含 `WAITING_START` 与 `prebody_capture`）。
   - 对话输出流（`IDLE` 与 `WAITING_START` 的引子/缓冲，不含 `WRITING` 的文件正文）增加有界跨分片过滤器：在代码围栏与行内代码之外剥离泄漏的 `]<]minimax[>[<?tool_call>…` 块及流末未闭合残片。由于 `loop/engine.py` 在 `StreamAdapter` 之前收集原始 `tail_text`，引擎侧的未执行工具协议重跑不受影响。
   - `loop/completion_evidence.py` 的 `unexecuted_tool_protocol` 同步支持 `]<]minimax[>[` 吞掉 `<` 后的 `tool_call>` / `invoke` 协议对识别。不引入任何自然语言猜测。
5. **写后自查加入字数容差**：
   - `loop/engine.py` 的 `_BODY_SAVED_CONTEXT` 与 `_BODY_SAVED_REPLIED_CONTEXT`（中英文）明确字数目标默认允许约 ±15% 浮动；除非偏差超过 20% 或作者明确要求严格卡字数，不为凑/删字数反复微调，确需多处修改时控制在 1～2 个小批次。

## Alternatives considered

- **直接把全局 `token_utils.estimate_text_tokens` 改成按字符 0.6 倍估算。** 最强理由：一处改动就能让所有调用方对中文不再虚高，无需区分本地估算与供应商量纲。未采用：`estimate_text_tokens` 同时承担项目上下文组装、技能内容预算与既有单测契约，直接压低系数会让这些固定预算实际膨胀 2～3 倍；单循环历史本来就有上一轮供应商实测 `last_prompt_tokens`，优先信任实测值、仅在无实测和超窗降级时用供应商同量纲估算，边界更清楚且不牵连其他模块。
- **彻底删掉 `AGENT_LOOP_RUN_HISTORY_MAX_TOKENS` 与 `bounded_items` 滑窗兜底。** 最强理由：完全杜绝任何情况下丢弃旧消息的可能，逻辑最简单。未采用：若摘要接口因供应商故障连续跳过压缩、会话真的一路涨破 1M 上下文窗口，没有最后防线会导致后续每一轮请求直接 400 超窗不可用。保留 800k 真实 token 处的按比例降级，兼顾常态绝不砍消息与极端故障可用性。
- **只在流式结束收到 `file_content_end` 时才把流式文件算作 `file_written`。** 最强理由：不需要失败回滚逻辑，落库成功前一律不算写入。未采用：作者中途主动停止或断线时，流过一半的新建文件正文会由后台补存落库，既有契约（及单测）依赖非替换目标的 `file_content` 与保存回执配合判定产出；保持原有正向记账、仅在明确收到保存失败信号（`ERR_AGENT_FILE_SAVE_FAILED` 或回执 `None`）时精确回滚该文件，完全兼容所有既有停止/断线路径。
- **在数据库驱动或 SQLAlchemy 类型装饰器层全局拦截并剥离 `\x00`。** 最强理由：对所有表一劳永逸，业务层无需逐处处理。未采用：改动触及全站所有模型的底层读写热路径，超出本次写作 agent 修复范围；在文件工具与聊天消息的持久化入口显式剥离已完整覆盖模型输出落库面，风险可控且易于测试。

## Consequences

- 收益：中文长连载在 30 万实测 token 前完整保留全部历史与稳定前缀缓存，过线后正常进入轮间交接摘要，单轮输入成本不再爬升至 ¥2～¥5；压缩摘要调用也能读到第 0 轮起的完整设定。
- 收益：模型偶发吐出 `\x00` 不再导致文件或聊天记录保存崩溃；即便因其他数据库故障导致流式文件保存失败，没有其他产出的轮次也会如实退还额度并清理空白占位文件。
- 收益：`dl/` 等短词和短句退化循环在连续第三遍 800 字窗口处即被截停；MiniMax 的 `]<]minimax[>[file>` 正文不再掉进聊天，`]<]minimax[>[<tool_call>` 协议残片不再暴露给作者，未执行的完整工具块仍能被循环引擎重跑。
- 代价：压缩门限从 600k 降到 300k 后，超长会话会比原先更早付出一次摘要调用（约数千输出 token），换取后续每轮节省数十万缓存/未缓存输入 token。
- 代价：`StreamProcessor` 增加了针对 `]<]minimax[>[` 的跨分片状态位；遇到普通右方括号 `]` 且恰好落在 chunk 末尾时会暂扣到下一个 chunk 或流结束才放出（最多几十字符）。
