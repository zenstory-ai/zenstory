# Agent Note: 素材拆书免费试拆一本（前 20 章），默认关闭，worker 上线后打开

Status: implemented

本 note 修改了 `feature/2026-04-29-materials-paid-entitlements.md` 中「免费用户只看预览」的规定：免费账号可以试拆一本的前 N 章，并在素材库里查看、引用这一本。付费权益（月度次数、退款口径）不变。

## Problem

10-03 至 10-09 的新用户里，43% 没建项目就离开；其中 19 人注册后几分钟内就碰到升级入口，12 人是从素材页点过去的，另有一笔从素材页预览发起、未付款的 Pro 订单。很多人是冲着「拆书」来的，免费版一次也试不了：素材库所有接口都要求 `materials_library_access`，免费套餐的拆解次数是 0。

成本：10-08 生产上 5 本、每本约 120 章的拆解，模型费用 ¥7～9 一本（约 ¥0.07/章，含按书的固定开销）。只拆前 20 章约 ¥2 一次。同期这几本都是「部分完成」：原因是角色提及抽取偶发返回非法 JSON（英文引号未转义）或空响应，每本丢 2～5 章的角色提及，摘要等核心产出完整；非法 JSON 已由 `bug-fix/2026-10-08-material-llm-json-repair.md` 修复（当时尚未上线）。不阻塞试用。

## Decision

- **试用额度**：`usage_quota.material_trial_used_at`（迁移 `20261009_120000`，可空列）。一个账号一次，不随月度重置。`quota_service.reserve_material_trial` 用条件更新原子占用（仅当为空），`release_material_trial` 清空。
- **开关**：`MaterialSettings.MATERIAL_TRIAL_ENABLED`（默认 False）与 `MATERIAL_TRIAL_MAX_CHAPTERS`（默认 20），环境变量可覆盖。章节上限在 Prefect worker 的阶段0 执行，必须先按发布流程上线 worker，再在 API 服务上打开开关；否则旧 worker 会拆整本。
- **权限拆分**（`api/materials/access.py`）：读取类接口（列表、详情、实体、预览、导入、搜索）改用 `require_materials_library_read`：有素材库权益，或已经用过试用。上传改用 `require_materials_upload`：有权益，或试用可用（开关打开、未用过、没有权益）。重试仍只对有权益的账号开放。
- **上传**：没有权益时走试用：占用试用而不扣月度次数；`source_meta.trial_chapter_limit`；任务 billing 记 `quota_mode="trial"` 与 `chapter_limit`。阶段0（`novel_ingestion_v3_flow._execute_stage0`）读 `job_chapter_limit(job)`，只为前 N 章建章节记录，后续阶段与费用随之只覆盖这些章。
- **退还**：任务以可退款错误码失败时，`IngestionJobsService._release_job_quota` 对试用任务调用 `release_material_trial`，作者可以再试一次；其余口径同月度次数（重试、部分完成都不退）。
- **前端**：`/subscription/quota` 新增 `material_trial {available, used, max_chapters}`。素材页：试用可用时预览页主按钮为「免费试拆一本（前 20 章）」，上传弹窗说明只拆前 20 章、一次、平台出错会退还；用过试用后显示素材库与横幅「免费试拆只拆了这本书的前 20 章…开通 Pro」。编辑器素材面板对用过试用的作者也加载这本书，可引用到对话、导入到作品。

## Alternatives considered

- **把免费套餐的 `material_decompositions` 改成 1。** 最强理由：沿用现有月度次数与退款代码，几乎不用写新逻辑。被否：月度次数每月重置，等于每月一次免费整本拆解（¥7～9/次）；而且访问权限不放开的话，免费用户上传后也看不到结果。
- **只开放一本公共示例书供浏览。** 最强理由：零边际成本，所有人都能看到拆解效果。被否：作者想拆的是自己要对标的那本书，示例书解决不了「这本书的结构是什么」；热门网文做公共示例还有版权问题。
- **按字数而不是章数截断。** 最强理由：章节长短差异大，按字数更能控制成本。被否：拆解按章执行、按章展示，截在章中间会产出不完整的章节梗概和角色提及；单章已有 5 万字上限，20 章的成本上界可控。
- **试用时跳过角色合并等按书的固定步骤以省钱。** 最强理由：试用成本还能再降一半。被否：角色卡是作者最想要的产出之一，去掉后试用展示不出拆书的价值。

## Consequences

- 收益：冲着拆书来的作者第一天就能看到自己那本书前 20 章的梗概、角色、世界观，并在写作时引用；用过试用的作者看到明确的「开通 Pro 拆完整本」入口。
- 代价：每次试用约 ¥2 模型费用，多账号可以重复白拿（成本靠一次性和章数上限约束，没有设备或手机号去重）。之前订阅过期、但用过试用的作者也能重新读到自己以前拆过的书。开关依赖发布顺序：worker 未上线时打开会按整本计费。迁移只在发布时由正常流程执行。

## Verification

- 后端：`.venv/bin/pytest tests/test_api/test_materials_trial.py tests/test_flows/integration/test_novel_ingestion_stage0.py -q --no-cov`：开关关闭时免费用户上传与读取仍是 402；打开后试用一次、只记试用不扣月度次数、任务带章数上限、第二次 402、试用书可在库中读取；平台原因失败退还试用；阶段0 只建前 N 章。相关素材、额度、权益、Prefect flow 测试（545 个）全部通过。
- 前端：`pnpm exec vitest run src/pages/__tests__/MaterialsPage.test.tsx src/hooks/__tests__/useMaterialLibrary.test.ts`。
- 未验证：迁移在真实 PostgreSQL 上的执行（本地从零跑 SQLite 迁移链在更早的迁移上就失败，与本次无关）；真实 Prefect worker 的试用拆解（需要 staging worker 发布）。
