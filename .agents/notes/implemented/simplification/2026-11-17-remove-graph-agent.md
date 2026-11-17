# Simplification Note: 删除多 agent 图

Status: implemented

接续 [single-loop-agent](../architecture/2026-11-16-single-loop-agent.md)：单循环 agent 已是唯一的生产路径，图只作回滚保留。本记录把图整套删除。

## Problem

单循环 agent 上线后，多 agent 图（`apps/server/agent/graph/`：路由、规划 / 写作 / 审稿角色的编排、交接、自动送审）只在 `AGENT_ENGINE=graph` 时运行，作为回滚路径保留。保留它的代价每天都在付：

- 几千行执行代码、路由合同、角色提示、三个只有图用的工具（`handoff_to_agent`、`request_clarification`、`parallel_execute`），以及两套提示协议（图的 `TOOL_USAGE_PROTOCOL` / `AUTHOR_REPLY_PROTOCOL` 与单循环的 `LOOP_*`）并存，提示构建要带 `loop=` 开关。
- 外壳测试用 `shell_engine` 夹具在两个引擎上各跑一遍，每次改 SSE 外壳、计费、停止或落库都要同时照顾两条路径。
- 单循环引擎反过来依赖图模块里的辅助函数（空文件核验与回滚、任务板收尾、收尾说明、正文字数、`<file>` 边界判断、`WritingState`），图的任何改动都可能波及生产路径。
- runner 和工具适配层里还留着只为图服务的分支：控制流工具的截停、交接包、只读 / 先问清楚的拒绝、按角色的工具权限。

## Decision

1. **删除**：`agent/graph/` 整个包；`agent/prompts/subagents.py`（路由、规划、爽点、写作、审稿提示）；`agent/tools/parallel_executor.py` 与 `handoff_to_agent`、`request_clarification` 的 schema 和实现；`agent/context/turn_snapshot.py`（项目资料快照）；`compute_history_token_budget`（历史窗口台账）；`MessageManager.build_turn_context` 及只被它用的分段；`AgentService` 里图的上下文组装分支与 `_prepare_prompt_artifacts`；service 对 `ROUTER_DECIDED` / `AGENT_SELECTED` 的路由累积（落库 `routing` 照旧为空）；runner 的控制流截停、交接 / 澄清事件、交接包（`build_handoff_packet`、`RepeatReadGuard.handoff_summary` / `completed_items`）、只读与先问清楚的拒绝、`_card_last_agent` 的角色分支；tools_adapter 的角色权限与只读拒绝；`author_facing_text` 里交接原因与角色名的改写（保留 `AgentTextShaper`）；`ToolContext` 里只被图置位的 `set_body_requested`、`set_review_handoff_refusal`、`refresh_file_inventory`。
2. **配置**：`AGENT_ENGINE`、`AGENT_ROUTER_STRATEGY`、`AGENT_ENABLE_GRAPH_AUTO_REVIEW`、`AGENT_AUTO_REVIEW_THRESHOLD_CHARS` 不再读取，线上仍设着也会被忽略，始终走单循环。`AGENT_COLLABORATION_MAX_ITERATIONS` 名字和默认值（12）不变，含义写明为「一轮里单循环 agent 最多跑几次」，线上覆盖值继续生效。请求里的 `metadata.generation_mode` 照常接受、不读取，旧客户端不会收到 422。
3. **搬移（行为不变）**：单循环用到的辅助函数逐字搬出图模块，改成公开名：`agent/core/empty_files.py`（空文件落库核验、恢复写入目标、回滚、工具轮数耗尽后的收尾，纠偏次数上限 `MAX_FILE_CORRECTION_ATTEMPTS`）、`agent/core/task_board_completion.py`（正常结束时按实际写出的文件补记任务板，tool_use_id `workflow_auto_completion` 不变）、`agent/loop/run_support.py`（收尾说明与暂停卡原因、插话追加轮提示、续跑说明里的正文字数、`AgentTextShaper` 事件整理、工具结果失败判断）、`agent/loop/completion_evidence.py`（停在 `<file>` 正文上、文字里没执行的工具调用的协议结构识别）、`agent/core/writing_state.py`（`WritingState`，只留仍被读写的字段）。
4. **单一提示与工具集**：`get_base_prompt`、`get_prompt_for_project_type`、`MessageManager.build_system_prompt` 不再有 `loop=` 参数，只生成单循环的文本；`tools/registry.py` 只列 `LOOP_TOOL_NAMES`（顺序不变）。删除前后单循环的系统提示（三种作品类型 × 中英文 × 有无技能目录）、工具 schema、SDK FunctionTool 清单和收尾提醒逐字相同（一次性比对删除前后的完整文本）。
5. **旧会话照常可读**：图写下的 assistant 行（routing、交接返修、状态卡、`parallel_execute` 面包屑、`display_events`）仍经 `loop/history.py` 的 `_legacy_row_items` → `SessionLoader._format_chat_message_for_history` 折叠回放；`SessionLoader` 判断旧行角色有没有写工具改用冻结的 `_LEGACY_GRAPH_WRITE_ROLES`（等于删除前 registry 的结果）。stream_adapter、计费与回执里处理 `parallel_execute`、路由事件的分支保留，前端渲染不改。新增 `test_loop_legacy_graph_rows.py`，用删除前的代码生成期望值，锁住这类旧行的回放输出。旧历史里模型照着调用已删除的工具时，SDK 按 `return_error_to_model` 把错误交还模型，这一轮继续。
6. **测试**：只测图的用例删除；`shell_engine` 夹具只跑单循环，外壳用例替换 `run_loop_agent_streaming`，原来在图那一遍上的被测行为（SSE、停止、插话、计费与退还、落库回执、PostgreSQL 卸载路径、首条消息提示、跨轮前缀）都在单循环那一遍保留。图测试里覆盖共享行为的部分改在单循环上测：空文件纠偏的跨层合成回归改由单循环引擎驱动，工具轮数耗尽的空文件回滚、收尾说明、文字里没执行的工具调用、文件流续写边界、空文件核验的短 session 与读库当前值都有单循环用例。
7. **回滚方式**：从 git 检出合并本批之前的提交（本批在分支上的起点为 `0b077823`）重新部署；不再有环境变量开关。图写下的数据结构没有变化，回滚后旧版本照常读写。

