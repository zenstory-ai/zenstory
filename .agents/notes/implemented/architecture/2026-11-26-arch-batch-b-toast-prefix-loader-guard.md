# Arch Batch B：TOAST UTF-8 压缩、System Prompt 全局静态前缀、SessionLoader 批量/游标查询与 RepeatEditGuard 闭环

Status: implemented

## Problem

架构深度审计定位到四类高频线上开销与守卫盲区：

1. **TOAST 体积翻倍（P0-1）**：`MessageManager._serialize_tool_calls` 与 `_serialize_message_metadata` 使用默认 `json.dumps`（`ensure_ascii=True`、`separators=(", ", ": ")`），中文字符被转义为 6 字节 `\uXXXX` 且携带多余空格，直接放大 `chat_message` TOAST 体积约 2 倍。
2. **跨项目 KV 前缀缓存被项目 UUID 截断（P0-4）**：`get_base_prompt` 末尾拼接项目级 `folder_ids`、`directory_structure`、`character_detection` 和 `project_id`，而 `prompts/__init__.py` 与 `MessageManager.build_system_prompt` 在其后追加纯静态的 `GENRE_NORMS`、回复语言规则、`LOOP_INPUT_LAYOUT_SECTION`、`LOOP_WORKING_RULES` 与 `skill_catalog`（约 4.5k–6k tokens）；此外 `_get_folder_ids` 在多个规范同名目录并存时以可变的子文件计数决胜，会话中途创建/移动文件会翻转 `folder_ids` 并击穿 `system_prompt_sha256`。
3. **SessionLoader 重复扫库与 N+1 / SAVEPOINT 往返（P0-3 / P1-2）**：
   - `session_written_file_ids` 在逐行循环内执行 `SELECT File.id ... WHERE File.id IN (...)`（N+1）且使用 `OFFSET` 分页；`session_read_snapshots` 同样使用 `OFFSET` 分页。
   - `author_edits_for_files` 对至多 30 个候选文件逐一开启 `session.begin_nested()`（每轮最多 60 条 `SAVEPOINT/RELEASE` 往返），并对无版本、纯作者原稿或未手改纯 AI 文件逐个跑 `line_blame`（每文件 3–4 条串行 SQL）。
4. **`RepeatEditGuard` 三个循环与绕过盲区（P0-8）**：
   - `self_read_guarded` 与 `blocked_edits` 均无连续/单文件硬上限且被 `ToolFailureBreaker` 排除，顽固模型可空转打满 `max_turns=60`；
   - `_plan_self_read` 仅拦截 `len(target_ids) == 1`，`query_files(ids=[刚写章, 另一文件])` 可完全绕过自读守卫；
   - `observe()` 写死 `status != "success"` 返回，导致 `continue_on_error=True` 下 `status="partial"` 且 `mutation_applied=True` 的真实落库编辑既不计数也不更新已写长度。

## Decision

1. **紧凑 UTF-8 JSON 持久化（`agent/core/message_manager.py`）**：
   - `_serialize_tool_calls` 与 `_serialize_message_metadata` 统一改为 `json.dumps(..., ensure_ascii=False, separators=(",", ":"))`，与 `chat_storage.py` 和 `round_outcome.py` 对齐。
2. **严格「全局静态前缀 → 项目静态后缀」与稳定目录排序（`agent/prompts/base.py`、`agent/prompts/__init__.py`、`agent/core/message_manager.py`）**：
   - 拆分 `get_base_prompt_static_prefix` 与 `get_base_prompt_project_suffix`（保留 `get_base_prompt` 完全向后兼容），在 `prompts/__init__.py` 新增 `get_prompt_parts_for_project_type` 并让 `get_prompt_for_project_type` 将 `GENRE_NORMS` 置于项目 UUID 之前。
   - `MessageManager.build_system_prompt` 先拼接 `static_prefix`（BASE + `GENRE_NORMS`）、回复语言规则、`LOOP_INPUT_LAYOUT_SECTION`、`LOOP_WORKING_RULES`、`skill_catalog`，再在最末尾拼接 `project_suffix`（`folder_ids`、`directory_structure`、`character_detection`、`project_id`）。
   - `_get_folder_ids` 改用不可变的 `(created_at ASC, id ASC)` 决胜（规范目录优先，兜底未识别旧目录仅在无规范目录时按需查去重 `(parent_id, file_type)` 并同样按 `(created_at ASC, id ASC)` 排序），确保会话中途增删移动文件永不翻转 `folder_ids`。
