# Vercel 依赖审计：未修补的开发期公告进白名单

## Problem

CI 的 vercel-build 用 `npm audit --audit-level=high` 拦截高危依赖公告。2026-11-17 GHSA-fc5q-39xc-vjpf（picomatch 对攻击者提供的 glob 存在 ReDoS）更新为「4.0.7 及以前全部受影响、没有修补版本」，之后所有 PR 和 main 的 CI 都失败，生产发布门槛也随之卡住。picomatch 只经由构建和测试工具（vite、vitest、rollup、typescript-eslint、rollup-plugin-visualizer）进入依赖图，`npm audit --omit=dev` 为 0；这些工具只解析仓库里写死的 glob。

## Decision

- 审计步骤改为 `apps/web/scripts/audit-gate.mjs`：高危和严重公告照样失败，只有列在 `apps/web/audit-allowlist.json` 的公告放行。
- 放行同时要求：这条公告不出现在 `npm audit --omit=dev` 的结果里（只到开发期依赖），并且没过 `review_by` 日期。到期或进入运行时依赖就重新失败。
- 每条白名单写明公告 ID、包名、理由和复查日期。当前只有 GHSA-fc5q-39xc-vjpf，复查日期 2026-12-17。

## Alternatives considered

- **只审计运行时依赖（`--omit=dev --audit-level=high`）。** 最强理由：最简单，前端运行时才是用户暴露面。未采用：会一并放过构建链上将来真正可利用的高危公告（例如构建时处理外部输入的工具），失去对供应链的覆盖。
- **开发期依赖改成只拦严重级别（`--audit-level=critical`）。** 最强理由：一行改动。未采用：同样对所有开发期高危公告一刀切，没有逐条理由和到期复查。
- **用 audit-ci 等现成工具的白名单。** 最强理由：社区维护、功能更全。未采用：AGENTS.md 要求不新增依赖；所需逻辑只有几十行。
- **降级或替换 picomatch。** 未采用：没有不受影响的版本；替换会牵动 vite / rollup 等核心构建工具。

## Consequences

- 收益：CI 和发布门槛恢复；运行时依赖与未列入的开发期高危公告仍然拦截。
- 代价：多一个脚本和一份白名单要维护；白名单到期需要有人复查（有修补版本时升级并删除条目）。