## Alternatives considered

1. **继续保留图作回滚**。最强理由：切一个环境变量就能在重启后回到图，不用重新构建和部署，出问题时恢复最快。否决：真需要回滚时，重新部署一个旧提交同样可得，也只多一次部署的时间；而两套引擎、两套提示协议、双引擎测试的维护成本每天都在付，单循环对图模块的反向依赖还让图的改动波及生产路径。
2. **只删执行入口和开关，模块原样留着**。最强理由：改动最小、风险最低，几乎不碰共享代码。否决：死代码会继续被搜索命中、被误改，两套提示协议仍要求每次提示改动都核对两份，单循环仍从图模块导入辅助函数，删除的收益大半拿不到。
3. **把只服务于图的共享分支一并删干净**（session_loader 里给图准备输入的历史窗口、跨轮工作集加载，stream_adapter 的 CHAT_HOLD 与路由帧转换，计费 / 回执 / 熔断 / 重复读取守卫 / run 内裁剪里 `parallel_execute` 的分支，run_meter 的角色计时阶段，`ToolContext` 里非单循环角色的分支）。最强理由：一次清干净，不留下一批。本批不做：这些分支在 SSE、计费与回执的共享路径上，或者被旧行回放与前端仍在读的数据形状依赖；单循环不产生对应事件，留着它们没有行为风险，逐项删除需要各自的回归测试，另开一批。

## Consequences

- 收益：后端少了图的执行代码、路由、角色提示和三个工具，单循环只有一套提示协议和一份工具清单；外壳测试只跑一遍，全量测试更快；单循环不再依赖图模块。
- 收益：生产行为不变：系统提示、工具清单与收尾提醒逐字相同，SSE、计费 / 退还、停止 / 插话、残稿补存、版本并发、作者正文保护、回执都走原来的代码；对外 API 结构不变。
- 代价：回滚变成重新部署旧提交，比切环境变量慢，部署前要照常做发布检查。
- 代价：旧会话里的交接、路由、补问只能读，不能按图的语义续跑；作者回复「继续」时由单循环 agent 读着旧行的文字折叠接着做。
- 代价：线上残留的 `AGENT_ENGINE` 等变量被静默忽略，清理云端变量另行授权。
- 代价：第 5 条与备选 3 列出的共享分支和数据形状兼容代码仍在，下一批按回归测试逐项清理。
