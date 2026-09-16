# Agent Note: 未闭合的 <file> 块若带轮次控制标记则视为叙述而非正文，never 写入章节

Status: implemented

## Problem

一个 `StreamAdapter`/`StreamProcessor` 在整个多 Agent handoff 循环中复用。模型打开 `<file>` 却没有闭合（转而叙述或 handoff）时，流结束的收尾逻辑把累积缓冲——包括跨 Agent 的 handoff 叙述和结尾的 `[TASK_COMPLETE]`——当作章节正文落盘。剧本分集复用分支还曾把 `content` 谎报为空串来触发捕获模式，模型漏写 `</file>` 时截断补全用残稿整体覆盖已完成的整集（实测 8000 字变 19 字）。

## Decision

`agent/core/stream_processor.py` 的 `finalize_on_stream_end` 在 WRITING 状态收尾时检查缓冲：正文若携带轮次控制标记（`[TASK_COMPLETE]`、`[workflow_stopped]`、`[clarification_needed]`）即判定为聊天叙述，丢回对话并保持文件不动；无标记的截断正文仍被抢救。`stream_adapter` 在每个 Agent 边界（MESSAGE_END）finalize 处理器，后续 Agent 的文本 never 累积进前一个 Agent 的未闭合文件。`create_file` 的复用分支用显式字段 `reused_existing` / `original_content_length` 表达意图，是否进入 `<file>` 捕获以 `reused_existing` 为准；`auto_completed` 且目标文件非空时拒绝覆盖。缓冲超过 `BUFFER_MAX_SIZE`（1MB）时强制完成该文件并 drain 到 `</file>`，溢出尾巴 never 回流到聊天记录。Agent 留下空文件时 `writing_graph` 以显式提醒重跑 writer，次数以 `MAX_FILE_CORRECTION_ATTEMPTS` 为界。

来源：a6c00fc (#11)、689ae3b (#14)、ea85c83

## Alternatives considered

- **用 `content=""` 作为"进入捕获模式"的隐式信号**（ea85c83 所替换的复用分支做法）。最强理由：不改工具返回结构。被否：空串同时意味着"文件确实为空"，截断补全据此覆盖整集正文；歧义信号不能承载数据安全决定。
- **只加 stream_processor 的防御、不在 Agent 边界 finalize**。最强理由：改动集中一处。被否：a6c00fc 明确采用三层修复（防御 + 边界 + 纠偏），单层覆盖不了"handoff 后继续累积"的路径。

## Consequences

- 收益：章节正文只会来自闭合或无标记的 `<file>` 块；已有正文不会被更短的残稿覆盖。
- 代价：用户正文里恰好出现 `[TASK_COMPLETE]` 之类标记会被判为叙述而丢弃，属已接受的误判；`MAX_FILE_CORRECTION_ATTEMPTS` 抬高了一轮的模型调用成本上限。

## Verification

`cd apps/server && .venv/bin/pytest tests -k "stream_processor or stream_adapter" -q`；ea85c83 对流式覆盖保护做过 4 种变异验证。
