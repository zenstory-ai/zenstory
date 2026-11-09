# Docker Compose 启动与自托管

仓库提供两套 Compose：`docker-compose.yml` 用于本机快速体验，`docker-compose.full.yml` 用于 PostgreSQL 和 Redis 的完整自托管部署。托管站点的 Railway/Vercel 发布另见[发布流程](ops/release-ci-cd.md)。

## 本机快速启动

需要 Docker Compose。配置仓库根目录 `.env`，填写自己的模型 Key，并设置固定的随机 JWT 密钥：

```dotenv
DEEPSEEK_API_KEY=填写自己的Key
JWT_SECRET_KEY=填写至少32字符的随机密钥
```

写作服务当前使用 `deepseek-flash`。使用真实 Key 生成文字会产生模型费用；开发验证使用 mock。仅启动页面不会替你配置项目写作提示词。

```sh
docker compose up -d --build
docker compose ps
```

默认访问 Web `http://localhost:5173`、API `http://localhost:8000`，API 健康入口是 `/health`。

## 创建管理员与写作配置

准备邮箱、唯一用户名和至少 12 字符的密码，以环境变量传给脚本，避免把真实密码写进命令历史：

```sh
export ZENSTORY_ADMIN_EMAIL=you@example.com
export ZENSTORY_ADMIN_USERNAME=admin
read -r -s ZENSTORY_ADMIN_PASSWORD
export ZENSTORY_ADMIN_PASSWORD
docker compose exec -e ZENSTORY_ADMIN_EMAIL -e ZENSTORY_ADMIN_USERNAME \
  -e ZENSTORY_ADMIN_PASSWORD server python scripts/create_admin.py
unset ZENSTORY_ADMIN_PASSWORD
```

管理员账号不需要邀请码和邮箱验证。不要把此脚本当作普通用户注册入口。

在管理后台维护 novel、short、screenplay 的系统提示词，保存后重新加载。新数据库缺少启用的对应配置时，写作会明确报错。官方技能从数据库读取，没有源码默认技能或自动种子。维护方法见[写作配置](agent-writing-guidance.md)。

创建账号后默认使用免费权益：每天 3 条 AI 消息、3 个项目。按需运行 `scripts/seed_subscription_plans.py` 初始化套餐，再从后台给账号开通权益。

## 普通用户注册

默认注册需要邀请码。管理员从邀请页面生成后交给用户；可选邀请策略由 `AUTH_REGISTER_INVITE_CODE_OPTIONAL` 与前端注册配置控制。

邮箱验证和密码重设需要 Redis、`RESEND_API_KEY`、有效的 `RESEND_FROM_EMAIL`。快速 Compose 没有 Redis，不适合直接开放邮箱注册。先完成邮件和 Redis 配置，再让普通用户注册。

## 完整部署

复制环境模板并填齐所有必填值：

```sh
cp apps/server/.env.docker.example apps/server/.env.docker
cp apps/web/.env.docker.example apps/web/.env.docker
```

后端模板包括独立的 PostgreSQL owner/app 用户、数据库名、对应密码、Redis 密码、JWT 密钥和模型 Key。使用 URL 安全的随机密码，owner 与 app 凭证分开。Web 模板的 `VITE_API_BASE_URL` 是浏览器可访问的 API origin，不加 `/api/v1`。

Compose 的变量替换不会仅凭 `env_file` 读取所有值，启动前需导出两个文件：

```sh
set -a
. apps/server/.env.docker
. apps/web/.env.docker
set +a
docker compose -f docker-compose.full.yml config --quiet
docker compose -f docker-compose.full.yml up -d --build
```

完整部署默认只在本机绑定 Web `127.0.0.1:8080`、API `127.0.0.1:8000`；数据库和 Redis 不暴露宿主端口。对外服务先配置 TLS 反向代理与准确的 `CORS_ORIGINS`、`FRONTEND_URL`。

`postgres-init` 初始化 app 角色；名为 `migrate` 的服务当前调用 `init_db` 创建缺失表，**不会替已有数据库执行 Alembic 列迁移**。升级前核对待执行迁移、备份与回滚兼容性，不把容器健康当成 schema 已更新。

## 可选能力

使用快速启动（`docker-compose.yml`）时，后端变量加在 `server.environment` 下，前端 `VITE_*` 变量加在 `web.environment` 下，修改后运行 `docker compose up -d`；仓库根目录 `.env` 只填充 Compose 文件里声明的 `${…}` 占位符（如 `DEEPSEEK_API_KEY`、`JWT_SECRET_KEY`）。使用 `docker-compose.full.yml` 时，变量写入 `apps/server/.env.docker` 和 `apps/web/.env.docker`。

