# 错误码与统一异常响应

`core/error_codes.py` 定义 `ErrorCode` 常量、`ERROR_MESSAGES` 中英文消息和 `get_error_message(error_code, lang)`。`core/error_handler.py` 将异常转换为 API 响应，前端按错误码显示译文。

## 异常与响应

`apps/server/main.py` 注册五类处理器：

| 异常 | 处理器 | 响应 |
| --- | --- | --- |
| `APIException` | `api_exception_handler` | 保留 HTTP 状态，返回 `detail`、`error_code` 和 `error_detail` |
| `StarletteHTTPException` | `http_exception_handler` | 保留状态和响应头，返回字符串 `detail` |
| `RequestValidationError` | `validation_exception_handler` | 422，返回 `ERR_VALIDATION_ERROR` 和字段错误列表 |
| `IntegrityError` | `integrity_error_handler` | 409，返回 `ERR_RESOURCE_CONFLICT` |
| 其他未捕获异常 | `general_exception_handler` | 500，返回 `ERR_INTERNAL_SERVER_ERROR`，可附 `request_id` |

验证错误只保留 `loc`、`type`、`msg`，不返回提交值；未捕获异常的 SQL、堆栈和内部消息留在服务端日志。HTTP 响应头包括鉴权或限流需要的 `WWW-Authenticate`、`Retry-After`。

## 在 API 中抛出错误

```python
from core.error_codes import ErrorCode
from core.error_handler import APIException

raise APIException(
    error_code=ErrorCode.PROJECT_NOT_FOUND,
    status_code=404
)
```

对应响应：

```json
{
  "detail": "ERR_PROJECT_NOT_FOUND",
  "error_code": "ERR_PROJECT_NOT_FOUND",
  "error_detail": "ERR_PROJECT_NOT_FOUND"
}
```

调用方传入 `detail` 时，附加说明放在 `error_detail`，`detail` 和 `error_code` 仍是错误码。标准 `HTTPException` 也有处理器，但其 `detail` 不会自动变成错误码：

```python
from fastapi import HTTPException
raise HTTPException(status_code=404, detail="Project not found")
```

```json
{
  "detail": "Project not found"
}
```

## 错误码与翻译

错误码是 `ERR_...` 字符串，覆盖通用错误、鉴权、项目、文件、版本、快照、导出、聊天、语音、订阅、配额、兑换、积分和 Agent 流。新增错误时在 `ErrorCode` 中声明，并维护对应消息：

```python
# 认证错误
ErrorCode.AUTH_INVALID_CREDENTIALS  # 用户名或密码错误
ErrorCode.AUTH_TOKEN_EXPIRED         # 登录已过期
ErrorCode.NOT_AUTHORIZED            # 您没有权限执行此操作

# 项目错误
ErrorCode.PROJECT_NOT_FOUND          # 项目不存在
ErrorCode.PROJECT_ALREADY_EXISTS     # 项目已存在

# 文件错误
ErrorCode.FILE_NOT_FOUND            # 文件不存在
ErrorCode.FILE_TYPE_INVALID         # 文件类型无效
```

前端译文位于 `apps/web/public/locales/zh/errors.json` 和 `apps/web/public/locales/en/errors.json`。新增或调整对外错误码时同步核对这两份译文；处理器回归见 `apps/server/tests/test_core/test_error_handler.py`。
