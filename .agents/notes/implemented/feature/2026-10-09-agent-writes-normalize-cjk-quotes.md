# Agent Note: 应用内 agent 写正文时确定性规范化双引号；edit 锚点引号等价；字数口径与作品名自动命名

Status: implemented

相关：edit_file 的匹配守卫见 `bug-fix/2026-07-18-agent-edit-safety-guards.md`，模糊命中的边缘标点见 `bug-fix/2026-10-08-agent-edit-file-silent-corruption.md`，本笔记不改那两处的规则。AI 覆盖前备份见同批的 `bug-fix/2026-10-09-ai-write-backs-up-unversioned-text.md`。

## Problem

2026-10-08 新用户审计（#17）：AI 写的对白半角 `"`、全角 `“”` 混用，同一章里两种都有。提示词要求全角引号只能降低概率，模型还是会写出半角。确定性规范化当时没做，原因是 edit_file 的锚点问题：正文改成全角以后，模型下一次 edit_file 照旧用半角引号抄 `old`/`anchor`，逐字 exact 找不到，只能退到忽略全部标点的 fuzzy 匹配，命中更宽、更容易改错地方。

同批还有两处工具契约问题：

- #1：edit_file 只返回 `new_length = len(content)`，模型把它当「字数」报给作者，和编辑器显示的字数对不上。
- #35：新项目一直叫「我的小说」这类默认名。AI 在大纲里已经给作品起了名，项目名也不会跟着改。

## Decision

**规范化器** `apps/server/utils/cjk_quotes.py`，纯函数：

- `detect_quote_style(text)`：「」『』 多于 “” 时返回 corner（「」），否则返回 curly（“”）。
- `resolve_quote_style(*candidates)`：从第一个含 “”「」『』 的候选文本判定风格，都没有就用 “”。
- `normalize_double_quotes(text, style)` 按行处理（按 `\n` 切分）。以下几种行原样保留：
  - 没有 ASCII `"` 和全角 `＂` 的行，所以函数幂等；
  - 没有汉字的行，英文不动；
  - 双引号类字符（`" “ ” ＂`，corner 风格再加 `「 」`）合计为奇数的行，有歧义。

  其余行按出现顺序交替换成开引号、闭引号。单引号不处理。每次替换都是一个字符换一个字符，长度和换行不变。
- `normalize_inserted_spans(text, spans, style)`：给 edit_file 用。`text` 是编辑后的整篇，`spans` 是新写入的文本在其中的位置。对 span 碰到的每一行，看整行（原文 + 新文本）：
  - span 里没有 ASCII `"` / `＂`、整行没有汉字、整行双引号类字符为奇数时，原样不动；
  - 整行按出现顺序算开/闭位置，所以片段从对白中间开始时（行内片段前面的引号是奇数个），片段里第一个引号是闭引号；
  - 行内已有的方向引号（“「 是开、”」 是闭，包括片段里从原文抄来的）和它所在的位置对不上时，视为有歧义，这一行的 span 原样不动；从原文抄来的方向正确的 “” 不会被翻转；
  - 只改 span 里的字符，span 外的原文（包括作者留下的半角引号）不动。

**只用于应用内 agent 写 draft/script**（`edit.NORMALIZED_QUOTE_FILE_TYPES`）。`FileToolExecutor`、`FileCRUD.create_file/update_file`、`FileEditor.edit_file` 新增 `normalize_quotes: bool = False`，只有下面这些路径传 True：

- `mcp_tools` 的 `create_file`、`edit_file`；
- `StreamAdapter._save_file_content_sync`（流式 `<file>` 整篇落库）。

Agent API（外部 agent）不经过 FileCRUD，保持原样；`router.execute_file_tool_call` 用默认值 False。outline、character、lore 等类型不规范化，这些文件里的引号多半是在引用术语或原文。

**规范化范围**：

