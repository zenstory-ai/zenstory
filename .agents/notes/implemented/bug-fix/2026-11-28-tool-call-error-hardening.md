# 生产工具调用报错系统性加固

## Problem

对 `prod-v2026.11.28.2` 生产环境近 24 小时工具调用报错做交叉核验后，发现多类高频失败来自工具契约过严、容错顺序缺陷、缺少结构化 `error_type` 或批量熔断交互问题：

1. **`read_files` 批量范围与短文件越界（P0-A）**：模型高频对多个 `ids` 组合使用 `tail_chars` 或 `start_char=0 + max_chars` 速览多章，旧实现直接拒绝且无 `error_type`；短文件（`<= 12000` 字符）传 `start_char > total` 时越界校验排在短文件全文返回之前导致硬报错，且 `ContentRangeError` 落入泛化异常缺少 `error_type`。
2. **`write_chapter` / `create_file` 的 `parent_id` 校验失败与安全错误白名单缺口（P0-B）**：提示词要求 `write_chapter` 显式传 49 字符 `{project_id}-draft-folder`，诱发 UUID 抄错或把 `project_id` 当 `parent_id`；`FileCRUD.create_file` 抛裸 `ValueError` 且 `create_file` 未纳入 `_SAFE_FILE_ERROR_TOOLS`；`source_text` 跨轮引用缺少明确引导。
3. **`edit_file` 别名/空串容错与 `old == new` 误报（P1）**：`_require_insert_text` 只对 `None` 回退别名，导致 `text=""` + `new="<正文>"` 误报；字段别名归一晚于 `if not op:` 推断且未支持 `old_text`/`new_text` 与 `op="replace_all"`；`old == new` 在原文未精确命中时仍报 `no_change` 失败（触发大量 `partial` 警告卡片）。
4. **并行 `file_not_found` 过早熔断、UUID 抄错诊断与 `edit_file` 提示（P2）**：`ToolFailureBreaker` 对同一 `reply_seq` 内并行发出的多个 `file_not_found` 逐条累加，导致连续两轮并行调用即误杀同批合法编辑；缺失文件 ID 长度异常（35/37/40 位）未给格式提示；跨段缺换行与同文件多次编辑缺少更直接的模型恢复提示。
5. **技能工具结构化错误与待写正文提示（P3）**：`load_skill` / `read_skill_resource` 缺少 `error_type`、无技能目录时模型仍误调、跨技能同名资源路径未指明所属技能；新建空文件后的 `awaiting_body` 未明确强调不要对其调用 `edit_file`。

## Decision

按 P0 → P3 统一收口，严格遵守「不新增自然语言关键词启发式、不模糊猜测/自动纠偏 UUID、不放宽段落换行/版本并发/计费不变量」边界：

- **P0-A（`content_range.py`、`mcp_tools.py`、`tool_inputs.py`、`tool_schemas.py`）**：
  - `apply_content_range` 对 `total <= SHORT_FILE_FULL_RETURN_MAX_CHARS`（12,000）且 `start_char > total` 改为直接返回全文并附说明 `note`，仅对超长文件保留 `ContentRangeError`。
  - `read_files` 支持批量 `ids` 搭配 `tail_chars` / `start_char`+`max_chars`（逐文件独立应用 `apply_content_range`、分别记录全文或范围快照与 `content_partial`/`content_range`）；批量 `section` 与所有 `ContentRangeError` 均返回 `error_type="invalid_read_range"`。
- **P0-B（`prompts/base.py`、`crud.py`、`mcp_tools.py`、`stream_adapter.py`、`tool_inputs.py`）**：
  - 提示词改为要求 `write_chapter` 默认省略 `parent_id`（仅分卷时传子目录 ID），并澄清 `project_id` 不是文件夹 `parent_id`。
  - `crud.py` 支持 6 个确定性逻辑目录别名（`draft`/`outline`/`character`/`lore`/`material`/`script`），仅在当前项目存在唯一匹配顶级目录或唯一预期模板根目录时解析，多义不猜测；`create_file(file_type="folder")` 传 `parent_id == project_id` 归一为根目录，`write_chapter` 传 `parent_id == project_id` 在单正文目录无歧义时视同省略（多目录分卷仍按 `chapter_target_ambiguous` 拒绝）。
  - 父目录校验失败改为抛 `EditFileError(..., error_type="invalid_parent_id")`（兼容 `ValueError`），`_create_file_sync` 输出结构化错误与 `user_message`，并将 `create_file` 加入 `_SAFE_FILE_ERROR_TOOLS`。
  - `CreateFileInput.source_text` 描述与错误提示明确限定为本轮最新一条作者消息原文，更早轮次资料改走空文件 + `<file>`。
