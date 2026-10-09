# Agent Note: 返修交回 writer 时不再回放审稿任务；只想没写就撞上输出上限按「回复继续」类错误结束

Status: implemented

证据来源：根因追查报告 `audit-runs/20261009-r4-fix-release-supervisor/loop-rootcause.md`（本地 live 流程 + 同一请求的 A/B 重放，原始请求/响应在仓库外的 loop-repro 目录）。

## Problem

生产里出现过一次 writer 思考循环：第 3 步 writer 想了约 340 秒、215893 字符，后段反复「OK.」，最后没有任何正文就结束，作者只看到一大段思考。

**已证实**

1. writer → 审稿人 → writer 时，第二次 writer 的模型输入里带着一条只写给审稿人的 user 消息：`[质量检查任务] 请审查上一个 Agent 完成的内容…`，其中还附着约 8.8k 字的 `[待审查内容]` 旧稿，紧接着才是审稿人交回的 `[来自上一个Agent的交接信息]`。来源：`writing_graph` 给审稿人构造这条 user_message；`runner.build_history_messages` 把它追加进 SDK 输入；`_append_assistant_turn_to_state_messages` 把整份输入写回 `state["messages"]`；graph 原样传给下一个 agent；`normalize_messages_for_openai_agents` 只丢 tool/thinking block，不按角色过滤 user 轮。
2. 这份输入会让 writer 在思考里把自己当成审稿人（「My role now is quality_reviewer… Wait… the system prompt says 当前角色：writer」）。同一请求原样调用 2 次都出现角色混淆；只删掉这一条消息的对照组 1 次，没有出现，思考长度约为原样的 39%～57%（28674 vs 50453 / 73367 字符）。
3. openai-agents 0.17.x 的 Chat Completions 流式 handler 不处理 `finish_reason`（`ResponseCompletedEvent.status` 恒为 `completed`），runner 也没有 length 判断。live 实测 `finish_reason=length` 的响应被当成正常完成。一次 run 只有思考就撞上 `max_tokens` 时：`agent_content` 为空，graph 静默结束，`stop_reason` 为 `end_turn`，不发 ERROR / 状态卡。
4. 已否定：工具未被禁用（11 个工具齐全，tool_choice=auto）；前端没有对 thinking 帧做重放或续传拼接（读码 + 本地帧逐条对应模型 delta）。

**很可能（未直接证实）**

- 生产那次 340 秒的「OK.」循环 = 上面的角色混淆导致无法决断，在高 reasoning effort + 64000 输出上限下退化成重复，最后撞上 64000 上限结束。依据：生产思考里循环前的内容与本地 A 组几乎同构；215893 字符约 6.3 万 token，与上限吻合。生产那一步的 usage / finish_reason 日志已缺失，无法直接确认。

**未证实**

- 本地没有复现「OK.」重复退化本身（A 组两次都没有出现同句连续重复）。所以本修复只消除了已证实的角色混淆诱因，**不能宣称循环一定不会再出现**；与输入无关的长上下文退化（模型自身）既不能排除，也没有证据支持。
- 样本极小（A=2，B=1），只能说明机制成立、方向一致，不是统计结论。

## Decision

**1. 根因（编排层，`agent/graph/writing_graph.py`）**

- 新增 `REVIEWER_TASK_MARKER = "[质量检查任务]"`，审稿任务消息用它构造；新增 `_drop_reviewer_task_messages(messages)`：去掉 role=user 且正文（字符串或 text block 列表，经 `runner.extract_text_from_message_content` 取文本、去掉前导空白后）以该标记开头的消息。
- 交接给**非审稿人** agent 时（handoff 分支的 `else`），`modified_state["messages"]` 换成过滤后的历史。只过滤这一类消息：作者原话、各 agent 自己的输出、`[来自上一个Agent的交接信息]` 照常保留。审稿人自己那次 run 的输入不变，仍能看到它的任务。
- 下一个 agent 跑完后会把（已过滤的）历史写回 `state["messages"]`，之后的 agent 也不会再看到旧的审稿任务；第二轮审稿时审稿人收到的是新的审稿任务，不再带着上一轮的旧稿。

