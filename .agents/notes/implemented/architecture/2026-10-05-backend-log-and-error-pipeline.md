# Agent Note: 后端日志与错误管线——堆栈、位置、关联 id、脱敏、500 与请求体上限

Status: implemented

## Problem

后端只有 stdout JSON 日志，没有 Sentry，日志本身却有几处结构性缺陷：

- `log_with_context` 没有传 `stacklevel`，所有经它的日志 `file/line/function` 都是 `utils/logger.py` 自己；它也不接受 `exc_info`，约 85 处 ERROR 只有 `error=str(e)`，没有堆栈。
- `JsonFormatter` 只输出 `custom_fields`，`logger.warning(..., extra={...})` 的字段全部丢失；直接 `logger.*` 调用和 BackgroundTasks 里的日志不带 `request_id`；时间戳是 `...+00:00Z`，不是合法 ISO-8601。
- 未捕获异常的 500 由挂在最外层 `ServerErrorMiddleware` 上的 `general_exception_handler` 生成，绕过了 CORS 与 `LoggingMiddleware`：浏览器读不到响应（没有 CORS 头）也拿不到 `X-Request-ID`，唯一带堆栈的日志又因为上下文已重置而没有 `request_id`；uvicorn 另外往 stderr 打一份纯文本 traceback。CORS 也没有 `expose_headers`，前端跨域读不到关联 id。
- 422 处理器把 Pydantic 错误原样 `str()` 进日志（`input` 含明文密码或整章正文），响应里的 `ctx` 含 `ValueError` 对象，`json.dumps` 失败后整个请求变成 500。
- 请求日志原样记录 query string（`?token=`、OAuth `code`/`state`、`?email=`），还有大量明文 email 与提示词前 100 字。
- 每个 SSE Agent 流都超过 500ms，被记成 WARNING「慢请求」；客户端中途断开的流没有完成日志。
- `/health` 是静态回应，没有能真正检查依赖的端点；也没有全局请求体上限。

## Decision

日志管线（`utils/logger.py`、`config/logger_config.py`、`utils/sanitize.py`）：

- `log_with_context(logger, level, message, *, exc_info=None, **fields)` 以 `stacklevel=2` 调 `Logger.log`，位置指向调用方。`exc_info` 未指定且级别 ≥ ERROR、当前正处理异常时自动带上堆栈。
- 根 handler 挂 `RequestContextFilter`，把 `get_log_context()`（request_id/trace_id/agent_run_id）放到每条 record 上；`JsonFormatter` 依次合并上下文、`extra=` 的非保留属性、`custom_fields`（后者可覆盖）。时间戳取 `record.created`，毫秒精度、以 `Z` 结尾。
- 输出前脱敏：消息与所有字段里的 email 统一掩码为 `a***@example.com`；`message_preview`、`user_message_preview`、`prompt`、`user_prompt`、`prompt_preview`、`content_preview`、`query`（上下文组装与向量检索记录的使用者查询，`agent/context/assembler.py`、`api/vector_search.py`、`services/infra/vector_search_service.py`）、`original_snippet`（`agent/core/stream_processor.py` 记录的模型输出片段）字段只保留长度（`[redacted N chars]`），嵌套 dict 里同名键同样处理。集中在 formatter 做，不改各调用点（包括 `api/agent.py` 等正在被其他 PR 修改的文件）。
- `uvicorn`、`uvicorn.error` 的 handler 清空并向根 logger 传播，traceback 也走 JSON。

`LoggingMiddleware`：

- query 参数经 `sanitize_query_params` 渲染：`token`、`access_token`、`refresh_token`、`id_token`、`code`、`state`、`password`、`key`、`api_key`、`secret`、`sign`、`signature` 记为 `[redacted]`，其余值掩码 email；zpay 回调整体继续标记为 redacted。
- 下游抛出未捕获异常时，在请求上下文仍有效时以 `exc_info` 记「Exception occurred」（带 request_id），若响应尚未开始就自己发出 `internal_error_response(request_id)`（`{"detail","error_code":"ERR_INTERNAL_SERVER_ERROR","request_id"}`），不再向外抛；响应已开始（流式）则照旧抛出。完成日志显式 `exc_info=False`，避免同一堆栈打两遍。
- `text/event-stream` 响应不参与慢请求判定；响应开始但没有收到最后一块时，在 `finally` 里补一条完成日志，带 `client_disconnected=true`（或 app 中途抛错时 `incomplete_response=true`）。
- 请求上下文改在整个 ASGI 调用返回后重置，BackgroundTasks 里的日志也带 request_id。
- `general_exception_handler` 保留作为最外层兜底，同样带 `exc_info` 与 `request.state.request_id`。

