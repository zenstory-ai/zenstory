# Agent Note: 删除工具动作 artifact ledger 与超限结果的外溢持久化 / 回填

关联：[update_project 的行锁自锁](../bug-fix/2026-10-20-update-project-lock-and-startup-ddl.md)（本 note 是它的后续：事故里等锁的那条 ledger 插入不再存在）、[读取路径不改写历史](../bug-fix/2026-10-08-agent-read-path-no-history-rewrite.md)（`_compact_oversized_payload` 保留，事实已同步）、[AI 起的作品名可以再改](../bug-fix/2026-10-09-ai-named-project-can-be-renamed.md)（`project_title` 记录保留）。旧 note 的决定不改写。

## Problem

2026-10-25 审计（A-S2 / D-02）核实了 `agent/tools/mcp_tools.py` 里两套只写不读的机制：

- **工具动作 ledger**：`create_file`、`edit_file`、`delete_file`、`rename_file`、`update_project` 成功后，各自用另一个 session 往 `agent_artifact_ledger` 写一行（`_record_artifact_ledger`）。每次写操作多一个事务；2026-10-20 生产事故里自锁等的就是这条插入。唯一的读取方是 `handoff_to_agent` 把最近的引用并进交接包的 `artifact_refs`，而 `artifact_refs` 没有消费方：图只把 `todo` / `evidence` / `context` 渲染给下一个 agent，前端的 `SSEHandoffPacket` 也不声明它。
- **超限结果外溢**：工具结果超过 `TOOL_RESULT_MAX_CHARS` 时，`_persist_tool_result_overflow` 把整份原文写进 ledger，给结果加 `overflow_ref`；交接时 `_build_tool_result_overflow_backfill_entries` 再查库，把最近 3 条外溢的 180 字预览以「[工具外溢引用回填]」追加到交接 context。模型没有任何工具能按 `overflow_ref` 取回原文；回填只是几段截断的 JSON 开头，查库还在工具调用路径上同步进行。

## Decision

- 删除五个写入点、`_record_artifact_ledger`、`_get_ledger_session`、`_extract_tool_artifact_refs`、`_merge_unique_refs`、`_load_recent_artifact_refs_for_handoff`、`_persist_tool_result_overflow`、`_load_tool_result_overflow_entry`、两个回填 helper，以及 `TOOL_RESULT_OVERFLOW_*`、`STANDARD_HANDOFF_ARTIFACT_ACTIONS`、`DEFAULT_ARTIFACT_REF_LOOKBACK` 常量。写工具、超限截断和交接都不再为此另开 session。
- 超限结果照旧走 `_compact_oversized_payload` 的保结构截断（id/title/file_type/status 保留、长文本「注水」截短、`mutation_applied` 透传），只是不再带 `overflow_ref`；`overflow_ref` 也从受保护键 `_BOUND_PROTECTED_KEYS` 和 trimmer 的 `_TRACE_KEYS` 中移除。`_serialize_tool_payload` 不再需要 `tool_name`。
- `handoff_to_agent` 返回的交接包只有 `target_agent` / `reason` / `context` / `completed` / `todo` / `evidence`；`context` 原样使用模型写的内容（缺省时回退到 reason）。runner 不再从工具结果累积 `artifact_refs`（删 `extract_artifact_refs`、`merge_unique_strings`），图生成的计划交接包和自动送审交接包也不再带 `artifact_refs`。
- `handoff_to_agent` 的 schema **保留** `artifact_refs` 属性：工具列表不变，prompt 缓存不失效；模型传了也被忽略（以前同样不会出现在下一个 agent 的输入里）。
- **保留**：`AgentArtifactLedger` 表与迁移、`project_title` 记录的写入与读取（`file_ops/project.py`、`api/projects.py`）、`scripts/cleanup_compaction_summary_rows.py`、任务板自己的 `artifact_refs` / `completed_artifact_refs`（与本表无关）。
- `ProjectOperations.update_project_status` 在无改动路径上照旧 commit：请求级 session 可能在整轮 run 里一直开着，行锁不能活过这次调用。`tests/test_agent/test_update_project_lock_postgres.py` 改写为直接锁定这一点：调用方 session 保持打开，另一个连接对该项目行执行 `SELECT … FOR UPDATE NOWAIT` 必须立即成功，覆盖作者起名、名字未变、标题无效、AI 改默认名、只改状态字段五条路径。

## Alternatives considered

- **保留外溢持久化，补一个按 `overflow_ref` 取回原文的工具。**
  - 最强论据：超限结果真正丢掉的内容可以找回，交接不怕信息缺口。
  - 不选的原因：结构化截断已经保留每个条目的 id，并提示用 `query_files(id=…, response_mode="full")` 单独读，取回路径本来就有；新增工具会改动工具列表、让缓存失效，还要为一份只读副本维护权限与清理。
- **保留 ledger 写入，只把它改到工具自己的 session 里。**
  - 最强论据：不会再等自己的行锁，同时留着一份工具动作审计。
  - 不选的原因：这份记录没有读取方，审计需求也没有出现过；合进主事务后 ledger 失败会连带回滚工具写入（2026-10-20 note 已否决过）。写入本身就是多余的事务。
- **保留交接包里的 `artifact_refs` 字段，只停止查库。**
  - 最强论据：SSE 交接事件的形状不变。
  - 不选的原因：前端不声明也不读这个字段，图也不渲染；留一个永远只含模型自填内容、无人读取的字段没有意义。前端对缺字段本来就容忍。

## Consequences

- **收益**：每次写工具少一个独立事务和一次连接占用；超限结果不再把整份原文写库；交接不再同步查库；去掉 2026-10-20 自锁里等锁的那条插入。生产代码约 -560 行，测试约 -310 行。
- **代价 / 行为变化**：
  - 交接 context 不再附「[工具外溢引用回填]」段落。这段文字以前模型看得到，但只有几条 180 字的 JSON 开头，没有可用的取回方式；下一个 agent 需要原文时照旧按 id 读文件。
  - `agent_artifact_ledger` 不再新增工具动作行和 `tool_result_overflow` 行；已有的旧行留在表里，不做迁移或删除（本次不动数据）。
  - SSE 交接包少了 `artifact_refs` / `overflow_backfill` 两个字段，前端不读，Vercel 与 Railway 发布先后不影响。
- **验证**：
  - 先写的回归（改动前均失败，改动后通过）：`tests/test_agent/test_mcp_tools.py::test_oversized_tool_result_is_bounded_without_opening_a_session`、`::test_handoff_to_agent_passes_the_models_context_without_ledger_backfill`、`::test_successful_writes_do_not_open_a_ledger_session`（用会计数的 `create_session_func`，改动前分别打开了 1、1、3 次 session）。
  - `test_update_project_lock_postgres.py` 在本机一次性 PostgreSQL 库上 5 条通过；临时去掉无改动路径的 commit 后，作者起名、名字未变、标题无效三条以 `LockNotAvailable` 失败。
  - `project_title` 相关测试（`test_project_title_atomic.py`、`test_project_title_provenance.py`、`test_update_project_title.py`）与 `test_api/test_chat.py` 的 ledger 行用例照常通过；超限结构化截断用例去掉 `overflow_ref` 断言后通过。
  - 待做：staging 合成一次带超大 `query_files` 结果的 writer→reviewer 交接，确认交接正常、AI 命名的作品仍可改名、作者起的名字仍受保护。
