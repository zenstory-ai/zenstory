# Bug-Fix Note: 单循环 Agent 生产复盘跟进修复（自查收尾落地、重载按键保护、整章替换标题并发与计费/压缩对齐）

Status: implemented

关联决策：补充修订 [时限收尾与墙钟硬超时暂停卡](2026-10-31-wall-clock-pause-card-and-receipt.md) 在单循环写后自查尾巴上的落地行为，补充 [用单循环 agent 取代多 agent 图](../architecture/2026-11-16-single-loop-agent.md)（第 4、6、9、10 项）、[停止产出定义与回合收尾](../architecture/2026-10-09-stop-output-definition-and-round-outcome.md)、[单循环上线首日修复](2026-11-18-loop-post-launch-fixes.md) 与 [聊天改稿直接展示](../feature/2026-11-18-direct-ai-edits.md)。旧决策记录本身不改写。

## Problem

单循环写作 Agent 上线生产约 23 小时（对照只读生产数据库与日志）后，暴露出五组高价值确切问题：

1. **已落库章节的写后自查尾巴撞上收尾时间时发出「任务可能还没做完 / 继续」暂停卡（P2-1）**：`loop/engine.py` 在完整章节正文确认落库后固定排一次自查续跑；若下一轮循环顶端 `run_meter.past_wrap_up` 为真，引擎不区分待办类型一律发出 `WRAP_UP_TEXT` 与 `ITERATION_EXHAUSTED(layer="collaboration", limit="wall_clock")` 并将本轮记为 `run_deadline_exceeded`。生产 Pro 会话中 35 轮已落库章节撞到该时间点后，有 25 轮（71%）紧接着点击通用「继续」按钮，导致模型在无待办结构上下文时写出超出作者要求的新章节。
2. **流式结束后编辑器重载覆盖作者在 `fileApi.get` 往返期间刚打的字（P2-2）**：`Editor.tsx` 在收到 `file_content_end` 时立即解锁输入框，100ms 后调用 `loadData({ followServer: true })`。`loadData` 只在 `await fileApi.get` 之前检查未保存改动；若作者在网络请求在途期间输入文字，响应返回后会无条件 `setEditContent(serverContent)` 并重置基线，静默抹掉刚输入的文字。
3. **同一条回复内 `write_chapter(replaced)` 后紧跟 `rename_file` / `edit_file` 使整章替换因乐观锁作废（P2-3）**：工具结果与提示词曾告知模型「替换已有章节时标题沿用，要改标题用 `rename_file`」。模型在同一回复内依次调用 `write_chapter`（记录基线 `updated_at`）和 `rename_file` / `edit_file` 时，后者先推进数据库 `File.updated_at`，使下一条回复流式生成的数千字 `<file>` 新章在 `replace_body` 乐观锁校验时 100% 以 `ConcurrentFileWriteError` 拒存。
4. **轮间压缩请求的工具 schema 缺少 `"strict": false`，且未显式禁用客户端自动重试（P2-5c）**：`compaction._chat_tools()` 手工拼装工具字典，比 `tools_adapter` 经 SDK `Converter.tool_to_openai` 序列化的 `FunctionTool(strict_json_schema=False)` 少了 `"strict": false` 字段，在 `tools` 头部打断 300k+ token 前缀缓存；同时非流式压缩请求沿用了底层 OpenAI 客户端的默认自动重试。
5. **待写导语计费、导入拆章识别、MiniMax 超窗错误码与过时提示文案（P2-4 / P3-H / P3-B / P3-D）**：
   - `StreamBillingTracker` 在 `file_body_pending()` 为真时未拦截 `_prose_reached`：`<file>` 前被扣留的引子一旦达到 60 单位，后续报错、停止或超时仍被按 `prose_output` 扣费；`body_saved=False` 的拒存替换也未从 `replace_target_ids` / `empty_replace_target_ids` 清除；
   - 导入多章小说生成的章节标题形如 `"{书名} - 第N章 …"`，章号存于 `file_metadata.chapter_number`，`write_chapter` 只匹配标题前缀而漏认导入章节、重复新建；
   - MiniMax 返回 `"context window exceeds limit (2013)"` HTTP 400 未命中 `_CONTEXT_TOO_LONG_MARKERS`，且单循环下 `ERR_AGENT_CONTEXT_TOO_LONG` 需要保留 `retryable=True` 以便点击重试触发轮前压缩恢复；
   - `mcp_tools.py`、`tool_schemas.py`、`prompts/base.py` 与 `agent/AGENTS.md` 仍残留已下线的「diff 审阅」「empty 设了也不生效」及与一到三句总结冲突的强制 `- ` 列表要求。

