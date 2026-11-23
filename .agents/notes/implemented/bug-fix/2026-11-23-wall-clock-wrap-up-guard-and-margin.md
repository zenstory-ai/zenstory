# Bug Fix Note: 墙钟收尾时间窗加宽与收尾期工具调用拦截

Status: implemented

关联：[接近请求墙钟时限时收尾](2026-10-21-wall-clock-wrap-up.md)、[时限收尾与墙钟硬超时都以暂停卡和继续入口结束](2026-10-31-wall-clock-pause-card-and-receipt.md)、[空闲滑动 TTL 会话粘性 Key 路由与软着陆 wire_prefix_cache 写入/失效](../architecture/2026-11-21-cache-soft-land-and-session-key-sticky.md)。

## Problem

2026-11-22 16:52 UTC 之后 22.4 小时的生产审计（369 轮单循环写作）中，热续写（间隔 ≤ 15 分钟）缓存未命中分析定位到两类现象：

1. **Case 1（`empty_file_correction` 与 `leaked_tool_call_early_stop`）**：实查生产数据库 5/5 次 `empty_file_correction`（含 `0f322667e87a` 与 `4d475b5b6a76`）均有 `rolledBackFiles` 长度为 1（此前诊断脚本按 snake_case 查 `status_cards` 误读为 False），`leaked_tool_call_early_stop` 作为最终 `stop_reason` 仅出现在纠偏失败/空文件回滚或整章替换作废时。此时 `writing_state["run_items"]` 仍停留在「文件已建好待写正文」且不含数据库软删/作废事实，并常以 `function_call_output` 结尾；拒绝写入 `wire_prefix_cache` 是必需的数据一致性安全护栏，不可放宽。
2. **Case 2（780 秒墙钟硬超时）**：5 次硬超时中 2 次为单次上游调用卡死 770+ 秒，其余 3 次（`16af429b0de6`、`bd35c2e9ac2b`、`230bcf5720cf`，均有轮前压缩且在下一轮「继续」合计损失 304k+ 缓存未命中 token）暴露了两个可修复的根因：
   - **收尾提醒不约束工具调用且无程序兜底**：`LOOP_TIME_LANDING_PROMPT` 未明确禁止继续调用 `edit_file` / `write_chapter` 等工具，且 `_stop_run_after_tools` / `tools_adapter` 对已进入墙钟收尾的调用无任何拦截。`230bcf5720cf` 在 632s 收到收尾提醒后仍执行了 99.7s 的 `edit_file` 并在第 17 步撞上 780s 硬超时；另有 8 轮在 600s 后继续发起了 2–6 次额外工具调用（如 `7e122c3c26e0` 连调 6 次 `edit_file`、`966cbb436d2b` 连调 4 次工具）。
   - **180 秒收尾余量低于 MiniMax-M3.1-Flash 长步骤尾部耗时**：生产 3,667 次成功步骤中 p99 为 152.4s、24 次 ≥ 180s。`bd35c2e9ac2b` 第 9 步于 562s 启动耗时 204s、结束时距 780s 仅剩 13.6s；`16af429b0de6` 第 11 步于 584s 启动调用 `write_chapter`（97s），留给 `<file>` 正文的 99s 不足以写完一整章而被硬截停。硬超时取消路径会经 `StreamAdapter` 补存残稿且清空 `state["run_items"]`，绝不能强行缓存 `run_items`，必须从根因上让这类长请求在 780s 之前干净软着陆。

## Decision

