# 删除三块默认关闭的工作台引导：首页导览、新手上手清单、今天可以做的事

Status: implemented

## Problem

2026-10-19 盘点新手引导代码时，工作台首页有三块引导一直默认关闭：

| 模块 | 开关（默认 false） | 内容 |
|---|---|---|
| 首页导览 | `VITE_DASHBOARD_COACHMARK_TOUR_ENABLED` | 3 步聚光灯导览，菜单里有「重新查看新手引导」 |
| 新手上手清单 | `VITE_DASHBOARD_FIRST_DAY_ACTIVATION_GUIDE_ENABLED` | 注册首日的 4 步清单，数据来自 `/activation/guide` |
| 今天可以做的事 | `VITE_DASHBOARD_TODAY_ACTION_PLAN_ENABLED` | 按画像推荐下一步，数据来自 `/persona/recommendations` |

2026-10-19 只读核对过生产包：`dashboardOnboarding-*.js` 里三个开关都是 `a(void 0,!1)`，也就是没有设置、取默认值 false。

这三块和现在的流程已经对不上：

- 导览的终点是「点开始创作」，和聊天起手卡重复。
- 清单里没有「发第一条消息」。
- 推荐依赖画像的目标和经验，而画像页已经不再问这两题。

留着它们，要多维护三套界面、两个接口和一堆测试。车主要求把引导整理干净再合并。

## Decision

删除范围仅限以下内容，其他行为不变。

**前端**
- 导览：`contexts/ProductTourContext.tsx`、`hooks/useProductTour.ts`、`components/onboarding/*`、`config/productTours/dashboardFirstRun.ts`、`lib/productTour{Storage,Positioning}.ts`。
- 开关配置：`config/dashboardOnboarding.ts`。
- 今日推荐：`lib/dashboardActionPlan.ts`。
- `Dashboard.tsx` 里的导览外壳和「重新查看新手引导」。
- `DashboardHome.tsx` 里的清单卡、推荐卡和相关数据请求。
- 画像页跳转时附带的 `startDashboardCoachmark`。
- `writingStatsApi.getActivationGuide`、`onboardingPersonaApi.getRecommendations` 以及相关类型。
- 只被上面这些用到的文案键、单元测试，以及 `e2e/dashboard-coachmark.spec.ts`。

**服务端**
- `GET /api/v1/activation/guide` 和 `ActivationEventService.get_user_guide`（及其步骤常量）。
- `GET /api/v1/persona/recommendations`、`_build_recommendations`，以及 `/persona/onboarding` 响应里的 `recommendations` 字段。
- 对应测试。

**实施中发现的连带删除**
- 首页「新建项目」弹窗（作品名可不填）原来只能从新手上手清单的「继续下一步」打开，清单删掉后已无入口，一并删除，连同 `handleCreateProject` 和 `projects.createNew / nameLabel / namePlaceholder` 文案。「开始创作」和导入照常直接建作品。
- 灵感区和首页想法框上为导览留下的 `data-tour-id` 锚点。
- `scripts/ci/workflows.test.mjs` 检查的文件清单里去掉已删除的 `e2e/dashboard-coachmark.spec.ts`。

**不删**
- 激活事件记录和后台首日漏斗。
- 首部作品：生产要不要开还没定。
- 灵感库：属于产品功能，不算引导。

**一起修**
- 首部作品关闭时，首页加载那一刻会闪一下「正在准备你的写作空间…」。改为只在真正初始化时才显示。
- 手机上，从首页「开始创作」新建、且没有填想法的作品，第一次打开时停在 AI 页，作者一进来就能看到起手卡。做法是复用首页想法的本地标记（`lib/pendingInspiration.requestChatOnOpen / takeChatOnOpen`），只生效一次，5 分钟内有效。之所以没按「作品第一次对话」来判断：那样会把只想自己手写的作者也每次推到 AI 页。

**验证**
- 前端：页面、工作台、配置、灵感、Layout、ChatPanel、编辑器、文件树等 71 个测试文件全部通过。其中包括第二批漏改的 `FirstWork.preferenceAccount.test.tsx`，已按新画像页改写，见 `feature/2026-10-19-persona-one-question.md`。另外 `tsc -b`、`lint`（0 错误）、`lint:i18n-keys`、`lint:tokens` 通过。
- 服务端：画像、激活漏斗、后台、首部作品相关测试通过，`ruff check api/ services/` 通过；CI 清单测试 `node --test scripts/ci/workflows.test.mjs` 通过。
- 独立审查的结论记在 PR 里。

## Alternatives considered

- **保留代码，只是不开开关**
  - 最强论据：以后想开随时能开，删除有误删的风险。
  - 不选的原因：三块内容都已过时，真要开也得重写；不删，测试和类型还要跟着每次改动一起维护。git 历史里随时能找回。
- **只删前端，保留接口**
  - 最强论据：接口留着不影响别人。
  - 不选的原因：仓库里没有其他调用方（CLI、后台都不调用），留下的就是没人用的接口和测试。

## Consequences

**收益**
- 引导只剩真正在用的几条路径，代码和测试明显减少，之后改引导不必再顾及三套关着的界面。

**代价**
- 以后想要导览或清单，需要重新实现。
- `/persona/onboarding` 不再返回 `recommendations`：还停在旧版本的前端最多读到一个缺失的字段，旧前端也只在开关打开时才用它。
