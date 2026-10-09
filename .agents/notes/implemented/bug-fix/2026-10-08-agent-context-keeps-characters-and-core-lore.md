# Agent Note: 长篇续写时上下文组装保住角色卡与核心设定

Status: implemented

## Problem

打开某章续写时，角色卡和高重要度设定会被章节挤出组装上下文（评审报告 F2）。以 60 章的书为例：打开第 60 章，说"继续写第61章，林凡和苏瑶在山门重逢"。

1. `ContextPrioritizer` 把前一章和 3 个"最近修改的兄弟章节"都归入 CONSTRAINT 档。档内按相关度排序，章节是 0.8，角色卡是 0.7，于是章节先把这一档约 35% 的预算花光，10 张角色卡和 3 条高重要度设定全部被丢弃或只剩残片，中低重要度设定反而完整保留。
2. 中文查询没有空格，`_extract_query_terms` 把整句当成一个词，查询里点名的"林凡"不会给对应角色卡加分。
3. `assemble()` 把收集到的全部文件 id（包括后来被预算丢掉的）都传给检索的 `exclude_entity_ids`，混合检索也没法把丢掉的角色捞回来。
4. 被截断到只剩几百字的条目照样注入。半张角色卡或章节开头帮不上忙，还占预算，模型可能以为设定原文就这么多。
5. `TokenBudget._allocate_tier_budgets` 给每个下位档封顶在名义份额。INSPIRATION 用不完的额度空着，CONSTRAINT 里的前一章却被截断。

文件清单里仍有角色的标题和 id，工作集也会带上两轮读过的卡片，所以模型不是完全看不到。实际后果是多花几次读取（会推高重复读取计数），以及模型没去读时设定前后不一致。

同批顺带修的 L4：并行工具的 `write_chapter` 子任务把 `file_type` 写死为 `draft`，写进角色或设定目录的卡片会被归成正文。

## Decision

- **兄弟章节降到 RELEVANT**（`agent/context/prioritizer.py`）：`classify_priority` 对 `type == "outline"`（承载大纲和正文）、`relation == "sibling"` 且不是用户附加的条目返回 RELEVANT。`from_outline` 给所有非焦点章节预设了 CONSTRAINT，所以这是"只升不降"规则唯一的显式例外。用户附加文件的 relation 是 `attached`，检索片段的预设档位都不受影响。前一章（`previous`）和子章节（`child`）留在 CONSTRAINT，焦点仍是 CRITICAL，父大纲的升级规则不变。
- **CONSTRAINT 档内约束优先**：`group_by_priority` 和 `prioritize` 的排序键在用户意图（焦点 > 附加 > 其他）之后、相关度之前加了 `_tier_rank`。CONSTRAINT 档里，非焦点、非父大纲的章节类条目排在角色卡、高重要度设定和父大纲之后；其他档的顺序不变。
- **查询点名加分**（`agent/context/assembler.py`）：角色或设定的名字（设定同时用去掉"分类 - "前缀的原标题）至少 2 个字、并作为子串出现在小写后的查询里时，相关度加 `QUERY_NAME_MATCH_BOOST`（0.5）。这个加分独立于原有的分词加分，单字名不参与。
- **检索只排除入选条目**：`assemble()` 先在不含检索片段的条目上用同一份条目预算预选一次（`_select_within_budget`），只把入选条目的 id 传给 `_get_retrieved_snippets(exclude_entity_ids=…)`。有检索片段时，片段单独做 query 加分（已有条目不重复叠加），合并去重后再选一次；没有片段时直接用预选结果。"相关内容详情"段头的开销改为无条件预留。
- **丢弃截断残片**（`agent/context/budget.py`）：非 CRITICAL 档截断后显示的正文少于 `MIN_TRUNCATED_ITEM_CHARS`（500 字）且不到原文一半时，整条丢弃，记在 `TokenBudget.dropped_stubs`，组装时打一条 INFO 日志。被丢弃的文件仍在文件清单里（带 id），不再出现在检索排除名单里。CRITICAL 档（焦点、用户附加、引用）不变，宁可截断也不丢。
- **下位档余量补给**：`_allocate_tier_budgets` 按名义份额分完后，把下位档实际用不到的额度（需求低于份额的部分，以及份额之和小于剩余预算的部分）按 CONSTRAINT → RELEVANT → INSPIRATION 的顺序补给还有缺口的档。各档实际占用不超过真实需求，因此总占用仍 ≤ `max_tokens`。
- **L4**（`agent/tools/parallel_executor.py`）：`handle_write_chapter` 用 `_infer_write_chapter_file_type` 决定类型。父目录或它的上级是模板根目录 `{project_id}-character-folder` / `-lore-folder` / `-outline-folder` / `-script-folder` 时，分别建成 character / lore / outline / script，其余情况仍是 draft。直接父目录就是根目录时不查库；否则在工作线程里借 `ToolContext.short_lived_session()` 沿 `parent_id` 最多向上走 16 层。查询失败按 draft 处理，不影响写入。

