# Bug-Fix Note: 泄漏协议早停与同轮续跑绝不丢失本轮作者原始消息

Status: implemented

关联决策：[缩短新建/替换章节后的首字等待并消除空白画布假死感](../feature/2026-11-22-chapter-first-token-speedup.md)、[单循环 agent](../architecture/2026-11-16-single-loop-agent.md)。

## Problem

在单循环 agent 使用真实 MiniMax-M3.1-Flash 跑首轮建档与建章时，复现出一处导致模型在同轮纠偏中“彻底失忆、编造无关故事”的缺陷：

1. 模型在 `create_file` / `write_chapter` 之后若触发 `leaked_tool_call_stopped` 早停（或正常结束时 `result.to_input_list()` 抛错 / 返回空），旧版 `_sanitized_early_stop_items` 只要在 `raw_items` 尾段遇到未配对的 `function_call` 或异常就整体返回 `None`。
2. `runner.py` 随后执行 `state.pop("run_items", None)` 清空本轮已完成输入项；紧接着 `loop/engine.py` 在路由进 `FILE_BODY_RESUME`（或同轮 `followup`）时将 `state["user_message"]` 覆写为系统提醒文案（如「你创建的文件《分章大纲》正文仍为空」）。
3. 纠偏轮启动时 `state["run_items"]` 为空，退回 `state["history_items"]` + 新的 `state["user_message"]`：本轮作者原话和 `<turn_context>`（新书简介、焦点文件、附件、选区）全部丢失。新作品首轮因 `history_items` 为空，模型会直接凭空编造一个与作者需求毫不相干的故事。

## Decision

在 `runner.py` 与 `loop/engine.py` 两层加装互补的结构化保底，不改变任何正常路径的前缀缓存与 `wire_prefix_cache` 校验口径：

1. **`_sanitized_early_stop_items` 保留最长成对前缀**（`apps/server/agent/openai_agents/runner.py`）：顺序扫描 `result.to_input_list()`，每当 `function_call` / `function_call_output` 配平就推进 `last_balanced_len`，遇到尾段未配对项或异常结构立即截停并回退到 `last_balanced_len`，再剥离末尾悬空 `reasoning`。既满足 `is_well_formed_wire_prefix` 之前的成对约束，又保留已完成轮次与原始作者消息。
2. **早停与正常结束均以 `run_input` 兜底**（`apps/server/agent/openai_agents/runner.py`）：`leaked_tool_call_stopped` 时 `completed_items = _sanitized_early_stop_items(result) or list(run_input)`；`not max_turns_exhausted` 路径在 `to_input_list()` 抛错或为空时同样回退到 `list(run_input)`，不再清空 `state["run_items"]`。
3. **单循环引擎续跑边界兜底原始用户消息**（`apps/server/agent/loop/engine.py`）：`run_loop_agent_streaming` 入口记录 `initial_user_message`，在覆写 `state["user_message"]` 进入 `FILE_BODY_RESUME` 或 `followup` 续跑前，若 `state["run_items"]` 为空且 `initial_user_message` 非空，自动补入 `[*history_items, {"role": "user", "content": initial_user_message}]`，从根上杜绝任何续跑路径丢掉本轮作者需求。

## Alternatives considered

- **只在 `engine.py` 把 `initial_user_message` 拼进每次续跑的 `state["user_message"]`**：最强理由是完全不依赖 `runner.py`。未采用：正常路径 `state["run_items"]` 已经包含完整的原始用户消息，重复拼接会双倍消耗 token 并破坏后续轮次的会话前缀结构；仅在 `run_items` 缺失时回填才零额外开销。
- **遇到 `leaked_tool_call_stopped` 且 `to_input_list()` 不完整时直接以可退费 `ERROR` 结束整轮**：最强理由是避免任何不完整历史续跑。未采用：直接报错会让作者看到失败卡并被迫手动重试，违背 PR #447 用 `FILE_BODY_RESUME` 无感自动纠偏的初衷；保留最长成对前缀并回退 `run_input` 既严格保证工具调用成对安全，又能直接完成正文补写。

## Consequences

- **收益**：彻底消除早停或 `to_input_list()` 异常导致同轮纠偏/续跑丢失作者原始消息和 `<turn_context>` 的线上失忆问题；正常路径与 `wire_prefix_cache` 行为零回归。
- **代价**：新增 10 余行防御性前缀截断与边界兜底代码，无额外模型调用、无数据库变更、无新依赖。
