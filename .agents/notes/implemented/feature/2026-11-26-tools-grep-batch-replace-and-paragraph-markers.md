# 写作工具 v2：精确检索（matches）、跨文件批量替换与段落字数视图

## Problem

生产复盘显示，单循环写作 agent 在跨章检索、全书统一改名/标点修正以及控字数改稿三类高频任务上存在明显摩擦与 token 浪费：

1. **跨章排查缺乏紧凑 grep 视图**：查找单字符标点（如半角 `?`）或特定词句时，普通列表/全文读取容易拉出大量无关预览或整章正文，且无法直接给出 1-based 段落号与行号。
2. **全书统一替换只能逐章串行 `edit_file`**：全书人名、专名或标点统一被迫在几十章上逐个调用 `edit_file`，轮次多、易触发单轮上限或因某章偶发失败中断全局进度。
3. **控字数与长章定位缺段落级字数标注，且抄写段号会导致匹配失败**：模型在改长章或控字数时习惯心算字数或误把阅读视图里的段号前缀抄进 `edit_file` 的 `old`/`new`，甚至在长段上被近似匹配吞掉前缀并把标记写进正文。

## Decision

在严守现有架构不变量（无自然语言语义启发式、完整文件写锁与版本并发控制、原稿历史备份与撤销锚点、项目隔离）的前提下，落定以下四项工具增强：

1. **`query_files(response_mode="matches")` 精确字面检索**：
   - 在 `agent/tools/file_ops/content_range.py` 提供纯字面子串检索 `find_literal_matches`（无正则编译、零 ReDoS 风险，支持 `?` 等单字符标点），按 1-based 段落号（非空行）与物理行号返回紧凑上下文片段。
   - 在 `agent/tools/mcp_tools.py` 实现 `_query_files_matches_sync`，严格限定 `req_ctx.project_id` 与项目归属校验，排除软删除文件与文件夹，支持 `file_type` / `file_types` / `parent_id` / `id` / `ids` 过滤，并以单文件 8 处、总计 60 处、最多 30 个文件封顶返回 `total_matches`、`matched_files`、`truncated` 与紧凑 `files[]`，不返回全文。
2. **`batch_replace_files` 跨文件确定性批量替换**：
   - 在 `tool_inputs.py` / `tool_schemas.py` / `registry.py` 新增超紧凑 5 字段工具 `batch_replace_files(old_text, new_text, file_ids?, file_type?, dry_run=false)`，追加至 `LOOP_TOOL_NAMES` 末尾以保住既有 9 个工具的前缀缓存。
   - 在 `FileEditor.batch_replace_files` 实现纯字面精确替换（零模糊猜测）、严格项目隔离与软删除/文件夹排除、按确定性顺序（`order, created_at, id`）单次最多修改 30 个文件并返回 `remaining_files`；`dry_run=true` 仅预览命中数与示例片段，不写库；`dry_run=false` 对每个命中文件走 `FileEditor._commit_ai_content`（文件写锁、`stage_pre_ai_write_backup` 原稿备份、AI `FileVersion`、`body_guards` 保护、陈旧快照核验与逐文件 `undo` 锚点），单文件失败按 `skipped_files` 隔离回滚且不影响其余文件。
   - 接入 `tools_adapter.py`（待写 `<file>` 期间与 `edit_file` 同口径拦截；跨文件真实写入后重置 `ToolFailureBreaker.consecutive_failures`）、`repeat_read_guard.py`（记入写入台账并清零已改文件读取计数）与 `stream_adapter.py`（为每个真实修改的文件发出 `file_edit_start` / `file_edit_applied` / `file_edit_end` 事件，供前端实时刷新）。
3. **段落编号与字数视图（`show_paragraph_stats`）与 `edit_file` 仅失败触发的前缀剥离重试**：
   - `query_files` 增加 `show_paragraph_stats: bool = False`（并在全文/范围读取返回 `paragraph_count`）：开启时将每个非空段落格式化为 `[P01 · 84字] <原段>`（范围读取按全文全局段号起算），且保证 `read_snapshots` 始终保存未标注的原始正文，不破坏 `edit_file` 陈旧检查。
   - 在 `FileEditor._edit_file_impl` 内置仅在定位失败（`anchor_not_found`）时触发一次的前缀剥离重试：识别 `^\[P\d+(?:\s*·\s*\d+字)?\][ \t]*` 与 `^[ \t]*\d+→`（保留中文全角缩进 `\u3000`）。首次尝试不剥离，确保正文里原本就带 `[P1]` 的文本可逐字编辑，同时在首次尝试含前缀目标时延后近似匹配避免长段把标记写入正文；仅当 `old`/`anchor` 的所有非空行都带前缀时才剥离重试一次（若 `new`/`text` 的所有非空行也带前缀则同步剥离，防止标记落盘），成功时在 detail 标记 `paragraph_prefixes_stripped=True`。
4. **提示词精炼引导**：
   - 在 `agent/loop/prompts.py` 的 `LOOP_WORKING_RULES` 增加 2 条精炼工作方式说明，引导跨章排查用 `response_mode="matches"`、跨文件统一替换用 `batch_replace_files`、长章控字用 `show_paragraph_stats=true`，并避免在思考里预写整章正文或完整 `old`/`new` 副本。

## Alternatives considered

1. **在 `query_files` 默认总是返回带 `[P01 · 84字]` 前缀的正文**：
   - 最强理由：模型无需额外传参就能随时看到每段字数与段号，零学习成本。
   - 放弃原因：会改变既有全文读取契约、增加所有普通读取的 token 消耗并破坏大量依赖原始 `content` 逐字返回的测试与调用方；改为默认 `False`、按需传 `show_paragraph_stats=True` 既兼容既有行为，又在控字数/长章定位时提供精确视图。
2. **让 `query_files(response_mode="matches")` 支持用户/模型传入正则表达式**：
   - 最强理由：正则在复杂模式匹配时表达力更强。
   - 放弃原因：不可信正则在长小说正文上存在灾难性回溯（ReDoS）风险，且容易因转义错误导致问号 `?`、括号等标点检索失败；纯字面子串检索天然安全、行为确定，已覆盖人名、术语与标点排查。
3. **在 `edit_file` 首次匹配前直接无条件剥离 `[P01]` 前缀**：
   - 最强理由：实现更简单，省去一次重试分支。
   - 放弃原因：会破坏正文或设定集里本身以 `[P1]` 开头的真实文本编辑能力；遵循「仅在定位失败且所有非空行都带前缀时剥离重试一次」的安全契约，既零副作用又不漏拦长段近似匹配污染。

## Consequences

- **收益**：跨章检索不再把整章正文灌入上下文；全书统一改名/标点从几十轮串行 `edit_file` 收敛为 1–2 次确定性 `batch_replace_files` 且每文件保留独立版本历史与撤销锚点；控字数改稿获得真实段级字数，误抄段号前缀可自动无损恢复。
- **代价与维护点**：`TOOL_SCHEMAS` 紧凑体积因新增 `batch_replace_files` 与 `query_files` 两项参数增加约 1KB（已同步更新 `test_tool_inputs.py` 预算基线）；`batch_replace_files` 单次上限 30 个文件，超大项目需模型按 `remaining_files` 分批续调。
