# Staging 素材栈测试窗口

本手册只用于人工开启或关闭 staging 的 Prefect Server 和 Worker。它不修改
API、数据库、卷、模型、生产环境或 CI/E2E，也不创建新的部署 producer。
CI/E2E 继续使用 runner 内的本地 Prefect。

> **验证状态（2026-10-17 UTC）：**已完成 staging Server/Worker 的原位停启
> 实测，恢复后的 source SHA、image digest、工作池和已登记 deployment ID 均保持
> 一致，未使用 Redeploy 兜底；本次结束时两项服务均已关闭，工作池 paused。
> 之后以最新平台读回为准。这不是自动化，也不是账单降幅证明。关闭窗口内
> 素材导入不会自动唤醒服务；测试前必须由 agent 按本页恢复并验证。

部署与任务保护仍遵循[发布流程](release-ci-cd.md)和
[Prefect Railway 部署说明](../../apps/server/docs/PREFECT_RAILWAY_DEPLOY.md)。

## 固定范围

每次操作先核对以下 ID；任一不符立即停止。deployment ID 不在表中固定，它必须
来自本次操作开始时对对应 staging 服务的最新读回。

| 资源 | ID / 名称 |
| --- | --- |
| Railway project | `cbe4812b-c4fd-4d4d-ae13-b06395b2c7f0` |
| Railway environment | `3fe557b3-467e-4256-98f7-961cfbe9394a` |
| staging API service | `9bd3a8b6-81df-4202-a3e2-104fcfd3486a` |
| staging Prefect Worker service | `f8c74749-aa35-4dc7-be9a-1b5a0fda4ea7` |
| staging Prefect Server service | `d72bd93c-db4f-4002-975e-761f05b53c6b` |
| Prefect work pool | `zenstory-staging-pool` |

不要对 production、`zenstory-pool` 或其他 project/environment 执行本页操作。
不要使用 `railway up`、`railway down`、deployment Remove 或本地源码上传。
本次 `scale 0` 尝试遇到 CLI 参数解析错误且没有提交变更，因此不要把它当作
停机或恢复入口。

## 私有 receipt

把状态写入 Git 之外的私有 receipt；建议归档到
`~/.local/share/zenstory/ops/staging-ingestion/` 的独立日期目录，不只留在临时目录。
凭据继续使用既有登录态，不写进 receipt。至少记录：

- 操作人、开始时间、窗口目的，以及上表全部 ID；
- 工作池 ID、原始 pause 状态，以及已登记的 Prefect deployment ID 集合；
- Worker 和 Server 刚读回的 deployment ID、source SHA、image digest；
- 停机前 Worker ID、状态和最后心跳时间；
- 两轮排空查询的时间、Prefect 非终态数量和 `ingestion_jobs` 非终态数量；
- 每次 Stop、Restart 或兜底 Redeploy 的操作与响应；
- 恢复后的实际 Prefect health、工作池状态、新旧 Worker ID/心跳和资源 metrics；
- 若主线或 deployment 在窗口中变化，新的 CI/source 读回和最终采用的 deployment。

不要把 token、私有 URL、数据库结果明细或 receipt 提交到仓库。运行命令前关闭
shell trace，输出也要脱敏。

## 关闭窗口

1. **排除并发部署。** 确认 Worker 和 Server 没有正在进行的 Git/provider 部署。
   分别从固定 service 和 environment 读回当前 deployment ID、source SHA 与 image
   digest，写入 receipt。若 `main` 自动部署已改变来源，旧 receipt 立即失效；先
   核对最新 main CI 和 provider source，再继续，不能用旧 Git SHA 覆盖新版本。
2. **记录并暂停工作池。** 记录 `zenstory-staging-pool` 原本是否 paused，然后将其
   设为 paused。后续所有失败路径都保持 paused，直到恢复门禁全部通过。
