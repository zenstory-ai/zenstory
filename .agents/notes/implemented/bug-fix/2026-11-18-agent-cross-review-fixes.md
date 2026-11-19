# Bug-Fix Note: 写作 Agent 交叉审查修复（局部去 AI 味原稿保护、单轮上下文上限、压缩边界与终态结算）

Status: implemented

关联决策：部分修订 [审阅拒绝后的保存受保护](2026-10-25-reviewed-reject-save-protected-from-chat-delete.md) 中「`AI polish (reviewed)` 一律不保护」在含作者段落文件上的口径（旧笔记不改写）；补充 [确认落库的写入证据](2026-11-17-committed-write-evidence.md) 与 [单循环上线首日修复](2026-11-18-loop-post-launch-fixes.md)。

## Problem

对单循环写作 Agent 执行三路独立审查与交叉复核（基准提交 `a33febfd`）后，定位出四组主流程可达的确切缺陷：

1. **手写稿或混合稿局部点「去 AI 味 → 采用修改」后，整章丧失防整份覆盖 / 防删保护**：编辑器采纳去 AI 味时保存为 `change_source="user", change_type="ai_edit", change_summary="AI polish (reviewed)"`。`author_edit_guard._latest_text_owner_is_author` 见 `latest.change_type == "ai_edit"` 且非紧跟 AI 写入的 `"AI edit (reviewed)"` 即直接返回 `False`，将包含数千字作者亲手原稿的章节判为纯 AI 资产，使应用内对话的 `write_chapter` 可直接整章覆盖、`delete_file` 可直接软删除。
2. **`<turn_context>` 对焦点文件与附加长稿无单轮预算上限**：上传草稿合法上限为单篇 50 万字、单次最多附加 20 个文件与 20 条素材，而 `build_loop_turn_context` 原样拼入全文，且本轮 `user` 消息同时绕过轮前压缩、`bound_history_for_provider` 与 `IntraRunToolOutputTrimmer`，导入长卷附加提问时首轮即可突破 1M 上下文窗口报 `ERR_AGENT_CONTEXT_TOO_LONG`。
3. **轮间压缩在裁剪切片退化为空时仍生成摘要覆盖会话，且吞掉成本上限 `APIException`**：
   - 末轮以超大 `function_call_output` 或悬空 `assistant` 结尾且预算减半低于末项体积时，`bounded_items` 返回 `[]`（或留下无前导 `user` 的悬空切片），`compact_history` 未校验非空即调用模型并把空白摘要以 `covers_through_message_id = last_message_id` 落库，丢失此前压缩范围内的历史脉络；
   - `compact_history` 用宽泛 `except Exception:` 吞掉免费用户 ¥15 每日成本兜底抛出的 `APIException(402)`，重撞主循环并将 `+100k` 压缩退避写入用户消息；
   - 无 `prompt_chars` 的存量旧会话兜底分支用 `items is not history.items` 判折叠副本，被 `service.py` 的 `list(...)` 浅拷贝恒真绕过。
4. **流式保存失败残留空白占位、0 字已有空章待写状态漏记与终态后停止竞争**：
   - `StreamAdapter._save_file_content` 在同步明确失败（已回滚）分支与 `TimeoutError` 分支共用 `saved_file_word_counts[id] = None`，被 `RunOutcome.removable` 的 `set(self.saved_file_word_counts)` 全部保留，使 `ERR_AGENT_FILE_SAVE_FAILED` 退费后的空白占位章回收失效；
   - 0 字已有空章走 `write_chapter(action="replaced", original_content_length=0)` 时未纳入 `file_body_pending()`，`<file>` 前短过渡语遇保存失败或流报错时被误判 `ended_on_reply=True` 不退费；
   - 终态帧（`done` 或不可退 `error`）已发出、服务端仍在 `finally` 收尾时到达 `/agent/stop` 会抢占终态并触发停止退费；`update_project` 未接入 `_offload_write_tool` 与写调用跟踪，停止瞬间已落库的项目设定修改会漏记计费。

## Decision

