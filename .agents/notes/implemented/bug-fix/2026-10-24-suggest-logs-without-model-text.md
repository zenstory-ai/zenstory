# Agent Note: 下一步建议的日志不记模型回复原文和异常原文

Status: implemented

关联：[素材摘要的安全错误](2026-10-24-material-summary-json-and-safe-errors.md)、[后端日志与错误管线](../architecture/2026-10-05-backend-log-and-error-pipeline.md)。

## Problem

2026-10-24 合成试用的根因排查读到 `agent/suggest_service.py`：收到回复时 INFO 记 `response[:200]`，解析失败时记 `raw_response[:200]`，异常分支记 `str(e)`（ERROR 级别还会自动附上异常栈），JSON 修复失败也记 `str(e)`。建议模型的输入含项目上下文和最近的对话，回复与供应商异常都可能复述作品正文。这不是那次生成失败的原因，但不符合日志只留固定分类和必要元数据的边界。

## Decision

- 收到回复：`stage=llm_call`、`response_length`。
- 解析失败：`stage=parse`、`failure=parse_failed`、`response_length`、`falling_back`；JSON 解码 / 修复失败只记 `error_type`。
- 异常：`failure=timeout|exception`、`error_type`、`falling_back`，`exc_info=False` 不附异常栈。
- 建不起模型客户端（`SuggestService.__init__`）：`stage=init`、`failure=llm_unavailable`、`error_type`，不记异常原文、不附异常栈。
- `request_id` / `agent_run_id` 仍由 `log_with_context` 从请求上下文自动带上。不新增观测框架、表或面板，返回给前端的建议与回退逻辑不变。

## Alternatives considered

- **保留截断原文，只缩短到几十字。** 最强理由：排查解析失败时能直接看到格式问题。被否：几十字同样可能是作品原句；格式问题用合成样本或本地重放即可复现。
- **异常保留异常栈、只去掉 error_message 字段。** 最强理由：栈对定位代码路径有用。被否：栈的最后一行就是异常文本，会把同样的内容带回日志；固定分类 + 异常类型足够区分超时、供应商错误与解析错误。

## Consequences

日志里不再出现建议模型的回复和供应商异常文本；排查解析失败只剩长度和分类，需要在本地用合成输入复现。

## Verification

`apps/server/tests/test_agent/test_suggest_log_privacy.py` 用带哨兵字符串的合成回复/异常覆盖成功、解析失败、异常三条路径；修改前 3 项失败（哨兵出现在日志里），修改后与既有 suggest 服务、解析、`/agent/suggest` API 测试共 99 项通过。独立审查指出构造失败路径仍记 `str(exc)`（原用例只构造成功），补构造失败哨兵用例：修改前失败，修改后通过。
