# Agent Note: Agent 读到的字数与编辑器一致，送审交接不再带字符数，[任务完成] 认作完成标记

Status: implemented

## Problem

- 新用户审计（连载、短篇两条路径，三个评审视角都提到）：AI 自报的字数和编辑器显示的对不上，还标「✅ 达标」。根因在服务端给模型的数字：
  - `query_files` 的结果里没有编辑器口径的字数。summary 模式只有 `content_length`（全文字符数，含标点、空白、换行），full 模式什么都没有，模型只能自己估或者拿 `content_length` 当字数。中文正文的字符数通常比编辑器字数多 20%–30%。
  - `file_metadata` 里缓存的 `word_count` 由写作统计写入，正文改过之后可能是旧值，原样返回给模型。
  - 自动送审的交接文案是「内容长度 {len(agent_content)} 字，自动触发质量检查」，evidence 里还有 `content_length=…`。`len()` 是 writer 本轮整段输出（含聊天文字）的字符数，审稿人会把它当成正文字数转述给作者。
  - 审稿材料过长时的省略提示「中间省略 N 字」里的 N 也是字符数。
- 审计里审稿人的回复以 `[任务完成]` 收尾（三个 persona 都出现）。图只认行尾的 `[TASK_COMPLETE]`，中文写法不算完成：计划交接、自动送审会越过它继续往下跑，`ends_with_question_to_user` 也没把它剥掉。

## Decision

- **`serialize_file` 给出 `word_count`**（`agent/tools/file_ops/serialization.py`）：用 `utils.text_metrics.count_words`，与前端 `countWords` 同一定义（U+4E00–U+9FA5 每字计 1，连续拉丁字母计 1，数字和符号不计）。
  - full 模式总是返回 `word_count`。
  - summary 模式只在 `content_truncated == false`（预览就是全文）时用预览计算 `word_count`；截断时不给，预览算出的数不是全文字数。SQL 投影路径同一规则。
  - 返回副本里的 `file_metadata`（JSON 字符串）带 `word_count` 时：能算出就覆盖成新值，算不出（summary 截断）就删掉这个键；其他键保留。只改序列化副本，不写库、不改模型实例。
  - `content_length` 保持字符数语义不变，仍用于判断预览是否截断。
- **自动送审交接不带数字**（`agent/graph/writing_graph.py`）：自动质检门的交接文案改为固定的「正文已写完，自动进入质量检查」；交接包 evidence 去掉 `content_length=…`。日志字段 `content_length` / `threshold` 保留。触发条件仍是 `len(agent_content) >= auto_review_threshold`（字符长度），语义不变。
- **审稿材料的省略提示**：`_format_review_payload` 截断时「中间省略 N 字」的 N 改为被省略部分的 `count_words`。截断本身仍按字符数（`max_chars=9000`）。
- **完成标记别名**（`agent/graph/nodes.py`）：`_TASK_COMPLETE_MARKER_RE` 在去掉首尾空白后的行尾匹配 `[TASK_COMPLETE]`（不区分大小写）或 `[任务完成]` / `【任务完成】`。`evaluate_agent_output`（`detect_task_complete` 经由它判断）和 `ends_with_question_to_user` 共用这一个匹配：前者认作 `explicit_complete_marker`，后者先剥掉标记再判断是否以提问收尾。文中出现的 `[任务完成]` 不算。
- 澄清没有文字标记检测：`detect_clarification_needed` 只认结构化的 `request_clarification` 调用，所以 `[需要澄清]` 在服务端不需要别名。前端隐藏 `[任务完成]` / `[需要澄清]` 由 WP5（`apps/web/src/lib/utils.ts` 的 `AGENT_CONTROL_MARKERS`）负责。
- 不改提示词（提示词里「字数只引用 `word_count`」由 WP2 负责），不改 router，`MAX_REVIEW_ROUNDS` 仍为 2。

## Alternatives considered