1. **去 AI 味保存按段落归属保留作者保护**：`author_edit_guard._latest_text_owner_is_author` 在最新版本为编辑器润色保存（`user/ai_edit` 的 `"AI polish (reviewed)"` 或不紧跟 AI 写入的旧版 `"AI edit (reviewed)"`）时调用 `author_changes.line_blame`：只要回放窗口不完整、存在非空 `AUTHOR`/`ORIGINAL` 段落或存在 `author_hunks`（含作者删除记录），仍判定为受保护（`True`）；纯 AI 章节经去 AI 味润色后全篇段落仍归 `AI`，维持不受保护（`False`）。回放异常或空版本链照旧按保护状态未知拒绝。
2. **`<turn_context>` 增加 `AGENT_LOOP_TURN_CONTEXT_MAX_TOKENS`（默认 `120,000` 供应商 token）上限**：`build_loop_turn_context` 在条目超出上限时复用 `TokenBudget` 截断并渲染既有 `[已截断：显示 x/y 个字符；全文用 query_files(id="…")]` 标注，预算分配优先保住引用与当前焦点文件全文（使焦点章 `read_snapshots` 继续供 `edit_file` 使用），被截断的超大附加稿因 `is_truncated=True` 自动不登记完整快照。`service.py` 运行前历史兜底同步扣减超大 `<turn_context>` 增量，并在丢弃旧消息前先执行 `IntraRunToolOutputTrimmer` 折叠旧工具大载荷。
3. **收紧轮间压缩与浅拷贝边界**：
   - `bounded_items` 在裁剪后找不到任何前导 `user` 消息时统一返回 `[]`，不向供应商发送 `assistant` / `function_call` 开头的悬空切片；
   - `compact_history` 在 `bounded` 为空时直接返回 `SKIPPED_EMPTY_OR_TRUNCATED`，绝不发送空上下文摘要或写入 `covers_through_message_id`；`400/413/422` 减半重试时同步重算工具折叠上限；捕获到 `APIException` 时直接原样上抛，不降级为 `SKIPPED_PROVIDER_ERROR`；
   - `effective_history_provider_tokens` 的存量旧行分支改用 `target_items != history.items` 判别已折叠/裁剪副本，`list(history.items)` 浅拷贝保留实测 `last_prompt_tokens`。
4. **精确区分保存超时与同步失败，对齐空章替换、事后停止与项目修改结算**：
   - `StreamAdapter` 仅在发生 `TimeoutError`（后台线程可能仍在提交）时记录 `_timed_out_save_file_ids` 并同步至 `RunOutcome.uncertain_save_file_ids`；`RunOutcome.removable` 只保留 `keep_file_ids`、`uncertain_save_file_ids` 与确认保存（`words is not None`）的文件，同步失败回滚的空白占位章在退费时照常软删回收，`saved_file_word_counts[id] = None` 的交付/未确认回执语义不变；
   - `StreamBillingTracker` 新增 `empty_replace_target_ids` 跟踪 `original_content_length == 0` 的 `write_chapter(replaced)`：收到流式正文前（或收到 `ERR_AGENT_FILE_SAVE_FAILED` / `body_saved=False` 回滚后）计入 `file_body_pending()`，中途停止时已流出的正文仍照常计为写入；
   - `api/agent.py` 的 `_RunningStream` 绑定 `tracker`，`stop_stream` 在 `tracker.saw_terminal_event` 已为真时不再接受停止（返回 `stop_requested=False`）；
   - `mcp_tools.update_project`（模型调用路径）改走 `_offload_write_tool` 并纳入 `StreamBillingTracker` 的在途写调用跟踪。

## Alternatives considered

- **把所有 `user/ai_edit/"AI polish (reviewed)"` 一律当作作者所有。** 最强理由：无需调用 `line_blame`，O(1) 元组判断最简单。未采用：纯 AI 生成的章节做一次去 AI 味后没有任何作者手写字句，若一律锁死会让后续正常的「重写这一章」（`write_chapter`）误报「有作者手动保存的内容」，破坏既有契约与单测。按 `line_blame` 仅在编辑器润色保存这一个分支回放，既不误锁纯 AI 章，也不放过任何含作者原稿或手改的章节。
- **把同步保存失败从 `saved_file_word_counts` 中彻底删键（不写 `None`）。** 最强理由：无需新增 `uncertain_save_file_ids` 字段，`RunOutcome.removable` 原样不动即可放行清理。未采用：`RoundWrittenFiles.files`、`_body_attempted` 与 `_has_unconfirmed_body` 均依赖 `final_word_counts[id] is None` 排除流过 chunk 但未保存的文件并标记未确认正文；保留 `None` 回执语义、单独记录真正超时的 `uncertain_save_file_ids`，不会牵动交付回执与既有单测契约。
- **把 `original_content_length == 0` 的 `write_chapter(replaced)` 直接并入 `replace_target_ids`。** 最强理由：少维护一个集合。未采用：非空替换章在中途停止时由截断覆盖保护拒绝落库（故流式 `file_content` 不算写入），而 0 字空章无原文要保护、中途停止时流过的半截正文会由后台补存落库；直接并入 `replace_target_ids` 会使空章流过正文后停止误判未产出而退费。独立跟踪 `empty_replace_target_ids` 同时兼顾落库前待写状态与中途停止补存计费。

## Consequences

- 收益：作者手写原稿或局部手改过的章节使用「去 AI 味」后，聊天整章覆盖与删除保护不再失效；附加数十万字导入长稿不会在首轮打穿 1M 窗口；压缩在极端大工具输出下不会生成空白摘要覆盖会话，免费成本兜底 402 也不会污染压缩退避；同步保存失败能如实回收空白占位章，空章替换失败、事后停止和在途项目设定修改的计费与实际落库保持一致。
- 代价：最新版本是编辑器 `AI polish (reviewed)`（或非紧跟 AI 写入的旧版 `AI edit (reviewed)`）的文件，在聊天触发 `delete_file` 或 `write_chapter` 整章替换时会多执行一次有界 `line_blame`（最多 40 个版本、6000 行上限）；普通 AI 版本与作者手动编辑版本仍走 O(1) 元组判断。
