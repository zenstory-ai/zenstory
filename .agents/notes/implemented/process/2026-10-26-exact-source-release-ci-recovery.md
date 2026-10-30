# 精确源发布校验恢复入口

## Problem

#325 合并后的 main SHA 没有 Actions run；仓库已启用 Actions，工作流包含 main push，现有证据不足以归因平台故障。原 CI 没有手动入口，promotion 仅接收 push，不能在保持完整校验的同时恢复。CI-only 修复还可能被提供商 watch paths 跳过，遗漏实际尚未上线的产品变更。

本次 agent 修复不迁移存储或卸卷；缺少历史迁移记录不是普通发布门禁。保持卷、原件、挂载与 drain 参数不变。既有 CI 分片决策见 [2026-10-08](2026-10-08-ci-e2e-sharding-and-backend-split.md)。

## Decision

优先使用真实 main push。只有某个 exact SHA/workflow 完全不存在 push run 时，才接收现有工作流的显式 workflow_dispatch 恢复证据；失败、取消、排队、进行中或多义 push 均不被手动成功掩盖。

CI 手动输入 expected_source_sha，校验事件 SHA、main ref 并强制 backend/frontend/CLI/CI 全范围。E2E 恢复要求相同源校验、default/full 与完整八个 Chromium 分片；Firefox 出现时也必须八个成功。普通手动子集仍可用于诊断，但不能作为发布证据。

共享证明校验器验证 GitHub Actions check suite、workflow、SHA、run attempt、成功 summary 和唯一成功的源校验 step。promotion 查询不过滤事件或终态，保留真实 workflow_dispatch 标签。prod-v* tag、祖先校验、固定 release-production、重读 baseline 和非强制快进保持不变。

提供商比较实际 deployed SHA 到目标 SHA 的累计相关文件，而非仅最后一个 CI-only commit。确有漏发时仅通过既有绑定 Git 集成的 Git 源入口补发，回读新源；不上传本地代码、不新建 producer、不更改源分支或云配置。

## Alternatives considered

- 仅等待下一次产品改动：无需调整校验控制，但没有恢复期限，也可能再次丢失触发，不能完成已授权发布。
- 无意义改动或重推/绕过分支：可能产生新事件，但污染历史或破坏保护，拒绝。
- 独立恢复/部署 workflow：可定制路径，但重复完整检查与部署生产者，增加漂移；复用原工作流与共享验证器更小。
- 将所有检查成功手动 run 都视为 push：实现简单，但会误认 smoke、子集或错误源证据，拒绝；保留真实事件及严格来源证明。
- 新增 artifact 协议与硬编码全部 CI jobs：可额外携带输入，但重复现有 fail-closed summary。受认证 Jobs API 的唯一源校验 step 加既有完整 job graph 足以验证当前恢复入口。

## Consequences

恢复入口可测试且不关闭校验，不依赖推测的事件故障原因。增加了一个需维护的手动来源合同；固定 step 名称成为受回归测试保护的机器合同。人工仍须完成 staging/provider 源、Prefect drain/旧 deployment REMOVED 和生产回归，CI 成功不等于上线。