- create 和流式整篇写入：对整篇规范化。风格依次取写入前文件已有的正文、同项目「上一章」（`previous_chapter_content`：先找同父目录里 `order` 更小的最近一章，再找项目里最近更新的 draft/script）、本次写入的文本，都判定不出来就用 “”。
- edit_file：只规范化每个 edit 自己写进去的文本，包括 replace 的 `new`（别名补齐之后）和 insert_*/append/prepend 的 `text`。新文本先按原样放进正文，再用 `normalize_inserted_spans` 按所在行定方向（`FileEditor._place_new_text`；replace_all 记下每一处的位置后一起规范，`_normalize_placed_spans`）。详情里的 `new_preview` / `text_preview` 是规范后实际写入的文本。没被编辑的段落不动，AI 改稿审阅里就不会出现和本次修改无关的引号 diff。

**锚点引号等价匹配**（`text_matching.locate_exact_or_quote_equivalent`）：在 exact 和 fuzzy 之间加一级。把 `"“”＂「」` 在原文和 `old`/`anchor` 里都映射成同一个占位符再查找。这个映射一字对一字，下标可以直接用在原文上。命中时 `match_mode="quote_equivalent"`，唯一性、occurrence、replace_all 的规则和 exact 路径完全相同，歧义报错里的候选片段从原文截取，模型看到的是稿件里的全角引号。`old` 里没有引号字符时不做映射，因为映射不会带来新的命中。`match_mode="exact"` 也接受这一级：引号写法本来就是本功能自己统一的，不能让它变成定位失败。原来的 fuzzy（忽略全部标点）保留，作为兜底。

**字数口径（#1）**：edit_file 结果增加 `new_word_count = utils.text_metrics.count_words(new_content)`，和编辑器同一个算法。`tool_schemas.WORD_COUNT_CONTRACT` 写进 query_files、create_file、edit_file 的描述：`word_count`、`new_word_count` 就是编辑器显示的字数，作者说的「字数」都指它；`content_length`、`new_length` 是含标点和空白的字符数，不能当字数报给作者。query_files 和 create 结果里的 `word_count` 字段由同批的字数口径工作包在 `serialization.py` 里补。

**作品名自动命名（#35）**：`update_project` 新增可选参数 `title`（schema 里 `maxLength: 30`）。`ProjectOperations.update_project_status(title=…)` 的处理：

- 去掉 `《》“”`，首尾空白去掉，中间连续空白并成一个。结果长度不在 1–30 之间时不改名，返回 `title_skipped: "invalid_title"` 和 `title_error`，同一次调用里的其他字段照常更新。
- 项目当前名属于 `DEFAULT_PROJECT_NAMES`、或是 AI 自动命名留下的名字时才改名（后者和作者明确要求改名的 `author_requested` 路径见 `bug-fix/2026-10-09-ai-named-project-can-be-renamed.md`）。这个集合是 `config/project_templates.py` 里各类型、各语言的 `default_project_name`，加上「我的项目」「未命名项目」「Untitled」。改名后更新 `project.updated_at`，结果里给 `project_name_updated: true` 和 `project_name`。
- 当前名是作者起的，返回 `title_skipped: "author_named"` 和写明手动改名入口的 `title_note`；和当前名相同，返回 `unchanged`。
- `title` 不进 `updated_fields`，前端按字段名显示「已更新：摘要、备注」，不认识 title。只传 title、又没有改名时不提交、不改 `updated_at`。mcp 层把上面这几个字段同时提到结果顶层。

## Alternatives considered

