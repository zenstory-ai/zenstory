# Agent Note: 素材库按"权益 + 配额"两级门禁，区分未包含与已用尽

Status: implemented

## Problem

素材库要成为付费功能：免费用户只看预览，付费用户每月 5 次素材拆解。按套餐名硬编码（`plan.name == "pro"` 直接放行为无限）无法表达"功能不包含"与"配额用完"的区别，前端只能统一弹升级框，付费用户用尽额度时被误导去升级。

## Decision

后端权益是单一真相：`services/subscription/defaults.py` 里免费套餐 `materials_library_access: False`、`material_decompositions: 0`；`core/permissions.py` 把 `material_upload` 与 `material_decompose` 两个动作映射到 `materials_library_access` 权益。无该权益的请求返回 `ERR_FEATURE_NOT_INCLUDED`（"当前套餐暂不包含该功能"）；有权益但月度配额耗尽返回 `ERR_QUOTA_EXCEEDED`。配额判断只看配置值，`-1` 表示无限，代码 never 按套餐名分支。付费套餐的 5 次/月是套餐数据（见 `apps/server/scripts/seed_test_user.py` 的种子），不是代码常量。前端 `MaterialsPage` 对免费用户渲染预览态并发 `materials_teaser_exposed` 事件，对付费用户显示剩余次数；用尽时提示下月恢复且 never 弹升级框。面向用户的计量单位统一为"素材拆解次数"。

来源：3ef311e（初始提交；实施稿 `docs/plans/2026-04-05-materials-paid-entitlements-implementation.md`，已落地）

## Alternatives considered

- **继续按套餐名硬编码放行**（实施稿所替换的做法）。最强理由：最少改动，付费即无限简单直观。被否：加第二个付费档或临时活动就要改代码；"无限"应是配置 `-1` 而不是名字。

## Consequences

- 收益：前后端对"未包含 / 已用尽"用同一对错误码，升级引导只出现在真正需要升级的场景。
- 代价：新增付费功能 must 同时登记权益键、动作映射和错误码映射三处；配额数值在套餐数据里，改数字不需要发版，但也不在代码 review 范围内。

## Verification

`cd apps/server && .venv/bin/pytest tests/test_api/test_materials.py tests/test_api/test_materials_retry.py tests/test_services/test_quota_service.py -q`；`grep -rn '== "pro"' apps/server/core apps/server/services` 应无命中。
