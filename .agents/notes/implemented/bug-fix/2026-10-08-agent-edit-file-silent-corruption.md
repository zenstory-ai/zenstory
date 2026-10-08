# Agent Note: edit_file 不再「报告成功、实际损坏或没生效」：边缘标点、缺字段、整批回滚说明与稳定错误类型

Status: implemented

相关：匹配守卫（工作量上限、歧义拒绝、occurrence）见 `2026-07-18-agent-edit-safety-guards.md`，本笔记不改变那里的规则。

## Problem

2026-10-08 的写作 Agent 评审（F6、F7、F8）核实了 edit_file 的三条静默失败路径：

- 精确匹配因一个标点差异失败（直引号与弯引号、`?` 与 `？`）后走模糊匹配。模糊匹配在去掉全部标点和空白的归一化空间里比较，映射回原文的 span 只覆盖首尾两个非标点字符，原文边缘的引号和句号落在范围外。替换得到「““有事？”他声音冷得像冰。。」，删除留下孤立的「“。」，结果仍报 success。
- replace 漏写 `new` 时默认成空串，台词被静默删除；append/prepend/insert 的 text 为空也记一次 applied，模型、守卫和计费都以为写进去了。
- `continue_on_error=false` 时第 k 条失败整批不提交，但错误只有「Edit k: …」，读起来像只有一步失败，模型修正后只重发剩下几条，前面几处其实从未生效。
- 错误结果只有 `{status, error}`，界面只能把写给模型的长指令（候选片段、occurrence=N）原样给作者看。

## Decision

`agent/tools/file_ops/text_matching.py` 新增 `edge_punct_run` 与 `extend_span_over_edge_punct`。`edit.py` 在模糊命中（`ignore_punct_whitespace=true`）和近似命中之后，把 span 向两侧扩展到与参照串边缘「同类」的原文标点：

- 参照串优先取 old/anchor 自己的边缘标点；replace 时 old 那一侧没有标点而 new 有，就取 new 的（new 会把引号、句号重新写回去，原文那一个必须并入，否则重复）。delete 只看 old。
- 同类按位置从内往外逐个配对：所有引号一类，`。.！!？?…` 一类，`，,、；;：:` 一类，破折号一类，括号一类，换行单独一类，其余空白一类，其他 P* 标点按字符本身。遇到第一个不同类或非标点字符就停，最多吞参照串那么多个字符。只认归一化时会被去掉的字符（空白与 Unicode P*）。
- insert_after 只扩展尾部，insert_before 只扩展头部，插入点越过对应的标点。精确匹配和 `fuzzy_paragraph` 不扩展。
- 模糊/近似的 replace 和 delete 的 detail 带 `matched_original`（实际改动的原文）和 `warning: "已按近似匹配改动，请核对"`。replace_all 取第一处的原文，相邻两处扩展后重叠时后一处从前一处的结尾开始。

缺字段：

- replace 没有 `new` 时依次取 `text/content/new_text/replacement` 并在 warnings 里写明用了哪个键；都没有就报错，绝不当成删除。`new: ""` 是明确要求替换为空，照常执行。replace/delete 缺 `old` 由「告警并跳过」改为报错。
- append/prepend/insert_* 的 `text` 缺失时取 `new/new_text/replacement` 并留告警；仍为空就报错并点名 `text` 键。

整批语义：`continue_on_error=false` 时遇到失败不再立即抛出，而是在内存里把剩余编辑也检查完，然后抛 `EditBatchError`，不提交、不建版本。错误开头固定为「本次调用的 N 处编辑全部未生效（已整体回滚），请修正失败项后重新提交完整 edits 列表：」，后面列出全部失败项的序号与各自的原始错误。`EDIT_FILE_TOOL` 的 `continue_on_error`、`new`、`text` 描述同步写明这些规则。

错误类型：`EditFileError(ValueError)` 带 `error_type`，取值 `anchor_not_found`、`anchor_ambiguous`、`file_not_found`、`invalid_edit`；工具层另有 `file_busy`、`permission_denied`、`edit_failed`。`mcp_tools._edit_file_sync` 的错误载荷是 `{status: "error", error, error_type, user_message, edits_applied: 0, mutation_applied: false}`，整批回滚再加 `edits_total` 和 `failed_edits`（每项含 index、op、error、error_type）。未分类异常（`edit_failed`）可能发生在提交之后，不带 `edits_applied/mutation_applied`。`continue_on_error=true` 下全部失败时同样带 `error_type/user_message`。`error` 文本仍是给模型重新定位用的原文，不删减。`user_message` 用文案清单 C12 的措辞：

- anchor_not_found：「AI 没在原文里找到要改的那一段，正在重新定位。」
- anchor_ambiguous：「要改的这句话在文中出现了好几次，AI 正在确认是哪一处。」
- file_not_found：「这个文件已经不存在了，可能刚被删除。」
- 其他：「这一步没做成，AI 会换个方式继续。」

计费与重复读取守卫不需要改：整批回滚的结果 status 为 error，`StreamBillingTracker._is_committed_write` 和 `RepeatReadGuard._observe_direct_write` 都只认 status=success，不记为写入。

## Alternatives considered

- **边缘有标点就无条件吞掉相邻的原文标点串（评审报告的原始建议）。** 最强理由：实现最简单，任何标点差异都能覆盖。被否：old 以引号开头时会把前一句的句号、甚至段落换行一起并进替换范围，「他问。“…”」会丢掉「问」后面的句号。按同类逐个配对只多几行代码，能挡住这种误吞。
- **append 等 text 为空时只报错，不认 new 等别名。** 最强理由：不猜模型意图，契约更严格。被否：空正文纠偏轮里模型把正文放进 `new` 是已观察到的漂移，别名补齐加告警能少跑一轮纠偏；new 用在插入类操作上没有第二种含义，不存在误解。
- **整批失败时只报第一个失败项（保留原来的提前退出）。** 最强理由：省掉对剩余编辑的匹配开销，后面的失败项可能只是前一项失败的连锁反应。被否：每条编辑的匹配开销已有工作量上限，一次列全失败项能省掉多轮「改一条、再撞下一条」的往返；连锁失败的情况下，模型修正前一项后重新提交完整列表即可。

## Consequences

- 收益：模糊匹配不再留下重复或孤立的标点；漏写字段不会再删台词或空写一轮；模型从错误里就能看出整批未生效、需要重发完整列表；界面能按 `error_type` 给作者看一句短话，不再展示模型指令。
- 代价：同类配对是启发式规则，new 与原文在同一位置用了不同类标点（如「，」对「。」）时仍会保留原文那一个。原来「缺 old 告警跳过、其余照常」的批次现在整批报错，需要模型补齐字段后重发。`continue_on_error=false` 的失败批次要多跑完剩余编辑的匹配（仍在原有工作量上限内）。前端卡片改用 `user_message` 由文案任务（C12）负责，本改动只提供字段。

## Verification

`cd apps/server && venv/bin/python -m pytest tests/test_agent/test_edit_file_semantics.py tests/test_agent/test_round3_edit_match.py tests/test_agent/test_file_ops_regressions.py tests/test_agent/test_mcp_tools.py -q --no-cov`。基线 830e551 上用同样输入复现过：替换得到「““有事？”他声音冷得像冰。。」，删除留下「“。」，replace 只给 text 时台词被删除。
