# Simplification Note: 以 Pydantic v2 入参模型统一工具 Schema 与运行时参数校验

Status: implemented

关联：[单循环 agent](../architecture/2026-11-16-single-loop-agent.md)、[跨轮未变更会话快照结转、edit_file 批量合并指引与 query_files 批量 ids 读取](../architecture/2026-11-21-batch-tool-calls-and-session-snapshots.md)。

## Problem

写作 agent 的工具层长期存在「声明与校验分裂」的维护痛点：

1. `apps/server/agent/tools/tool_schemas.py` 手写维护 530 行 JSON Schema 字典，与 `apps/server/agent/tools/mcp_tools.py` 的运行时参数解析完全脱节，改一个参数或约束需要手改两处。
2. `mcp_tools.py` 的各个工具处理函数（`_write_chapter_sync`、`_create_file_sync`、`_edit_file_sync`、`_delete_file_sync`、`_rename_file_sync`、`_query_files_sync`、`_update_project_sync`、`_load_skill_sync`、`_read_skill_resource_sync`）各自重复编写零散的 `isinstance`、防 `bool` 混充 `int`、`coerce_bool`、别名映射（`id`/`file_id`/`fileId`、`edits`/`operations`、`currentPhase`/`writingStyle`/`projectSummary`）与不统一的错误返回格式。
3. 同时，不能简单切换为 `openai-agents` SDK 的 `@function_tool` + `strict_json_schema=True`：
   - SDK strict schema 拒绝开放的 `metadata: dict`、强行把所有可选字段塞进 `required` + nullable union，且会破坏 `minimum` / `maxLength` 在 DeepSeek 与 MiniMax Chat Completions 上的兼容性；
   - SDK `@function_tool` 使用严格 `json.loads`，丢失 `tools_adapter.py` 中面向国产模型坏 JSON 的 `json_repair` 容错，并抛出英文 `ModelBehaviorError`，与 `ToolFailureBreaker` 和前端卡片依赖的结构化中文错误契约冲突；
   - 原生 Pydantic v2 `model_json_schema()` 会为每个模型和字段塞入 `title`、`$defs`、`"default": null`、`"anyOf": [{"type": "T"}, {"type": "null"}]` 和类 docstring，直接导出会导致系统前缀工具清单 token 膨胀并击穿前缀缓存。

## Decision

1. **新增单一真相源 `apps/server/agent/tools/tool_inputs.py`**：
   - 为 `TOOL_SCHEMAS` 里的全部 9 个工具（`create_file`、`write_chapter`、`edit_file`、`delete_file`、`rename_file`、`query_files`、`update_project`、`load_skill`、`read_skill_resource`）及子结构（`EditOperationInput`、`UpdateProjectTaskInput`、`UnitSpecInput`）定义 Pydantic v2 `BaseModel` 入参模型并注册在 `TOOL_INPUT_MODELS`。
   - 集中共享校验器与类型别名：
     - `CoercedBool` / `CoercedBoolDefaultTrue` / `CoercedOptionalBool`（基于 `BeforeValidator(coerce_bool)` 归一 `"true"`/`"false"`/`"1"`/`"0"` 等松散布尔值，保留 `include_content` 的三值 `None`/`False`/`True` 语义）；
     - `NonBoolInt` / `OptionalNonBoolInt`（显式拒绝把 Python `bool` 当成 `int` 传入 `chapter`、`limit`、`offset`、`start_char`、`max_chars`、`tail_chars`、`occurrence` 等字段）；
     - `AliasChoices` + 统一的别名回退（`id`/`file_id`/`fileId`、`edits`/`operations`、`summary`/`projectSummary`、`current_phase`/`currentPhase`、`writing_style`/`writingStyle`）；
     - `format_validation_error` / `validate_tool_input` 统一输出结构化中文校验错误字典（含 `status="error"`、`error_type`、`error`、`message`、`mutation_applied=False` 及工具特定附加字段，完全兼容 `ToolFailureBreaker`、UI 卡片与既有测试）。
2. **零 token 膨胀的紧凑 JSON Schema 导出（`export_tool_schema` / `build_tool_schema`）**：
   - 内联 `$defs`/`$ref`、剔除全部 `title`、剔除类 docstring 产生的对象级 `description`、剔除冗余 `"default": null` 与开放字典的 `"additionalProperties": true`、把可选联合 `{"anyOf": [T, {"type": "null"}]}` 扁平化为 `T`（保留显式 `additionalProperties: false`、`minimum`、`maxLength`、`maxItems`、`uniqueItems` 与有意义的非空默认值）。
   - `tool_schemas.py` 改为直接由 `tool_inputs.py` 的模型导出 `TOOL_SCHEMAS`，序列化字符数与重构前手写基线严格持平（零前缀缓存膨胀）。
3. **保留 `strict_json_schema=False` + `json_repair` 并统一接入点**：
   - `openai_agents/tools_adapter.py` 继续使用 `FunctionTool(..., strict_json_schema=False)` 与 `parse_json_object`（含 `json_repair`）；`_cancels_overdue_file` 同步改用 `DeleteFileInput`，与 `_delete_file_sync` 共用同一套 `id` 别名和 `recursive` 布尔归一逻辑。
   - `mcp_tools.py` 全部 9 个同步处理器在入口调用 `validate_tool_input`，确保 SDK 适配路径与单测直接调用 `mcp_tools.*` 走完全一致的校验契约，并保留各工具的领域错误语义（如 `edit_file` 按项报告 `failed_edits`、`query_files` 延迟校验 `response_mode`/`content_preview_chars`、`update_project` 超长 `title` 返回 `title_skipped="invalid_title"` 与部分成功回退）。

## Alternatives considered

- **直接迁移到 SDK `@function_tool` + `strict_json_schema=True`**：最强理由是可以直接复用 SDK 的自动参数解析与 schema 生成，少写一层适配。未采用：会拒绝 `create_file.metadata` 的开放 `dict`、在 DeepSeek / MiniMax 上触发 schema 兼容报错、丢失 `json_repair` 对坏 JSON 的容错，并把结构化中文错误退化为英文 `ModelBehaviorError`。
- **保留手写 `tool_schemas.py`，只在 `mcp_tools.py` 中用 Pydantic 做运行时校验**：最强理由是无需编写 `export_tool_schema` 清洗器，零 schema 漂移风险。未采用：schema 与校验依然双写分离，新增或修改参数仍需维护两份定义，未真正解决单一真相源问题；实测约 60 行的 `export_tool_schema` 即可做到与手写基线 100% 等价且零字节膨胀。

## Consequences

- **收益**：
  - 9 个写作工具的 JSON Schema 与运行时入参校验收束为单一真相源 `tool_inputs.py`，大幅削减 `tool_schemas.py` 与 `mcp_tools.py` 的手写样板与漂移风险。
  - 所有工具共享一致的布尔强转、防 `bool` 混充 `int`、参数别名和结构化中文报错，同时完整保留 `strict_json_schema=False`、`json_repair`、`ToolFailureBreaker` 与前缀缓存字节稳定性。
- **代价与注意事项**：
  - 新增或调整工具参数时需同步维护 `tool_inputs.py` 中对应模型的字段定义；若某些字段因历史语义需延迟校验（如 `query_files.response_mode`、`update_project.tasks`）或仅供内部调用（如 `create_file.content`/`order`），需显式使用 `WithJsonSchema` 或 `_schema_excluded_fields` 保持对外 schema 与运行时边界清晰。