3. **连续确认排空。** 做两次独立读回，两次之间至少覆盖当前 Worker 的一个
   heartbeat/poll 周期。每次都必须同时满足：
   - Prefect 中没有任何非终态 flow run；按当前 Prefect 状态类型的终态语义判断，
     `SCHEDULED`、`PAUSED`、pending、running 和 cancelling 都会阻止停机；
   - 业务库 `ingestion_jobs` 没有非终态行。当前终态只有 `completed`、
     `completed_with_errors` 和 `failed`，其他状态都阻止停机。
4. **先停 Worker。** 对刚核验的 Worker deployment ID 执行 `deploymentStop`，不要
   down/remove。等待停机前所有 Worker ID 的心跳时间在连续读回中不再推进且状态
   为 `OFFLINE`，并确认没有其他 `ONLINE` Worker，再查看平台资源 metrics。Railway 的
   `SUCCESS`/`RUNNING` 和产品公开 status 可能滞后，不能单独作为停机凭证；Prefect
   的 `OFFLINE` 也不等于容器 RSS 已经是 0。
5. **再停 Server。** 仅在第 4 步通过后，对刚核验的 Server deployment ID 执行
   `deploymentStop`。用实际 Prefect health 端点不可达和资源 metrics 佐证停止，
   不以 Railway 展示状态单独下结论。工作池继续保持 paused。

任一步骤出现新任务、心跳继续推进、来源变化或读回矛盾，都停止关闭流程。若
Worker 已停但 Server 尚可用，直接进入恢复流程；不要继续停 Server。

## 开启测试窗口

1. 再次确认没有并发 Git/provider 部署，并核对 receipt 中 deployment 的 source
   SHA 与 image digest 仍是当前应该运行的 staging 来源。若 `main` 已自动部署，
   先重新建立最新 CI/source receipt 和 deployment 基线，绝不盲目恢复旧 SHA。
2. 对 receipt 中已核验的 Server deployment ID 执行 `deploymentRestart`。必须用
   实际 Prefect `/health` 响应及一次 Prefect API 读回证明 Server 可用，并确认
   Server 的 source SHA/image digest 仍匹配其 receipt，且
   `zenstory-staging-pool` 的 ID、已登记 deployment ID 集合均未变化且工作池仍然
   paused；Railway `SUCCESS` 不能替代这些证据。
3. Server 门禁通过后，对已核验的 Worker deployment ID 执行
   `deploymentRestart`。确认新 Worker ID 持续产生新 heartbeat、属于
   `zenstory-staging-pool`，Worker 的 source SHA/image digest 仍匹配其 receipt；
   停机前旧 Worker ID 必须保持 `OFFLINE` 且旧心跳不再推进。
4. 若恢复产生了 replacement deployment，必须确认旧 deployment 为 `REMOVED`，
   再把新 deployment ID 写入 receipt。只看到新 Worker `ONLINE` 不足以通过。
5. 读回实际 health、Worker heartbeat 和平台资源 metrics 后，恢复工作池的**原始**
   pause 状态：原来未暂停才 resume，原来已暂停则保持 paused。此时才可开始人工
   素材测试。

测试结束后重新执行“关闭窗口”。本手册不提供上传请求自动唤醒，也不承诺 staging
素材功能全天可用。

### Main 自动部署前

当前 Worker 的 Git watch pattern 是 `/apps/server/**`。推送或合并会触发其部署的
变更前，先开启本窗口；Worker 启动脚本等待 Server，关闭 Server 不会被新部署
自动唤醒，可能导致启动超时。完成最新 main 的部署/source 核验、旧 deployment
移除及所需测试后，再按新 deployment 基线关闭窗口。不要用旧 receipt 恢复旧代码。

## Railway deployment 操作

