# Agent Note: Docker 快速启动能真正跑起来，数据留在卷里

Status: implemented

## Problem

README 与 `docs/docker-compose.md` 写的是「`export DEEPSEEK_API_KEY=...` 后 `docker compose up -d --build` 一条命令启动，数据持久化在 Docker volume 中」。2026-09-24 从 `git archive HEAD` 的干净目录实测，这条路有四处断开：

- **web 镜像构建失败**：`apps/web/Dockerfile` 执行 `COPY package.json pnpm-lock.yaml ./`，但构建上下文是 `apps/web`，pnpm 锁文件在仓库根目录，报 `"/pnpm-lock.yaml": not found`。CI 只构建 server 与 Prefect 镜像，从未构建过这个 Dockerfile，所以它从首个提交起就是坏的。
- **新构建的后端建不了任何记录**：`requirements.txt` 写的是 `sqlmodel>=0.0.14`。sqlmodel 0.0.45（2026-09-21 发布）起拒绝写入不带时区的 datetime，而模型普遍用 `default_factory=datetime.utcnow`。任何一次全新安装都会拿到 0.0.47，注册、`create_admin.py` 以及一切带时间戳默认值的插入都会报 `Datetime values must have timezone information`。本地虚拟环境停在 0.0.38，CI 的 backend-test 因路径过滤近 30 次都被跳过，所以没被发现。这也影响任何一次重新构建的线上后端。
- **数据库不在卷里**：`DATABASE_URL: sqlite:///./zenstory.db` 落在容器层的 `/app/zenstory.db`，而 `zenstory_db` 卷挂在 `/app/db`。`docker compose down` 或重建镜像就会清空全部账号和项目，issue #1 的回复恰好建议了 `down` 再 `up --build`。
- **卷没有写权限**：镜像里不存在 `/app/uploads`、`/app/chroma_data`、`/app/db`，Docker 以 root 身份创建挂载点，而进程以 uid 1000 的 `zenstory` 运行，三处写入都是 `Permission denied`。

## Decision

- `apps/server/requirements.txt` 把 sqlmodel 限制为 `>=0.0.14,<0.0.45`，并用注释写明原因与解除条件（时间戳默认值改成带时区之后）。
- `apps/web/Dockerfile` 改用 `apps/web/package-lock.json` 执行 `npm ci --legacy-peer-deps`，以 `npm run dev` 启动。这份 npm 锁文件本来就被 Vercel 构建与 CI 的 vercel-build 任务使用并校验。
- `apps/server/Dockerfile` 在切换到 `zenstory` 用户之前创建 `/app/db`、`/app/uploads`、`/app/chroma_data` 并交给该用户，命名卷首次挂载时继承这个属主。Railway 的卷挂在 `/app/chroma_data` 与 `/app/uploads`，不受影响。
- `docker-compose.yml` 的 SQLite 路径改为 `sqlite:////app/db/zenstory.db`；文件头改为真实可走的步骤（导出 Key 或写 `.env`、启动、用 `create_admin.py` 建第一个账号）。
- `docker-compose.full.yml` 给 server 挂上 uploads 与 chroma 两个卷；`apps/server/.env.docker.example` 的 `DATABASE_URL` 改成与 compose 默认的 `zenstory / changeme` 一致。
- `docs/docker-compose.md` 与 `CONTRIBUTING.md` 写明第一个账号怎么来、邀请码与邮箱验证的默认行为、各可选功能需要的 Key，删除指向不存在的 `docker-compose.mini-local.yml` 的段落；Node 版本要求改为 20.19+（Vite 7 的下限）。

## Alternatives considered

- **把时间戳默认值全部改成带时区，而不是限制 sqlmodel 版本**。最强理由：这是根治，不会被版本上限卡住以后的升级。被否：模型、服务与测试里有大量 `datetime.utcnow` 和 naive datetime 比较，SQLite 与 PostgreSQL 的列类型也要一起核对，改动面远超这次修复；版本上限一行就能让现有代码在全新安装下恢复可用，根治另开一项。
- **web 镜像改为以仓库根目录为构建上下文，继续用 pnpm workspace 安装**。最强理由：与本地开发、CI 的 pnpm 流程一致，只维护一份依赖解析。被否：需要把根目录的 `package.json`、`pnpm-workspace.yaml`、锁文件和整个 `apps/web` 一起送进上下文，并调整 compose 的 build 配置；`apps/web/package-lock.json` 已被 Vercel 与 CI 持续校验，改用它只动一个 Dockerfile。
- **把快速启动的数据库换成 PostgreSQL**。最强理由：与线上一致，也不再有 SQLite 路径问题。被否：快速启动的价值是一个 Key 就能跑；`docker-compose.full.yml` 已经提供 PostgreSQL + Redis。

## Consequences

- 收益：从干净检出执行文档里的四步即可登录使用，`docker compose down` 与重建镜像后账号和项目仍在；线上后端下次重新构建不会因 sqlmodel 新版本而无法写入。
- 代价：sqlmodel 停在 0.0.44，直到时间戳默认值改为带时区；web 容器仍然运行 Vite 开发服务器，而不是生产构建。
- 未做：`apps/web/Dockerfile.mini-local` 仍引用不存在的 pnpm 锁文件（没有任何 compose 文件使用它）；CI 仍不构建 web 镜像；时间戳默认值的根治。

## Verification

在 `git archive HEAD` 导出的干净目录中 `docker compose build` 与 `up -d`，依次确认：server 健康检查通过、web 返回页面、`create_admin.py` 建出管理员并能登录、通过 API 创建项目与文件、导出与版本比较正常、`docker compose down` 后再 `up -d`，账号与项目仍在。
