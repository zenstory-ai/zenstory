# Agent Note: old 与 new 相同的 replace 报「未修改」，不算失败

Status: implemented

关联：收窄 [edit_file 没改动就如实报错](2026-10-11-edit-file-keeps-paragraphs-and-reports-no-change.md) 第 4 点的适用范围，那份记录的其余规则不变。熔断见 `openai_agents/tool_failure_breaker.py`。

## Problem

2026-10-21 生产：作者让模型给一批分集文件末尾各加一个标记，模型分 10 次 `parallel_execute` 全部加好；之后又对其中 5 个文件发 `replace`，old 与 new 一字不差（就是刚加的那几个字）。每条都按 `no_change` 报失败、整批回滚，模型把同一批任务原样重发 3 次，熔断器以 `repeated_identical_tool_failure` 停止了这一轮。前面的改动都已落库，作者看到的却是一张错误卡，这一轮也不退还次数。

10-11 把「执行后正文没变」一律当失败，针对的是模型以为改了、其实没改（U079：半角引号被按体例写回「」）。old 与 new 相同则不同：模型要的就是原样，原文也已经是这样，没有什么可以「修正后重发」的，报失败只会诱导它重试。

## Decision

- `FileEditor._edit_file_impl`：一条 replace 执行后正文没变，且模型给的 old 与 new 字面完全相同时，不再抛 `no_change`。这条记进结果的 `unchanged_edits`（`index`、`op`、`reason="already_matches"`），并在 warnings 里写明「原文这里已经是这样，没有修改，不用重发」；不计入 `edits_applied`，不进 `failed_edits`，也不让整批回滚。
- 只有这一条的调用返回 `status=success`、`edits_applied=0`、`mutation_applied=false`；`parallel_execute` 的子任务因此算完成，熔断器不再记账。
- old 在原文里找不到时照旧 `anchor_not_found`；old 与 new 不同、执行后却没变（引号统一、规范化）时照旧 `no_change`。

## Alternatives considered

- **保留报错，只把错误文案改成「原文已是目标内容，不要重发」**。最强理由：不改 10-11 的规则，结果语义最保守。被否：这一轮模型在第二次失败时已经收到熔断器追加的「别再原样重试」，仍然原样重发；只要它还是失败，熔断和错误卡就还会出现。
- **熔断器把 `no_change` 当作顺序性错误，不参与「同一调用连续失败」判定**。最强理由：工具结果不变。被否：模型照样在重试里空转，只是要拖到累计 10 次失败才停，更费时间和 token；真正的引号类 `no_change` 也会失去及时熔断。
- **所有执行后没变的编辑都改回成功**。最强理由：最简单。被否：这正是 10-11 否掉的假账，U079 的引号场景会再次报成「已改」。

## Consequences

- 收益：模型重复确认已完成的改动时不再触发熔断和错误卡，前面已经落库的工作以正常结束收尾。
- 代价：old 与 new 相同而模型其实想改别处时（写错了 new），现在得到的是「未修改」提示而不是失败；`edits_applied=0`、`mutation_applied=false` 仍如实说明没有改动。

## Verification

`test_edit_file_paragraph_integrity.py` 改写两条：单条相同 replace 报未修改、不失败、正文不变；与一条真实改动同批时不回滚、真实改动生效。两条在改动前都失败（抛 `EditBatchError`）。引号类 `no_change`（`test_curly_to_ascii_noop_is_reported_honestly`）照旧报错。`pytest tests/test_agent -k edit`：259 passed；熔断与流式加固测试 140 passed。

### 补充（2026-10-25）

old 与 new 相同的判断已挪到定位之前：原文逐字（含引号写法等价）有这段才报 `already_matches`，否则报 `anchor_not_found`，不再走模糊定位。见 [2026-10-25-fuzzy-edit-closing-quote-and-identical-replace](2026-10-25-fuzzy-edit-closing-quote-and-identical-replace.md)。