- **只靠提示词要求全角引号**（审计决策 #17 原来的本期范围）。最强的理由：零代码风险，edit 锚点不受任何影响。没选的原因：审计三份样本里提示词约束下仍然出现混用；而锚点问题用一字对一字的引号等价匹配就能解决，不需要放宽到 fuzzy。
- **edit_file 之后把整篇正文重新规范化一遍**。最强的理由：一次就能把历史遗留的半角引号全部修好，规则也最简单。没选的原因：作者自己写的、没被这次编辑碰到的段落会被改掉，AI 改稿审阅里会冒出一串和本次修改无关的 diff，作者要么逐条拒绝，要么在不知情的情况下接受。
- **edit 的新文本只在自己内部配对**（`normalize_double_quotes(new)`，本笔记第一版的做法）。最强的理由：纯函数只看新文本，不依赖周围原文，规则和整篇写入完全一样。没选的原因：集成复审发现片段从对白中间开始时（如把「明天就走，你别拦我」换成 `明天就走"她急了："你别拦我`），片段里的第一个引号其实是闭引号，自己配对会把开闭整对翻反，抄来的方向正确的 ” 也会被翻成 “。方向只能看所在整行。
- **把半角引号统一改写成 “”，不看稿件风格**。最强的理由：不用判定风格，也不用查上一章。没选的原因：用「」写对白的稿件会在同一章里出现两种引号，等于又造出了这次要消灭的混用。
- **不让 exact 模式接受引号等价**。最强的理由：`match_mode="exact"` 字面上就是逐字一致。没选的原因：模型选 exact 是为了不要 fuzzy 的宽松命中；引号等价是一字对一字的，不会比 exact 更宽。拒绝它只会让「我们自己改了引号」变成定位失败。
- **作品名无条件覆盖项目名**。最强的理由：实现最简单，AI 最新起的名字总能生效。没选的原因：作者自己起的名字会被 AI 改掉，这是信任问题；只替换默认名，最坏情况也只是不改。

## Consequences

- 收益：
  - AI 写进 draft/script 的对白引号和稿件体例一致；edit 用半角引号也能精确定位全角原文，唯一性守卫照常生效，不必退到 fuzzy。
  - 模型拿到的 `new_word_count` 和作者在编辑器里看到的字数一致。
  - AI 给作品起名以后，默认项目名会跟着变。
- 代价：
  - 整行引号为奇数的行（对白跨行、原文本来就缺一个引号）保持原样，新文本里的半角引号会留下来；片段只带一个闭引号、把行内已开的对白收住时，整行成对，会被规范。行内方向引号前后矛盾时也不动。
  - 作者整篇用半角引号写作时，AI 新写的段落会变成 “”，因为风格判定只区分 “” 和「」。
  - 每次规范化可能多一次「上一章」查询（只取一行，只在文件自己判定不出风格时才查）。
  - 前端还不会在改名后立刻刷新顶栏的项目名，要等下一次读取项目信息。模型要在 schema 描述的提示下主动传 `title`，本包没有改提示词。
  - `test_mcp_tools.py`、`test_stream_adapter.py` 里转发参数的断言加了 `normalize_quotes=True`。

## Verification

`cd apps/server && venv/bin/python -m pytest tests/test_utils/test_cjk_quotes.py tests/test_agent/test_agent_write_quote_normalization.py tests/test_agent/test_update_project_title.py -q --no-cov -n 0` 全部通过，覆盖：

- 成对替换、奇数个引号不动、英文行不动、「」稿件跟随「」、已经是全角的内容幂等、长度和换行不变；
- 流式整篇写入后落库为全角，新章沿用上一章的「」；
- edit_file 只规范化 new/text，未编辑的段落保持不变；
- edit 片段按所在行定方向：对白中间的 replace（含 replace_all）、只带闭引号的 insert_after、整行 replace、片段里抄来的 ” 不翻转、方向矛盾时不动、英文行不动、整行奇数不动、多行片段只在第一行借上下文、「」风格、半角 `old` 从对白中间开始仍以 `quote_equivalent` 命中且 `new_preview` 是规范后的文本（这几例在旧实现上失败）；
- 半角 `old` 以 `quote_equivalent` 精确定位全角原文，多处命中时报歧义，用 occurrence 指定后插入到正确位置；
- Agent API 的 create/update 不被规范化；outline、character 不被规范化；
- `new_word_count` 等于编辑器口径；
- 默认名会被改成作品名，作者起的名字保留，书名号被去掉，超长被拒。

`pytest tests/test_agent -n 8` 全部通过（1675 passed、22 skipped）。本包不改提示词，所以没有做真实模型验证。