- **P1（`edit.py`）**：
  - `_require_insert_text` 将 `""` 与 `None` 同等触发 `_INSERT_TEXT_ALIASES` 回退。
  - 字段别名归一（新增 `old_text -> old`、`new_text -> new`）前移至 `if not op:` 之前，并兼容 `op="replace_all"` → `op="replace", replace_all=True`。
  - `old == new`（明确无改动意图）无论原文是否逐字命中均永不走模糊匹配（守住 F-01 防删标点不变量），统一归入 `unchanged_edits`（`already_matches` / `identical_old_new`），零落库、不计入 `failed_edits`；`old != new` 引号规范化回退仍保留 `EDIT_ERROR_NO_CHANGE`。
- **P2（`tool_failure_breaker.py`、`tool_context.py`、`edit.py`、`mcp_tools.py`）**：
  - `ToolFailureBreaker` 对同一 `reply_seq` 内并行 `edit_file` 的 `file_not_found` 最多计 +1 次 `_FILE_NOT_FOUND_FAMILY`（成功 `edit_file` 时清空批次记录），保留跨轮 3 次熔断与请求总失败上限。
  - 缺失 ID 不符合标准 36 位 UUID 格式时在模型侧 `error` 中附加格式/长度诊断（永不自动纠偏）。
  - `edit_file` 在 `batch_anchor_invalidated` 或本轮已改过同一文件又遇 `anchor_not_found` 时提示合并为一次 `edits` 数组；`MISSING_PARAGRAPH_BREAK` 保持 `keep_line_breaks=True` 拒绝合并段落，仅在跨段匹配唯一时附加紧凑 JSON 转义原文片段。
- **P3（`mcp_tools.py`、`tool_schemas.py`）& 前端同步（`toolCardHelpers.tsx`、`locales/{zh,en}/chat.json`）**：
  - `load_skill` / `read_skill_resource` 补齐 `skill_not_found` / `skill_resource_not_found` / `skill_limit_exceeded`；找不到资源时在当前用户其他已启用技能中做精确路径查找并提示所属技能名；`LOAD_SKILL_TOOL` 描述明确无技能目录时禁止调用；`_awaiting_body_data` 明确禁止对新建空文件调用 `edit_file`。
  - 前后端错误文案与 `classifyToolFailure` 同步覆盖全部 5 个新 `error_type`（并修正 `files_not_found` 归类至 `fileMissing`）。

## Alternatives considered

1. **后端按编辑距离自动“纠偏”近似 UUID**：可直接吞掉大部分 1–3 字符 UUID 漂移。但长篇项目常有上百个结构相似的章节 UUID，自动猜测存在误改他章的高风险，违背数据安全红线，因此明确拒绝，仅提供格式/长度诊断与按 `reply_seq` 去重熔断。
2. **仅靠提示词劝模型不要批量传 `read_files` 范围参数、保留后端拒绝**：实现成本最低，但实证显示模型在多章速览时天然会组合 `ids + tail_chars/start_char`，仅改提示词无法彻底消除报错；直接支持逐文件范围切片兼顾省上下文与零报错。
3. **放宽 `keep_line_breaks=True` 允许跨段模糊替换自动补换行**：可减少一部分 `anchor_not_found`，但模型在 `new` 中往往同样漏了换行，放宽会直接合并作者段落破坏排版；因此严格保留拒绝合并的不变量，只附带唯一跨段原文片段辅助模型单次重试成功。

## Consequences

- **收益**：直接消除生产中绝大多数 `read_files` 范围报错、`parent_id` 抄错/误填 `project_id` 报错、`edit_file` 别名/空串与 `old == new` 噪声失败，避免并行 `file_not_found` 误杀同批合法编辑，并让全部新增错误具备结构化 `error_type` 与中英安全用户文案。
- **成本与注意**：`read_files` 批量范围读取现在可能在同一响应中混合部分切片文件与短文件全文，快照登记需按文件区分 `record_range_read_snapshot` 与 `_note_file_read`（已在 `_read_files_sync` 按行严格区分并在响应超限时跳过登记）；`test_query_files_range.py`、`test_edit_file_paragraph_integrity.py`、`test_edit_failure_visibility.py` 中依赖旧报错行为的 4 处用例需同步更新为新契约。
