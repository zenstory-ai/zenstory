# 管理后台按模型的调用指标：成功率、耗时、首 token、解码速度

Status: implemented

关联：[按调用落库的 LLM 用量账本](2026-10-06-llm-usage-ledger.md)、[模型供应商主备切换](../architecture/2026-10-20-llm-primary-backup-failover.md)、[慢请求阶段耗时](../bug-fix/2026-10-24-agent-stage-timing.md)。

## Problem

运营要求在管理后台按模型看成功率、TPS 等常见 LLM 指标。现状：

- `llm_usage_event` 只在调用成功且有 token 时写一行，没有结果、耗时和首 token 时间；失败、超时、取消的调用不留痕，成功率无从算起。
- 阶段耗时（`run_stage_timings`）只在结算日志里，按请求汇总、不分模型，`model` 阶段包含 SDK/主备切换/流消费等待，不是逐次调用的供应商耗时。
- 主备切换下一次逻辑调用可能有两次供应商尝试（主用失败、备用接上），按逻辑调用统计会把主用的失败算到备用模型头上。
- 2026-10-24 审计的「短篇继续不收敛」「5–7 分钟无可见成果」缺的正是逐次调用的耗时、首 token 与输出量。

## Decision

**数据：在账本上加 3 个可空列，一行 = 一次供应商尝试。** 迁移 `alembic/versions/20261024_120000_add_llm_usage_call_metrics.py`（`down_revision = 20261021_100000`），只 `add_column`，已存在的列跳过（staging 手工 `ADD COLUMN IF NOT EXISTS` 后再升级是空操作）。

| 列 | 含义 |
|---|---|
| `outcome` VARCHAR(16) NULL | `success` / `error` / `timeout` / `cancelled`；NULL = 未记录（本列之前的行、回填行、素材拆解） |
| `duration_ms` INT NULL | 发起调用 → 流结束（`response.completed`）/ 非流式响应返回 / 抛错；免费账号含成本守卫（`model_call_guard`）预留与结算的数据库事务；付费账号含 OpenAI 客户端自身的 HTTP 重试（`max_retries`，免费账号的调用守卫设为 0） |
| `ttft_ms` INT NULL | 只流式：发起调用 → 第一个输出 delta（`output_text` / `reasoning_text` / `reasoning_summary_text` / `refusal` / `function_call_arguments`）。SDK 的 `response.created` 在首个任意 chunk（可能只有 role）就发，不算 |

**记录点**（都不 await 数据库，沿用 `schedule_llm_usage_record`）：

- 写作 agent：`FailoverChatModel` 每次尝试计时；成功时在看到 `response.completed`（SDK 消费它之前）记下 `CallTiming`，由 `usage_hooks.on_llm_end` 用 `take_call_timing()` 取一次、随该次 token 写入；失败或取消（`CancelledError` / `GeneratorExit`，且尚未完成）的尝试由模型自己写 0 token 行。归属 `usage_hooks.agent_usage_attribution()`，runner 建模型时传入。
- 路由、输入建议、润色：`llm_failover.acreate_chat_completion(usage_attribution=...)` 每次尝试写一行（成功带 usage，失败/取消 0 token），不测首 token。路由和 `LLMClient` 不再各自写账本（原 `_meter_router_call` 删除），避免双计。
- 素材拆解（Prefect worker，同步）本批不接入：outcome 为 NULL，页面算「未记录」；避免为指标发布 worker。
- `failure_outcome`：`APIException`（我们自己的成本兜底/预算不可确认，请求没发给供应商）→ 不是尝试，不写行；`CancelledError`/`GeneratorExit` → cancelled（作者停止、断线、本轮墙钟、发布交接、建议接口 `wait_for` 时限，都是调用方放弃，不是模型失败）；`TimeoutError`、`openai.APITimeoutError`、`httpx.TimeoutException`（流中途读超时，OpenAI 客户端只在发送阶段包装超时）→ timeout；其余 → error。`acreate_chat_completion` 的流式请求不在这里计量（usage 与结束都在返回之后）。

**既有口径不变**：模型 `has_usage_tokens()`（三项 token 之和 > 0）加在用量与成本（`admin_usage_service._in_period`）、增长（4 处）、获客、成本兜底初始化的账本查询上。旧代码从不写 0 token 行，所以这些报表与之前完全一致；失败/取消行只进模型指标。

**后台**：`GET /api/admin/usage/models?window=today|yesterday|7d&source=`（superuser；`source` 为既有功能维度，可空），逻辑 `admin_usage_service.get_model_metrics`。按 `model` 分组，另给合并的 `totals`：

- 计数：attempts、success、error、timeout、cancelled、not_recorded（SQL `SUM(CASE…)`）。
- 成功率 = success ÷ (success + error + timeout)；分母为 0 时 null。cancelled 与 not_recorded 不进分母。
- 总耗时 p50/p95：成功尝试的 `duration_ms`；首 token p50/p95：成功且有 `ttft_ms` 的尝试。窗口内逐行取两个整数到 Python，最近秩百分位（SQLite 测试与 PostgreSQL 生产同一实现）；`totals` 合并样本计算，不平均各模型百分位。
- 解码速度 = Σ输出 token ÷ Σ(`duration_ms` − `ttft_ms`)，只取成功、有首 token、输出 token > 0、生成时长 > 0 的尝试；比值合计，不平均单次速度。输出 token 来自供应商 `completion_tokens`，含推理与工具参数。
- Token 与费用：窗口内全部行（含未记录的历史行），费用复用按行价目版本的 SQL 计价。
- 每个分布与速度都带样本数；没有样本是 null，界面显示 N/A。

