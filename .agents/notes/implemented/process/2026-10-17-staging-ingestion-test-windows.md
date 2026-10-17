# Staging 素材栈按测试窗口运行

## Problem

Railway 近七日观测中，staging Prefect Server 和 Worker 合计平均占用约 0.30 GB 内存，而同期没有业务素材导入任务。现有 CI/E2E 在 runner 内启动本地 Prefect，不依赖这两个云服务。保持云端素材栈全天常驻带来固定空转用量。

相关决定：[Prefect 部署文档单一入口](../bug-fix/2026-10-15-prefect-deployment-document-source.md)。

## Decision

仅将 staging Prefect Server/Worker 改为明确的测试窗口运行；API、数据库、卷和生产环境不作调整。复用已登录 Railway CLI，按最新核验的 deployment ID 调用官方 Stop/Restart，不删除 deployment、不上传代码、不引入新的部署工作流、凭据或依赖。操作步骤归入[测试窗口手册](../../../../docs/ops/staging-ingestion-test-windows.md)，发布规范引用该入口。

2026-10-17 UTC 已完成真实 staging 停启实测：先暂停工作池，两轮确认 Prefect 与业务库无非终态任务；先停 Worker，确认全部 Worker 离线且旧心跳不再推进，再停 Server 并确认实际 health 连续不可达。恢复时先启动 Server，确认工作池 ID、已登记 deployment ID 集合不变，再启动 Worker，确认新 ID 的新心跳及旧 Worker 离线，最后恢复工作池原始未暂停状态。Server 和 Worker 均使用原 deployment，source SHA 和 image digest 未变。用户仅为本窗口授权一次同源 Railway Redeploy 恢复兜底，本次没有使用；后续窗口不默认继承该授权。

完成可逆验证后再次排空并关闭这两个服务，工作池保持 paused；结束时 API readiness 返回 200。生产的 6 个服务以及其余 4 个 staging 服务，其部署、来源和镜像快照均与操作前一致。私有 receipt 与平台读回保留在 Git 之外；Railway 的 SUCCESS/RUNNING 标记可能滞后，不以其单独证明停止或恢复。首次完整尝试遇到 SSH transport 失败后已恢复原状态；后续仅对只读 SSH transport 失败作最多三次重试，不重试启停、工作池写操作或任何生成请求。

关闭窗口不承诺上传请求自动唤醒；人工素材测试前由 agent 恢复并验证。当前 Worker 的 Git watch pattern 为 `/apps/server/**`，会触发该部署的 main 推送/合并前也必须先开启窗口，因为新 Worker 不会自动唤醒 Server。部署与测试完成后按最新来源和 deployment receipt 再关闭，不用旧 SHA 覆盖新的 main。不改现有 CI/E2E，不改模型、提示词、生成预算、嵌入开关或扣费语义。

## Alternatives considered

- **全天常驻。** 无需额外启停步骤，任何时刻都可测试素材；但近期没有业务素材导入，持续保留约 0.30 GB 空转内存不划算。接受显式测试窗口的操作成本。
- **直接缩容到零。** 平台区域缩容看似最小操作，但 CLI 5.43.2 在本次显式 target 参数上报解析错误，未提交缩容；不能把该命令当作已验证的可逆控制。
- **给 Worker 开 Serverless。** 可复用平台空闲判断，但 Prefect 的轮询和心跳会阻止休眠，且没有队列自动唤醒 Worker 的保证。
- **替换 Prefect 为新的按需队列。** 可进一步减少常驻服务，但必须重新承担持久队列、取消、checkpoint、私有文件和扣费恢复；当前收益不支持扩大为调度架构重写。
- **关闭数据库或整个 staging。** 节省更多固定用量，但需要额外验证卷、数据库恢复与在线测试可用性，本批不包含。

## Consequences

预期减少测试窗口外约 0.30 GB 常驻内存，不承诺同等比例的实际账单降幅，数据库与 volume 仍然计费。停止后 CPU 指标归零，但内存序列重复末值，不能据此断言当前 RSS 或用量为零；实际费用仍需对应关闭时段的资源用量与账单验证。

素材功能在关闭窗口不可用于人工测试，后续触发 Worker 的 main 部署也多了一步恢复门禁。收益是无需改模型输出或重写队列即可关闭闲置计算；代价是人工窗口管理，不是全天可用或自动按需调度。
