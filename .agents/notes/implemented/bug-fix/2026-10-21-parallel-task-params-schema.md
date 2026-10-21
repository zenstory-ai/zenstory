# Agent Note: parallel_execute 的任务参数声明字段

Status: implemented

关联：[主备供应商](../architecture/2026-10-20-llm-primary-backup-failover.md)、[统一工具清单](../architecture/2026-10-21-context-snapshot-and-cache-stable-tools.md)。

## Problem

`parallel_execute` 的 `tasks[].params` 在 schema 里只写了 `"type": "object"`，字段全靠描述里的一句话。2026-10-21 生产（主模型 MiniMax）一个会话里 7 个 edit_file 子任务被拒：`edits` 被写成 `{"item": [...]}`（单条时 `{"item": {...}}`），`continue_on_error` 写成字符串，模型退回逐个调用，其中一次连续失败触发熔断。

用真实 key 复现（同一请求各 4 次）：不声明字段时，4 次全部自造字段名（`old_text`/`new_text`、`type`/`old_string`/`new_string`），没有一条能执行；声明之后 8 次都用 `op`/`old`/`new`。

## Decision

- `PARALLEL_EXECUTE_TOOL` 从 `tools/parallel_executor.py` 移到 `tools/tool_schemas.py`，`params.properties` 取各任务类型对应单独工具的字段并集：query_files 的全部字段、edit_file 的 `id`/`edits`/`continue_on_error`（`edits` 与单独工具同一份定义）、delete_file 的 `recursive`，以及 write_chapter 的 `title`/`content`/`parent_id`。`id`、`parent_id` 在不同任务里含义略有不同，单独写说明。
- 执行端不加任何别名或拆包装，参数格式错误照旧报错。

## Alternatives considered

- **执行端拆 `{"item": ...}` 包装**。最强理由：改动最小，生产里的那种形状立刻能用。被否：只认一种形状，自造字段名的情况仍然全部失败；根因是 schema 没告诉模型字段长什么样。
- **按任务类型写 oneOf**。最强理由：每种任务的参数更精确。被否：type 和 params 是同级字段，oneOf 需要按 type 判别，部分供应商对这类 schema 支持不一；字段并集已经足够让模型用对名字。

## Consequences

- 收益：子任务参数与单独调用同名同形，批量编辑不再白烧一轮调用或触发熔断。
- 代价：工具清单变了，上线后各会话第一轮的工具前缀缓存失效一次；schema 多了约 90 行，每次请求多一些（命中缓存的）输入。
