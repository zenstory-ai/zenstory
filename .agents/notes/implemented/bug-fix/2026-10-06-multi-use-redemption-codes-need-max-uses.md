# Agent Note: 多次使用的兑换码必须设置可兑换次数

Status: implemented

## Problem

`max_uses=None` 在兑换时表示不限次数（`redemption_service.redeem_code` 只在 `max_uses is not None` 时检查次数）。批量创建接口对所有 `multi_use` 码都写入 `None`，`CodeBatchCreateRequest` 也没有 `max_uses` 字段，后台批量弹窗却提供「多次使用」选项：点一次最多生成 100 个、每个都能被任意多人兑换付费套餐的码。单个创建接口在调用方传 `code_type=multi_use` 而不带 `max_uses` 时同样写入 `None`，只是后台界面恰好总是传 1。批量生成的码只在 toast 里报一个数量，无法复制或导出；审计记录也不含生成了哪些码。

## Decision

- `CodeCreateRequest` 与 `CodeBatchCreateRequest` 共用校验：`code_type` 为 `multi_use`/`multi` 时必须给出 `max_uses`（1–100000），否则 422。批量请求新增 `max_uses` 字段。
- `redemption_service.resolve_max_uses(code_type, max_uses)` 是写入前的唯一出口：单次码固定为 1；多次码缺少或小于 1 时抛 `APIException(VALIDATION_ERROR, 400)`。两个创建路由都调用它，绕过 schema 的调用方也无法写入不限次数的码。
- 批量创建的审计 `new_value` 记录 `code_ids`、`count`、`code_type`、`max_uses`、`tier`、`duration_days`；响应增加 `code_type`、`max_uses`。
- 兑换码路由里的 `HTTPException` 全部换成 `APIException`（限流用 `ERR_AUTH_RATE_LIMIT_EXCEEDED`，参数错误用 `ERR_VALIDATION_ERROR`，原文案移到 `error_detail`）；「最多 100 个」改由 schema 的 `le=100` 保证。
- 后台：批量弹窗选「多次使用」时出现「每个码可兑换次数」；单个创建只在多次码时显示次数并只在多次码时提交 `max_uses`。批量成功后弹出结果框，列出全部码，可「复制全部」或「下载 CSV」。套餐选项改从 `/admin/plans` 读取（创建时排除 free 和停用的套餐），不再写死 free/pro；列表里的套餐和类型显示为名称而不是原始值。

## Alternatives considered

- **批量接口直接拒绝 `multi_use`**。最强理由：最小改动就能堵住漏洞，批量发码的主要场景本来就是一人一码。被否：运营有「一个码发给一个群、限 N 人」的需求，单个创建已经支持带次数的多次码，批量也应一致。
- **把 `max_uses=None` 在兑换时改为按 1 处理**。最强理由：连已经存在的不限次数码也一并收紧。被否：会悄悄改变已经发出去、运营明确知道是不限次数的码的行为；存量码应由运营在列表里检查后手动停用。

## Consequences

- 收益：后台和 API 都无法再生成不限次数的码；批量生成的码可以直接拿去分发，审计里能查到是哪一批。
- 代价：直接调用 API、依赖旧行为（多次码不带次数）的脚本会收到 422。
- 存量：此前批量生成的多次码仍为不限次数（列表显示 `x/∞`），需要运营核对后停用。

## Verification

`cd apps/server && python -m pytest tests/test_api/test_admin_codes_extra.py tests/test_api/test_entitlement_consistency.py -q`；`cd apps/web && pnpm exec vitest run src/pages/admin/__tests__/CodeManagement.test.tsx`
