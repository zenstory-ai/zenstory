# RepeatEditGuard 与编辑/分页阈值校准

## Problem

生产取证（2026-11-22 16:52 UTC 后约 22.9 小时窗口，321 条 assistant 消息 / 1,846 次 `edit_file`）显示，单轮内对同一文件连续执行 `>= 4` 次 `edit_file` 的串行链占全部编辑文件对的 13.0%，却消耗了 **42.5% 的 `edit_file` 调用**与 **39.0% 的编辑前思考字符**（折合约 ¥27.5/天，占平台 LLM 支出约 20.5%）。两大主导机制占比约 80%：

1. **闭环凑字数/微调打磨（52.1%）**：模型在 `write_chapter` 保存新章后或修改旧章时，反复根据 `edit_file` 回显的 `new_word_count` 每次增删 `<= 80` 字（如 `2209 → 2203 → 2202 → 2199`），单轮串行调用 4–23 次、累计重写整章 100%–978%，且经常在 `write_chapter` 刚落库后立刻 `query_files(id=..., start_char=0)` 重读刚写完的文本。仅靠提示词“至多调用一次 `edit_file`”未能止住，仍有 38.2%（110/288）的新写章节发生 `>= 2` 次后续 `edit_file`。
2. **超长章分页交错编辑（23.1% 链条，33.8% 思考量）**：提示词与工具 schema 将 `start_char` 分段读取阈值定在 `15,000` 字、单次 `edits` 拆分阈值定在 `15` 处，导致 16k–22k 字长章出现 10–21 步「读一段 → 改 1–3 处 → 读下一段」的串行循环，也拆散了合法的大批量大纲修改。

同时，任何防循环机制必须严格遵守 `AGENTS.md` 的语义判断边界（禁止对自然语言做关键词/正则意图猜测），并绝对不能误伤合法的大批量修订、输出截断 `op="append"` 续写、定位失败重试及跨文件批量修改。

## Decision

1. **新增严格结构化的 `RepeatEditGuard`（`apps/server/agent/openai_agents/repeat_edit_guard.py`）**：
   - 接入 `tools_adapter.py`、`runner.py`、`loop/engine.py`、`writing_state.py`，与 `ToolFailureBreaker`、`RepeatReadGuard` 共享请求级生命周期；只依据结构化执行事实（单文件干净成功调用次数、`len(edits)`、`op` 枚举、字符长度净增量、以及 `AgentRequestContext.saved_content_body_ids()`），零自然语言启发式。
   - **严格豁免（不计数、不拦截）**：失败/部分成功/无变更调用（`status != "success"` 或 `mutation_applied=False`，交给 `ToolFailureBreaker`）；纯 `op="append"` 续写（保护 `_BODY_CUT_CONTEXT` 截断续写）；既有文件上 `len(edits) >= 6` 的批量修改（同时清零连续微调计数，确保大纲/长章 10–30 处批量修订不被误伤）；不同 `file_id` 独立计数。
   - **本轮新写章节（`file_id in req_ctx.saved_content_body_ids()`）**：第 1 次干净 `edit_file` 正常放行；第 2 次放行并附带简短 `edit_hint`；第 3 次（非 `append`）同步拦截并返回 `error_type="edit_repeat_limit"`、当前 `word_count` 与收尾指引。
   - **既有文件**：第 1–3 次干净非批量 `edit_file` 正常放行；第 4 次放行并附 `edit_hint`（若累计替换字符 `>= 60%` 原文长度，提示考虑改用 `write_chapter`）；仅在以下两种结构条件之一成立时拦截并返回 `edit_repeat_limit`：（a）第 5 次及以上非批量调用且此前已连续出现 `>= 2` 次微调（`len(edits) <= 4`、非 `append`、净增量 `|new_chars - old_chars| <= 80`）；（b）第 8 次及以上非批量调用（小批量串行兜底上限）。
   - **刚写章节自读拦截**：对本轮 `write_chapter` 刚保存的章节，单文件 `query_files(id=...)`（全文或 `start_char=0`）直接返回轻量 `status="success"`（`self_read_guarded=True`、`content_omitted=True`、`word_count`、`read_hint`），不把刚写完的正文再次灌回上下文；若该文件刚遭遇 `anchor_not_found` 或 `stale_content`，自动放行一次真实重读用于定位恢复；关键词搜索（`query`）、`section`、`tail_chars`、`start_char > 0` 与多文件 `ids=[...]` 不受影响。
   - **遥测与非致命集成**：触发拦截时记录结构化 `INFO` 日志（`RepeatEditGuard triggered`，含 `file_id`、`written_this_turn`、`clean_calls`、`consecutive_micro_calls`、`reason`）；将 `EDIT_GUARD_ERROR_TYPES`（`edit_repeat_limit`）从 `ToolFailureBreaker` 排除，避免触发致命熔断；前端 `apps/web/src/lib/agentRoundProgress.ts` 将 `'edit_repeat_limit'` 加入 `INTERNAL_ORDERING_ERROR_TYPES`，不向作者渲染红色失败卡片。
