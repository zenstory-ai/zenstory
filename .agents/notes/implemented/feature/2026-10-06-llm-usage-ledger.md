# Agent Note: 按调用落库的 LLM 用量账本与后台「用量与成本」页

Status: implemented

## Problem

运营需要在后台看到每位用户每天、以及最近 7 天在 DeepSeek 上花了多少钱，按 DeepSeek Flash 的价格计费。上线前的现状支撑不了这个需求：

- 唯一存下来的用量在 `chat_message.message_metadata.usage`，只覆盖写作助手的完整轮次。取消、断线、失败时的部分保存不带 usage；SDK run 抛错时那一轮的 token 只记在日志里；`DELETE /chat/session/{project_id}` 会把消息连同用量一起硬删除。
- 输入建议（`/agent/suggest`）、自然润色（`/editor/natural-polish`）的 usage 只打日志；素材拆解的 Prefect 流程（推理开启、`max_tokens` 64000、Prefect 重试再叠加 SDK 重试，往往是最大的一块成本）拿到 `LLMResponse.usage` 后也只打日志。
- 现有统计（`writing_stats_service`）按项目、按 UTC 日、用默认 0 的美元单价估算，没有高峰/空闲两档，也没有按用户汇总的后台入口。
- 服务端没有任何北京时间的日界线工具。

## Decision

**账本表 `llm_usage_event`，一次模型调用一行。** 模型在 `apps/server/models/llm_usage.py`，迁移 `alembic/versions/20261006_090000_add_llm_usage_event.py`（`down_revision = 20261005_180100`）。字段：`user_id`（FK `user.id`，非空、有索引）、`project_id`（可空）、`source`（`agent | router | suggest | polish | material`）、`model`、`cache_hit_tokens`、`cache_miss_tokens`、`output_tokens`（含推理 token）、`price_band`（`peak | offpeak`）、`pricing_version`、`occurred_at`（naive UTC，沿用全库惯例）、`correlation_id`（agent 为 `agent_run_id`，素材为 `novel:<id>`，回填为 `chat_message:<id>`）、`is_backfilled`。索引：`(occurred_at)`、`(user_id, occurred_at)`。表里只存 token，不存金额；金额由三项 token、档位和价格版本推出，可以复算。

**价格只在一个模块里。** `services/usage/pricing.py`：`PRICING_VERSION = "deepseek-flash-2026-10"`，单位为元/百万 tokens，高峰：缓存命中 0.04、未命中 2、输出 8；空闲：0.02、1、4。高峰为北京时间周一至周五 `[09:00, 12:00)` 与 `[14:00, 18:00)`，其余为空闲。档位按每次调用自己的完成时间判定。**不处理法定节假日和调休**：工作日的节假日按工作日计价。金额用 `Decimal`；为了在 SQL 里精确聚合，价格乘 100 变成整数权重，`tokens × 权重` 的单位是 1e-8 元，接口输出四舍五入（ROUND_HALF_UP）到 4 位小数的字符串。

**写账本永远不影响用户请求。** `services/usage/llm_usage_service.py`：

- `extract_usage_tokens` 接受任意 usage 对象或 dict：优先读 DeepSeek 的 `prompt_cache_hit_tokens / prompt_cache_miss_tokens`；没有时命中数取 `prompt_tokens_details.cached_tokens`（或 openai-agents 的 `input_tokens_details.cached_tokens`），未命中 = prompt − 命中；输出取 `completion_tokens` / `output_tokens`。非数值（包括测试里的 MagicMock）一律按 0。线上探测确认 deepseek-flash 流式（include_usage）与非流式响应里两组字段都有且相等，所以只走 SDK 的 `cached_tokens` 路径也是对的。
- `record_llm_usage`（同步）与 `record_llm_usage_async`（在事件循环上取 token 和时间戳，`asyncio.to_thread` 落库）都用**自己的** session（默认 `database.create_session`），从不复用调用方或 ToolContext 的 session；没有 user_id 或三项 token 全为 0 时跳过；所有异常只记 ERROR 日志并返回 False。

**每个 DeepSeek 调用点都计量：**

