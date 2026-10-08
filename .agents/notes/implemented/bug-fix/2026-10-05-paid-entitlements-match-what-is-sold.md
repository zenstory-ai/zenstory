# Agent Note: 付费权益以方案自身默认值补齐，宣传只列已实现的权益

Status: implemented

## Problem

开放在线付费前审查发现，Pro 用户实际拿到的权益与定价页不一致，几条订阅状态路径也会算错：

- 生产 `subscription_plan.features`（pro）由早期 seed 建立，缺 `custom_skills` 与 `inspiration_copies_monthly`。`quota_service` 缺键时退回**免费版**默认值，付费用户只能建 3 个技能、每月复制 10 次灵感，而 catalog 与计费页按 `PLAN_CATALOG_PRESETS` 宣传 20 和 100。
- catalog 与前端对比表宣传「上下文 16384 tokens」「优先队列」，后端没有任何实现（`agent/context/budget.py` 不读方案）。
- `get_user_plan` 发现 active 但已过期时直接把 ORM 对象改成 `expired` 并 commit，UPDATE 只有 `WHERE id=?`；在 PostgreSQL READ COMMITTED 下，它会等付款续期提交后照样写入 expired，已付款用户被降回免费版（本机已重现）。
- 同方案续费只看 `period_end < now`，不看 status：管理员把还剩 300 天的订阅改成 `cancelled` 后，用户再付一个月或兑换 7 天码，就拿回 300 多天。
- 可以建出 `tier=free` 的兑换码；切换方案时直接从 now 重算，Pro 用户兑换免费码会丢失剩余付费天数。
- 后台「活跃订阅」只看 `status=active`，每个用户注册都有一条 100 年的 free 订阅，数字约等于总用户数；过期 Pro 也因为 status 惰性更新而一直被算进去。

## Decision

**缺键用方案自己的默认值。** `services/subscription/defaults.py` 新增 `DEFAULT_PRO_PLAN_FEATURES`（含 `custom_skills: 20`、`inspiration_copies_monthly: 100`）与 `PLAN_FEATURE_DEFAULTS`；`resolve_plan_feature(plan_name, features, key)` 先取存储的 features，缺键才取该方案的默认值，未知方案取免费版。`quota_service.get_plan_feature` 统一经由它，`check_feature_quota`、`consume_feature_quota`、`get_quota_snapshot`、`check_project_limit`、`/subscription/quota` 的项目上限与后台配额详情都改用它。存储的 features 仍是唯一真相，默认值表只在缺键时生效，代码里仍然不按方案名分支放行（延续 `2026-04-29-materials-paid-entitlements.md`）。`api/subscription.py` 的 catalog 预设直接引用这两张表的数值，`scripts/seed_subscription_plans.py` 也从这里取 features，三处不再各写一份。

**数据迁移补键。** `alembic/versions/20261005_180100_backfill_pro_plan_entitlements.py`（down_revision `20261005_180000`）只给 `name='pro'` 的行补**缺失**的 `custom_skills=20`、`inspiration_copies_monthly=100`，营运已设的值保留，重复执行不改任何东西。数值写死在迁移里，不 import 应用代码。downgrade 刻意不做任何事：删键会把付费用户打回免费额度，也可能删掉迁移后营运手设的值。

**只宣传已实现的权益。** `PLAN_CATALOG_PRESETS` 与 `_normalize_plan_entitlements` 不再产生 `context_tokens_limit`、`priority_queue_level`，存储的 `context_window_tokens / priority_support` 也不再映射过去。`SubscriptionCatalogEntitlementsResponse` 暂时保留这两个字段，但固定为中性值 `0` 与 `"standard"`（已标 deprecated），只为让 2026-10 之前缓存的前端包不因读到 `undefined` 而在计费页崩溃；前端 `subscriptionEntitlements.ts` 对比表删除这两行，首页 Pro 文案与计费文档同步去掉「上下文容量 / 优先级」。设置页「订阅」卡片（`SubscriptionStatus.tsx`）不再逐项渲染 `/subscription/me` 的原始 `features` 字典，改用 `getSubscriptionFeatureRows`：只列每日 AI 消息、项目数、素材拆解（无素材库权限时显示「不含」）、自定义技能、复制灵感（灵感功能开启时）和每个文件保留的历史版本；素材上传（后端只扣 `material_decompose`）与导出格式（各方案都只有 TXT）不列，计费页对比表同样不列导出格式，标签与计费页对比表共用 `dashboard:billing.metric*`；`context_window_tokens`、`priority_support`、`custom_prompts`、`materials_library_access` 这些存储键不再出现在作者界面，`settings:subscription.features.*` 与 `yes/no` 已删除。素材拆解在生产可用（Prefect 已上线），保留。后台方案编辑器里的 `context_window_tokens` 等原始 feature 键不动。