1. **加宽默认墙钟收尾余量至 240 秒（`apps/server/config/agent_runtime.py`）**：`AGENT_RUN_WRAP_UP_AFTER_S` 默认值由 `AGENT_RUN_WALL_CLOCK_TIMEOUT_S - 180`（600s）调整为 `AGENT_RUN_WALL_CLOCK_TIMEOUT_S - 240`（540s），仍支持同名环境变量覆盖。22.4 小时样本里落在 `[540s, 600s)` 且未触发收尾的轮次仅 3/369（0.8%），而 `16af429b0de6` 与 `bd35c2e9ac2b` 均可在 575s 前干净软着陆并保留 `wire_prefix_cache`。
2. **强化 `LOOP_TIME_LANDING_PROMPT`（`apps/server/agent/openai_agents/runner.py`）**：明确要求「立即停止调用任何工具（包括 query_files / edit_file / write_chapter / create_file / update_project），思考尽量简短」，同时保留已调用 `write_chapter`/`create_file` 后补写 `<file>...</file>` 正文的条款。
3. **收尾期同步工具拦截与上限兜底（`agent/core/run_meter.py`、`agent/openai_agents/tools_adapter.py`、`agent/openai_agents/tool_failure_breaker.py`、`agent/openai_agents/runner.py`）**：
   - `AgentRunMeter` 新增 `wall_clock_landing_calls` 计数（`_ModelCallCountingFilter._with_landing` 在 `past_wrap_up` 注入收尾提醒时递增）。收尾前已经启动的模型调用产出的工具照常执行（不浪费已生成的合法修改）；**只有在模型调用开始前已收到墙钟收尾提醒后**仍发出的工具调用，由 `tools_adapter` 在执行前以 `error_type="wall_clock_wrap_up"`（带面向作者的 `user_message`）同步拦下（取消待写空文件的精确 `delete_file` 及既有的 `file_body_pending` 守卫保持优先）。
   - `ToolFailureBreaker` 将 `"wall_clock_wrap_up"` 归入不计入熔断的守卫错误集合，避免模型并行调用多个工具时误触发致命 `tool_failure_circuit_open` 错误，让模型在下一跳立即输出简洁阶段总结并干净软着陆、完整写入 `wire_prefix_cache`；前端 `INTERNAL_ORDERING_ERROR_TYPES`（`apps/web/src/lib/agentRoundProgress.ts`）同步收录 `'wall_clock_wrap_up'`，不渲染为虚假失败卡（保留后端 `user_message` 作滚动发布兜底）。
   - 若模型在收到提醒与 `wall_clock_wrap_up` 拦截后仍连续 `MAX_WALL_CLOCK_LANDING_CALLS`（3）次继续调用工具，`_stop_run_after_tools` 同步结束 run，避免拖到 780s 硬超时（此时尾部为 `function_call_output`，`is_well_formed_wire_prefix` 自动拒绝缓存并失效旧键，保证零状态分歧）。
4. **保持既有安全护栏不变**：不放行 `empty_file_correction`、`leaked_tool_call_early_stop` 与 780s 取消路径的 `wire_prefix_cache` 写入。

## Alternatives considered

- **在 780s 硬超时取消路径尝试复用取消前的 `run_items` 写入 `wire_prefix_cache`**。最强理由：直接收回硬超时后的缓存命中，无需提前收尾。未采用：取消瞬间 `StreamAdapter` 会后台补存 `<file>` 残稿、`RunOutcome` 会等待线程中在途写工具提交、部分回复文字也会落库，而取消前的 `run_items` 既不包含这些已落库事实也可能停在未配对工具调用或 `function_call_output` 上，缓存它会导致严重的数据库/前缀状态分歧。
- **在收到墙钟收尾提醒后对第一次模型调用直接设 `tool_choice="none"`**。最强理由：模型物理上无法再发起工具调用。未采用：实测 MiniMax 在 `tool_choice="none"` 时会剥离工具清单并击穿本轮及后续前缀缓存（见 [prompt-cache-stability](../architecture/2026-11-20-agent-prompt-cache-stability.md)），同步工具拦截既保持工具清单与默认 `tool_choice` 不变以保住缓存前缀，又能确定性阻止工具继续执行。
- **在第一次 `wall_clock_wrap_up` 拦截后立即通过 `_stop_run_after_tools` 结束 run**。最强理由：省去 1 次约 5 秒的总结模型调用。未采用：SDK 在 `tool_use_behavior` 返回 `is_final_output=True` 时 `to_input_list()` 以 `function_call_output` 结尾且没有阶段总结文字，既无法通过 `is_well_formed_wire_prefix` 写入 `wire_prefix_cache`，也会让作者看不到总结。给模型一次看到 `wall_clock_wrap_up` 后输出总结的机会，既拿到阶段总结又保住跨轮前缀缓存。

## Consequences

- **收益**：
  - 消除模型忽略墙钟收尾提醒继续连环调工具导致的 780s 硬超时与中途截断；
  - 为 MiniMax 长步骤和已触发的 `write_chapter` + `<file>` 正文留出 240 秒收尾窗口，使长轮次干净软着陆并完整写入 `wire_prefix_cache`，次轮「继续」直接复用热前缀缓存；
  - 保持所有既有安全护栏（`empty_file_correction`、`leaked_tool_call_early_stop`、硬超时取消路径、`is_well_formed_wire_prefix`）零松动。
- **代价与风险**：
  - 默认在 540s（而非 600s）开始提醒收尾，约 <1% 原本在 540–600s 内自然结束的超长单轮会提前一跳收尾并由作者点击「继续」承接（且次轮享有热前缀缓存）。
  - 若模型忽略收尾提醒仍调用工具，会额外经历一次 <1ms 同步拦截和一次简短总结调用（约 3–8s）；如连续 3 次仍不收敛则同步截停并跳过缓存。
