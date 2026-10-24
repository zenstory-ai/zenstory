# Agent Note: 空文件纠偏轮为同一个文件恢复正文捕获，指令与两次回复守卫一致

Status: implemented

关联：[create_file 之后那次回复只能写正文](2026-10-21-fast-mode-summary-and-file-body-turn.md)、[正文分两次回复写](2026-10-10-file-streaming-protocol-two-replies.md)、[写完 `</file>` 后同一 agent 续写](2026-10-11-file-stream-continuation-and-truthful-completion.md)、[工具失败熔断](2026-10-03-agent-tool-failure-circuit-breaker.md)、[作者只要建文件时空文件不等正文](2026-10-22-author-requested-empty-files.md)。

## Problem

2026-10-24 生产合成试用（prod-v2026.10.24.1）：planner 建好空大纲后没写正文就结束了 run，随后两次空文件纠偏、10 次同类 `file_body_pending` 拒绝，最后 `repeated_identical_tool_failure` 熔断。根因是两条合同互相矛盾、捕获状态也没有闭环：

1. 纠偏指令要求纠偏轮「立即调用 edit_file(op=append)」，而 2026-10-21 加的守卫在 create_file 之后的回复里拒绝除 delete_file / request_clarification 以外的所有工具，并要求「以 `<file>` 开头写正文，不要用 edit_file」。
2. 上一个 run 的 MESSAGE_END 把 StreamProcessor 从 WAITING_START 复位成 IDLE（防止下一个 agent 的叙述流进上一个文件，需保留）；纠偏轮没有任何事件重新等待同一个文件。模型照守卫写 `<file>…</file>` 时，独占一行的标签被丢弃（生产日志 `stream_processor.py:453`），正文进了聊天，文件仍为空。

本地复现（mock 模型、真实 graph / runner 事件映射 / tools_adapter / create_file / StreamAdapter / 临时 SQLite）：两轮纠偏后正文进聊天、空文件被回滚删除；作者在等待期间改过的文件、别的项目的文件、先问清楚的请求同样被安排纠偏轮。

## Decision

