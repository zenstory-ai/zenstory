# Agent Note: 自定义技能上限按「当前拥有的技能数」计算，检查与建立在同一把锁内

Status: implemented

## Problem

`FEATURE_QUOTA_MAP["skill_create"]` 把技能上限映射到每月清零的 `skill_creates_used`，再由 `@require_quota("skill_create")` 先检查、函数 commit 之后才扣减。结果是：

- 文案写的是「自定义技能数量」，实际却是「每月建立次数」：删掉一个技能不退名额，免费用户画面上只有 2 个技能却显示 3/3、建不了新的；跨月后又能再建 3 个，总数没有上限。
- 检查与扣减之间没有锁：同时送出多个 `POST /skills` 或 `/skills/import` 会全部通过检查并各自建立，`consume_feature_quota` 超限时回 False 但调用方不看。

## Decision

上限改成「用户当前拥有的 `UserSkill` 数」，删除即释放名额。

- `quota_service.check_custom_skill_slot(session, user_id)`：先取方案上限（`get_plan_feature(plan, "custom_skills")`，`-1` 为无限），然后对用户的 `usage_quota` 行执行 `skill_creates_used = skill_creates_used + 1` 取得写锁（PostgreSQL 行锁、SQLite 写锁），再 `count(UserSkill)`。锁一直持有到调用方提交新技能或 rollback，所以并发请求中第二个会等第一个提交后再计数。
- `core/permissions.require_custom_skill_slot` 包装它：超限时 rollback 释放锁并抛 `QuotaExceededException(feature_type="skill_create", used=拥有数, limit=上限)`，402 的错误格式和前端提示不变。
- `POST /api/v1/skills` 与 `POST /api/v1/skills/import`（技能包解析完、落库前）调用它，并移除 `@require_quota("skill_create")`；插入与上面的锁在同一个事务里提交。
- `skill_creates_used` 只在成功建立时随同一事务 +1，保留为后台「本月建立次数」的活动指标，不再参与限额。`/subscription/quota` 的 `skill_creates.used` 与后台配额详情的 `skill_create_used` 改为拥有数，`reset_at` 为 `null`；`check_feature_quota("skill_create")` 也按拥有数回答。

## Alternatives considered

- **保留每月建立次数的语义，只把文案改成「每月可建立 N 个」**。最强理由：不改计量逻辑，现有配额表与月度重置直接沿用。被否：用户对「自定义技能」的直觉是能同时保有几个，删掉不退名额的体验像 bug；而且这样仍要另外修检查与扣减之间的竞态。
- **先原子扣减计数器再建立，失败时补偿退回**。最强理由：沿用 `consume_feature_quota` 的条件 UPDATE，不需要额外的锁语义。被否：计数器与实际拥有的技能会因删除、补偿失败而漂移；以表中真实行数为准不会漂移。
- **锁 `user` 行（`SELECT ... FOR UPDATE`）**。最强理由：语义上锁的是「这个用户」，与订阅变更的锁一致。被否：SQLite 忽略 `FOR UPDATE`，开发环境与测试里竞态仍在；对 `usage_quota` 的 UPDATE 在两种数据库上都会真正取得写锁，还顺便维护活动指标。

## Consequences

- 收益：上限与用户看到的技能列表一致，删除即可再建；并发请求不会超额（`tests/test_services/test_entitlement_hardening.py::test_concurrent_skill_creates_cannot_exceed_owned_limit`）。
- 代价：建立技能的事务从检查开始就持有 `usage_quota` 行的写锁，同一用户的其他配额写入（例如素材上传扣减）会短暂排队；SQLite 下是全库写锁，只影响开发环境。
- 代价：已经因旧语义超过上限的用户不会被删技能，只是在降到上限以下之前不能再建新的。

## Verification

`cd apps/server && venv/bin/python -m pytest tests/test_services/test_entitlement_hardening.py tests/test_api/test_entitlement_consistency.py tests/test_api/test_skill_packages_api.py tests/e2e/test_skills_contract_e2e.py -q -p no:cacheprovider`