## Alternatives considered

- **兄弟章节整体去掉，不再收集。** 最有力的理由：它们是按最近修改时间挑的，和当前任务的关系最弱，去掉最省预算，实现也最简单。没选：写到中段时，兄弟章节常常是用户刚改过、正要衔接的内容，放在 RELEVANT 档仍有 25% 的保底，预算宽裕时照样全文进入，紧张时才让位。
- **把角色卡升到 CRITICAL。** 最有力的理由：一行代码就能保证角色卡最先拿到预算，还能借用 CRITICAL 的池化额度。没选：CRITICAL 的池化额度和逐项保底是给焦点文件与用户显式附加的内容留的（见 `_allows_parent_upgrade` 的说明）。10 张自动收集的角色卡进入 CRITICAL，会把用户附加的文件截断甚至挤掉，重演父大纲曾经出过的问题。
- **中文查询改用 jieba 等分词。** 最有力的理由：分词后所有条目（包括章节和片段）的关键词匹配都会变准，不只是角色名。没选：要新增依赖，网文人名、门派名多是生造词，分词器反而容易切错；"名字作为子串出现在查询里"对角色卡和设定这两类以名字为标题的条目已经足够准确，成本也最低。
- **保留截断残片，只在标注里提示"残片"。** 最有力的理由：哪怕只有开头几百字，也给了模型一点信息，不用读就知道大概。没选：残片占的预算可以让后面的条目整条放下；模型看到半张角色卡时，更可能直接按残片写，而不是去读全文。条目在文件清单里有 id，需要时一次读取就能拿到全文。

## Consequences

- 收益：长篇续写时角色卡和高重要度设定先拿到 CONSTRAINT 档的预算，前一章在余量补给后通常也能以全文进入。查询里点名的角色排在最前面。被挤掉的角色和设定还能被混合检索捞回来。
- 代价：有检索片段的请求要做两次预算选择（纯内存计算，没有额外的数据库或检索调用）。兄弟章节在预算紧张时会被截断或丢弃，比以前更早让位。
- 代价：残片规则是固定阈值。原文只有 600 字左右的卡片截到不足 300 字时会被丢弃，模型需要按 id 读取。
- 代价：名字子串匹配可能误命中。例如角色名"青云"会被"青云宗"命中，被加分的条目只是排序靠前，不会挤掉焦点或用户附加的内容。
- 代价：L4 的推断只认模板根目录的确定性 id。旧项目里 id 不规则的根目录仍按 draft 创建，和改动前一样。

## Verification

- `tests/test_agent/test_context_constraint_tier.py`：合成场景（焦点第 60 章、前一章第 59 章、3 个兄弟章节，章节各 3000 字；10 张 500 字角色卡；高/中/低重要度设定 3/5/2 条；28k 条目预算）下，焦点和前一章是全文，高重要度设定全部是全文，至少 8 张角色卡是全文；兄弟章节在 RELEVANT 档；CONSTRAINT 档里前一章排在最后；"继续写第61章，林凡和苏瑶在山门重逢"让林凡、苏瑶两张卡加分并排在最前；设定按去掉分类前缀的标题命中；单字名不加分；检索排除名单等于入选条目；非 CRITICAL 残片被丢弃，CRITICAL 只截断不丢。这些用例在改动前的代码上失败。
- `tests/test_agent/test_parallel_executor.py`：`write_chapter` 的父目录是 character/lore/outline/script 根目录时类型跟随目录，draft 根目录或没有父目录时是 draft；角色根目录下的子目录经数据库向上查找后建成 character。
- 更新了断言旧行为的用例：`test_context_prioritizer.py`（兄弟章节现在是 RELEVANT）；`test_context_budget.py::test_select_items_within_budget` 放大预算，让低重要度设定整条放下，不再依赖残片。