**2. 状态正确性的最小补充（`agent/openai_agents/runner.py`）**

这不是修循环，只是让「被截断」不再被当成正常完成。

- 一次 agent run 结束时，若**最后一次模型响应**只有思考（没有正文、没有工具调用），本 run 没有交接/澄清，并且这次响应的 `usage.output_tokens ≥ max_tokens × 0.98`（`OUTPUT_CAP_RATIO`；SDK 不透出 finish_reason，只能看 usage），就先发 `MESSAGE_END(stop_reason="output_truncated")` 让用量入账，再沿既有 ERROR 路径发 `ERR_AGENT_OUTPUT_TRUNCATED`（`retryable=False, refundable=True`，与 `ERR_AGENT_MODEL_CALL_LIMIT`、`ERR_AGENT_RUN_TIMEOUT` 同类：不给重试按钮，避免重发原请求从头再做并再计一次），并记一条 WARNING（agent_type、output_tokens、max_output_tokens、thinking_chars、stop_basis=`usage_output_tokens_at_cap`）。只看最后一次响应而不是整个 run：交接后的 writer 常先调工具重读稿件，之后那次响应才只剩思考并撞上上限（根因复现中的 call 7→8 就是这样）。
- 文案：`core/error_codes.py` 与前端 `public/locales/{zh,en}/errors.json`；前端按错误码的既有映射显示，没有改 UI。
- 计费不加分支，沿用 `stream_billing` 既有规则 5：本轮没有实质产出 → `internal_error` 退还；同一请求里前面已经写入了正文 → `error_after_output` 照常计费。
- 没有新增重复阈值、思考长度上限、超时、自动重试或退款规则，也没改思考的展示和语言。

## Alternatives considered

- **在 runner 的 `build_history_messages` 处按 agent 过滤**：需要把 agent_type 传进去，且 runner 不该知道审稿任务的格式；graph 是构造这条消息的地方，在交接处过滤改动最小。
- **把交接头改成明确角色（「审稿人给 writer 的返修意见，你是 writer」）**：可能进一步降低混淆，但没有测过，本轮不做。
- **重复检测、思考长度上限、调低 reasoning_effort / max_tokens**：都是治标，且有未评估的质量代价；用户明确要求先修根因、不以保护措施宣称修复。
- **把 length 截断改成 ITERATION_EXHAUSTED 状态卡**：状态卡语义是「工具调用轮数用尽」，会被计费当成失控停止（runaway），与这里不符；ERROR 路径的退款规则已覆盖。

## Consequences

- 返修交回 writer 时输入少了一条约 9k 字的消息，已证实的角色冲突消失；writer 仍能从交接信息、审稿人的发言和文件读写看到要改什么。
- 只想没写就撞上限的一轮，作者现在会看到一条说明，而不是只有思考；本轮无产出时额度按既有规则退还。
- **相邻风险（未证实，本轮不改）**：审稿人若在交接前输出了正文，这段正文会以 assistant 角色回放给 writer（`normalize_messages_for_openai_agents` 不区分 agent）。本地 live 里审稿人有一次 run 只调用工具没有正文，所以这条没有被验证；单测里审稿人有正文时它仍出现在 writer 输入中，这是现状。
- `output_truncated` 加入 `router.RESUMABLE_STOP_REASONS`：文案请作者回复「继续」，与 `no_progress`、`model_call_budget_exhausted` 等同类一样直接交回上一个 agent，不重新路由。
- length 截断但本 run 已经调用过工具（例如截断的 `edit_file` 参数）的情况不归这条规则管；SDK 如何处理被截断的工具参数本轮没有深究（本地 live 那次之后模型自行恢复）。
- 判定依赖 usage 末尾 chunk（`include_usage=True` 已开启）；上游不返回 usage 时 output_tokens 为 0，不会触发，行为与之前一致。
