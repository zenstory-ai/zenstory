# Agent Note: 流程卡片不露内部字段，修改卡片的字数按编辑器口径

Status: implemented

## Problem

2026-10-09 第二轮新用户审计（N5，P2）：流程卡片里出现 `id=68212e4b-…`、`word_count=2434`、`[query_files]`。

- `lib/agentDisplayName.ts` 的 `formatHandoffMessage` 把交接原因原样拼进「接下来由{{agent}}继续：{{reason}}」。交接原因是模型写的，常抄工具结果里的字段，例如「第2章《他不是我男朋友》(id=…) 经核对 word_count=2434，低于…」。回复规则只约束最终回复，管不到交接原因。
- `ToolResultCard` 的并行任务行固定渲染 `[{task.type}]`，作者看到的是内部类型名。

同一张卡片上的「字 / 千字」用的是 `content.length`（含标点、空格、换行），比编辑器显示的字数多 20%–30%；编辑详情里的长度来自服务端 `text_len`，也是字符数。

## Decision

- **交接原因脱敏**：新增 `sanitizeAgentText(text, t)`，`formatHandoffMessage` 在拼接前调用；`ToolResultCard` 的并行子任务描述也用它。只影响显示，不改服务端和持久化内容。
  - `word_count` / `content_length` / `char_count` 后跟 `=` 或冒号的数字，改写为 `chat:workflow.charCount`「{{count}} 字」。
  - `id=…`、`file_id: …` 这类 `id` 或 `*_id` 字段、裸 UUID、方括号里的 snake_case 名字（`[query_files]`、`【edit_file】`）删掉。
  - 删除后收拾现场：括号里只剩分隔符或空了就去掉；中文之间、中文标点旁不留空格，原来有空格的英文和数字之间留一个空格；连续的分隔符合并。
  - 脱敏后为空时用短句「接下来由{{agent}}继续」。
- **并行任务行**：`[{task.type}]` 换成本地化任务名 `chat:tool.parallelTaskLabel`「{{label}}：」，名字来自 `chat:tool.{type}`；只认 `parallel_executor.PARALLEL_TASK_TYPES` 的五种（新增 `chat:tool.write_chapter`「写章节」），不认识的类型不显示标签。任务名和描述相同时只显示一次。
- **字数口径**：`ToolResultCard` 的 `formatContentLength` 改为 `formatWordCount`，`create_file` / `update_file` 成功卡片用 `lib/documentChunker.countWords(content)`（编辑器同一定义）。服务端 `edit_file` 的每条插入类明细在 `text_len` 旁新增 `text_words`（`utils.text_metrics.count_words`），卡片只用 `text_words` 显示；旧服务端没有这个字段时不显示长度，不再用字符数冒充字数。`text_len` 语义不变，仍决定是否显示长度（> 200）。

## Alternatives considered

- **在服务端过滤交接原因，或让提示词要求交接原因也用作者口吻。** 最强理由：从源头解决，历史和导出都干净。本次没做：提示词遵从是概率性的，旧会话的历史也会重放；显示层脱敏对新旧数据都生效。提示词一侧列在下一批，与审稿策略一起改。
- **复用 `lib/thinkingDisplay.ts` 的 `sanitizeThinkingForDisplay`。** 最强理由：已有一套去 UUID、换工具名的规则。被否：思考过程里的工具名是句子成分，要换成名字；交接原因里的 `[query_files]` 和 `id=` 是噪音，要删掉。UUID 在思考里换成「…」，在一句交接说明里留个「…」反而更难读。两处规则不同，各自一个函数。
- **编辑详情仍显示 `text_len`，只把单位改成「字符」。** 最强理由：不动服务端契约。被否：术语表规定字数单位是「字 / 千字」，同一个产品里「字」和「字符」并存会让作者以为是两个东西；服务端本来就有 `count_words`，多给一个字段是向后兼容的。

## Consequences

- 收益：流程卡片不再出现 id、内部字段名和工具类型名；卡片上的字数和编辑器、AI 读到的 `word_count` 一致。
- 代价：脱敏是规则匹配，模型换一种写法（如 `fileId=`、`长度=`）仍可能漏网，需要按审计结果补规则。作者自己的文本里若恰好有 `xxx_id: 值` 也会被删（只出现在交接原因和任务描述里，风险低）。部署错开期间（Vercel 先上线、Railway 后上线）编辑详情不显示长度。

## Verification

- 前端：`pnpm --dir apps/web exec vitest run src/lib/__tests__/agentDisplayName.test.ts src/components/__tests__/ToolResultCard.test.tsx src/lib/__tests__/chatDisplayEvents.test.ts`：审计原句的 id 和 `word_count` 被处理、括号里只去掉 id、`content_length` / `file_id` / UUID / `[query_files]` 被去掉、普通英文句子里的 `said:` 不受影响、只剩 id 时退回短句；并行任务行显示「Query files:」而非 `[query_files]`、未知类型不显示、描述脱敏；`update_file` 卡片 2400 字符 / 1200 字的正文显示 1.2k 而非 2.4k。
- 后端：`venv/bin/python -m pytest tests/test_agent/test_edit_file_semantics.py tests/test_agent/test_round3_stream.py -q --no-cov -n 0`：插入明细的 `text_words` 等于汉字加英文单词数；`pytest tests/test_agent -n 4` 全部通过。