## Decision

1. **结构化区分续跑类型，仅剩写后自查的收尾正常落地（P2-1）**：
   - `loop/engine.py` 按结构化事实跟踪 `followup_kind`（`"self_check"` / `"correction"` / `"cut_body"` / `"unsaved_replace"` / `"unexecuted_tool"` / `"file_continuation"` / `"steering"`），绝不从自然语言猜测作者要写几章。
   - 当 `runs > 0 and run_meter.past_wrap_up` 且 `followup_kind == "self_check"`（本轮已有确认落库的正文、无待写空文件、无输出上限截断残稿、无未保存整章替换、无未执行工具协议、无运行中插话）时：不发 `ITERATION_EXHAUSTED` 暂停卡；若上一轮恰好停在 `</file>` 尚未向作者写总结（`ended_on_file` 为真），补发一句确定性的中/英文已保存说明（`run_support.wrap_up_self_check_skipped_text`，列出已存标题并说明时间快到未再多做一轮自查，不引导回复「继续」）；随后照常执行 `auto_finalize_task_board_on_completion`，以 `end_turn`（`completed`）正常收尾。其余未完成续跑类型（`cut_body`、`correction`、`unsaved_replace`、`steering` 等）保持既有 `collaboration/wall_clock` 暂停卡不变。
2. **编辑器流式重载返回后二次校验脏缓冲区（P2-2）**：
   - `apps/web/src/components/Editor.tsx` 的 `loadData` 在 `await fileApi.get` 返回并通过 `generation` / `serverReadSeq` 校验后、`setFile(data)` 之前，再次检查 `!options?.discardUnsaved && currentFileRef.current?.id === data.id && editContentRef.current !== (currentFileRef.current.content || "")`；若作者在请求在途期间输入了文字，保留旧基线并直接转交 `syncOpenFileFromServerRef.current()` 走三方合并或冲突比较，绝不覆盖未保存输入。
3. **`write_chapter` 整章替换支持同事务改标题，并拦截对锁定待写文件的 `rename_file` / `edit_file`（P2-3）**：
   - `write_chapter` 命中已有章节（`action=replaced`）且传入了带章名的新标题（非空且非「第N章 / Chapter N」裸编号）时，校验标题长度与同目录重名后将 `new_title` 随 `PendingFileWrite.target_title` 传给 `FileEditor.replace_body`，在 `</file>` 落库的同一数据库写事务内一并更新 `file.title` 与 `file.order`；只传裸章号时照旧保留原标题。同步更新工具结果说明、`tool_schemas.py` 与 `prompts/base.py`。
   - `ToolContext.pending_empty_file_for(file_id)` 暴露按文件 ID 的待写查询；`_rename_file_sync`（对任意待写文件）与 `_edit_file_sync`（对整章替换锁定文件及单循环待写文件）拒绝在 `<file>` 落库前修改目标文件，返回 `error_type="chapter_replace_pending"`、`mutation_applied=False`，不推进数据库 `updated_at`。
4. **压缩工具清单对齐 SDK `Converter.tool_to_openai` 并传 `max_retries=0`（P2-5c）**：
   - `compaction._chat_tools()` 直接复用 `build_agent_function_tools()` 与 `Converter.tool_to_openai` 生成工具定义（含 `"strict": False`），与正式运行请求逐字节一致，消除 `tools` 处的前缀缓存断裂。
   - `llm_failover.acreate_chat_completion` 支持可选 `max_retries`（通过 `client.with_options(max_retries=...)` 透传并保留成本守卫），`compact_history` 显式传 `max_retries=0`，不改动 #397 的摘要篇幅策略。
