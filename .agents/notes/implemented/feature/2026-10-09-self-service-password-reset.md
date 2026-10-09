# Agent Note: 自助重置密码（邮件验证码），改密后所有会话失效

Status: implemented

## Problem

新用户审计 #16：登录页默认不显示「忘记密码？」（`VITE_FORGOT_PASSWORD_ENABLED` 默认关闭），打开后的 `/forgot-password` 也只能引导用户发邮件找客服，没有自助重置。忘了密码的用户只能等人工处理，基本等于流失。

同时，改密码（`/api/auth/change-password`）只撤销 refresh token，已签发的 access token 还能继续用到过期（最长 60 分钟）。如果密码是因为账号被盗才改的，攻击者手里的 access token 仍然有效。

## Decision

不改数据库 schema，不加依赖，只新增 Redis 键。

**Redis（`services/infra/redis_client.py`）**：重置验证码使用独立命名空间，和注册验证码的 `verification:{email}` / `attempts:{email}` / `resend_cooldown:{email}` 完全隔离：

- `pwreset:code:{email}`：存 `sha256(code)`，TTL 为 `PASSWORD_RESET_CODE_TTL`，默认 600 秒；
- `pwreset:attempts:{email}`：这一个验证码的错误次数，TTL 与验证码相同，发新码时清零；
- `pwreset:fails:{email}`：这个邮箱 24 小时内的错误总次数（第一次错误时设 TTL 86400 秒），发新码、发信失败清理都不会清零它；
- `pwreset:cooldown:{email}`：60 秒发送冷却，用 `SET NX EX` 一步完成「检查并占用」；
- 两段 Lua 脚本：`consume` 在哈希仍然一致时一次删除 code 和 attempts；`record failure` 同时递增 attempts 和 fails，attempts 达到 5 次或 fails 达到 10 次的那一次同时删除 code。
- `password_reset_failures_capped(email, cap)`：读 fails 是否已到上限；Redis 出错时按已到上限处理（fail closed）。

**服务（`services/features/password_reset_service.py`）**：

- `request_reset(session, email, language, background_tasks)`：规范化邮箱后查用户；只有 `is_active` 且 `email_verified` 的账号才会用 `secrets` 生成 6 位码，在冷却期内或这个邮箱的错误总次数已到 `PASSWORD_RESET_MAX_DAILY_FAILURES`（10）时跳过。邮件由 `BackgroundTasks` 在响应发出后发送；发送失败会清掉 code 和冷却，用户可以立刻重发。整个过程的异常都被吞掉并记录 `error_type`，响应前补足 `PASSWORD_RESET_REQUEST_MIN_SECONDS`（默认 0.4 秒），所以已注册、未注册、未验证、已停用、Redis 出错的邮箱都在相同时间返回相同结果。
- `confirm_reset(session, email, code, new_password)`：邮箱错误总次数已到上限时直接返回通用错误，不再比对；否则先读出哈希用 `hmac.compare_digest` 比对；不一致就记一次失败；一致再用 Lua 原子消费，所以并发的两次正确提交只有一次成功。消费成功后才算 bcrypt、加行锁查用户，再在同一事务里写入新密码哈希、更新 `updated_at`、调用 `revoke_active_refresh_tokens_for_user(reason="password_reset")`。不自动登录。账号不存在、未验证、已停用、验证码错误、过期、已用完次数、邮箱已到每日错误上限，一律返回 `ERR_AUTH_PASSWORD_RESET_CODE_INVALID`（400）。
- 新密码校验复用注册规则（`_validate_new_password`：至少 6 个字符，最多 72 字节），在路由的 Pydantic 模型里执行；校验失败返回 422，不消耗验证码。
- 日志只记 `user_id`、`revoked_count`、`error_type`，从不记录验证码，重置相关的 Redis 日志也不记邮箱。

**路由（`api/auth.py`，保持薄）**：

- `POST /api/auth/password-reset/request {email, language}`：每个 IP 每 10 分钟 `AUTH_PASSWORD_RESET_IP_PER_10_MIN` 次（默认 5）；每个邮箱每小时 `AUTH_PASSWORD_RESET_EMAIL_PER_HOUR` 次（默认 3，`include_client_ip=False`）。超限返回 429 `ERR_AUTH_PASSWORD_RESET_TOO_MANY_REQUESTS`，否则一律 200 和 `auth_password_reset_requested` 通用消息。
- `POST /api/auth/password-reset/confirm {email, code, new_password}`：每个 IP 每 10 分钟 `AUTH_PASSWORD_RESET_CONFIRM_IP_PER_10_MIN` 次（默认 20）。
- `_enforce_auth_rate_limit` 增加 `error_code` 参数，其他调用点不变。
- `_revoke_active_refresh_tokens_for_user` 移到 `services/core/auth_service.py`，改名为 `revoke_active_refresh_tokens_for_user`，`api/auth.py` 以原名导入，锁顺序（先锁 User 行）不变。