- 新增一个图 → StreamAdapter 的内部控制事件 `FILE_BODY_RESUME`（与 CHAT_HOLD 同类，不发给前端），`data["request"]` 是 `FileBodyResume(file_id, expected_updated_at)`，适配器把 `armed` / 固定 `refusal` 写回它。
- 图的纠偏分支只在本来就允许补写时尝试：当前角色自己有写权限（不再在当前角色没有写权限时改派 writer：本轮没授予的写权限不合成）、非只读、非先问清楚、还有纠偏配额与协作轮数。目标是最近建的那份未完成文件（适配器正绑定着它）。发事件前 `_resume_target_version` 再读一次库：同一 ToolContext 项目、未删除、正文仍为空、不是文件夹，取 `updated_at.isoformat()`。
- StreamAdapter 只恢复自己在等的那份（本请求 create_file 成功结果设的 `_pending_file_write`）：同一 id、建时为空（`original_content_length == 0`）、图读到的版本等于 create_file 结果里的版本、没有进行中的非 IDLE 捕获、流未出错。`armed` 同时证明捕获与这个文件的待写守卫都已恢复：先 `ToolContext.rearm_pending_file_body` 恢复精确的守卫条目（清掉没被捕获的「正文已写出」标记，返回是否确实恢复；没有请求上下文、条目已不在或抛错都算失败，回执 `guard_missing`），成功后才开始等 `<file>`。已经在等同一文件时重复请求不重置。下一次回复仍须先写正文，守卫与熔断器不变。落库照旧走 `_save_file_content_sync(protect_author_content=True, expected_updated_at=create_file 结果的版本)`。
- 只有 `armed` 才安排纠偏轮，指令改为与 create_file 的 `next_step`、守卫同一个动作：这一次回复第一行直接输出 `<file>`，写完整正文，用 `</file>` 结束，写完前不要调用工具，不要只在对话里复述。不再要求 edit_file，也不再要求「同一轮补齐另几个空文件」（一次只能流式写一个文件；其余的在下个边界再核验）。`pending_empty_file_unwritten` 的拒绝文案同样去掉 edit_file 解除法。
- 空的或只有空白的 `<file>` 块不算写成：StreamAdapter 不落库、不发 `file_content_end`，待写绑定与守卫都留着，交给图的边界核验（中止补存同样只认非空白正文）。
- 没恢复成（配额用尽、核验不了、不在本项目、已删除、已有正文、适配器不在等它或守卫不在、版本变了、当前角色没有写权限）就不跑纠偏轮：回滚仍为空的本请求产物并说明，报告无法回滚的未完成文件；版本变了的文件（建后作者或别的写入动过）不回滚也不写。随后这一轮停在未完成：不按计划或显式交接继续、不送审、不因完成标记发 WORKFLOW_COMPLETE；已写成的文件与真实用量照旧，计费按既有终态规则（不新增退还条件）。`_probe_pending_file_body` 把不在本请求项目里的文件按 gone 处理。
- 回滚只删没被动过的空产物：待写条目记下 create_file 结果里的版本（updated_at），`_rollback_unfinished_empty_files` 只在库里版本仍是它、正文为空、同项目时软删，UPDATE 条件带上刚读到的 updated_at；之后被改过（例如作者在最后一次纠偏期间改了标题）或拿不出建好时版本的文件保留并报告未完成，不用回滚时重新读到的版本冒充建好时的版本。工具轮数耗尽的回滚走同一个 helper。
- 版本只记本轮新建的文件：create_file 复用了已有的空白剧集（`reused_existing=true`）时照样等正文、照样能写，但不记建好时的版本，纠偏补不成、工具轮数耗尽都不删它，只报告未完成（作者停止退还时计费 tracker 本来就不把复用文件当候选）。作者停止 / 断线被退还时移除空白占位文件也改为比这个版本（见 [停止的产出口径与终态](../architecture/2026-10-09-stop-output-definition-and-round-outcome.md)）：作者在生成期间改过标题等就保留。
- 工具调用轮数耗尽时还有空文件在等正文：图先扣住 runner 发的 `tool_call` 耗尽卡，等这次 run 收尾（MESSAGE_END 之后）核验 / 回滚这些空文件，再把 `unfinished_files` / `rolled_back_files` 放进同一张卡发出，不再另发一段文字说明。层仍是 `tool_call`，失控停止的退还 / 计费判定不变；没有空文件时卡片照旧当场发出、内容不变。前端只要卡上点名了这类文件（任何层），就按「这一轮没写完」显示、不给建议并清缓存（实时与刷新后一致）；交付回执不再把已撤销的文件列为未确认保存。
- 停下时的状态卡接通既有协议：`iteration_exhausted.layer=empty_file_correction` 与 `unfinished_files` 本来就有，只是 StreamAdapter 发 SSE 时丢了文件、历史状态卡也没存、前端只认 collaboration / tool_call。现在停止分支一律发这张卡（全部撤销了也发），带 `unfinished_files`（没能撤销、仍空着）与 `rolled_back_files`（已撤销），只有 id 与标题；SSE、`status_cards`（`unfinishedFiles` / `rolledBackFiles`）与时间线都保留；前端卡片说「这一轮没写完」并点名文件（中英），不再显示「任务比较大」。交付回执不再把已撤销的空文件列为「未确认保存」。层名不在退还条件里，计费不变。
- 恢复写入的纠偏轮：正文类文件（draft / script）按 create_file 后的 `<file>` 正文同口径计入自动质检门的本轮字数，并与 create_file 后一样可开始 CHAT_HOLD；这一程写完的是之前建好的文件，续写条件（`ends_on_file_block`）同样成立。计费与回合回执只认 StreamAdapter 的保存回执，没有改变。

## Alternatives considered

- **只把纠偏提示改成「直接输出 `<file>`」。** 最强理由：一行改动，与守卫文案一致。被否：MESSAGE_END 已复位捕获，标签会被丢弃、正文落进聊天；独立反证审查已否决。
- **纠偏轮放行 edit_file（或把 `file_body_pending` 加入熔断豁免）。** 最强理由：沿用旧指令，不碰捕获状态。被否：守卫存在正是为了不让模型用 edit_file 分段拼正文、绕开实时写入；全局放行或豁免会让这个保护失效，也掩盖了指令与守卫的矛盾。
- **MESSAGE_END 不复位等待中的捕获。** 最强理由：不需要新事件。被否：会让下一个 agent（审稿人、计划内的下一个角色）的叙述流进上一个文件，这正是 2026-06 的 agent 边界收尾修复要防的。
- **让模型重复 create_file 同一标题来重新绑定（已有 `_repeat_of_pending_empty_file` 路径）。** 最强理由：完全复用现有接线。被否：要靠模型多发一次工具调用，而那次调用本身会被守卫拒绝；也不能保证只针对图已核验的那个文件。
- **图直接重放 create_file 结果给适配器。** 最强理由：适配器已有处理逻辑。被否：会再发一次 file_created / 工具卡，前端与回合回执把它当成新建；并且绕不开「不要重复创建」的约束。

