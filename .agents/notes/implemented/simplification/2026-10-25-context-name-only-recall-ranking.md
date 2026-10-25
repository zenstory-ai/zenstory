# Context name-only recall ranking

Status: implemented

关联：[点名的旧卡片进入候选](../bug-fix/2026-10-18-context-named-cards-and-truncated-snippets.md)、[上下文保住角色卡和核心设定](../bug-fix/2026-10-08-agent-context-keeps-characters-and-core-lore.md)。

## Problem

上下文组装器为了给普通查询词加分，自带了一套 Aho-Corasick 自动机、重叠消歧、词组/标签提取和六路计分。中文查询通常整句没有空格，这套通用词项加分很少命中预期目标；重叠消歧还会把等长交叠命中全部丢掉。真正有事故背景和产品价值的是 #270 的卡片点名召回：用户在本轮明确写出角色或设定名称时，该卡片必须获得稳定加分并进入候选。

同批核实还发现 `TokenBudget.allocate/can_fit/get_usage_report`、相关 remaining helper，以及 `ContextPrioritizer.get_budget_allocation` 没有生产、前端或反射消费者，只有直接单测。

## Decision

- `_apply_query_recall_ranking` 只保留角色/设定名称的大小写无关子串判断和 `QUERY_NAME_MATCH_BOOST`；中文名称、去掉分类前缀的设定名称、英文多词角色名都沿用同一规则，单字名继续不加分。
- 删除通用词项/词组/标签加分、重叠消歧和 `utils/aho_corasick.py`。普通正文、附件和检索片段继续使用它们已有的相关度、检索分数、用户附加/焦点优先级排序。
- 删除只由测试调用的预算与分配 API，以及文件清单中始终为空的 `word_count` 传递/渲染分支；保留生产实际使用的分档、排序、池化预算、关键档保底、截断和残片丢弃逻辑。

## Alternatives considered

- **把自动机换成 `str.count`，保留原六路计分。** 最强理由是排序变化最小。未采用：仍会保留没有事故证据的词项/标签启发式，中文整句分词缺口也没有解决；代码只是换实现，没有消除多余行为。
- **删除所有 query 加分。** 最强理由是实现最简单。未采用：会回退 #270，点名的旧角色卡和设定可能再次被最近更新时间窗口与同档长章节挤出。
- **合并 `prioritize` 与 `group_by_priority` 的两次排序。** 最强理由是可以继续减少重复。未采用：同分条目的稳定顺序会改变，审计也把这项标为可能影响排序；本批没有足够收益去承担该行为变化。

## Consequences

- 点名卡片召回保持不变，并新增英文多词名称回归。
- 普通标题、正文或标签仅因包含查询词而获得的额外加分不再存在；这属于已接受的细微排序变化，检索服务自身的相关度仍保留。
- 上下文核心预算和排序路径不变；删除的 S9 API 没有兼容消费者。
