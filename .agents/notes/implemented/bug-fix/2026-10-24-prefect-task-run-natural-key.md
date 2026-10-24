# Agent Note: Prefect 最小升级修复任务事件自然键冲突

Status: implemented

## Problem

当前固定 Prefect 3.6.28。审计发现 task-run recorder 的自然键冲突重试；该版本按 id 做冲突处理，而数据库同时约束 `(flow_run_id, task_key, dynamic_key)`。日志不足以确认历史重复事件的全部来源，也不能把另外一次旧事件清理死锁归为同一根因。

## Decision

- API、worker、server、部署配置、CI 版本断言同步固定到 **3.6.29**，FastAPI 0.123.7 与其他依赖声明不变。采用上游 #21726 的自然键分组、canonical task-run id 与关联状态回填，不在应用中另写 recorder 补丁。
- 扩展现有 `prefect_contract.py`：向真实 disposable PostgreSQL recorder 提交相同自然键、不同 UUID 的 sequential 与同批事件，检查唯一 TaskRun、状态外键、state_details 与当前状态指针；保留原 server/client/task/flow 合同和 CI 3.4.19 旧 schema 升级检查。
- 本地补测当前 3.6.28 schema/data 升级到 3.6.29 的保留性。官方 wheel 的 PG head 都是 `09a9e091e578`，SQLite head 都是 `4dfa692e02a7`，migration graph 未变化。这里只准备和验证代码，不升级托管服务。
- 后续任何经授权的 worker 发布仍须暂停目标池、排空任务，确认新 worker online 且旧部署 REMOVED 后恢复；不因依赖小版本升级跳过门禁。

## Alternatives considered

- **直接升级 3.7.0**：包含 #21813 对额外并发 TOCTOU 窗口的修复。但该窗口没有本批复现证据，并引入删除 block-document 自引用行的 migration，downgrade 不恢复删除行。3.6.29 已直接覆盖本次可证缺陷，因此选择风险更小的版本。
- **保留 3.6.28，只加重试**：无依赖变动，但不能修正自然键与状态归属，且现有日志已经显示重试，故不采用。

## Consequences

修复范围是合成用例可证实的 recorder 自然键处理，不承诺历史所有重试、生产重复 producer 或 vacuum 死锁已消失。没有生产迁移、重放或发布。后续版本另行评估迁移风险。

官方依据：[Prefect 3.6.29](https://github.com/PrefectHQ/prefect/releases/tag/3.6.29)、[上游修复 #21726](https://github.com/PrefectHQ/prefect/pull/21726)，查阅于 2026-10-24。

## Verification

真实 disposable PostgreSQL 上，3.6.28 用同一合同复现自然键唯一约束错误，3.6.29 通过 sequential 与同批事件验证。3.6.28 → 3.6.29 升级命令成功，head 保持 `09a9e091e578`，合成 flow、flow_run、task_run 标记逐字段不变。真实 process pool/worker 执行部署流程到 COMPLETED，流程内部断言结果为 42。版本测试 5 passed，现有 workflow 合同测试 12 passed，candidate pip check 与独立审查通过。CI 保留 3.4.19 bootstrap 和 Python 3.11/3.13 matrix；本地数据保留验证针对 3.6.28，不冒充已运行完整远程矩阵。