**access token 指纹（`services/core/auth_service.py`）**：

- `password_fingerprint(user) = sha256(user.hashed_password or "")[:16]`，`access_token_claims(user)` 返回 `{sub, pwf}`。签发点 `login`、`refresh`、`verify-email`、Google OAuth 回调共 4 处都改用它。
- `get_current_user`、`get_optional_current_user` 和 `/api/auth/validate-token`：token 里有 `pwf` 且与当前指纹不一致时，分别返回 401 `ERR_AUTH_TOKEN_INVALID`、`None`、401。没有 `pwf` 的旧 token 照常放行，直到自然过期（≤ `ACCESS_TOKEN_EXPIRE_MINUTES`，默认 60）。
- 因此重置密码和 change-password 都会让所有旧 access token 立即失效。

**邮件（`services/infra/email_client.py`）**：`send_verification_email` 增加 `purpose` 参数（默认 `registration`，注册邮件不变）。`password_reset` 使用独立的标题和正文，中英两版，用「你」，正文写明「如果不是你本人操作，忽略这封邮件即可，密码不会改变」。

**前端**：

- `config/auth.ts`：除非 `VITE_FORGOT_PASSWORD_ENABLED === 'false'`，否则 `forgotPasswordEnabled` 为 true，登录页默认显示「忘记密码？」。
- `pages/ForgotPassword.tsx` 改为两步：输入邮箱 →「发送验证码」；然后显示「如果这个邮箱注册过 zenstory，验证码已经发出，10 分钟内有效。」，输入 6 位验证码、新密码、确认新密码 →「重设密码」，60 秒后可重发，可以换邮箱。提交前在本地检查验证码位数、密码长度、72 字节上限和两次输入是否一致。成功后弹 toast「密码已重设，请用新密码登录」并跳转 `/login`。页面底部保留「收不到邮件？发邮件给 support@zenstory.ai」。
- 版本错开兜底：任一接口返回 404 或 405 时，显示原来的「发邮件给客服」内容。
- `lib/passwordResetApi.ts` 放这两个调用，匿名请求，不带 access token，也不走 401 刷新逻辑。
- `auth.json`、`errors.json` 中英文同步补齐；`t()` 的兜底文本和 zh 文案一致。

## Alternatives considered

- **邮件里发一次性重置链接，而不是 6 位验证码**。最强理由：一次点击即可，熵更高，不需要手动输入。被否：邮件安全网关和杀毒软件会预取链接，一次性 token 可能在用户点开前就被消费；token 放在 URL 里还会进入浏览器历史和 Referer；验证码可以在手机上看邮件、在电脑上输入，而且和现有注册验证码的交互、邮件模板一致。
- **把重置凭据存进数据库（新表或新列）**。最强理由：持久、可审计，Redis 重启也不会丢。被否：需要迁移，而生产迁移是手动执行的；重置码只活 10 分钟，丢失的代价只是重发一次，Redis TTL 正好合适，注册验证码也已经放在 Redis。
- **用 `token_version` 列或 Redis 黑名单让 access token 失效**。最强理由：语义直接，还可以单独实现「退出所有设备」而不必改密码。被否：新列需要迁移；黑名单要求每个请求多查一次 Redis，还要决定 Redis 不可用时放行还是拒绝。`pwf` 由已有的 `hashed_password` 推导，不需要存储，并且恰好在密码变化时变化。代价是单纯的 logout 仍然不会让 access token 立即失效。
- **直接复用注册验证码的 `verification:` 键和函数**。最强理由：代码最少。被否：注册验证码可以直接拿来重置密码；两个流程会共用错误次数和冷却，互相干扰；注册验证码是明文存储。
- **请求接口不补时间，只依赖后台发信**。最强理由：没有额外延迟。被否：对存在的账号还要多做两次 Redis 写和一次冷却检查，响应时间可以区分账号是否存在；补到固定下限的成本只是每次请求多等约 0.4 秒。

## Consequences

