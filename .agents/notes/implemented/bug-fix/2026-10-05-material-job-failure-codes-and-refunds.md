# Agent Note: 拆解任务失败只存错误码，平台故障退额度、重试一律计费

Status: implemented

## Problem

重新启用 Prefect 拆解之前，任务失败路径上有一串计费与可靠性问题：

- 派送失败时上传接口会退额度，并把 job 标成 `stage=deployment_start`；`retry` 看到这个标记就直接跳过 `check_quota`/`consume_quota`。结果每次派送失败都等于送一次免费拆解。Prefect 停用期间累积的这类 job，重新启用后都能免费重试，月额度用完也挡不住。
- Prefect 已接单但 worker 没执行时，job 永远停在 `pending`（对账只处理没有 `correlation_id` 的 pending），重试回 409，额度不退。worker 崩溃时 job 要卡在 `processing` 两小时，重试还要再扣一次。
- flow 层 `retries=1` 再加上「先标 failed 再 raise」：30 秒的空窗期里用户点重试，会和自动重试并行跑两条 flow，重复扣费。
- DeepSeek 401/402/余额不足时，每章仍按 3–5 次重试空转，最后显示「部分完成」，摘要被换成原文前 200 字。所有 LLM 错误一律重试，包括上下文超长、JSON 解析失败、校验失败这类注定再次失败的错误。
- `error_message` 存的是原始异常字符串，会把内网 URL、容器路径、LLM 原文带给前端，而且没有 i18n。

## Decision

- **错误码**：`IngestionJob.error_message` 只存 `ERR_*` 错误码（定义在 `core/error_codes.py` 的 Material Decomposition 段，失败码集中在 `services/material/job_errors.py`）。原始异常只写日志；`error_details` 记 `stage`、`error_code`、`exception_type`。读取接口（list/detail/status）经 `public_job_error` 返回错误码，旧的自由文本一律映射为 `ERR_MATERIAL_DECOMPOSE_FAILED`。前端用 `errors` 命名空间翻译，详情页对 failed / completed_with_errors 显示原因和重试按钮。部分完成写 `ERR_MATERIAL_PARTIALLY_COMPLETED`。
- **计费状态**：放在既有 JSON 字段 `IngestionJob.stage_progress["billing"]` = `{quota_charged, quota_refunded, refund_reason}`，没有新增栏位或 migration。上传和重试在扣费成功后写 `quota_charged=true`；派送失败时退款，并写 `quota_charged=false, quota_refunded=true`。
- **补偿策略：平台故障退款，重试一律计费**。这是二选一里的「补偿重试正确检查并扣额度」：`_is_compensatory_retry` 已删除，每次重试都走 `check_quota` + 原子 `consume_quota`。平台侧失败改在失败发生时由 `IngestionJobsService.fail_job` 退还该 job 占用的额度，可退款的错误码见 `REFUNDABLE_JOB_ERROR_CODES`：派送失败、排队超时、处理超时、LLM 不可用、0 章、超章节上限、文件无法读取。只有 `quota_charged=true` 的 job 才会退款；退款后 `quota_charged=false`，同一个 job 不可能再退第二次，也不可能免费重试。章节内容拆解失败（`ERR_MATERIAL_EXTRACTION_FAILED`）和未知异常不退款，避免精心构造的输入换来免费的 LLM 调用。
- **对账**：`reconcile_stale_job` 在读取路径上执行。没有 `correlation_id` 的 pending 超过 10 分钟、有 `correlation_id` 的 pending 超过 30 分钟、processing 超过 2 小时未更新，都会标记为 failed（`ERR_MATERIAL_DISPATCH_TIMEOUT` / `ERR_MATERIAL_PROCESSING_TIMEOUT`）并退款。写入用 `status` + `updated_at` 做 compare-and-set，列表和详情同时轮询时只有一个请求能写成功，因此退款最多一次。flow 开始时如果 job 已是终态（例如在队列里等太久，已被对账判失败并退款），直接返回 `skipped`，不做任何工作。
- **flow 不再自动重试**：`novel_ingestion_v3` 及章节提取、角色实体、关系、故事聚合子流程都设为 `retries=0`。瞬时错误交给 task 级重试处理，失败的任务由用户通过重试端点续跑。
- **LLM 错误分级**：`DeepSeekClient` 显式设置 `timeout=MATERIAL_LLM_REQUEST_TIMEOUT_SECONDS`（180）和 `max_retries=MATERIAL_LLM_SDK_MAX_RETRIES`（2）。401/402/403/404 与未配置密钥视为账号级故障（`LLMNonRetryableError`，错误码 `ERR_MATERIAL_LLM_UNAVAILABLE`）；400/413/422 不可重试；无法解析的输出是 `LLMOutputError`（严格解析失败后先用 `json_repair` 修复一次，非截断输出修出非空对象即接受，见 `2026-10-08-material-llm-json-repair.md`）。`api_task` 的 `retry_condition_fn` 对这些错误以及数据校验错误直接失败、不再重试。章节提取遇到账号级故障立即终止整次拆解；某个能力本轮提交的章节全部失败时，判为 failed：失败全部来自 LLM 调用时用 `ERR_MATERIAL_LLM_UNAVAILABLE`，否则用 `ERR_MATERIAL_EXTRACTION_FAILED`，不再标成「部分完成」。失败章节不写任何降级摘要。
- Worker 的 Postgres engine 加上 `pool_pre_ping=True`、`pool_recycle=1800`（`flows/database_session.py`）。