- 写作助手：`agent/openai_agents/usage_hooks.py` 的 `RunHooks.on_llm_end`，作为 `hooks=` 传给 `Runner.run_streamed`（`runner.py`）。openai-agents 0.17.8 在每次模型调用结束时回调一次并给出那次调用的 `ModelResponse.usage`，所以每次调用一行、各自的时间戳，之后 run 抛错或被取消也不会丢掉已完成调用的用量。user/project 在 `run_streamed` 之前从 `ToolContext` 读出、`agent_run_id` 从 `utils/request_context.get_agent_run_id()` 读出，绑定在 hooks 实例上，不依赖 contextvars 进入 SDK 后台任务。hooks 类按 SDK 的 `RunHooks` 动态建子类并缓存，测试里替换的假 `agents` 模块不会污染真实 SDK。
- 意图路由：`agent/graph/router.py::_route_with_deepseek_chat` 拿到响应后计量（`source=router`）。
- 输入建议与自然润色：`LLMClient.acomplete` 新增 `usage_attribution` 参数；`suggest_service` 传入请求用户和项目；`natural_polish_service.natural_polish` 新增 `user_id / project_id` 参数，由 `api/editor.py` 传入 `current_user.id` 和 `body.project_id`。
- 素材拆解（Prefect worker）：`flows/utils/clients/llm.py::DeepSeekClient._call_deepseek` 每次真实调用都计量（含 Prefect 重试与 JSON 解析失败后的重跑），计费给小说所有者。归属用**显式参数**：`call_deepseek_api(..., usage_novel_id=...)` 或 `usage_chapter_id=...`，客户端经 `Chapter.novel_id → Novel.user_id` 解析所有者（进程内缓存），写入走 `flows/database_session.create_prefect_session`。没有验证 contextvars 能否跨 Prefect 任务线程传递，所以不用它。`tests/test_flows/unit/test_llm_usage_metering.py` 用 AST 检查 `flows/atomic_tasks` 下每个 `call_deepseek_api(...)` 都带了这两个参数之一。

**顺手补的展示用量缺口**（聊天消息里的 usage 仍只用于对话内展示，计费以账本为准）：路由模型已经答复、只是解析失败时，`routing_usage` 照样返回；取消/失败路径的部分历史保存带上 stream adapter 已累计的 usage。

**后台接口**（`api/admin/usage.py`，superuser，沿用既有的 `/api/admin` 前缀；逻辑在 `services/usage/admin_usage_service.py`）：

- `GET /api/admin/usage/summary?window=today|yesterday|7d`：`timezone=Asia/Shanghai`、`period_start / period_end`（北京日期）、`pricing_version`、`prices`、`totals`（用户数、调用数、三项 token、总费用、高峰/空闲费用）、`by_source`、`daily`（7d 固定 7 行，零值补齐）。
- `GET /api/admin/usage/users?window=&search=&sort=cost|calls|tokens&page=&page_size=`：按用户聚合，`search` 匹配用户名、邮箱（不区分大小写）或完整用户 ID；按费用排序在 SQL 里用 `CASE price_band` 算整数成本单位；同值按 `user.id` 升序，翻页不重叠。`last_used_at` 带时区（`Z`）。
- `GET /api/admin/usage/users/{user_id}/daily?days=7|14|30`：逐日（补零）、合计、按功能拆分。
- 「近 7 天」= 截至今天（含）的 7 个北京日。窗口边界在 Python 里算成半开的 naive UTC 区间；逐日分桶用基于这些边界的 `CASE` 表达式放在子查询里，外层按普通列 `GROUP BY`，SQLite（测试）和 PostgreSQL（生产）同一套查询。已在本地 PostgreSQL 14 上跑过迁移往返、`alembic check`（无差异）、三个聚合函数和回填脚本。

**后台页面** `apps/web/src/pages/admin/UsageCostPage.tsx`，路由 `/admin/usage`，侧栏「用量与成本」紧挨「用户管理」。时间范围切换（今天/昨天/近7天）、KPI（总费用、高峰/空闲费用、调用次数、活跃用户、三项 token）、近 7 天逐日表（带条形）、按功能拆分、按用户表（搜索、排序、分页，lg 以下用卡片）；点用户打开共享 `ui/Modal` 的详情（7/14/30 天逐日与按功能拆分），`/admin/usage?user=<id>` 可直接打开。价格表压成一行，另有一句「已取消或超时的请求、向量嵌入不计入」。金额 `¥`，≥1 元两位小数，以下最多四位（`lib/formatCny.ts`）。

**回填** `apps/server/scripts/backfill_llm_usage_from_chat.py`（支持 `--dry-run`、`--before`）：读取 `role=assistant` 且 `message_metadata.usage` 是规范格式（有 `total_tokens`，且没有旧 Anthropic 格式的 `cache_read_input_tokens / cache_creation_input_tokens`；值为 0 的键写入时就被省略）的消息，`input_tokens` 即未命中部分、`cache_read_tokens` 即命中部分，经 `chat_session` 取 user/project，写 `source=agent, is_backfilled=true, correlation_id=chat_message:<id>`，按消息 `created_at` 定档位（一轮一行，跨档的轮次是近似值）。已有同一 `correlation_id` 的回填行就跳过，可重复执行。默认只回填**第一条实时 agent/router 账本行之前**的消息，避免与实时计量重复。

