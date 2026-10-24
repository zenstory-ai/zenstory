# PR305 合并前安全审计修复

## Problem

PR305 的后端、Prefect 合同与本地回归已通过，但 required CI 的 npm high-level audit 被已有锁定依赖拦住，不能靠重跑或跳过检查合并。fresh audit 在本批修改前仍 exit 1：happy-dom 20.11.6 落入 GHSA-834v-8h66-3h79，npm 锁中的 zod 4.4.3 / pnpm 锁中的 4.3.5 落入 GHSA-mrhx-c7xc-m3w5。用户授权审查、修复后逐个合并，故补入必要门禁修复；不改变 Agent 或 UI 源码。

## Decision

- happy-dom 升到 `^21.0.4`，这是该高危问题首个修复版本；同步 npm 与 pnpm 两份锁。Vitest 4.1.11 的 happy-dom peer 为 `*`，Node 最低要求仍为 >=20，不联动升级 Vitest/MSW。
- 对既有 Zod 4.x 增加范围 override：`zod@>=4 <4.6.7` → `4.6.7`，两处包管理器配置一致。最低安全版本 4.6.6 有已确认 union 错误码回归，选择修复该回归的下一个 patch 4.6.7；不强迫潜在旧 3.x 使用方跨 API major。
- 用 CI 同版 pnpm 10.29.3 生成根锁，npm 以 `--package-lock-only --ignore-scripts --legacy-peer-deps` 生成，匹配 CI 的 `npm ci --legacy-peer-deps`；只接受目标节点及相应 peer snapshot key 的变化。
- 保留原 audit threshold、覆盖率、测试断言和 CI 门禁。更新后的真实依赖图必须跑前端全量测试、lint、typecheck/build、npm clean install 与 high-level audit，不能拿旧 node_modules 的绿当新图证明。
- Happy DOM 21 默认不加载 CSS 文件与 iframe 页面是安全 hardening，不为旧测试盲目打开网络加载。失败先判断测试模拟与实际产品合同，不降低断言。

Primary sources：[Happy DOM advisory](https://github.com/advisories/GHSA-834v-8h66-3h79)、[21.0.4 release](https://github.com/capricorn86/happy-dom/releases/tag/v21.0.4)、[21.0.0 changes](https://github.com/capricorn86/happy-dom/releases/tag/v21.0.0)、[Zod advisory](https://github.com/advisories/GHSA-mrhx-c7xc-m3w5)、[union regression](https://github.com/colinhacks/zod/issues/6659)、[4.6.7 release](https://github.com/colinhacks/zod/releases/tag/v4.6.7)、[npm ci lock flags](https://docs.npmjs.com/cli/v9/commands/npm-ci/)。

## Alternatives considered

- `npm audit fix --force` 或全量升级：最快消除更多告警，但可能改写整张依赖图与测试栈；拒绝，采用可审计的精确目标。
- 把测试环境迁到 jsdom：可以去除 happy-dom，但改变 DOM/网络模拟语义，范围比必要安全升级大，拒绝。
- 只修 npm 锁：可使 Vercel job 绿，但大部分测试使用 pnpm，留下两套不同安全图，拒绝。
- 只选 Zod 4.6.6：最小安全号，但已知错误呈现回归不必引入，选择紧邻修复 patch。
- 借机升级 Vitest 和所有旧依赖：有独立安全收益，但不是 high-level 门禁的必要条件，本批不展开。

## Consequences

解决本批两个明确高危节点和 required npm audit，不宣称整个仓库零漏洞。Vitest moderate 仍保留；pnpm 图已有 follow-redirects/vite 高危不属于本次 npm 门禁根因，单独记录后续事项，不假称 pnpm full-audit 全绿。无新依赖包、无 Agent 模型/提示/预算/重试/计费变化，无生产操作或 tag。本地 fresh 证据：修改前 npm audit high-level exit 1；clean `npm ci --legacy-peer-deps` 后 audit exit 0（0 high/critical，4 moderate）；pnpm 10.29.3 frozen install 后实际 happy-dom 21.0.4 / zod 4.6.7。前端313文件4903通过/1跳过、覆盖率门禁、lint/token/i18n、TypeScript与生产构建通过，CLI112通过并构建成功。npm 锁语义变化仅根元数据、happy-dom、zod 三节点；原测试断言与门槛未改。实际合并仍依据更新后的精确 SHA 的 CI/E2E，而非本 note 代替远程证据。
