# 开发入口

从仓库根目录工作，使用根 `pnpm-lock.yaml`。先读 [AGENTS.md](../../AGENTS.md)，改 UI 前读 [DESIGN.md](../../DESIGN.md)；子系统有 agent guide 时先核对其约束。

## 仓库结构

| 路径 | 负责内容 |
| --- | --- |
| `apps/web/src` | React 工作台、管理后台和共享组件 |
| `apps/web/docs` | 公开工作台文档，包含 `en/` 翻译 |
| `apps/web/content`、`apps/web/scripts` | 组织站内容与静态页面生成 |
| `apps/server` | FastAPI、服务、写作 Agent、素材处理与数据模型 |
| `apps/cli` | 独立 ZenStory CLI，发布流程与网站分开 |
| `scripts/ci` | 本地门禁与 CI 共用入口 |
| `.agents/notes` | 行为、架构和流程决策 |

## 运行

前端从根目录启动：

```sh
pnpm --dir apps/web dev
```

后端使用项目虚拟环境，按 `apps/server/.env.example` 配置本地资源，再启动：

```sh
cd apps/server
python main.py
```

前端 API 地址由 `VITE_API_BASE_URL` 决定。本地开发和验证使用测试数据及模型、支付 mock；不要把生产数据库或凭证接入本地。完整容器启动见 [Docker Compose](../docker-compose.md)。

## 检查

按修改范围先跑目标回归，再跑相关静态检查：

```sh
pnpm --dir apps/web exec vitest run <affected-test-files>
pnpm --dir apps/web lint
pnpm --dir apps/web lint:tokens
pnpm --dir apps/web lint:i18n-keys
pnpm --dir apps/web build:typecheck
pnpm --dir apps/web test:site
```

后端在项目环境中运行：

```sh
pytest <affected-tests> -q --no-cov
ruff check agent/ api/ services/
```

浏览器测试与完整门禁见 [E2E 与 CI](e2e.md)。这些命令不授权线上迁移或部署。

## 更新文档

公开操作说明只改 `apps/web/docs`，同一路径下中英文同步更新。导航在 `apps/web/src/data/docsNavigation.ts`，页面由 `build-docs-pages.mjs` 静态生成。保留既有公开 URL；新增页面需同步导航和生成检查。

仓库 `docs/` 不再复制用户指南全文。当前开发说明放在相应目录，旧方案保留在历史目录；历史验证记录不能改写为当前验收结论。

## 提交与发布

保护未提交工作，按行为变化补有意义的回归。记录检查对应的最终源码，创建 PR 后等必要 CI 完成。main 部署预发布；生产必须另有授权并通过 tag 提升。完整要求见[发布流程](../ops/release-ci-cd.md)。