中间件顺序（`main.py`，后加者在外）：`CORSMiddleware` → `LoggingMiddleware` → `BodySizeLimitMiddleware` → app。CORS 增加 `expose_headers=["X-Request-ID", "X-Trace-ID", "X-Agent-Run-ID"]`。

422：`validation_exception_handler` 用 `summarize_validation_errors` 只保留每条错误的 `loc`/`type`/`msg`，日志与响应共用，响应经 `jsonable_encoder`；含 `ValueError` 的校验错误返回 422。前端注册表单在提交前检查密码 UTF-8 字节数 ≤ 72（bcrypt 上限，与后端校验一致）。

请求体上限：`middleware/body_size_limit.py` 的 ASGI 中间件，`MAX_REQUEST_BODY_BYTES` 默认 25 MiB（无效或非正值回退默认）。声明的 `Content-Length` 超限直接回 413；chunked 或声明不实时，在 `receive` 里累计字节，超限抛 `RequestBodyTooLarge`（`HTTPException(413, ERR_FILE_TOO_LARGE)`），FastAPI 的 body 解析原样上抛、由 HTTP 异常处理器渲染；漏出到中间件时若响应未开始也回 413。只看请求，不碰响应，SSE 不受影响。

健康检查：`/health` 保持静态（Railway 部署健康检查用）。新增 `/health/ready`，由 `services/infra/readiness_service.py` 在线程里执行 `SELECT 1` 与 Redis `PING`，每项 `READINESS_CHECK_TIMEOUT_S`（默认 2s）超时；`REDIS_URL` 未设置时 Redis 记为 `skipped`。全部通过回 200 `{"status":"ready","checks":{...}}`，否则 503（`checks.*.status` 为 `error`/`timeout`，`error` 只给异常类名）。

## Alternatives considered

- **接入 Sentry**。最强理由：堆栈聚合、告警、release 关联一次到位。被否：目前没有 DSN，属于本次延后项；结构化日志是 Sentry 接入后仍要的基础，先修好不浪费。
- **调整中间件顺序，让 `general_exception_handler` 落在 CORS 内层**。最强理由：不用在中间件里自己发响应。被否：Starlette 把 `Exception` 处理器固定挂在最外层 `ServerErrorMiddleware`，无法通过注册顺序移进去；在 `LoggingMiddleware` 里答复是唯一能同时拿到 CORS 头、`X-Request-ID` 和有效上下文的位置。
- **在各调用点改掉 email 与提示词预览日志**。最强理由：从源头不产生敏感日志，意图更清楚。被否：涉及几十处，而且部分文件正由其他 PR 修改；formatter 集中脱敏能覆盖第三方库和未来新增的日志，调用点可以之后逐步降级。
- **请求体上限只看 `Content-Length`**。最强理由：实现简单，不包装 `receive`。被否：chunked 上传与谎报长度可以绕过，正是需要防的情况。
- **把 `/health` 直接改成检查依赖**。最强理由：只有一个端点，监控配置简单。被否：Railway 部署健康检查用 `/health`，数据库或 Redis 短暂抖动会让部署失败或回滚；存活与就绪分开是常规做法。

## Consequences

- 收益：ERROR 带堆栈与正确位置；每条日志（含后台任务、第三方 logger、uvicorn）都能按 request_id 关联；500 对浏览器可读、带 request_id，用户反馈能对上日志；422 不再变 500，也不泄露密码与正文；SSE 不再淹没慢请求告警；外部探活可以用 `/health/ready`。
- 代价：每条日志多一次正则扫描（email 掩码）；被掩码的 email 在排障时只能按首字母和域名辨认，需要用 user_id。测试里依赖「未处理异常直接抛给 httpx 客户端」的用例改为断言 500。25 MiB 上限低于素材上传声明的 100 MB（`api/materials/constants.py`、前端 `MATERIALS_UPLOAD_MAX_BYTES`），超过 25 MiB 的 txt 现在得到 413 而不是字数超限提示；实际可拆解内容受 30 万字限制，远小于 25 MiB，需要时可调 `MAX_REQUEST_BODY_BYTES`。