- **把 `content_length` 直接改成 `count_words` 的值。** 最强理由：模型已经在用这个字段，改了值不用教它认新字段。没采用：`content_length` 和 `content_preview` 的长度比较决定 `content_truncated`，并行读取、超限截断（`_bound_task_result`、`_compact_oversized_payload`）也按字符数写 `content_length`；同名字段两种口径会让截断判断出错。新加 `word_count`，字符数和字数各有一个名字。
- **summary 截断时也给一个 `word_count`（按预览比例外推，或在 SQL 里多算一列）。** 最强理由：列表查询时模型也能看到每章字数。没采用：外推是估算，正是这次要消除的「约 2300 字」；在库里按 `count_words` 的定义算需要正则，PostgreSQL 和 SQLite 写法不同，投影查询的预算测试也要重做。需要准确字数时按 id 读一次 full 即可。
- **读库时顺手把 `file_metadata.word_count` 修正后写回。** 最强理由：一次修好，写作统计也能读到新值。没采用：读工具不应有写副作用（版本、并发令牌、`updated_at` 都会被牵动），而且只读请求下也会走到这里。写作统计自己的口径由 `writing_stats_service` 负责。
- **自动送审的交接文案改用 `count_words(agent_content)`。** 最强理由：保留信息量，审稿人一眼知道写了多少。没采用：`agent_content` 是 writer 本轮全部输出，包含聊天说明和多个文件，不等于任何一个文件的字数；审稿人需要字数时应读文件的 `word_count`。

## Consequences

- 收益：模型读文件时拿到的字数和作者在编辑器里看到的一致；陈旧的缓存字数不会再被转述；审稿人收不到可以误当字数的字符数；以 `[任务完成]` 收尾的轮次按完成处理，不再被计划交接越过。
- 代价：full 结果每个文件多一次正则计数（O(n)，相对读库可以忽略）；summary 截断的列表结果仍然没有字数，模型要准确字数时得再按 id 读一次。
- 代价：`file_metadata` 里有 `word_count` 时，返回副本按 `ensure_ascii=False` 重新序列化，非 ASCII 字符不再是 `\uXXXX` 转义，与库里的原始字符串不逐字相同（JSON 等价）。
- 代价：`[ TASK_COMPLETE ]` 这类括号内带空格的写法现在也算完成标记，比原来的严格匹配宽一点。
- `2026-10-08-agent-review-gating-and-handoff-details.md` 里举例的内部 evidence 标记 `content_length=812` 已不再由图写入；同一规则（`^[a-z_]+=\S*$` 不渲染）现在覆盖 `auto_checks=…`（见 `feature/2026-10-09-review-evidence-repetition-and-facts.md`）。

## Verification

`cd apps/server && venv/bin/python -m pytest tests/test_agent/test_word_count_contract.py tests/test_agent/test_agent_routing_continuity.py tests/test_agent/test_query_files_summary_projection.py -q --no-cov -n 0`：

- `test_word_count_contract.py`：2234 字样本（字符数不同）full 模式 `word_count == 2234`，`serialize_query_file(response_mode="full")` 同样；summary 未截断给出 `count_words`、截断时没有 `word_count`；SQL 投影路径同一规则；陈旧 `file_metadata.word_count` 在 full 时被覆盖、截断时被删除，其他键保留，模型实例不变；自动送审的 HANDOFF context 与交接包 context 不含数字，evidence 没有 `content_length`，审稿人消息里没有「内容长度」。
- `test_agent_routing_continuity.py`：行尾 `[任务完成]` / `【任务完成】` / `[TASK_COMPLETE]` 判定完成且不算提问；文中出现的 `[任务完成]` 不算完成；「需要我继续写第二章吗？\n[任务完成]」仍算提问；planner 以 `[任务完成]` 收尾时计划中的 writer 不再运行；内部 evidence 标记用例改为 `auto_checks=repeat:2,facts:1`。
- `test_query_files_summary_projection.py` 的期望值同步加入 `word_count`（full 总有，summary 未截断时有）。
- 以上新用例在改动前的代码上全部失败（14 个），改动后通过；`pytest tests/test_agent -n 8` 1681 passed / 22 skipped；`ruff check agent/` 通过。

不涉及提示词，未做真实模型调用。
