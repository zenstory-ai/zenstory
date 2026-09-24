# zenstory Docker Compose 使用指南

## Quick Start（推荐）

写作 Agent 只需要 DeepSeek API Key（模型固定为 `deepseek-v4-flash`）：

```bash
# 1. 设置 DeepSeek API Key（也可以写进与 docker-compose.yml 同目录的 .env 文件）
export DEEPSEEK_API_KEY=your-key-here

# 2. 启动
docker compose up -d --build

# 3. 创建第一个账号：管理员，不需要邀请码和邮箱验证，密码至少 12 位
docker compose exec -e ZENSTORY_ADMIN_EMAIL=you@example.com \
  -e ZENSTORY_ADMIN_PASSWORD='at-least-12-characters' server python scripts/create_admin.py

# 4. 访问
# Web:  http://localhost:5173
# API:  http://localhost:8000/docs
```

数据库是 SQLite，和上传文件、向量索引一起放在 Docker volume 里：`docker compose down`、重新构建镜像都不会清掉数据，`docker compose down -v` 会。

## 其他人怎么注册

默认注册要填邀请码。用管理员账号在「设置 → 邀请」或管理后台「邀请系统」生成邀请码；不想要邀请码，就在 `docker-compose.yml` 的 `server.environment` 里加 `AUTH_REGISTER_INVITE_CODE_OPTIONAL: "true"`，再 `docker compose up -d`。

用邮箱注册的账号要先收验证码才能登录，发验证码需要 Redis 和 `RESEND_API_KEY`（以及在 Resend 验证过的 `RESEND_FROM_EMAIL`）。快速启动不带 Redis，需要的话用下面的 `docker-compose.full.yml`。

## 可选功能

| 功能 | 需要的配置 |
| --- | --- |
| 项目内语义检索（Agent 的 `hybrid_search` 与每轮自动检索片段） | `ZHIPU_EMBEDDINGS_API_KEY` |
| 邮箱注册验证码 | `REDIS_URL`、`RESEND_API_KEY`、`RESEND_FROM_EMAIL` |
| 语音输入 | `TENCENT_SECRET_ID`、`TENCENT_SECRET_KEY` |
| Google 登录 | 后端 `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET`、`GOOGLE_REDIRECT_URI`、`FRONTEND_URL`；前端 `VITE_GOOGLE_OAUTH_ENABLED=true`、`VITE_GOOGLE_CLIENT_ID` |
| 素材库拆解 | 另行运行 Prefect server 与 worker（见 `apps/server/prefect.yaml`），并给套餐打开素材库权限 |
| 外部 Agent 接入（Agent API） | `API_BASE_URL=http://你的服务器地址/api/v1`；设置页复制的提示词写的是 `https://api.zenstory.ai/skill.md`，发给 Agent 前换成 `http://你的服务器地址/skill.md` |
| Pro 套餐、兑换码 | `python scripts/seed_subscription_plans.py` 创建 Pro 套餐；`REDEMPTION_CODE_HMAC_SECRET`（至少 32 字符） |

## 生产部署（PostgreSQL + Redis）

```bash
# 1. 准备环境文件
cp apps/server/.env.docker.example apps/server/.env.docker
cp apps/web/.env.docker.example apps/web/.env.docker

# 2. 编辑 apps/server/.env.docker，填入：
#    - DEEPSEEK_API_KEY
#    - JWT_SECRET_KEY（至少 32 字符）
#    - DATABASE_URL 与 POSTGRES_USER / POSTGRES_PASSWORD 保持一致（默认 zenstory / changeme）

# 3. 启动
docker compose -f docker-compose.full.yml up -d --build
```

## 常用命令

```bash
docker compose up -d --build      # 启动并构建
docker compose down               # 停止并删除容器（数据留在卷里）
docker compose down -v            # 停止并删除容器和卷（数据一起删除）
docker compose logs -f server     # 查看后端日志
docker compose ps                 # 查看服务状态
```

## 故障排查

### 端口冲突

```bash
lsof -i :5173  # Web
lsof -i :8000  # Server
```

修改端口：`SERVER_PORT=9000 WEB_PORT=3000 docker compose up -d`

### 服务不健康

```bash
docker compose logs --tail=100 server
curl -f http://localhost:8000/health
```