5. **计费导语门禁、导入章号回退、超窗分类与提示词/文档清理（P2-4 / P3-H / P3-B / P3-D）**：
   - `StreamBillingTracker` 在 `file_body_pending()` 为真时不累计 `_prose_reached`、不置 `prose_output = True`；收到 `write_chapter` 的 `body_saved=False` 结果时同步从 `replace_target_ids` 与 `empty_replace_target_ids` 移除该文件。
   - `write_chapter` 扫描现有章节时，标题开头无章号则回退按 `file_type` 解析 `file_metadata`（`extract_sequence_number_from_metadata`），识别导入拆分的 `"书名 - 第N章 …"` 章节。
   - `stream_errors._CONTEXT_TOO_LONG_MARKERS` 新增 `"context window"`，并将 `ERR_AGENT_CONTEXT_TOO_LONG` 设为 `retryable=True`（前端重试进入下一轮压缩自愈）。
   - 清理 `mcp_tools.py`、`tool_schemas.py`、`prompts/base.py` 与 `apps/server/agent/AGENTS.md` 中过时的 diff 审阅、`empty` 描述与列表格式冲突。

## Alternatives considered

- **在 `past_wrap_up` 时仍给自查尾巴发暂停卡，只把前端「继续」按钮文案改成「仅做自查、不写新章」。** 最强理由：保留作者显式触发自查的入口，且多章请求写完第一章撞上收尾时仍能一键继续写第二章。未采用：把「不要写新章」塞进下一轮用户消息文本会破坏多章请求原本合法的续写语义，而服务端无法在不违反语义决策规则的前提下从自然语言区分作者要一章还是多章；生产数据显示 71% 的点击并非为了自查，直接正常完成已落库章节并收尾任务板最符合真实预期，确需多章续写时作者本就可直接发下一条消息。
- **让 `_rename_file_sync` 在命中待替换章节时顺手同步更新 `PendingFileWrite.baseline_updated_at`。** 最强理由：即使模型不看提示词继续连调 `write_chapter` + `rename_file`，两步都能成功。未采用：工具在 SDK 工作线程内先于 `StreamAdapter` 消费事件流执行，直接跨层改写适配器内部状态耦合重且存在时序竞争；在 `write_chapter` 原生支持同事务改标题并对同轮 `rename_file` / `edit_file` 返回清晰的 `mutation_applied=False` 拒绝，边界干净且零副作用。
- **将 `ERR_AGENT_CONTEXT_TOO_LONG` 保持为 `retryable=False`。** 最强理由：部分单轮超长附件在压缩后仍可能超窗，提示开新对话最稳妥。未采用：`<turn_context>` 已在 `a92ed494` 加上 120k 上限，线上超窗主要来自存量长历史会话；在单循环架构下点击「重试」会先跑轮前交接压缩或 `bound_history_for_provider` 折叠自愈，隐藏重试按钮反而切断恢复路径。

## Consequences

- **收益**：已保存章节撞上 600s 收尾点不再诱导 Pro 用户陷入「继续 → 多写计划外新章」循环；流式刚结束时作者立刻打字不再被在途 GET 静默覆盖；重写章节并改标题在单次提交内原子完成，同轮误调 `rename_file` / `edit_file` 不再作废已付费生成的整章正文；压缩请求命中同一工具前缀且不自动重发超时大包；长导语未交付轮次如实退费，导入小说不再被 `write_chapter` 重复建章。
- **代价**：多章连写请求恰好在某一章刚保存、准备自查兼续写下一章的边界撞上 600s 收尾时间时，本轮会按已完成当前章节正常收尾，不再弹出「继续完成」按钮，作者需再发一条消息让 AI 接着写后续章节。
