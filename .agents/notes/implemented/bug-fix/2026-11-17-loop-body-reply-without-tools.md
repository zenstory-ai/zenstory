# Agent Note: 单循环里待写正文的那次回复不提供工具调用

## Problem

单循环 agent 的两次回复协议要求：`write_chapter` / `create_file` 建好（或定为整章替换）之后的下一次回复只写 `<file>` 正文、到 `</file>` 结束（`agent/prompts/base.py` 第 4 条）。但这次请求仍带着全部工具和默认的 `tool_choice`，模型有一部分概率在这一步调用工具：`file_body_pending` 守卫（`openai_agents/tools_adapter.py`）只能在模型已经付出一段长思考和一次调用之后拒绝，run 随之结束，再由 `FILE_BODY_RESUME` 纠偏 run 补写，第一次保存正文被明显推迟。请求参数本身允许了协议禁止的动作，这是协议设计上的缺口。

[2026-10-21](2026-10-21-fast-mode-summary-and-file-body-turn.md) 曾否决在这一步设 `tool_choice="none"`，理由是多 agent 图时期的批量写作依赖「同一条回复先写完上一章正文、再 create_file 下一章」。[单循环 agent](../architecture/2026-11-16-single-loop-agent.md) 改为正文回复到 `</file>` 结束、由引擎带着保存结果和自查继续下一次调用，这个前提已不存在：这次回复里的任何工具调用都是错的。本记录是对 10-21 那条否决的反转。

## Decision

- `agent/openai_agents/model.py` 新增 `_body_reply_settings`，在 `FailoverChatModel.get_response` 与 `stream_response` 开头调用：`ToolContext.overdue_file_bodies()`（守卫用的同一结构化状态：更早的回复建好或定为整章替换、这次回复还没写出正文的文件，包括纠偏轮 `rearm` 后的第一次调用）非空时，把这次调用的 `tool_choice` 改为 `"none"`；已经是 `"none"`、没有请求上下文、或设置不是 dataclass 时原样返回。
- 状态在调用时刻正确：SDK 先等 `on_llm_start`（usage_hooks 里 `ToolContext.begin_model_reply()` 推进回复序号）再调模型。建文件的那次回复不受限；正文写出（`on_llm_end` 标记）或保存（StreamAdapter 清掉条目）之后的自查续跑、下一次 `write_chapter` 照常带原来的 `tool_choice`。
- 工具清单不变。前缀缓存是否受影响没有实测：有的供应商可能把 `tool_choice` 渲染进模板，或在 `none` 时不渲染工具定义，那样这次（上下文最长的）调用会错过缓存；发布前没有做对比请求（付费调用需要单独授权），列在发布后复查里。改写发生在供应商循环之前，主供应商失败转到备用时同一份设置照样传过去。
- 守卫不改，留作供应商忽略 `tool_choice` 时的兜底；其中按 id 删除待写文件的例外只在下面的作者插话例外或供应商不遵守 `none` 时用得上，这一批不删。
- 作者插话例外：文件定为待写之后本请求又收到作者插话（`ToolContext` 记下定为待写时已收到的插话条数，与 service 的 consumed_steering 计数比较，只看条数、不看内容），这次调用不改 `tool_choice`。run 中途在工具边界取出的插话进不了当前 run，要到下一个 run（通常就是 `FILE_BODY_RESUME` 纠偏轮）的输入里才出现；引擎先安排纠偏轮、后处理插话，若这一调用仍限为 `none`，作者「这章不要了」只能被强行写成正文，或白付两次纠偏 run 再由空文件回滚收尾。保留工具后，模型可以按守卫的例外删掉这份待写文件，其余工具仍被守卫拒绝。代价是同一 run 里插话之后的正文调用也不受限（与改动前一样，靠守卫兜底）。
- 供应商确认：MiniMax-M3.1-Flash 的 OpenAI 兼容接口做过一次带工具、`tool_choice: "none"` 的最小真实流式请求，返回 200、没有工具调用增量、`finish_reason=stop`、正常文字且没有文字形式的工具调用标记；同样请求用 `"auto"` 时会调用工具。DeepSeek 文档写明有工具时 `"none"` 表示不调用工具，但还没有做过真实请求（[示例记录](2026-11-17-loop-body-reply-example.md)要求每个供应商各一次真实请求；DeepSeek 这一项目前只有文档依据，是已知缺口）。
- 测试：`tests/test_agent/test_llm_failover.py` 覆盖 helper 在各种待写状态下的结果、发到供应商客户端的请求只有正文那次带 `none`、保存后下一次恢复原值、故障转移时备用供应商也带 `none`，一次真实 SDK `Runner.run` 里的调用顺序，以及文件定为待写之后有作者插话时纠偏轮第一次调用保留原 `tool_choice`。

## Alternatives considered

- **只靠提示词强调（含修正示例，见 [2026-11-17 示例](2026-11-17-loop-body-reply-example.md)）。** 最强理由：不改请求参数、对所有供应商零风险。不单独依赖：10-21 起协议就写在系统提示里，模型仍在这一步调用工具；示例修正保留，作为配合。
- **`parallel_tool_calls=False`。** 最强理由：同样是请求参数，不改提示词。不采用：它不阻止调用工具，只把 query_files 等批量读取串行化，伤及正常回合。
- **只保留守卫。** 最强理由：已有、已测，不依赖供应商行为。不采用：每次失败都要付一段思考、一次被拒调用和一次纠偏 run，正是要消除的代价。

## Consequences

- 收益：正文回复在请求层面就不能跳过正文去调工具，省掉被拒调用和纠偏 run，正文更早开始流式显示和保存；不增加请求，工具清单不变。
- 代价：没有新插话时，这次回复里不能再用 delete_file 取消待写文件；效果依赖供应商遵守 `tool_choice`（不遵守时退回守卫）。模型把工具调用写成文字（被当作协议残片丢弃）不在覆盖范围内，仍按现有纠偏处理。
- 发布后按同一口径复查：`file_body_pending` 拒绝次数（预期接近 0）、每个待写正文的纠偏 run、协议残片丢弃、受限调用是否仍返回工具调用、这类调用的 `cache_hit_tokens` / `prompt_tokens_details.cached_tokens` 与相邻调用对比（若明显下降，说明 `none` 改变了缓存前缀）、DeepSeek 作为备用时这类调用是否成功、`first_output_ms`。