**过期写入是条件 UPDATE。** `QuotaService._expire_if_still_lapsed` 执行 `UPDATE user_subscription SET status='expired' WHERE id=? AND status='active' AND current_period_end <= now`（比较用 naive UTC），没命中就 rollback。并发续期把 `current_period_end` 推到未来后，这条 UPDATE 不再命中。

**续费只延长正在生效的订阅。** `create_user_subscription` 的同方案分支只在 `status=='active'` 且 `period_end >= now` 时从旧 `period_end` 延长，其余（cancelled / expired / 已过期）一律从 now 起算。支付回调、兑换码、积分兑换都走这里。

**兑换码不能降级。** 后台建码（单个与批量）拒绝 `tier='free'`。`redemption_service.redeem_code` 在使用次数检查之后，若用户有正在生效的付费方案（`subscription_service.get_effective_paid_plan`：active、未过期、非 free），而码的方案月价更低（或方案不存在），返回 `ERR_REDEMPTION_CODE_DOWNGRADE`，不扣次数。付费方案过期后同一个码可以正常兑换。

**后台只数正在付费的人。** `/api/admin/dashboard/stats` 的 `active_subscriptions` 与 `pro_users` 都要求 `status='active' AND current_period_end > now AND plan.name != 'free'`；前端卡片改为显示「付费 Pro 用户」。

## Alternatives considered

- **只靠数据迁移补键，`quota_service` 继续退回免费版默认值**。最强理由：真相只在数据库一处，代码不需要知道任何方案的数值。被否：任何新环境、手工建的方案或将来新增的键都会再次静默降级到免费额度，而且这种降级只有付费用户撞上 402 才会被发现。
- **把上下文与优先队列标为「即将推出」**。最强理由：保留产品路线图信号，不必改文案结构。被否：付费页上列出买不到的东西仍是宣传未交付权益；实现时再加回对比表的成本很低。
- **`get_user_plan` 不回写 status，只按 `current_period_end` 判断**。最强理由：读路径完全无写，彻底没有锁与竞态。被否：后台、历史与其他依赖 `status` 的查询（例如兑换码降级判断、管理员筛选）会长期看到 active 的过期订阅；条件 UPDATE 已足以消除 lost update。
- **跨方案时把剩余付费天数折算到新方案**。最强理由：用户任何操作都不丢已付费时间。被否：折算规则（按价格还是按天数、降级是否退差）需要产品决策；本次先拒绝会造成损失的降级。

## Consequences

- 收益：生产 Pro 用户立刻拿到宣传的 20 个技能与每月 100 次灵感复制；`/quota` 与 `/catalog` 的数值有测试锁住（`tests/test_api/test_entitlement_consistency.py`）。
- 收益：付款续期不会再被并发的惰性过期覆盖；被撤销的剩余天数不会因续费复活；后台付费人数可信。
- 代价：方案默认值表成为第二个需要维护的地方：新增付费权益时要同时更新存储的 features、默认值表和（若对外宣传）catalog。
- 代价：兑换低档码现在要等付费方案到期；营运若要给 Pro 用户发其他档位的码，需要先手动处理订阅。
- 代价：catalog 响应里留着两个固定中性值的 deprecated 字段，旧前端包会显示「0 tokens / 标准队列」直到刷新；等旧包淘汰后要另行删除这两个字段。

## Verification

`cd apps/server && venv/bin/python -m pytest tests/test_services/test_entitlement_hardening.py tests/test_api/test_entitlement_consistency.py tests/test_models/test_payment_order_migration.py tests/test_api/test_subscription_plans_catalog.py -q -p no:cacheprovider`；上线后执行 `SELECT features FROM subscription_plan WHERE name='pro'` 应含 `custom_skills` 与 `inspiration_copies_monthly`。
