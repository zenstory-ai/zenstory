# 移除已下线的积分、灵感广场与邀请裂变运行时代码

## Problem

积分/签到（`POINTS_ENABLED` / `VITE_POINTS_ENABLED`）、灵感广场（`INSPIRATIONS_ENABLED` / `VITE_INSPIRATIONS_ENABLED`）与邀请码裂变（`REFERRAL_ENABLED` / `VITE_REFERRAL_ENABLED`）三套早期增长模块已在产品中下线并长期处于关闭状态，但仓库仍保留了大量前后端运行时路由、服务、开关、注册邀请码门控、UI 组件、翻译命名空间、生成物、文档与测试代码：

- 新功能演进（如认证、注册、套餐、管理后台与 Dashboard 导航）每次改动都要持续维护死分支与组合测试；
- 特征开关矩阵放大了构建、测试与代码审查负担；
- 下线功能与仍在活跃使用的核心能力（尤其是**订阅兑换码**、Dashboard 创作起稿流与渠道归因统计）混杂在相近目录中，容易引发误改或维护歧义。

## Decision

基于删除前基准提交 **`ee38b1a7`**，彻底移除积分/签到、灵感广场与邀请裂变的运行时代码、UI、配置开关、独立翻译文件与专属测试，同时严格保留数据库表结构与仍在使用的兑换码等活跃能力：

1. **后端运行时清理**
   - 删除 `api/inspirations.py`、`api/admin/inspirations.py`、`services/inspiration_service.py`、`api/points.py`、`api/admin/points.py`、`api/admin/checkin.py`、`services/features/points_service.py`、`api/referral.py`、`api/admin/referrals.py`、`services/features/referral_service.py` 及对应路由注册、管理后台统计字段与特征开关（`config/feature_flags.py`）。
   - 清理 `api/auth.py`、`api/oauth.py`、`api/verification.py`、`api/admin/skills.py`、`services/project_service.py` 与 `scripts/seed_test_user.py` 中仅服务于已下线积分/邀请码的钩子、注册邀请码策略接口（`/api/auth/register-policy`）与字段。
   - 删除 21 个专属后端测试文件，并清理共享测试中对已下线接口与开关的断言。

2. **前端运行时、资产与测试清理**
   - 删除 `src/components/{inspirations,points,referral}/`、`src/pages/{InspirationsPage,InspirationDetailPage}.tsx`、`src/config/{inspirations,points,referral}.ts`、`src/hooks/{useInspirations,useDashboardInspirations}.ts`、`src/lib/{dashboardInspirationSource,pointsApi,referralApi}.ts`、`src/types/{points,referral}.ts`、`public/generated/`、`public/locales/{zh,en}/{inspirations,points,referral}.json`、`docs/{,en/}user-guide/inspirations.md`、`e2e/{inspirations,points,referral}.spec.ts` 及 30 个专属前端单元测试文件。
   - 清理 `App.tsx`、Dashboard、Settings、Billing、Register、Admin 页面、API 客户端、i18n 命名空间注册（`localeNamespaces.ts`、`localeBundleBackend.ts`、`virtual-locales.d.ts`、`i18n.ts`）、文档导航、环境模板及 CI/Playwright 配置中的残余分支。
   - 保留仍在使用的活跃模块：`src/lib/pendingInspiration.ts`（Dashboard 创作起稿流）、`src/lib/preferredProjectType.ts`、管理后台历史审计日志展示标签（`adminAuditLabels.ts` / `RecentActivityList.tsx`）与流量来源渠道 `referral`（`acquisition_service.py` / `AcquisitionPage.tsx`）。

3. **数据库表保持休眠（零 `DROP TABLE`、零破坏性迁移）**
   - 不新增任何 `DROP TABLE` 或破坏性 Alembic 迁移；所有历史业务数据保持完整。
   - 保留 `apps/server/models/inspiration.py`、`apps/server/models/points.py`、`apps/server/models/referral.py` 及其在 `apps/server/models/__init__.py` 中的导出，并标注为 DORMANT，确保 SQLModel / Alembic 元数据与外键关系（以及测试夹具清理路径）保持一致。

4. **兑换码系统 100% 原样保留**
   - 订阅兑换码（`apps/server/api/admin/codes.py`、`apps/server/services/subscription/redemption_service.py`、`apps/server/utils/code_generator.py`、`apps/server/api/subscription.py` 兑换接口、`apps/web/src/pages/admin/CodeManagement.tsx`、`apps/web/src/components/RedeemCodeModal.tsx` 及全部 `test_redemption*` / `test_admin_codes*` 测试）完全不受影响并独立通过回归验证。

5. **Git 恢复锚点**
   - 若未来需要重新启用或参考任意已移除模块，可直接从删除前基准提交 **`ee38b1a7`**（`git show ee38b1a7:<path>`）找回完整实现，且休眠数据表仍在库中可供平滑恢复。

## Alternatives considered

### 1. 继续保留特征开关并保持默认关闭
- **最强论据**：重新上线成本最低，无需从 Git 历史恢复代码；短期改动面为零。
- **放弃原因**：长期关闭的 2.8 万余行死代码持续拖累类型检查、路由/设置面板改造、注册流程演进与测试矩阵维护；保留关闭开关本身会制造“仍受支持”的误导信号。通过记录基准 SHA `ee38b1a7` 与保留休眠数据表，恢复路径依然清晰低风险。

### 2. 同步执行 Alembic `DROP TABLE` 物理删表
- **最强论据**：数据库 schema 与运行时代码一步到位保持完全一致，不再需要保留休眠 ORM 模型文件。
- **放弃原因**：物理删表不可逆，会直接丢失历史积分/邀请/灵感记录，并在滚动发布、历史数据核查或回滚时带来高风险。保持表休眠、零破坏性迁移符合 [2026-11-19 简化可恢复编辑系统](./2026-11-19-simplify-recoverable-agent-edits.md) 确立的演进惯例。

### 3. 连同订阅兑换码一并下线
- **最强论据**：表面上同属“码/权益发放”类目，一并清理可以再省少量代码。
- **放弃原因**：订阅兑换码当前仍在真实使用，属于明确的活跃产品能力，与已下线的用户裂变邀请码完全独立，必须 100% 保持原样可用。

## Consequences

- **收益**：显著减少约 2.8 万行已下线前后端代码、翻译资源与冗余测试；注册流程移除多余的 `/api/auth/register-policy` 往返与邀请码门控；Dashboard、Settings 与管理后台路径更清晰；全套前端静态检查与前后端回归更聚焦于真实活跃能力。
- **代价与后续注意事项**：
  - 三个休眠模型文件（`models/inspiration.py`、`models/points.py`、`models/referral.py`）与对应数据库表仍会保留在元数据中，直到未来单独批准数据归档或退场迁移。
  - 管理后台历史审计日志标签保留了 `points` / `invite_code` 映射以正确渲染旧日志条目；若未来历史日志归档完成可一并清理。
  - 若今后决定恢复其中任一模块，需从 `ee38b1a7` 按需检出并适配最新接口契约，而非假定旧代码可直接拼回。
