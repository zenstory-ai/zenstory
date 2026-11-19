# Bug-Fix Note: `write_chapter` 只定位请求的正文类型

Status: implemented

## Problem

`write_chapter` 扫描 draft 与 script 后，先从标题解析章号/集号，随后才校验文件类型。标题碰巧使用另一种类型的编号格式时，工具会误报章节已存在、锁定错误文件做整章替换，或把另一类型所在目录计入分卷歧义。

## Decision

章节查询只读取请求的 `file_type`；`_row_chapter_number` 同时在解析标题与导入元数据前拒绝类型不符的行，作为调用边界的防御。draft 与 script 双向覆盖空章节存在判断、替换锁定和目录歧义。

## Alternatives considered

- **只在整章替换前复核类型。** 最强理由是改动最靠近危险写入。未采用：这仍会让 `empty=true` 误报存在，并让另一类型目录制造歧义；错误目标应该在候选查询和编号解析时就排除。

## Consequences

同编号的小说章节与剧本分集可以共存，`write_chapter` 只创建、判断或替换请求类型的文件；导入章节的同类型元数据回退行为保持不变。