2. **工具与提示词阈值校准**：
   - 将 `start_char` 分段读取指引从 `15,000` 字提高至 `25,000` 字，单次 `edit_file` 拆分指引从 `15` 处提高至 `30` 处，并明确不要在同一条回复里对同一 `id` 并列发出多个 `edit_file` 调用（同步更新 `prompts/base.py`、`tools/tool_inputs.py`、`tools/tool_schemas.py`、`tools/file_ops/edit.py`、`openai_agents/tool_failure_breaker.py`，总导出 schema 字符数保持在既有基线预算 `11,530` 内）。
   - 实测 A/B 发现，在 `_TOOL_USAGE_THINKING` 中新增“调用 `edit_file` 前不在思考里逐字核算字数”的反面指令后，MiniMax-M3.1-Flash 在控字数任务上反而出现“粉红大象效应”（4/6 组控字数实验 `output_tokens` 不降反升），因此在合入前移除该句，不靠反面提示词压制单轮思考量。

## Alternatives considered

1. **仅继续收紧系统提示词与自查文案，不加服务端结构化守卫**：
   - 最强论据：零运行期代码复杂度、零误拦截风险，且改动可逆。
   - 拒绝原因：生产数据已经证明，上线“至多 1 次 `edit_file`”提示词后，新写章节发生 `>= 2` 次 `edit_file` 的比例仍高达 38.2%（40 章仍跑出 4–23 次），无法靠软指令封住高耗时/高费用长尾。
2. **用关键词/正则识别用户是否要求“控字数”或“精简”，或对所有文件一刀切限制最多 3 次 `edit_file`**：
   - 最强论据：实现最简单、逻辑最少。
   - 拒绝原因：自然语言关键词/正则违反 `AGENTS.md` 语义判断边界；对既有文件一刀切 3 次上限会误伤合法的 10–30 处大纲批量修订或长章多步实质改写（`|new-old| > 80`）。
3. **在 `edit_file` 返回中直接隐藏 `new_word_count`**：
   - 最强论据：切断模型闭环追字数的数值反馈源。
   - 拒绝原因：作者和回复协议需要模型如实汇报编辑器口径的真实字数，隐藏 `new_word_count` 会诱发模型额外调用 `query_files` 查字数或自行估算虚报字数。

## Consequences

- **收益**：
  - 为 `write_chapter` 后微调循环（`>= 3` 次）、既有文件微调循环（`>= 5` 次且连续 `>= 2` 次微调）、小批量串行兜底（`>= 8` 次）以及写后无意义自读建立确定性的服务端天花板，同时不触发致命熔断、不渲染红色失败卡。
  - 16k–24k 字长章与 15–30 处批量修订不再被自家提示词人为拆碎。
  - 在 MiniMax-M3.1-Flash 真实 staging A/B（2 轮共 22 次运行，含 12 处 15k 大纲批量修订、4 文件跨文件改名+改细节、17.4k 长章 5 处定点修改及控字数压力用例）中，交付率 **22/22（100%）**，大纲批量与跨文件安全对照 **0 次误拦截**，`mega_chapter_17k_edit` 延迟显著下降（12.3s/13.4s vs 17.7s/24.3s）。
- **成本与后续**：
  - 若作者在单轮内确实对同一既有文件连续做 8 次以上小批量非微调修改，第 8 次会被提示合并为批量 `edits` 或分轮执行（可通过一次 `>= 6` 处的批量 `edits` 继续）。
  - 常规 1–2 次编辑内的思考开销（约占编辑前总思考量的 84.8%）无法通过本守卫或反面提示词压低，仍需后续单独开展 `MINIMAX_REASONING_EFFORT=medium` 盲评 A/B 评估。