3. **SessionLoader 批量查询、游标分页与免 SAVEPOINT 健康路径（`agent/core/session_loader.py`）**：
   - `session_written_file_ids` 与 `session_read_snapshots` 全部改用 `(created_at, id) < cursor` 游标分页消除 `OFFSET`；`session_written_file_ids` 先收集扫描出的候选 `file_id`，循环外执行单次批量 `SELECT File.id ... WHERE File.id IN (...)`。
   - `author_edits_for_files` 先用单条批量聚合 + 最新版本元数据联表 SQL（不碰 `FileVersion.content`）预查候选文件的 `max_ver`、`ai_count`、`author_count`、最新 `char_count` 与 `created_at`，直接跳过无版本、无 AI 版本、以及无作者版本且长度/时间戳均未漂移（未超回放窗口与行数上限）的文件；同时保留无版本手改（长度或 `updated_at` 漂移）的完整检测。
   - 移除 `author_edits_for_files` 健康路径上的逐文件 `session.begin_nested()`，仅在 `author_changes` 异常或返回 `failed=True` 时回滚只读事务。
4. **`RepeatEditGuard` 闭环加固（`agent/openai_agents/repeat_edit_guard.py`）**：
   - 新增连续拦截硬上限 `MAX_CONSECUTIVE_GUARD_INTERCEPTS = 3` 与单文件累计上限 `MAX_PER_FILE_GUARD_INTERCEPTS = 4`：前 1–2 次保持软跳过/自读保护以维持既有温和收敛体验，触顶后升级返回 `status="error"`、`error_type="edit_guard_loop_exhausted"`（`guard_escalated=True`、`stop_calling_tools=True`），由既有 `ToolFailureBreaker` 计数并在继续空转时熔断，无需改动 `tools_adapter.py` 或 `runner.py`。
   - `_plan_self_read` 支持多 ID `query_files(ids=[...])`：全为本轮刚写文件时同步整次拦截（零 DB 查询）；混合批（刚写章 + 既有文件）放行既有文件读取并在 `observe()` 中剥离刚写章正文并累计单文件拦截计数（超单文件上限时同步升级拒绝）。
   - `observe()` 将 `status="partial"` 且实际发生落库变更（`mutation_applied=True` 或 `edits_applied > 0`）计为真实编辑并更新 `last_length` / `last_word_count`，仅豁免真实失败与无变更调用。

## Alternatives considered

1. **在 `_get_folder_ids` 中保留“规范目录并存时按子文件计数决胜”**：
   - 最强论据：历史遗留项目若在创建确定性目录后又把文件放进同名旧目录，按子文件计数可自动偏向非空目录。
   - 拒绝原因：子文件计数随每轮新建/移动文件实时变动，只要两个同名规范目录同时存在，会话中途写入即可翻转 `folder_ids` 并立即击穿 `system_prompt_sha256` 与 `wire_prefix_cache`；改为 `(created_at ASC, id ASC)` 在正常项目和“旧目录早于后续误建的确定性目录”的遗留项目中均能稳定命中真实主目录。
2. **在 `author_edits_for_files` 中跳过所有 `author_count == 0` 的文件而不核对长度/时间戳**：
   - 最强论据：预查询更简单，无需联表查最新版本 `char_count` / `created_at`。
   - 拒绝原因：免费用户版本额度用满后在编辑器保存不会插入新的 `FileVersion` 行，直接跳过会漏检无版本手改（违反 `test_unversioned_author_text_counts`）。联表读取轻量元数据列仍为单条 SQL、不碰 TOAST 正文，兼顾性能与零漏检。
3. **让 `RepeatEditGuard` 首次拦截即返回 `status="error"` 计入 `ToolFailureBreaker`**：
   - 最强论据：实现最简单，无需维护连续/单文件计数。
   - 拒绝原因：会退回历史 #483 之前的问题——普通模型偶尔触发 1 次 `edit_repeat_limit` 就落库红色错误卡并拉高失败率。保留前 1–2 次软跳过、仅在顽固重复（连续 3 次或单文件 4 次）时升级熔断，同时保住正常体验与防 60 轮空转底线。

## Consequences

- **收益**：
  - 新落库的 `chat_message.tool_calls` 与 `message_metadata` 中文体积立降 ~45%–50%。
  - 同一写作类型跨项目系统提示词公共静态前缀完整覆盖基础协议、题材规范、回复语言、循环规则与技能目录，且会话内 `folder_ids` 不再因写入抖动。
  - `session_written_file_ids` 消除 N+1 和 `OFFSET`；`author_edits_for_files` 健康路径零 `SAVEPOINT` 并跳过绝大多数未手改文件的 `line_blame` / `SequenceMatcher`。
  - `RepeatEditGuard` 彻底堵住自读/软跳过无限空转、多 ID 绕过和 `partial` 编辑漏记三个盲区。
- **成本**：
  - 系统提示词段落重排会在发布后触发一次全量旧前缀缓存刷新（一次性，随后跨项目命中率显著提升）。
  - 历史遗留双规范目录测试已更新以对齐不可变 `(created_at, id)` 规则；`test_message_manager_persistence.py` 与 `test_repeat_edit_guard.py` 同步更新为新契约。
