# Agent Note: Prefect 部署文档单一入口

Status: implemented

## Problem

服务端保留两份同主题 Railway/Prefect 指南，一份推荐 Prefect Cloud 并让所有部署共用默认池，另一份直接运行 `prefect deploy --all`，两份都没有 worker 排空顺序。当前 Dockerfile、启动脚本与仓库发布规范已经采用 Prefect 3.6.28、自托管 Server、隔离的 staging/production 工作池，并逐个登记 deployment；旧指南可能让操作者把 staging 任务登记进生产池，或在旧 worker 尚未移除时恢复新任务。

## Decision

保留 `apps/server/docs/PREFECT_RAILWAY_DEPLOY.md` 作为素材处理基础设施说明，删除 `RAILWAY_PREFECT_DEPLOY.md`。镜像、环境隔离和具名 deployment 登记按现有代码说明；发布门禁与 pause/drain/替换/旧部署 REMOVED/恢复顺序引用 `docs/ops/release-ci-cd.md`。文档只解释已存在的流程，不执行部署、不创建服务或改云配置。

同主题检索未找到专门拥有这两份指南或 deployment 登记选择的现存决策笔记。`2026-10-13-docs-source-and-repository-navigation.md` 拥有文档单一来源原则，`2026-09-27-material-decompose-stage-defaults.md` 拥有素材阶段开关，两者都不替代本次部署文档纠错。

## Alternatives considered

- 保留两份指南并互链：原路径均可访问，但操作者仍需在冲突的 Cloud、自托管和默认池命令之间判断，继续维护两份步骤会再次漂移。
- 只删除指南、全部依赖通用发布规范：入口最少，但发布规范不负责说明三个服务、镜像和素材下载所需变量；保留一份基础设施说明能补齐这部分。

## Consequences

操作者只有一份当前部署说明，具名登记和隔离工作池与启动脚本一致，旧指南的可执行错误命令移除。旧仓库路径删除，可从 Git 历史查阅；发布流程仍需明确授权和源码绑定验证，本次文档修改不证明当前云服务已经完成切换。