**页面**：「用量与成本」页在按功能与按用户之间加「按模型」区块，沿用页面的时间范围，带功能筛选（`AdminSelect`），九列表格（桌面 1440 不横向滚动，窄屏与其他表一样横向滚动），模型多于一个时加「全部模型」行；「指标怎么算」折叠说明列出六条口径。刷新按钮同时作废模型查询。

## Alternatives considered

- **另建 `llm_call_attempt` 表，账本不动。** 最强理由：既有报表一行都不用改，账本「只存有 token 的行」的约定不破。被否：成功调用要写两张表（多一次事务），两表覆盖不一致时 token/费用与成功率对不上；在账本上加可空列、给既有读取加一个共享条件，改动更小，且用户明确要求只加最少可空字段。
- **用 SSE 首个事件或 `response.created` 当首 token。** 最强理由：接线最简单。被否：`response.created` 在首个 chunk（可能只有 role、没有内容）就发；会把排队/建连时间算成「首 token 很快」，再把整段等待算进解码阶段，速度偏低且口径失真。
- **用总请求时间算 TPS。** 最强理由：所有调用都有。被否：包含排队与首 token 前的推理/预填，叫 TPS 会冒充解码速度；非流式调用没有生成阶段，只显示 N/A。
- **按逻辑调用统计，主备切换只算最后的结果。** 最强理由：更接近作者体验。被否：主用的失败会被算成备用模型的成功，按模型的成功率失真；作者体验应看结算日志与回合结果，不是这张表。
- **把取消算失败。** 最强理由：作者确实没拿到结果。被否：取消多由作者停止、断线或我们自己的时限造成，算失败会把产品时限问题算到模型头上；单独列出并说明。
- **在 SQL 里算百分位（`percentile_cont`）。** 最强理由：不用把行取到 Python。被否：SQLite 测试不支持，生产 SQL 将没有测试覆盖；窗口最多 7 天、每行两个整数，取回成本可接受。

## Consequences

- 收益：按模型看成功/失败/超时/取消、耗时与首 token 分位、解码速度与 token/费用；审计缺的逐次调用耗时与输出量今后在账本里。
- 代价：失败与取消的尝试每次多写一行（后台任务）；7 天窗口的模型指标要把成功行的耗时取回内存，行数随调用量线性增长（按当前量级是数万行内）。
- 局限：OpenAI 客户端内部重试不可见，算在同一次尝试里；免费账号的耗时含成本预留/结算的数据库事务（同一用户并发时会排队）；SDK 自身异常导致的 `GeneratorExit` 也记为 cancelled；回填行一轮一行，也计入调用尝试；流式中途失败的尝试拿不到 usage（与账本「下限」一致）；供应商若不流式返回推理内容，推理时间落入首 token、解码速度偏高；SDK 在 `response.completed` 后被取消、`on_llm_end` 没跑时，那次成功不会入账（极少见）；素材拆解与 2026-10-24 之前的行显示为未记录。
- 上线前置：**新列必须先于新代码存在**。缺列时每次账本写入都会 `UndefinedColumn`（失败只记日志，用量与费用丢失），`cost_budget` 每个用户每天第一次初始化时以实体读账本也会失败并返回 503 `ERR_AI_COST_BUDGET_UNAVAILABLE`，免费用户当天的 AI 消息被拒。合并到 main 前 staging 先手工 `ALTER TABLE llm_usage_event ADD COLUMN IF NOT EXISTS outcome VARCHAR(16), ADD COLUMN IF NOT EXISTS duration_ms INTEGER, ADD COLUMN IF NOT EXISTS ttft_ms INTEGER;`；生产按 `docs/ops/release-ci-cd.md` 在打 `prod-v*` 前备份并 `alembic upgrade head`。

## Verification

- `tests/test_api/test_admin_model_metrics.py`：成功（有/无首 token、无 usage）、失败、超时、取消、历史 NULL、主备切换两次尝试、昨天的行；成功率分子分母、最近秩百分位、解码速度合计比、N/A、合并行不平均；功能筛选、空窗口、非法 source 422、未登录 401、普通用户 403；0 token 行不改变用量摘要、按用户列表与增长指标。
- `tests/test_agent/test_llm_failover.py`：首 token 取第一个输出 delta 而非 `response.created`、成功计时在 `response.completed` 时刻、`response.completed` 之后被关闭不再写取消行、主备切换失败尝试记在主用模型、流中途出错/读超时/取消写 0 token 行、成本兜底拒绝不写行、非流式逐次尝试与无归属不写。
- 独立审查（另起 Claude Code 只读会话）初审 REQUEST_CHANGES、无 blocker：成本兜底拒绝被记为 error、流中途读超时被记为 error、耗时起点与说明不符、缺「完成后关闭」测试，以及若干 nit（措辞、三次读取间新模型的 KeyError、流式 + 归属的陷阱、刷新按钮状态、source 枚举重复），均已在本批修正并补测试。`test_llm_usage_service.py`：带计时的 0 token 行写入、无计时照旧跳过、失败分类。`test_stage_timing.py`：hook 写入一次计时。
- 迁移：SQLite 往返（含手工先加一列再升级）；本地临时 PostgreSQL 17 容器 create_all → stamp → downgrade → 插旧行 → upgrade → `alembic check` 无差异 → 再往返；同一库上直接调用 `get_model_metrics` 结果与 SQLite 测试一致。
- 前端：`UsageCostPage.test.tsx`、`adminUsageApi.test.ts`；`admin-routes-smoke-mocked.spec.ts` 在本地 Chromium 走按模型区块、功能筛选到空数据、展开指标说明。只用合成数据与 mock，未调用付费模型，未接触 staging/生产数据库。