| 能力 | 需要的配置 |
| --- | --- |
| 免邀请码注册 | 默认要邀请码（管理员在后台「邀请系统」生成）；免邀请码设后端 `AUTH_REGISTER_INVITE_CODE_OPTIONAL=true`，前端可选加 `VITE_INVITE_CODE_OPTIONAL=true` |
| 积分与邀请（默认关闭） | 前端 `VITE_POINTS_ENABLED=true` 显示积分页和签到，`VITE_REFERRAL_ENABLED=true` 显示「设置 → 邀请」和注册页的可选邀请码；修改后重新构建前端 |
| 邮件验证、密码重设 | `REDIS_URL`、`RESEND_API_KEY`、`RESEND_FROM_EMAIL`（需使用带 Redis 的 `docker-compose.full.yml`） |
| 重启后保持登录 | `JWT_SECRET_KEY`（至少 32 字符） |
| 主备模型切换 | `LLM_PRIMARY_PROVIDER` / `LLM_PRIMARY_MODEL`、`LLM_BACKUP_PROVIDER` / `LLM_BACKUP_MODEL`，Key 分别为 `DEEPSEEK_API_KEY`、`MINIMAX_API_KEY`（见 [`apps/server/agent/core/llm_providers.py`](../apps/server/agent/core/llm_providers.py)） |
| Google 登录 | 后端 `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET`、`GOOGLE_REDIRECT_URI`、`FRONTEND_URL`；前端 `VITE_GOOGLE_OAUTH_ENABLED=true` |
| 语音输入 | `TENCENT_SECRET_ID`、`TENCENT_SECRET_KEY` |
| 素材拆解与扩展提取 | 另外运行 Prefect server 和 worker（见 [`apps/server/prefect.yaml`](../apps/server/prefect.yaml)），并在套餐里开启素材库权限。逐章情节点及跨章故事线、人物关系默认关闭，按需在后端开启 `MATERIAL_ENABLE_*`（见 `apps/server/.env.example`） |
| 灵感库（默认关闭） | 后端 `INSPIRATIONS_ENABLED=true`，前端 `VITE_INSPIRATIONS_ENABLED=true`（快速 Compose 只需设 `INSPIRATIONS_ENABLED=true` 并重建容器） |
| 兑换码 | 先初始化 Pro 套餐，再配置 `REDEMPTION_CODE_HMAC_SECRET`（至少 32 字符） |
| 支付宝在线开通 / 续费 | 后端 `ZPAY_ENABLED=true`、`ZPAY_PID`、`ZPAY_KEY`、`ZPAY_NOTIFY_URL`、`ZPAY_RETURN_URL`，可选 `ZPAY_CID` |
| 外部 Agent / CLI | 后端 `API_BASE_URL=https://你的服务器/api/v1`（写入 `/skill.md`）；CLI 除本机 `localhost` / `127.0.0.1` 外只接受 HTTPS，详见 [`apps/cli/README.md`](../apps/cli/README.md) |

### Zpay 支付宝收款注意事项

在线支付默认关闭。启用前先初始化 Pro 套餐并设置月付/年付价格，应用数据库迁移并确认 `payment_order` 表存在。商户密钥只配置在后端环境变量中，不要放进 `VITE_*`。`ZPAY_NOTIFY_URL` 使用公开可访问的后端 HTTPS 地址（如 `https://api.example.com/api/v1/payments/zpay/notify`），`ZPAY_RETURN_URL` 使用前端回跳页（如 `https://app.example.com/dashboard/billing/payment-return`），两者都不能预设查询参数或片段。会员权益仅在后端验证 Zpay 签名、商户、金额和订单后发放；停用新支付时设 `ZPAY_ENABLED=false`，保留密钥和回调地址以处理存量订单通知。

## 数据与排查

快速部署的 SQLite、完整部署的 PostgreSQL/Redis 和上传文件使用命名卷。停止或重建容器通常保留卷；删除卷会删除数据，清理前先核对用途和备份。

```sh
docker compose logs --tail=100 server
docker compose ps
curl -f http://localhost:8000/health
```

端口可通过 `SERVER_PORT`、`WEB_PORT` 调整。前端实际 origin 必须加入 CORS；不要把数据库、Redis 或生产凭证暴露到浏览器环境变量。

升级涉及 worker 时，先排空任务。托管环境的生产操作、卷保留与 tag 门禁遵循[发布流程](ops/release-ci-cd.md)。
