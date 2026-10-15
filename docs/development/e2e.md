# E2E 与 CI

本地测试用于定位问题，GitHub 的精确提交检查用于合并和发布。一次旧的成功运行不能证明新的源码通过。

## 本地入口

从根目录选择当前改动需要的范围：

```sh
pnpm e2e:zenstory:local          # smoke
pnpm e2e:zenstory:local:default
pnpm e2e:zenstory:local:nightly
pnpm e2e:zenstory:local:release
pnpm e2e:zenstory:local:full
```

这些本地入口使用 `scripts/ci/ci.sh e2e --lite`，与 GitHub 工作流同名 lane 的文件选择不完全相同。具体本地文件列表以该脚本为准：smoke 覆盖基础路径，default 覆盖账号与主要业务，nightly 覆盖历史和技能，本地 release 包含视觉、性能、大文档、并发和语音，full 打开本地脚本列出的可选场景。名称是测试范围，不代表已满足生产发布门禁。

`pnpm gate` 使用 `scripts/ci/local-gha.sh` 跑本地门禁；`pnpm gate:e2e` 增加浏览器测试。先核对运行环境，使用本地测试数据和 mock，不连接生产服务或付费模型。

## GitHub 门禁

- `CI / ci-summary` 汇总变更检测与相关检查，相关任务不能靠 skipped 通过。
- `E2E Tests / e2e-summary` 汇总浏览器任务。
- `.github/workflows/e2e.yml` 在 main/develop push、定时与手动调用时触发，没有 PR 触发器。push 和手动 default 使用八个 Chromium 分片，手动 full 额外加入 Firefox；nightly、release、smoke 使用各自固定范围。
- 生产提升要求同一 main 提交的两项总结成功，不用 PR 上另一 SHA 的结果替代。

工作流和发布条件见[发布流程](../ops/release-ci-cd.md)。

## 按失败证据定位

| 类型 | 先检查 |
| --- | --- |
| Runner 未启动 | Actions job 与基础设施错误，不当成产品回归 |
| 后端契约 | 对应 API 回归与响应形状、权限、配额语义 |
| 浏览器交互 | 页面是否就绪、焦点、实际状态与选择器范围 |
| 视觉或性能 | 同一环境、基线、数据规模与实际截图 |

点击会触发请求时，先注册响应等待，再执行点击；优先断言真实可见状态。不要因等待失败就弱化行为断言，也不要为没有变化的通过项重复跑全部检查。

## 证据边界

Mock 浏览器证明交互，真实测试服务证明集成，预发布验证证明目标环境。分别记录，不把其中一个说成另一个。生产来源还需要 provider 元数据；健康响应不能代替部署 SHA。

原有四月 lane 设计与当时 runner 记录保留在[历史设计](../archive/design/e2e-lane-governance-2026-04.md)，不用于判断今天的服务或账单状态。