[Railway deployment API](https://docs.railway.com/integrations/api/manage-deployments)
使用 deployment ID。下面的 helper 通过 `railway api` 提交 GraphQL variables；ID
只从环境变量读取，不拼进 query。先完成固定范围和来源核验，再把刚读回的
**staging** deployment ID 放入当前私有 shell。不要把 ID、token 或 helper 输出
写入仓库。

```sh
set +x
export RAILWAY_DEPLOYMENT_ID='deployment-id-from-fresh-staging-readback'

railway_deployment_action() {
  RAILWAY_DEPLOYMENT_ACTION="$1" python3 - <<'PY'
import json
import os
import subprocess

queries = {
    "stop": "mutation ($id: String!) { deploymentStop(id: $id) }",
    "restart": "mutation ($id: String!) { deploymentRestart(id: $id) }",
    "redeploy": (
        "mutation ($id: String!) { "
        "deploymentRedeploy(id: $id) { id status } }"
    ),
}
action = os.environ["RAILWAY_DEPLOYMENT_ACTION"]
deployment_id = os.environ["RAILWAY_DEPLOYMENT_ID"]
if action not in queries or not deployment_id:
    raise SystemExit("invalid action or empty RAILWAY_DEPLOYMENT_ID")
result = subprocess.run(
    ["railway", "api", queries[action], "--raw-var", f"id={deployment_id}"],
    capture_output=True, text=True, check=True,
)
response = json.loads(result.stdout)
field = {"stop": "deploymentStop", "restart": "deploymentRestart",
         "redeploy": "deploymentRedeploy"}[action]
ack = (response.get("data") or {}).get(field)
if response.get("errors") or (action != "redeploy" and ack is not True):
    raise SystemExit("Railway did not acknowledge the action")
if action == "redeploy" and (not isinstance(ack, dict) or not ack.get("id")):
    raise SystemExit("Railway did not return a recovery deployment ID")
print(json.dumps({"action": action, "deployment": deployment_id, "result": ack}))
PY
}
```

按流程单次调用对应动作：

```sh
railway_deployment_action stop
railway_deployment_action restart
```

每次只对一个已经核验的 deployment 操作；切换 Server/Worker 前重新设置并复核
`RAILWAY_DEPLOYMENT_ID`。GraphQL 返回成功只表示请求被接受，仍需完成前述
health、heartbeat 和 metrics 门禁。

### 一次性恢复兜底

仅在当前窗口已经明确获得 staging 同源恢复授权，且原 deployment 的
`deploymentRestart` 失败时，才对同一已核验来源执行一次兜底；本手册本身不授予部署权限：

```sh
railway_deployment_action redeploy
```

Redeploy 会产生 replacement deployment。重新核对返回的新 deployment ID、source
SHA、image digest 和实际 health；Worker 还要核对新 heartbeat，并确认旧 deployment
为 `REMOVED`。如果原 deployment 已因新的 `main` 部署而过时，不得 redeploy 它；
先核对最新 main CI/source 并重建基线。兜底失败时不要循环重试，保持工作池 paused，
停止测试窗口并记录 blocker。

## 完成条件

- **关闭完成：** 两轮任务排空成立，旧 Worker 心跳停止推进且 `OFFLINE`，Server
  实际 health 连续不可达，Stop 请求已确认，工作池 paused；另记资源观测及其
  未验证项，不以旧内存读数推断当前用量。
- **开启完成：** Server 实际 health 和 Prefect API 可用，新 Worker heartbeat 稳定，
  旧 Worker/deployment 门禁通过，source SHA/image digest 一致，工作池恢复原状态。
- **不能下结论：** 只有公开 status、Railway `SUCCESS`/`RUNNING`、单次 health、
  `OFFLINE` 或单个 metrics 读数。保留 receipt 并报告未验证项，不写“已节省”。

## 成本验证边界

近七日基线中，这两个 staging 服务合计平均约 0.30 GB 内存；按
[Railway RAM 定价](https://docs.railway.com/pricing/plans)估算，若整月均处于关闭
窗口，理论 RAM 用量金额约 $3/月，按关闭时长折算；账单收益另行核对。数据库与
volume 仍然计费。

本次停止后 CPU 读数降为零，但内存序列重复最后一个值。
[Railway 员工曾解释另一个案例存在相同的末值重复现象](https://station.railway.com/questions/railway-platform-technical-failure-causi-6356c6f0)；
这不能证明本次内存仍在持续消耗，也不能证明本次已节省费用或 RSS 为零。
实际成本结论要核对对应关闭时间段的资源用量与账单，不能把缺失或重复指标写成零。
