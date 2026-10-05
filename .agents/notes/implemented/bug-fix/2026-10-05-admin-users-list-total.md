# Agent Note: 后台用户列表由后端返回筛选后的总数，并按固定顺序分页

Status: implemented

## Problem

生产 `/admin/users` 第一页显示「显示 1-20 条,共 20 条」「1 / 2」，翻到第二页变成「共 40 条」，实际有 92 个用户。

- `GET /api/admin/users`（`apps/server/api/admin/users.py` 的 `get_users`）只回传当前页的用户数组，没有总数。
- 前端 `apps/web/src/pages/admin/UserManagement.tsx` 只能推估：`total = page * pageSize + users.length`、`totalPages = 本页满 20 条 ? page + 2 : page + 1`。所以「共 N 条」其实是当前页的结束序号，页数永远只比当前页多 1。
- 查询没有 `ORDER BY`，PostgreSQL 的 offset 分页不保证顺序稳定，翻页时可能重复或漏掉用户。

## Decision

- 后端 `GET /api/admin/users` 回传 `{"items": [AdminUserResponse...], "total": <int>}`（response model `AdminUserListResponse`，在 `api/admin/schemas.py`），与同目录 `feedback.py`、`payment_orders.py` 的 `{items, total}` 惯例一致。`total` 用 `select(func.count())` 计算，和 `items` 套用同一个 `search`（用户名或邮箱 `ilike`）条件。查询参数 `skip / limit / search` 不变。
- 排序固定为 `created_at desc, id desc`：最新注册的用户在前，`id` 作为同一时间戳下的决胜键，保证 offset 分页不重不漏。
- 前端 `adminApi.getUsers` 回传 `{ users, total: number | null }`：payload 是 `{items, total}` 时取 `total`；是旧版裸数组时 `total = null`。`UserManagement` 有 `total` 时 `totalPages = max(1, ceil(total / pageSize))`，显示「显示 {from}-{to} 条,共 {total} 条」，区间 `to = min((page + 1) * pageSize, total)` 与总数分开；`total = null` 时才退回原推估。搜索与重置仍把页码归零。
- 部署顺序无关：新后端 + 旧前端时，旧前端的 `pickArray(payload, ["items", "users"])` 能读 `items`（显示仍是旧的推估，但列表正常）；旧后端 + 新前端时走 `total = null` 的推估分支。

## Alternatives considered

- **前端改为请求 `limit = pageSize + 1` 探测下一页，不改后端。** 最强理由：零契约变更。被否：仍然拿不到真实总数，「共 N 条」与页数依旧是猜的，用户的问题没有解决。
- **改成 `page / page_size` 参数并回传 `page / page_size`（像 `payment_orders.py`、`CodeListResponse`）。** 最强理由：与部分后台列表完全一致。被否：要改查询参数，旧前端的 `skip / limit` 请求会失效，部署有时间差时会坏；只加 `total` 是能修好问题的最小改动。

## Consequences

- 收益：总数与页数在第一页、第二页、最后一页和搜索后都等于真实的匹配数；翻页顺序稳定。
- 代价：每次列表请求多一次 `COUNT(*)`；用户表规模（百级到万级）下可忽略。
- 代价：前端保留一段只为部署时间差存在的 `total = null` 推估分支；全量部署后可以删除。

## Verification

`cd apps/server && python -m pytest tests/test_api/test_admin.py tests/test_api/test_admin_review_boundaries.py tests/e2e/test_admin_management_e2e.py -q -p no:cacheprovider --no-cov`（`test_get_users_total_is_stable_across_pages_and_follows_search`：25 个用户、`limit=10` 时三页 `total` 都是 25、各页 10/10/5 条且不重复，`search` 后 `total` 为匹配数）；`cd apps/web && pnpm exec vitest run src/pages/admin/__tests__/UserManagement.test.tsx src/lib/__tests__/adminApi.test.ts`（92 个用户时 1-20 / 21-40 / 81-92 均为共 92、页数 5，搜索后总数改变并回到第 1 页，旧数组 payload 走推估）；`apps/web/e2e/admin-users-mocked.spec.ts` 在浏览器里验证同样的显示。
