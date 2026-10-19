# 新用户激活：首条 AI 消息事件、注册批次漏斗，不把自动建议算作激活

Status: implemented

接续 `feature/2026-10-05-activation-and-ai-outcome-events.md`。后台激活漏斗原来「各事件独立计数」，这次改成按注册批次计数。整体方案见 `proposed/feature/2026-10-19-onboarding-to-first-message.md`。

## Problem

新用户引导要把人带到「发出第一条 AI 消息并看到回复」，但现有数据回答不了「新用户里有多少人走到了这一步」：

- 后端激活事件只有四个：`signup_success`、`project_created`、`first_file_saved`、`first_ai_action_accepted`，没有「首条 AI 消息」和「首条回复成功」。
- 后台漏斗按时间窗口统计各事件的去重人数，看不出同一批注册用户逐步转化的情况。
- 增长页的「新用户 AI 激活率」和「AI 活跃用户」把任意一次模型调用都算上了。打开空项目时，界面会自动请求 `/agent/suggest`（`LLMUsageEvent.source = "suggest"`），作者一条消息没发也会被算作已激活。
- 前端的 `ai_chat_completed` 不带入口，画像页没有展示和提交事件，也没有通用的「打开项目」事件。

## Decision

**激活定义**：注册后 24 小时内，作者主动发出的 AI 消息至少有一轮正常结束，并且 AI 写进了作品文件，对应 `first_ai_action_accepted`。

**两个新的服务端里程碑**（`models/activation_event.py`；每个账号各记一次，`record_once` 依赖唯一约束）：
- `first_ai_message_sent`：`/agent/stream` 预扣额度成功后记录。
- `first_ai_reply_succeeded`：同一请求结算结果为 `completed`（正常结束、计费）时记录。
- 两者都只在 `event_metadata` 里记 `entry`，即前端 `metadata.entry` 的前 32 个字符，缺省为 `typed`。不记录消息内容。
- 记录失败只写日志，不影响这一轮：`api/agent.py::_record_activation` 和 `ActivationEventService.record_once_best_effort`。在 PostgreSQL 上，记录放进后台线程，用独立的短 session；在 SQLite 上（测试和本地）直接用请求 session。

**后台漏斗**（`get_funnel_stats`）：
- 基数是统计期内注册的客户（排除管理员）。取 User 表而不是 `signup_success` 事件，所以任何注册路径都算在内。
- 其余各步统计这批人中，在注册后 24 小时内到达该步的人数。
- 步骤顺序：注册 → 建项目 → 首条消息 → 首条回复成功 → AI 写入作品。`activation_rate` 等于最后一步的人数除以基数。
- 每一步统计「至少走到这一步」的人：走到后面一步的人也计入前面每一步，各步只减不增，转化率不会超过 100%。原因：发过 AI 消息必然已经有作品，但自动初始化的首部作品不写 `project_created`。
- 手动保存（`first_file_saved`）不放进这条链：先用 AI 的作者会跳过它，放进来会让下一步的转化率超过 100%。事件照常记录。
- 后台文案改为「新用户首日漏斗」，说明最近一天注册的用户还在观察期。
- 用户自己的首日清单（`/activation/guide`）后来随默认关闭的工作台引导一起删除，见 `simplification/2026-10-19-remove-dormant-dashboard-onboarding.md`。

**增长页**（`admin_growth_service`）：「AI 活跃用户」和「新用户 AI 激活率」，以及它们的每日分桶，都排除 `source = "suggest"` 的用量。

**前端埋点**：
- `ai_chat_completed`、`ai_chat_failed`、`ai_chat_stopped`、`ai_chat_cancelled` 带上 `entry`。
- 新增 `persona_viewed`、`persona_submitted`（带是否跳过和所选作品类型 `project_type`）、`project_opened`（项目路由解析成功后，每个项目记一次）。

## Alternatives considered

- **保留「各事件独立计数」，另加一个批次漏斗接口**
  - 最强论据：两种视角都保留，旧数字不变。
  - 不选的原因：后台已经有 7/14/30 天的批次增长指标，独立计数的那份已注明「不代表同一批用户逐步转化」，没有人拿它做判断。两份并存只会让人看错。
- **用 `ai_chat_submitted` 等前端事件衡量首条消息**
  - 最强论据：不用改服务端，PostHog 里本来就有这些事件。
  - 不选的原因：只有配置了 PostHog 才会上报，还会被广告拦截器挡掉。漏斗要按账号精确去重，服务端在预扣额度和结算时的事实更可靠。
- **激活率直接改成「24 小时内 AI 写入作品」**
  - 最强论据：和新的激活定义完全一致。
  - 不选的原因：增长页按 7/14/30 天和上一期对比，换口径会让对比断档。这次只去掉自动建议，24 小时口径由新的首日漏斗给出。

## Consequences

**收益**
- 能直接看到同一批新用户中，有多少走到了「首条消息」「首条回复」「AI 写入」。
- 起手卡等实验的效果能按 `entry` 拆开看。
- 激活率不再被自动建议抬高。

**代价**
- 每条 `/agent/stream` 多一次按唯一键的查询，回复正常结束时再多一次。
- 历史上从没记录过这两个新事件，上线前注册的用户在这两步显示为 0，只能从上线后的注册批次开始对比。
- 增长页的激活率和活跃人数会比之前低，属于口径修正，不代表实际下滑。
- `project_opened` 和画像页的事件只在开启了 PostHog 的环境才有数据。

**测试**
- `tests/test_api/test_agent_activation_events.py`：完成的一轮记录两个事件；退还的失败只记首条消息。
- `tests/test_api/test_activation_funnel.py`：同一批注册用户、24 小时窗口、排除超过一天才到达的用户。
- `tests/test_api/test_admin_growth.py`：只有 suggest 用量的用户不算激活。
- `src/lib/__tests__/agentStreamTelemetry.test.ts`：结果事件带 entry。
