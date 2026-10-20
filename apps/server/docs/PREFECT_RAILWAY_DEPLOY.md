# Railway 上的 Prefect 素材处理服务

素材库用 Prefect 调度小说拆解。API 接收上传并登记任务，Prefect Server 保存调度状态，process Worker 执行流程并通过 API 内部下载接口读取素材。API 与 Worker 使用业务数据库；Prefect Server 使用自己的元数据库连接。

发布门禁、迁移和运行中任务保护遵循[发布流程](../../../docs/ops/release-ci-cd.md)。本页说明服务配置，不代表某个环境已完成部署或启用素材处理。

## 服务与镜像

三个服务都以仓库根目录为构建上下文：

| 服务 | Dockerfile | 启动入口 |
| --- | --- | --- |
| API | `apps/server/Dockerfile` | API 服务，内部素材下载接口 |
| Prefect Server | `apps/server/docker/Dockerfile.prefect-server` | `start-prefect-server.sh` |
| Prefect Worker | `apps/server/docker/Dockerfile.prefect-worker` | `start-prefect-worker.sh` |

Server 镜像固定 Prefect `3.6.28` 和 FastAPI `0.123.7`，Worker 从 `apps/server/requirements.txt` 一次安装依赖。版本兼容由 CI 的 `Prefect compatibility` 检查验证，更新时同步核对镜像与依赖。

在已核实的 Railway 项目、服务和环境中设置 Dockerfile 路径。仓库中的 Dockerfile 路径相对构建上下文；不要依赖本地目录猜测云服务归属。

## 环境隔离

| 配置 | Staging | Production |
| --- | --- | --- |
| Git 来源 | `main` | `release-production`，由已授权的 `prod-v*` tag 提升 |
| `PREFECT_WORK_POOL` | `zenstory-staging-pool` | `zenstory-pool` |
| API、业务数据库、Redis、上传存储、Prefect Server | 各自独立 | 各自独立 |

Server 的 `PREFECT_API_DATABASE_CONNECTION_URL` 使用 PostgreSQL 异步驱动连接，例如 `postgresql+asyncpg://user:pass@host:5432/prefect`。`start-prefect-server.sh` 监听 `0.0.0.0`，使用 Railway 提供的 `PORT`，缺省为 `4200`。

API 与 Worker 必须连同一环境的 Prefect Server；Worker 的业务数据库也必须属于该环境。

| 变量 | 配置位置与用途 |
| --- | --- |
| `PREFECT_API_URL` | API、Worker：对应环境的 Prefect API，含 `/api` |
| `PREFECT_WORK_POOL` | Worker：当前环境的工作池 |
| `DATABASE_URL` | API、Worker：当前环境的业务数据库 |
| `API_SERVER_INTERNAL_URL` | Worker：可达的 API 内网地址，如 `http://server.railway.internal:8080` |
| `MATERIAL_INTERNAL_TOKEN` | API、Worker：一致的内部素材下载鉴权 token |
| `DEEPSEEK_API_KEY` / `MINIMAX_API_KEY` | Worker：素材分析模型凭证，由部署环境注入（与 API 相同的主备配置） |
| `LLM_PRIMARY_PROVIDER` / `LLM_BACKUP_PROVIDER`（及 `_MODEL`） | Worker：主备供应商与模型，配置方式见 `.env.example` |

上传对象存储的凭证只放在 API。Worker 通过内部下载接口取得临时文件，完成或失败后清理；本地与 S3 引用处理见[私有上传存储](upload-storage-bridge.md)。

## Worker 启动与发布顺序

`start-prefect-worker.sh` 等待 Prefect `/health`，创建 process 工作池，读取 `prefect.yaml` 中的 deployment 名称，逐一向 `PREFECT_WORK_POOL` 登记，再启动 Worker。具名登记使用：

```bash
prefect deploy --name "$deployment_name" --pool "$POOL_NAME"
```

Prefect `3.6.28` 在多 deployment 的 `--all` 模式下会忽略 `--pool` 覆盖。当前启动脚本逐个登记，避免 staging 误用 YAML 中的生产默认池。`prefect.yaml` 的工作目录为容器内 `/app`，不要直接当作本地流程目录运行。

替换已有 Worker 前，按发布流程暂停目标工作池，确认没有 pending、running、cancelling、paused flow 或 processing ingestion job，再进行已授权的发布。新 Worker 心跳正常且旧部署状态为 `REMOVED` 后才恢复工作池；仅看到新 Worker online 不足以恢复。生产流程须同时通过精确源码的 main CI、E2E 和发布授权。

## 验证与定位

核对服务的源码 SHA、Prefect Server 健康、deployment 所属工作池和 Worker 心跳；检查任务前先确认目标环境。素材拆解会调用模型，测试使用 mock；真实拆解需要单独授权。

- Worker 连不上 Server：核对 `PREFECT_API_URL`、Server 健康和网络可达性。
- 素材下载失败：核对 API 内网地址以及两端 `MATERIAL_INTERNAL_TOKEN` 是否一致。
- Worker 启动失败：查启动日志中的依赖检查、健康等待和具名 deployment 登记结果。
- 数据库错误：分清业务数据库与 Prefect 元数据库，核对当前环境的连接与迁移状态。