**上线顺序**：先在生产手动执行 `alembic upgrade head`（照惯例先 `pg_dump` 备份），再合并服务端和 worker 代码。否则 `init_db` 的 `create_all` 会先建表，之后 `op.create_table` 失败；Prefect worker 也会写这张表。表建好、新代码跑起来之后再执行回填（先 `--dry-run`）。

## Alternatives considered

- **直接聚合 `chat_message.message_metadata.usage`，不建新表**。最强理由：零迁移，历史数据现成。被否：只覆盖写作助手的完整轮次，清空对话会硬删除，取消/失败路径没有 usage，一轮只有一个时间戳，没法按每次调用定档位；建议、润色、素材拆解根本不在里面。
- **入库时就把金额算好存进表**。最强理由：查询最简单，不需要在 SQL 里按档位算。被否：价格一变历史数据就说不清；只存 token、档位和价格版本，金额随时可复算，在 SQL 里用整数权重聚合同样精确。
- **在共享 `AsyncOpenAI` 客户端的 `chat.completions.create` 包装层统一计量**（`deepseek_client.py` 已经有一层包装）。最强理由：一个点覆盖路由和 SDK 的所有调用。被否：流式调用的 usage 在最后一个 chunk 才到，包装层要接管流的迭代；归属（用户、项目、run id）也要再从 contextvars 取。`on_llm_end` 是 SDK 的公开接口，直接给每次调用的 usage。
- **素材流程靠 contextvar 传用户**。最强理由：不用改每个任务的调用。被否：Prefect 任务在线程池里跑，contextvar 能否传进去没有验证过；显式的 `usage_novel_id / usage_chapter_id` 加一次主键查找更可靠，AST 测试保证新调用点不会漏。
- **用数据库函数按北京日期分桶**（PostgreSQL `AT TIME ZONE`、SQLite `date(..., '+8 hours')`）。最强理由：SQL 更短。被否：两种方言写法不同，测试跑在 SQLite 上，等于生产查询没被测试覆盖；在 Python 里算边界、用 `CASE` 分桶，两边是同一条 SQL。

## Consequences

- 收益：每次 DeepSeek 调用（写作助手、路由、建议、润色、素材拆解）都有一行带时间戳和档位的记录，清空对话不影响；后台能按北京日看每个用户的费用，并拆成高峰/空闲与按功能。
- 收益：价格只在 `pricing.py` 一处；改价时升 `PRICING_VERSION`，历史行按原版本复算。
- 代价：每次模型调用多一次独立的小事务写入（在线程池里执行，不占事件循环），每轮写作请求多几行。
- 代价：报表是**下限**。被取消、断线或超时的流式调用，usage 只在最后一个 chunk 才有，DeepSeek 照样收费但我们拿不到；建议接口 `wait_for` 超时、SDK 内部重试的读超时同样拿不到。向量嵌入（智谱 embedding-3，另一家供应商）不在账本里。需要对账时以 DeepSeek 控制台为准。
- 代价：法定节假日不建模，节假日的工作时段会按高峰价计；回填行一轮一个时间戳，档位是近似值；2026-07-27 之前的旧格式消息不回填。

## Verification

`cd apps/server && <venv>/bin/python -m pytest tests/test_services/test_llm_usage_pricing.py tests/test_services/test_llm_usage_service.py tests/test_agent/test_llm_usage_metering.py tests/test_flows/unit/test_llm_usage_metering.py tests/test_api/test_admin_usage.py tests/test_scripts/test_backfill_llm_usage_from_chat.py tests/test_models/test_llm_usage_migration.py -q -p no:cacheprovider`；`cd apps/web && pnpm exec vitest run src/pages/admin/__tests__/UsageCostPage.test.tsx src/lib/__tests__/adminUsageApi.test.ts src/components/admin/__tests__/AdminSidebar.test.tsx`；`PLAYWRIGHT_EXTERNAL_BACKEND=1 pnpm exec playwright test e2e/admin-routes-smoke-mocked.spec.ts --project=chromium --no-deps`。上线后在后台「用量与成本」看今天的数字，与 DeepSeek 控制台当天消费对比（预期略低）。
