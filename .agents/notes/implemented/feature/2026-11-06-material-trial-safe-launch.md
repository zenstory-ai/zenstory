# Agent Note: 素材库免费试拆收紧为一次不退还、3 章 1.5 万字、终身成本上限

Status: implemented

本 note 修改 `feature/2026-10-09-material-free-trial.md` 与 `feature/2026-10-09-material-trial-first-chapters-upload.md` 中「平台失败退还试用」「前 20 章、30 万字」「退还记录从列表隐藏」的规定。付费权益和付费退款不变。

## Problem

旧试拆可以用 20 章装满 30 万字，单账号的模型调用次数和输出都没有独立上限，平台失败又会退还机会，用户可以反复获得新的试拆。多账号同一网络也没有试拆专属限制。入口示例只是字段占位（名字、类型、章节号），看不出拆解对写作的用处。

## Decision

- **一次且不退还**：`usage_quota.material_trial_used_at` 的条件更新与 novel、job 在同一事务提交。提交后 dispatch 失败、平台失败、超时、模型失败、取消、删书都不清空标记，失败结果和状态保留。事务回滚不算使用。
- **内容上限**：前 3 个完整章节且合计不超过 15000 个 Unicode 字符；统一分章，超限在存储和占用之前拒绝，不切半章。`source_meta` 与 job billing 记录生效限制；worker 在调用模型前按已建章节再验证一次，缺失或超范围直接失败。配置只能在 1..3 章、1..15000 字内收紧，无效配置使试拆不可用。
- **成本上限**：`usage_quota.material_trial_charged_units` 是账号终身累计，上限 ¥1。复用 `reserve_model_call` / `settle_model_call`，在每次真实 provider 请求（failover、Prefect 重试各算一次，SDK 重试关闭）前原子预留，已知 usage 结算，未知保留，永不清零。按 job 的 `quota_mode` 判定，中途升级不能绕过；已有的免费每日 ¥15 兜底继续生效。
- **输出上限**：实际 payload 的 `max_tokens` 上限 4096，合并设定 8192，含 thinking 头寸；试拆关闭 thinking，主要角色整合最多 5 人。
- **网络规则**：同一可信网络每个北京日最多 1 个账号开始试拆，主键 `(day, ip_key)` 原子占位，与 AI 消息额度分开，不释放。缺 IP 只跳过这一条。上传路径加入 `apiFallbackExcludedPaths` 并拒绝百分号编码的路径，否则生产 `/api` 回退的出口地址会被当成另一个网络。
- **调用归属**：一本书可能同时存在超时的试拆任务 A（旧 worker 还活着）和升级后的付费重试 B，所以模型调用不能看「最新任务」。流程把自己的 `job_id` 绑到 `material_job_scope`（ContextVar），由 Prefect 复制到子 flow、task 和 task 线程。调用按这个确切任务判定：A 已结束则拒绝，B 按付费规则。书上有试拆任务却没有绑定任务时也拒绝。
- **终态不回写**：`update_processed`、`update_stage_progress`、`update_status` 在行锁下重新读取任务，已结束就不再改动；阶段0 同样不把已结束的任务改回 processing。这一条关闭的漏洞是迟到的 worker 把 watchdog 判失败的任务复活，绕开调用前的终态检查。`fail_job` 在流程失败路径的那次重试也持行锁，否则并发进度写入会让失败连续落空，任务一直停在 processing；watchdog 路径仍在有新进度时放弃。
- **入口示例**：用真实小说《九天斩神诀》（小知了）前 3 章串起现有的角色、章节梗概、设定字段，文案对照原文人工核对，标注「拆解示例」，不写前 3 章以外的情节，也不宣传当前没开的阶段。

## Alternatives considered

- **按书的最新任务判定试拆。** 最强理由：不用传递任何身份，也不依赖 ContextVar。被否：付费重试成为最新任务后，迟到的试拆 worker 会被当成付费调用，绕开成本和输出上限。
- **把 job_id 显式传给每个任务和模型调用。** 最强理由：不依赖执行框架的上下文复制。被否：要改十多个任务签名和调用点，漏传同样要靠兜底；现在用真实 Prefect 测试验证传递，缺失时拒绝。

- **零模型工作可退还 + claim 身份 + 同试拆内恢复。** 最强理由：平台故障时对用户更公平。被否：所有者明确决定不做退款；需要 claim 账本和状态机改造，这些复杂度只为退款服务。
- **额外的 20 次 provider 计数和全站每日 50 名额。** 最强理由：对单账号和全站都多一层硬上限。被否：¥1 终身成本在每次请求前预留已经约束单账号支出，未知调用按上界保留，次数不会超过预算；全站闸门没有对应的实际攻击依据。
- **独立账本表。** 最强理由：与额度表解耦。被否：一个账号一行的 `usage_quota` 已存试拆标记，加一列即可。

## Consequences

- 收益：单账号模型成本有持久上限，试拆不能靠失败反复获得；入口说明与实际一致。
- 代价：平台故障也会消耗机会，未知结果的调用按上界占用预算（故障时预算可能提前耗尽）；网络占位不释放，同网络的第二个账号当天只能等明天。网络限制只增加摩擦，不能证明是同一个人。

## Verification

- 回归：`tests/test_api/test_materials_trial.py`、`tests/test_services/test_material_trial_budget.py`、`tests/test_flows/unit/test_material_trial_worker_limits.py`、`tests/test_flows/integration/test_material_trial_job_scope_prefect.py`、`tests/test_services/test_ingestion_jobs_lifecycle.py`。
- 真实 PostgreSQL：`tests/test_services/test_material_trial_postgres.py`（设置 `ZENSTORY_TEST_POSTGRES_URL`）覆盖同账号并发占用、同网络并发占位、成本并发预留与重复结算、并发进度下流程失败必定落地、迟到写入等待进行中的失败事务后不改动。去掉行锁或重试锁后，后两项测试会失败。
- 本地合成运行（真实 Prefect 引擎、OpenAI SDK、一次性 PostgreSQL、127.0.0.1 假模型）：正常 3 章 13 次请求，所有预留合计上界约 ¥0.80，低于 ¥1；输出上限 4096/8192 出现在实际 payload；角色整合 5 人。假模型全部 500 时，失败调用按上界保留，预算约 ¥0.97 后停止继续调用。