- 收益：用户可以自助找回密码，不需要客服介入；接口不暴露邮箱是否注册；重置或改密后，所有设备上的 refresh token 和 access token 都立即失效。
- 收益：重置码只存哈希，有独立的冷却、错误次数和 TTL，并且原子消费，不会被重放或并发使用两次。错误次数还按邮箱累计 24 小时：每个码 5 次的预算不能靠反复申请新码续上，一个邮箱一天最多被猜 10 次。
- 代价：别人知道某个邮箱后，故意输错 10 次就能让这个邮箱当天无法自助重置（请求照常返回成功但不发信，确认一律报验证码无效），只能等 24 小时或找客服。和无限次猜码相比，这是可以接受的代价；账号是否存在都不会因此暴露，因为只有真实账号才会有验证码可猜、才会累计错误次数，而响应完全相同。
- 代价：change-password 会让当前设备的 access token 也立即失效（目前前端没有调用这个接口的入口，所以没有可见影响）。
- 代价：上线后最长 60 分钟内，不带 `pwf` 的旧 token 仍然有效，直到自然过期。
- 代价：JWT 不加密，持有 token 的人能看到 `pwf`。它是 bcrypt 哈希的 sha256 前 16 位，而 bcrypt 的盐不在 token 里，所以无法据此离线猜密码。
- 代价：请求接口固定至少 0.4 秒。Redis 不可用时，请求接口照样返回通用消息但不会发信，确认接口则按失败处理。
- 代价：只用 Google 登录、邮箱已验证的账号，也可以通过重置为自己设置一个密码，从此可以用密码登录。这是有意保留的：持有该邮箱即可证明身份。
- 代价：前端文案里的「10 分钟」是固定写的；如果调整 `PASSWORD_RESET_CODE_TTL`，需要同步修改 `auth.json`（服务端消息会自动使用实际分钟数）。
- `apps/web/.env.example` 写的是 `VITE_FORGOT_PASSWORD_ENABLED=true`，与代码默认一致（只有字面量 `false` 才隐藏入口）。代价：部署环境如果还设置了这个变量为 false，需要删掉。
- 版本错开：Vercel 可能先于 Railway 上线，这段时间前端请求会得到 404，页面会回退到「发邮件给客服」；先上线后端则没有影响（新接口暂时没有调用方）。

## Verification

- pytest（本地拉起的 Redis；CI 中使用 `ZENSTORY_TEST_REDIS_URL`）：`tests/test_api/test_password_reset.py`、`tests/test_services/test_password_reset_service.py`、`tests/test_api/test_access_token_password_fingerprint.py` 共 24 项通过。覆盖内容：已注册和未注册邮箱的响应相同；请求接口有时间下限；未验证或已停用的账号不发信；冷却期内不重复发信；IP 和邮箱限流返回 429；确认接口按 IP 限流；账号不存在和验证码错误返回同一个错误；错 5 次后正确的码也失效；过期失效；密码规则；成功后旧密码不能登录、新密码可以登录，旧 access token 和 refresh token 都返回 401，记录的撤销原因是 `password_reset`，验证码不能重放；注册验证码不能用来重置；所有日志记录里都没有验证码；只存哈希且 TTL 正确；原子消费；并发两次确认只成功一次；Redis 消费失败时不会改密码；发信失败可以立即重发；`pwf` 签发；旧 token 放行；change-password 后旧 token 在 `/me` 和 `validate-token` 都失效；optional 依赖返回 None。把 `pwf` 校验临时关掉时，其中 3 项会失败。
- 每日错误上限（`tests/test_services/test_password_reset_service.py`）：连续申请 3 个新码、每个码只错 4 次（不触发单码上限），累计到第 10 次后 fails 为 10、TTL ≤ 86400、当前码被删除，正确的码也报 `ERR_AUTH_PASSWORD_RESET_CODE_INVALID`，再申请不发信、不写入新码，密码不变；fails 已到上限时刚发出的正确码同样报通用错误；不存在的邮箱错 11 次仍是同一个错误码且不产生 fails 键；Redis 出错时上限检查返回已到上限。
- 回归：auth、oauth、verification、security、core、middleware 相关测试共 357 项通过、8 项跳过；`tests/test_api tests/test_services tests/test_utils tests/test_core tests/test_middleware` 全量（`-n 8`）2922 项通过、254 项跳过、0 项失败；`ruff check api/ services/ core/` 只剩 `core/error_handler.py` 原有的 ARG002。
- vitest：`src/pages src/lib src/config` 全量 1451 项通过；其中`ForgotPassword.test.tsx`（两步流程、60 秒重发倒计时、成功后弹 toast 并跳转、本地密码校验、服务端报错时停留在当前步骤、404 和 405 回退到联系客服、开关关闭时跳转）和 `Login.test.tsx`（默认显示「忘记密码？」）共 25 项通过；`tsc`、`pnpm lint`、`lint:tokens`、`lint:i18n-keys`、`lint:i18n`、`build:typecheck` 都通过。
- 本地端到端演练：真实的 FastAPI 应用、SQLite、本地 Redis，限流使用 Redis 后端，Resend 换成本地文件收件箱，合成账号 `p7-walk-*@example.com`。17 项检查全部通过：注册并验证邮箱、开两个会话、已注册和未注册邮箱的响应完全相同（耗时分别为 0.412 秒和 0.414 秒）、邮件标题和正文正确、错误验证码返回 400、确认成功、两个旧会话的 access token 和 refresh token 都返回 401、旧密码登录失败、新密码登录成功、同一邮箱第 4 次请求返回 429、服务端日志里没有验证码。
- staging 尚未演练（本包不推送、不部署）；合并部署到 staging 后，还需要用合成账号和真实 Resend 走一遍。
