# zenstory Docker Compose 使用指南

## Quick Start（推荐）

写作 Agent 只需要 DeepSeek API Key（模型固定为 `deepseek-v4-flash`）：

```bash
# 1. 设置 DeepSeek API Key（也可以写进与 docker-compose.yml 同目录的 .env 文件）
export DEEPSEEK_API_KEY=your-key-here

# 2. 启动
docker compose up -d --build

# 3. 创建第一个账号：管理员，不需要邀请码和邮箱验证。
#    把邮箱和密码换成你自己的，密码至少 12 位（CHANGE-ME 不够长，会被拒绝）
docker compose exec -e ZENSTORY_ADMIN_EMAIL=you@example.com \
  -e ZENSTORY_ADMIN_PASSWORD='CHANGE-ME' server python scripts/create_admin.py

#    可选：把 13 个内置技能导入「技能 → 发现技能」
docker compose exec server python scripts/migrate_skills.py --db-url sqlite:////app/db/zenstory.db

# 4. 访问
# Web:  http://localhost:5173
# API:  http://localhost:8000/docs
```

数据库是 SQLite，和上传文件、向量索引一起放在 Docker volume 里：`docker compose down`、重新构建镜像都不会清掉数据，`docker compose down -v` 会。没有设置 `JWT_SECRET_KEY` 时，服务每次重启都会换一个签名密钥，所有人需要重新登录；在 `.env` 里写一个至少 32 字符的 `JWT_SECRET_KEY` 即可保持登录。

每个账号（包括管理员）默认都是免费套餐：每天 20 次 AI 对话、最多 3 个项目。要放开，先运行 `docker compose exec server python scripts/seed_subscription_plans.py` 创建 Pro 套餐，再在管理后台「订阅管理」里给账号开通 Pro，或在「订阅计划」里直接改免费套餐的额度。

## 其他人怎么注册

默认注册要填邀请码。用管理员账号在「设置 → 邀请」或管理后台「邀请系统」生成邀请码；不想要邀请码，就在 `docker-compose.yml` 的 `server.environment` 里加 `AUTH_REGISTER_INVITE_CODE_OPTIONAL: "true"`，再 `docker compose up -d`。

用邮箱注册的账号要先收验证码才能登录，发验证码需要 Redis 和 `RESEND_API_KEY`（以及在 Resend 验证过的 `RESEND_FROM_EMAIL`）。快速启动不带 Redis，别人在这里用邮箱注册后会因为「邮箱未验证」登录不了，需要的话用下面的 `docker-compose.full.yml`。只给几个信得过的人用，也可以再运行 `create_admin.py` 建账号，每次换一个邮箱和 `ZENSTORY_ADMIN_USERNAME`（默认是 `admin`，用户名不能重复）。这样建的都是管理员账号，能进管理后台；建好后在后台「用户管理」里编辑这个账号，取消「超级用户」。

## 可选功能

| 功能 | 需要的配置 |
| --- | --- |
| 项目内语义检索（Agent 的 `hybrid_search` 与每轮自动检索片段） | `ZHIPU_EMBEDDINGS_API_KEY` |
| 邮箱注册验证码 | `REDIS_URL`、`RESEND_API_KEY`、`RESEND_FROM_EMAIL` |
| 语音输入 | `TENCENT_SECRET_ID`、`TENCENT_SECRET_KEY` |
| Google 登录 | 后端 `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET`、`GOOGLE_REDIRECT_URI`、`FRONTEND_URL`；前端（`web.environment`）`VITE_GOOGLE_OAUTH_ENABLED=true` |
| 素材库拆解 | 另行运行 Prefect server 与 worker（见 `apps/server/prefect.yaml`），并给套餐打开素材库权限 |
| 外部 Agent 接入（Agent API） | `API_BASE_URL=http://你的服务器:8000/api/v1`（后端地址，不是 5173）；设置页复制的提示词写的是 `https://api.zenstory.ai/skill.md`，发给 Agent 前换成 `http://你的服务器:8000/skill.md` |
| Pro 套餐、兑换码 | `python scripts/seed_subscription_plans.py` 创建 Pro 套餐；`REDEMPTION_CODE_HMAC_SECRET`（至少 32 字符） |

## 生产部署（PostgreSQL + Redis）

```bash
# 1. 准备环境文件
cp apps/server/.env.docker.example apps/server/.env.docker
cp apps/web/.env.docker.example apps/web/.env.docker

# 2. 编辑 apps/server/.env.docker，填入：
#    - DEEPSEEK_API_KEY
#    - JWT_SECRET_KEY（至少 32 字符）
#    - DATABASE_URL 与 POSTGRES_USER / POSTGRES_PASSWORD 保持一致（默认 zenstory / changeme；
#      对外提供服务前，在仓库根目录的 .env 里改掉 POSTGRES_PASSWORD，并同步改 DATABASE_URL）

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

修改端口：`SERVER_PORT=9000 WEB_PORT=3000 docker compose up -d`。开发模式下后端默认允许 5173 和 3000 两个前端端口的跨域请求，用其他端口时把前端地址加进 `docker-compose.yml` 的 `CORS_ORIGINS`。

### 服务不健康

```bash
docker compose logs --tail=100 server
curl -f http://localhost:8000/health
```
