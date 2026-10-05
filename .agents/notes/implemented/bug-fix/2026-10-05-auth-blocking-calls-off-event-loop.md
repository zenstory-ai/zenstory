# Agent Note: 认证路径上的 bcrypt、Resend 与同步 Redis 移出事件循环

Status: implemented

## Problem

`register`、`login`、`change_password`、`resend_verification`、`check_verification` 都是 `async def`，却直接执行阻塞操作：

- passlib bcrypt（`$2b$12$`）每次哈希或校验约 0.3s（本机实测，Railway vCPU 更慢）；
- `send_verification_email` 在 async 函数里同步调用 `resend.Emails.send`，SDK 默认用 requests、超时 30s；
- 验证码服务对 Redis 的读写都是同步调用，每次 socket 超时 2s。

`railway.toml` 默认 `WEB_CONCURRENCY=1`，单进程单事件循环：一次注册就可能让所有 SSE 写作流和 API 停顿数百毫秒到 30 秒。

## Decision

- `api/auth.py`：`hash_password` / `verify_password` 一律 `await asyncio.to_thread(...)`。登录仍保持「用户不存在时不校验密码」的原有分支。
- `services/infra/email_client.py`：`resend.Emails.send` 经 `asyncio.to_thread` 执行；模块加载时用 `resend.RequestsClient(timeout=RESEND_TIMEOUT_SECONDS)` 替换 SDK 默认客户端（SDK 有该钩子时），`RESEND_TIMEOUT_SECONDS` 默认 8，限制在 5–10 秒。
- `services/features/verification_service.py`：`send_verification_code` 与 `verify_code` 中所有同步 Redis 调用改为 `await asyncio.to_thread(...)`；`api/verification.py` 的 `check_verification` 对 `get_remaining_cooldown` / `get_code_ttl` 同样处理。函数签名与返回值不变。

## Alternatives considered

- **把这些路由改成 `def`，交给 FastAPI 线程池**。最强理由：一次改动覆盖路由里所有阻塞调用，不用逐个包装。被否：`register` 还要 `await` 邀请关系服务与验证码发送（async），改成同步需要连带改调用链；逐个 `to_thread` 改动最小、意图明确。
- **换成 redis.asyncio 与 Resend 的异步客户端**。最强理由：真正的异步 IO，不占线程。被否：Redis 客户端被限流、验证码等多处同步代码共享，整体迁移超出本次范围；线程包装对低频的认证路径足够。
- **只调大 `WEB_CONCURRENCY`**。最强理由：零代码改动，多进程互相隔离。被否：每个进程内的事件循环仍会被阻塞，只是把影响面分摊；内存与连接数也随进程数翻倍。

## Consequences

- 收益：注册、登录、改密码、发验证码不再冻结同进程的 SSE 流与其他请求；Resend 卡住最多占用一个工作线程 10 秒，注册按原逻辑继续（发送失败可重发）。
- 代价：认证请求会占用默认线程池（AnyIO 默认 40 个），高并发登录时 bcrypt 仍受 CPU 限制，只是不再阻塞事件循环。替换 Resend 全局 HTTP 客户端会影响同进程内其他 Resend 调用（目前只有验证码邮件）。
