# Bug-Fix Note: 工具契约、匹配器与缺陷治理（Part 1）

Status: implemented

关联：
- [edit_file 没改动就如实报错](2026-10-11-edit-file-keeps-paragraphs-and-reports-no-change.md)
- [old 与 new 相同的 replace 报「未修改」，不算失败](2026-10-21-edit-file-identical-replace-is-unchanged.md)
- [模糊编辑吞掉闭引号前的句末标点；old 与 new 相同的 replace 不走模糊定位](2026-10-25-fuzzy-edit-closing-quote-and-identical-replace.md)
- [query_files 按章节或范围读取长文件](../feature/2026-10-19-query-files-range-reads.md)
- [工具载荷瘦身与范围读取快照优化（Batch A）](../architecture/2026-11-21-tool-payload-and-range-slimming.md)
- [「去AI味」统一至单循环写作 Agent（结构化 ui_action="natural_polish"）](../architecture/2026-11-22-deai-via-single-loop-agent.md)

## Problem

生产 48 小时工具调用审计（1,082 轮、7,687 次计量调用、690 次失败 / 340 次隐藏部分失败）暴露出五类高占比、纯结构/契约层面的误拒与缺陷：

1. **`update_project` 误拒（64/337，19.0%）**：模型同时提交 2–3 个 `in_progress` 任务时整次被 `ValueError` 拒绝（36 次）；单份合集大纲文件、或带有效 `artifact_refs` 的人物/设定任务因 `unit_spec`/标题前缀章号不匹配或大纲/文档类型轻微差异被 `unverified_done` 误拒（19 次），且错误里未给出引用文件的实际 `file_type`。
2. **`edit_file` 批量回滚、假 `no_change` 与过严匹配**：
   - 模型在多条编辑里漏传 `continue_on_error` 导致 64 次整批回滚（默认 `False`）。
   - 记忆陈旧但目标改动已存在时，近似匹配把 `old` 命到已改好的 `new` 上，触发 `edit.py:1052` 报 `no_change` 错误并回滚整批（29/31 次）。
   - 未读文件（`exact_only=True`）遇到 `\n` vs `\n\n`、缩进或引号样式差异即失败（140 项）；短锚点（`<6` 字）或 `ignore_punct_whitespace=False` 在 `\n` vs `\n\n` 上也无法匹配；`_check_approximate_edges` 把行尾/文末无标点标题误拒为句中截断；`_apply_delete` 缺少近似匹配兜底；`missing_id` 报错未提示本轮已知文件 ID（17/25 次 `invalid_edit`）。
3. **`create_file(source_text=...)` 漏检插话与长粘贴偶发复制噪点（10/13 次）**：`_source_copy_content` 只搜 `req_ctx.author_message`、忽略本轮运行中插话 `req_ctx.author_steering`（4 次）；1.4k–6.6k 长粘贴里模型 JSON 拷贝偶发 1–3 个字符失真（含 `U+FFFD`）即整次失败重来（6 次）。
4. **`ui_action="natural_polish"` 下 `read_skill_resource` 100% 失败**：`ui_actions.py` 注入官方 `PublicSkill('去AI味')` 指令（内含 `references/*.md` 指引）而不要求 `UserAddedSkill`，但 `mcp_tools._resolve_skill_for_tool` 仅查 `load_active_skills`，导致未手动添加该技能的用户必败。
5. **`query_files(section=...)` 标题正则偏窄与短文件硬报错（26 次）**：未识别 `卷四`、Markdown `#`/`**` 前缀、`一、` 中文序号及省略 `【】` 的标题；且对无内部标题的单章 `draft` 文件传 `section` 时直接硬报错。

## Decision

全部改动严格限定在结构/语法/工具契约层，不引入任何自然语言关键词或语义启发式：

1. **`update_project` 任务板容错与证据校准**（`file_ops/project.py`、`task_progress.py`）：
   - `in_progress_count > 1` 时保留首个 `in_progress`，其余自动归一为 `pending`，返回 `status="success"` 并附简明 `warning`（`done -> in_progress` 回退校验不变）。
   - 新增 `task_requires_unit_spec` 与 `ref_type_matches`：非正文资料文件（`outline`/`character`/`lore`/`snippet`/`document`）提供有效非空 `artifact_refs` 时直接以文件引用核验，不强求标题前缀章号范围或被误填的 `unit_spec` 卡死；正文类（`draft`/`script`）仍严格执行逐章/逐集编号核验。
   - 显式 `artifact_refs` 允许 `outline`/`document`/`lore` 三类兼容资料类型互认（`draft`/`script`/`character` 保持严格精确匹配）；仍有类型不符或缺失/空文件时，`unverified_done` 列出每个缺失 ref 的实际 `file_type` 或状态。
