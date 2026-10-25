# Agent Note: 模糊编辑吞掉闭引号前的句末标点；old 与 new 相同的 replace 不走模糊定位

Status: implemented

关联：边缘标点扩展规则见 [edit_file 不再报告成功实际损坏](2026-10-08-agent-edit-file-silent-corruption.md)，本笔记只在结尾一侧补一条例外；old 与 new 相同报「未修改」见 [identical replace](2026-10-21-edit-file-identical-replace-is-unchanged.md)，本笔记把那条判断挪到定位之前，其余规则不变。审计编号 F-01（Audit A-B1）。

## Problem

2026-10-25 写作 Agent 审计（交叉验证 `xverify/b1.py` 在 `1ccbc517` 上重现）：

- 模型抄 old 时常漏掉闭引号前的「。」「，」：old 写「这个方案很好”」，原文是「这个方案很好。”」。逐字找不到，走模糊匹配；命中范围停在「好」，`extend_span_over_edge_punct` 从内往外配对，原文第一个字符「。」与参照的「”」不同类就停下。原文的「。”」留在范围外，new 自带的「”」再写一遍，得到「不错”。”」。replace_all 每一处都这样；insert_after 的插入点停在「好」和「。」之间，旁白被插进引语里。这些都报 applied、记版本。
- old 与 new 相同（10-21 的场景）而原文只是模糊命中时，同一条路径把原文的逗号、句号删掉、引号重复，结果正文变了，也被记成一次成功编辑。

## Decision

1. `text_matching.extend_span_over_edge_punct` 结尾一侧：参照字符是闭引号（不是 `_OPENING_QUOTE_CHARS` 里的开引号；直引号分不出开闭，按可闭合处理），原文从当前位置起是一串句末/停顿标点（`end`/`pause` 类）、紧跟一个不是开引号的引号时，这串标点和引号一起并入范围（`_closing_quote_after_inner_punct`）。只跨这两类标点，换行、空白、文字都会让它停下，所以不会跨段；弯引号和「」能分出开闭，不会吞下一句对白的开引号（直引号的例外见代价）。开头一侧不变。
2. `FileEditor._edit_file_impl`：`op=replace`、old 是非空字符串且替换文本与 old 字面相同时（替换文本按 `_resolve_replace_new` 的优先级取：先 new，没有 new 再取 `text`/`content`/`new_text`/`replacement` 别名，由 `_peek_replace_new` 只读取值），在定位之前处理，不进 `_apply_replace`。原文里逐字（含引号写法等价，`locate_exact_or_quote_equivalent`）有这段就照 10-21 记进 `unchanged_edits`（`reason="already_matches"`）；没有就报 `anchor_not_found`，说明 old 与 new 相同、原文没有逐字对应的这段、没有修改，并附候选片段。原来「执行后正文没变且 old==new」的事后分支随之删除。

## Alternatives considered

- **old 与 new 相同就直接报 already_matches，不核对原文（修复计划原稿）。** 最强理由：最简单，模型的意图本来就是不改，不论原文有没有这段都不会出错。被否：warning 写的是「原文这里已经是这样」，原文里没有这段时这句话是假的；10-21 也明确保留了「old 找不到照旧 anchor_not_found」。逐字核对只多一次 `locate_exact_or_quote_equivalent`，不做任何模糊定位。
- **结尾一侧不论参照是什么，都跳过原文的句末/停顿标点。** 最强理由：覆盖面更大，例如 old 以「好」结尾、原文是「好，」也能吞。被否：参照串那一侧没有引号时，跳过的标点后面没有要对齐的东西，只会多删作者的标点；问题只在闭引号会被 new 重写一遍时才出现。
- **扩展时把原文的「。」保留下来、只吞闭引号（得到「不错。”」）。** 最强理由：不删作者的句号。被否：这需要把 new 的闭引号和原文的标点按位置重新拼接，等于替模型改 new；而 new 写的就是模型要的结果。现在的规则与 10-08 一致：new 带的边缘标点取代原文同一位置的标点。

## Consequences

- 收益：漏抄闭引号前标点的模糊 replace / replace_all 不再重复引号，insert_after 不再插进引语里；old 与 new 相同的编辑（new 写在别名键下也一样）不会走模糊定位改动正文。
- 代价：
  - new 以闭引号收尾而没写句号时，原文引号里的句号会随 new 一起被替换掉（「很好。”」→「不错”」），这是 new 的字面结果。
  - old 与 new 相同、原文只是模糊命中时，以前报成功（其实改坏了），现在报 `anchor_not_found`，模型要去掉这条或照原文重抄后重发。
  - 直引号 `"` 分不出开闭，原文一串句末或停顿标点（「。」「，」「：」都算）后面紧跟一个开对白的直引号时，这个引号仍可能被并入，例如 `他说，"你好。"` 里 old 以 `"` 结尾、命中停在「说」时会吞下「，"」。只在参照也是直引号、且同一行内紧跟时发生；比改动前（得到 `"："` 之类的重复）好，但不是零风险。
  - old 以闭引号收尾、new 连引号也不写时（old「很好”」、new「不错」，原文「“很好。”」），结果是「“不错」：原文的「。”」随 old 的范围一起被替换掉。这是 new 的字面结果；改动前误打误撞留下了「。”」。

## Verification

新增 `tests/test_agent/test_text_matching_edge_punct.py`（5 条）和 `test_edit_file_paragraph_integrity.py::test_identical_replace_never_takes_the_fuzzy_path`，以及别名键补漏 `test_identical_replace_under_a_new_alias_never_takes_the_fuzzy_path`（text/content/new_text/replacement 4 条，补漏前 4 条都失败：标点被删、报成功）。改动前失败 4 条：替换得到「不错”。”然后走了」、replace_all 两处都是「不错”。”」、insert_after 得到「很好他笑了笑。”」、old==new 的模糊命中没报错（正文被改）。两条守卫（下一句开引号、换行）改动前后都通过。
