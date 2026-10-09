# Agent Note: 交接原因和审稿意见在服务端换成作者口径

Status: implemented

相关：前端交接气泡的格式（「接下来由{{agent}}继续：{{reason}}」、角色名映射）见 `process/2026-10-05-workbench-copy-for-authors.md`；最终回复的写法规则（不写文件 id、工具名、「阻断」「返工」）在 `prompts/base.py`。本 note 只处理交接原因和审查上限时显示的审稿意见。前端并行任务行的 `[{task.type}]` 由另一个前端包处理。

## Problem

2026-10-09 第二轮审计 N5：聊天流程卡片里出现 `id=…`、`word_count=`、`[query_files]`、「阻断 / 返工」。

- 前端 `formatHandoffMessage` 把 HANDOFF 事件的 `reason` 原样拼进气泡。显式交接的 reason 是模型写给下一个 agent 的，回复规则只约束最终回复，管不到这里。
- 图自己生成的交接原因也是内部用语：计划交接是「工作流自动交接」，自动质检是「自动质量门控」，作者看到「接下来由质量审稿人继续：自动质量门控」。
- 审查轮数用完时，审稿人写给 writer 的修改意见（context + todo）原样追加到回复正文和 `WORKFLOW_COMPLETE.review_notes`，里面同样有文件 id 和工具名。
- 这些 HANDOFF 事件还会存进 `assistant_display_events`，历史回放时再显示一遍。

## Decision

**新模块 `agent/core/author_facing_text.py`**

- `author_facing_text(text)`：
  - 去掉 `[query_files]` / `【edit_file】` / 反引号包起来的内部名字；只装着字段或 UUID 的括号（`（id=…）`、`(word_count=812, …)`）；`key=value` 字段（`id=`、`file_id=`、`word_count=`、`content_length=` 等）；冒号写法只认蛇形命名和 `id`（`file_id: …`），英文句子里的 `Note: …` 不动；裸 UUID；剩下的蛇形命名（工具名、字段名），连同前面的「用 / 调用 / 使用 / 通过」。
  - 一个分句里删掉内部写法以后，剩下的汉字、字母、数字不足 3 个（如只剩「显示」），整个分句不要。
  - `planner` / `hook_designer` / `writer` / `quality_reviewer` 换成前端 `chat:workflow.agents.*` 的中文角色名。
  - 流程用语按表替换：阻断问题 → 需要先修正的问题，阻断项 → 需要先修正的地方，需返工 → 需要再改一轮，返工 / 返修 → 再改一轮，阻断 → 需要先修正，交接给 → 交给，交接 → 转交，送审 → 送去检查。
  - 最后整理空括号、重复标点、汉字之间的空格；列表项保留「- 」。
- `author_facing_handoff_reason(reason)`：图自己生成的原因按 `SYSTEM_HANDOFF_REASONS` 替换（「工作流自动交接」→ 空字符串，前端只显示「接下来由X继续」；「自动质量门控」→「正文写好了，检查一遍质量」），其余走 `author_facing_text`。

**接入点（`agent/graph/writing_graph.py`）**

- 发出 HANDOFF 事件时用 `_author_facing_handoff_event_data` 复制一份：`reason`、`context` 换成作者口径，`handoff_packet` 原样带上。`pending_handoff_event_data` 本身不改，下一个 agent 收到的交接信息（`handoff_context`、`incoming_handoff_packet` 的待办与依据）和原来一样。
- 审查轮数用完时，`review_limit_notes` 先过 `author_facing_text`，再进回复正文（`_review_limit_text`）和 `WORKFLOW_COMPLETE.review_notes`。

## Alternatives considered

- **在前端 `formatHandoffMessage` 里过滤。** 最强的理由：只改展示层，服务端事件保持原样，便于排查。没选：事件会存进展示记录，换前端或走 Agent API 的客户端都会拿到原文；任务要求在产生的地方处理，前端文件也由另一个包负责。
- **用提示词要求审稿人把交接原因写成作者口径。** 最强的理由：从源头写对，不用正则猜。没选（不是替代关系）：提示词只能降低概率，最终回复的同类规则已经有了，审计仍然看到泄露；而且交接原因的读者首先是下一个 agent，要求它写成作者口径会丢掉 id 这类对 agent 有用的信息。

## Consequences

- 收益：交接气泡、历史回放和审查上限说明里不再出现文件 id、字段名、工具名和流程用语；交给下一个 agent 的内容不变，不影响协作。
- 代价：
  - 规则是正则，不认识的内部写法（比如驼峰字段名、不带等号的英文单词）会漏过；删掉词以后个别句子会不太通顺（「修正 id=… 的错别字」→「修正的错别字」）。
  - `（word_count=1800，目标 3000）` 这种括号里混着作者能看懂的内容时，整个括号会被删掉。
  - 英文界面的交接原因仍是中文，和原来一样。
  - `test_word_count_contract.py` 里对 HANDOFF `reason` 的断言改为新文案，并补了交接包 reason 不变的断言。

## Verification

`cd apps/server && venv/bin/python -m pytest tests/test_agent/test_handoff_author_facing.py tests/test_agent/test_word_count_contract.py -q --no-cov -n 0` 通过，覆盖：清理规则的各类样本、普通作者文本不变；审稿人带 UUID / `word_count=` / `[query_files]` / 「阻断」「返工」的交接，HANDOFF 事件的 reason 和 context 都干净，交接包和 writer 收到的交接信息仍是原文；计划交接的 reason 为空、context 不带 `planner`；审查上限时 `review_notes` 和回复正文不带 id 和工具名。`pytest tests/test_agent -n 8` 全部通过。