## Consequences

- 收益：空文件纠偏轮第一次就能把正文写回同一个文件，正文不进聊天，不再因指令与守卫矛盾连续拒绝到熔断；作者改过、删掉、不在本项目或本轮未授权写入的文件不会被写入。
- 代价：多一个内部控制事件和一份回执；没有 StreamAdapter 的调用方（例如只跑图的单元测试）不会再安排纠偏轮，而是直接回滚 / 报告。核验不了的空文件以前还会白跑一轮纠偏，现在立即回滚或报告；补不成时整轮停在未完成，计划里后面的角色、显式交接与自动送审都不再执行（以前会照常交接、审稿甚至宣告完成）。
- 计费口径未改：补不成而停下的一轮仍以 done 结束，按「正常结束计费」判断（工具轮数耗尽那种「失控停止且没写成就退还」不覆盖这个层级）；是否把它纳入退还属于计费策略，留给单独决定。
- 只按建好时的版本判断能不能删：作者动过、复用来的、或说不出版本的空文件宁可留在文件树里由作者处理（卡片 / 终态如实说没写完），也不删。退还停止的版本比较在项目锁与行锁（PostgreSQL）下进行，并带版本条件 UPDATE；没有为核验与删除之间的并发单独写测试（低概率，图的回滚 helper 已有这条用例）。
- 不改模型、思考强度、时间预算、重试或熔断阈值；首次为何漏写正文（模型行为）仍未知，本修复只让恢复路径闭环，不能证明线上失败率已降低。

## Verification

- 先写 `apps/server/tests/test_agent/test_file_body_resume.py`（真实 graph + runner 事件映射与钩子 + tools_adapter 守卫/熔断 + create_file + StreamAdapter + 临时 SQLite，只替换模型与路由），在 base `6fb42851` 上 7 项失败（正文进聊天并在两轮后被回滚、作者改动 / 跨项目 / 先问清楚仍纠偏、取消时没有捕获），3 项既有保护通过；修复后 17 项通过（含适配器的拒绝与幂等用例）。
- 锁住旧合同的图单元测试改用 `file_body_resume_helpers`（替身：目标核验通过、适配器已回执），新增「没恢复捕获就立即回滚」「核验不了只报告」用例；`test_mcp_tools` 不再断言 edit_file 解除法。
- 最终整批审查（`df8e2f39`）后的回修：作者在最后一次纠偏期间改名的空文件被删（迁入审查的跨层复现）及核验与删除之间的并发改动，修改前 4 项失败；未完成状态卡在 SSE / 落库 / 前端丢失文件与层名，后端 4 项、前端 4 项修改前失败。正确依赖环境（Python 3.12、openai 3.37、agents 0.21、Prefect 3.6.29）下运行。
- 再次独立审查（`e0fe9bc3`）后的回修，迁入审查的三份合成复现，先写 red 再修：退还停止时作者改过标题的新空章节被删、没带版本的候选被删（round_outcome 2 例、API 停止 1 例）；复用的已有空白剧集被当成本轮产物回滚（create_file + 回滚 helper 1 例、工具轮数耗尽跨层 1 例）；工具轮数耗尽时卡片不带文件事实、已撤销的文件仍列为未确认保存（跨层 2 例、图单元 1 例）；修改前后端 8 项失败，前端 tool_call 带文件的实时 / 刷新 / 解析 3 项失败；修改后通过，没有文件的 tool_call 卡与提示照旧。
- 独立审查（`b0f74cf3`）后的回修，先写 red 再修：空 / 只有空白的 `<file>` 块（跨层，2 例）、守卫缺失 / 已清 / 只有别的文件 / 恢复抛错时不得 armed（4 例）、补不成后不交接审稿与不宣告完成（含计划交接、显式送审 + 配额用尽、完成标记，3 例并改正 2 个锁住旧 fallthrough 的用例）、审稿人带着待写标记时不改派 writer（1 例）；修改前共 11 项失败，修改后这三个文件 49 项通过。