## Alternatives considered

- **只允许一次免费补偿重试，用 `compensation_used` 标记**。最强理由：用户点重试时不会看到额度先扣后补，体验更直接。被否：需要在两个 job 之间维护「谁补偿了谁」，还要处理补偿重试本身又派送失败、并发重试这些边界；在失败当下退款只需要一个单调的 `quota_charged` 标记，读取时不需要额外判断，也不会出现「先退款、再免费重试」叠加的漏洞。
- **派送失败不退款，留给补偿重试沿用**。最强理由：少一次退款写入。被否：用户若不重试，额度就永远被占着；而且 Prefect 停用期间每次上传都会占住额度。
- **对所有失败都退款**。最强理由：对用户最宽松，规则也最简单。被否：可构造的输入（让每章都校验失败、诱导模型输出非 JSON）就能无限消耗 LLM 成本而不扣额度。
- **按时间判断 pending 之外，再通过 `correlation_id` 向 Prefect 查询 flow run 状态**。最强理由：可以区分合法排队（`concurrency_limit` 3）和 worker 不在线。被否：读取路径会多一次跨服务调用，Prefect 停用时还要额外降级处理；改由 flow 启动时检查 job 是否已终态，即使误判也不会重复工作或重复扣费。

## Consequences

- 收益：派送失败或 Prefect 停用期间累积的 job 不能再免费重试；平台故障一定退款，同一个 job 最多退一次；不会再出现两条 flow 并行的空窗；DeepSeek 账号故障会立即以可读的错误码失败，不再逐章空转；前端不再显示内部路径和原始异常。
- 代价：队列繁忙、pending 超过 30 分钟时，即使 Prefect 稍后真的会执行，任务也会被判失败并退款，用户需要重试（重试计费，因为额度已经退回），排队中的那次 run 启动后会直接跳过。没有 `billing` 记录的旧 job 被对账判失败时不退款（保守做法），需要客服人工补发。JSON 解析失败不再重试，原本重试一次就能成功的少数情况现在会记为章节失败。错误文案不承诺「已退款」，因为旧 job 不一定会退。

## Verification

`cd apps/server && pytest tests/test_services/test_ingestion_jobs_reconcile.py tests/test_flows/unit/test_llm_failure_policy.py tests/test_api/test_materials_retry.py tests/test_flows/integration/test_novel_ingestion_v3_resume.py -q`；`cd apps/web && pnpm exec vitest run src/lib/__tests__/materialsAccess.test.ts src/pages/__tests__/MaterialDetailPage.test.tsx`。