2. **`edit_file` 契约与匹配器升级**（`tool_inputs.py`、`file_ops/edit.py`、`mcp_tools.py`、`tool_context.py`）：
   - `EditFileInput` 在 `len(edits) > 1` 且未显式传 `continue_on_error` 时默认 `continue_on_error=True`；显式传 `false`/`"false"` 或单条编辑仍为 `False`。
   - `edit.py` 在 `content == content_before_edit` 且引号规范化前命中原文区间已逐字等于替换文本时，归入 `unchanged_edits`（`reason="already_matches"`）并附提示，不抛 `no_change` 错误也不回滚同批改动；引号规范化还原（U079）照旧报 `EDIT_ERROR_NO_CHANGE`。
   - 新增确定性空白/换行/双引号归一化匹配 `_locate_normalized_exact_spans`（保留全部其余标点与逐字一致，绝不跨段并段、不做错字猜测）：未读文件（`exact_only=True`，且未显式指定 `match_mode="exact"`）与短/严标点 `\n` vs `\n\n` 场景均支持唯一确定性命中。
   - `_check_approximate_edges` 在命中窗口止于行尾或文末时不误判为句中截断；`_apply_delete` 补齐与 `_apply_replace` 对齐的 `find_approximate_match` 兜底（含 `keep_line_breaks=True` 与 `allowed_fuzzy_spans` 守卫）。
   - `AgentRequestContext.known_file_ids()` 暴露本请求最近读写及上下文文件 ID，`_edit_file_sync` 在 `missing_id` 校验失败时将其附入错误提示与 `recent_file_ids`。
3. **`create_file(source_text=...)` 插话覆盖与长粘贴 98% 原字节回切**（`mcp_tools.py`）：
   - 同时检索 `req_ctx.author_message` 与 `req_ctx.author_steering` 中全部本轮作者原话。
   - 对 `>= 500` 字符长粘贴：若首尾非空行按顺序唯一命中、行数一致（禁止跳段/漏行/插行）、至少 `98%` 非空行逐字一致且差异字符极少，则截取并落库作者原始字节切片 `author_msg[start:end]`，保证 100% 作者原文保真。
4. **`ui_action="natural_polish"` 官方技能资源读取**（`tool_context.py`、`loop/ui_actions.py`、`mcp_tools.py`）：
   - `AgentRequestContext` 增加 `ui_action: str | None = None`（`service.py` 透传由 Wave 2 接线），`ui_actions.py` 抽出 `resolve_official_natural_polish_skill`。
   - 当 `req_ctx.ui_action == "natural_polish"` 且工具为 `read_skill_resource` 时，`_resolve_skill_for_tool` 允许解析已审核的官方 `PublicSkill('去AI味')` 及其资源，不要求 `UserAddedSkill`；普通轮次与其余技能权限不变。
5. **`query_files(section=...)` 标题识别与短文件兜底**（`file_ops/content_range.py`、`tool_inputs.py`）：
   - 扩展 `_HEADING_RE` 与匹配逻辑：支持 `卷[零〇一二两三四五六七八九十百千万\d]+`（与 `第N卷` 统一按卷号比较）、Markdown `#`/`**` 前缀与标题、`一、` 中文序号标题，以及 `【...】` 省略括号匹配。
   - 在 `QueryFilesInput.section` 说明中明确单章 `draft` 文件勿传 `section`；短文件（`<= SHORT_FILE_FULL_RETURN_MAX_CHARS` 12,000 字）上 `section` 未命中时返回全文（`content_partial=False`）与简明 `note`（超长文件未命中及多标题歧义照旧抛 `ContentRangeError`）。

## Alternatives considered

- **在 `FileEditor.edit_file` 层把默认 `continue_on_error` 改为 `True`**：最强理由是对所有调用方统一生效。未采用：直接调用 `FileEditor.edit_file` 的内部/测试契约依赖默认整批原子回滚；把多编辑默认 `continue_on_error=True` 放在 MCP 工具入参层（`EditFileInput`）既避免 64 次 LLM 批量误回滚，又保持底层 `FileEditor` 原子语义不变。
- **对未读文件直接放开 `find_fuzzy_spans` / 近似匹配**：最强理由是实现最简。未采用：会打破未读段落「不拿过时记忆做去标点/错字猜测」的安全边界；仅放开确定性空白/换行/双引号归一化匹配可消除绝大部分格式误拒，同时守住标点与文字零猜测。
- **`create_file(source_text=...)` 直接信任模型传入的含噪文本**：最强理由是省去回切逻辑。未采用：会把 `U+FFFD` 或模型笔误写进作者资料；在 `>=98%` 逐行对齐后回切作者原始字节兼顾容错与 100% 原文保真。

## Consequences

- **收益**：系统性消除 48h 审计中约半数以上可预防工具失败与整批回滚（`update_project` 误拒、`edit_file` 批量回滚/假 `no_change`/未读格式误拒、`source_text` 插话盲区与长粘贴重试、`natural_polish` 技能资源 100% 报错、`query_files(section)` 标题与单章误用），显著降低无效模型往返与延迟。
- **代价与后续接线**：
  - 已完成 `apps/server/agent/service.py` 构造 `AgentRequestContext` 时的 `ui_action=ui_action` 透传、`mcp_tools.py` 待写 `<file>` 期间允许 `create_file(file_type="folder")` 建目录，以及 `stream_adapter.py` 对 `guard_skipped=True` 的 `edit_file` 跳过假 `file_edit_*` 事件。
  - 短文件上 `section` 未命中不再报错而返回全文（同时写入全文快照）；超长文件与多标题歧义仍严格报错。
