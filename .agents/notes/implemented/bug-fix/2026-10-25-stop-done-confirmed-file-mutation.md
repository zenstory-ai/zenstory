# Stopped done frames report confirmed file mutation

Status: implemented

Related: [停止 / 断线的产出与终态](../architecture/2026-10-09-stop-output-definition-and-round-outcome.md)、[时限终态先于取消收尾](2026-10-14-deadline-terminal-before-cancellation-cleanup.md)。

## Problem

作者停止生成时，API 会在取消收尾后合成一条 `done`。残稿已经保存、`written_files` 与落库的 `stop_outcome.saved_output=true` 都有确认回执时，这条 `done` 仍使用 `done_event` 的默认值 `file_mutated=false`。前端因此不会按本轮已确认修改刷新打开的文件，也不会创建本轮编辑快照；真实探针保存了 164 字正文，却收到错误的 `file_mutated=false`。

正常结束也可能由 API 在 service 的 `done` 上补充较晚拿到的写入回执。只保留 service 帧原值会漏掉同类确认结果。

## Decision

`api/agent.py` 继续以已有 `RoundWrittenFiles.saved_output` 核对结构化工具成功结果、`RunOutcome.saved_file_word_counts` 保存回执与未确认正文，不从模型文字判断，也不把单独出现的 `file_content` 帧当成保存成功。两种消费者传入各自原有的事实范围：终态 `saved_output` 使用 `tracker.write_succeeded`，文件变更信号使用更窄的 `tracker.file_written`，因此修改作品名、简介、文风或备注仍是已保存产出，但不会触发文件刷新或编辑快照。

- 作者停止时合成的 `done.file_mutated` 使用该确认结果。
- 正常 `done` 被 API 附加 delivery 时，用“service 已确认 true，或 API 的保存回执确认 true”合并，保留原本的 true 路径。
- 停止、断线与时限收尾的 `stop_outcome.saved_output` 复用同一个局部判定，计费、退款和终态原因不变。断线与时限仍不额外发送 `done`。

## Alternatives considered

- **看到 `file_content` 或任一写工具帧就设为 true。** 最强理由是即时且实现最少。未采用：流式正文可能在取消保存时失败，帧只证明生成过，不证明数据库已提交。
- **只检查 `written_files` 是否非空。** 最强理由是字段直观且已有前端合同。未采用：删除、并行写章等确认写入无法总是列出文件；既有 `saved_output` 已处理这些结构化结果和未确认正文的冲突。
- **只修作者停止的合成 `done`。** 最强理由是直接覆盖真实探针。未采用：API 给正常 `done` 附加较晚回执时存在同一不一致；复用同一判定只需一个很小的局部函数。

## Consequences

- 已确认保存的停止轮与正常轮会给前端 `file_mutated=true`，打开文件刷新与快照逻辑能按真实结果运行。
- 只有正文帧、保存回执为失败或未知时仍为 false，不会把未落库内容宣称为已修改。
- 只有 `update_project` 的轮次保持 `file_mutated=false`；停止终态仍按既有合同记录 `saved_output=true`。
- capture settle 等待、空白占位处理、单次退款、断线/时限终态和结束帧顺序均保持原合同。

