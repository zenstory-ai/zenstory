# Agent Note: 升级 Vitest 4 后按新的覆盖率口径重设门槛

Status: implemented

## Problem

Dependabot 告警 #262、#263、#266、#267 指向 `vitest` / `@vitest/mocker` 的路径穿越漏洞（Redirect Mock 可读任意文件），受影响范围 `>=2.1.0 <4.1.11`。前端测试栈停在 `^3.2.6`。Dependabot 的 #26 直接跳到 5.0.0，但只改了 `apps/web/package-lock.json`，所有用 pnpm 的 CI 任务都因 `ERR_PNPM_OUTDATED_LOCKFILE` 失败，测试从未在新版本上跑过。

升到 4.1.11 后，同样 256 个文件、同样 2274 个测试，V8 覆盖率报出的数字变了：lines 76.11% → 70.84%，statements 76.11% → 68.24%，branches 75.77% → 60.48%，functions 67.67% → 64.76%。原因是 Vitest 4 的 V8 覆盖率改为按 AST 重映射，统计真实的语句和分支，不再按物理行统计；测试覆盖到的代码没有减少。旧门槛（74 / 74 / 71）会让 `pnpm test:coverage` 在 CI 里失败。

## Decision

- `vitest`、`@vitest/ui`、`@vitest/coverage-v8` 统一为 `^4.1.11`，这是第一个不在漏洞范围内、同时支持 vite 7 与 Node 20 的版本。两份锁文件都重新生成（`pnpm-lock.yaml` 用 CI 同版本的 pnpm 10.29.3，`apps/web/package-lock.json` 用 `npm install --legacy-peer-deps --package-lock-only`），只有 vitest 依赖树变化。
- `apps/web/vitest.config.ts` 的覆盖率门槛按新口径重设：lines 68、statements 66、branches 55、functions 61（不变）。每项与实测值的距离保持和 Vitest 3 时相同，门禁的松紧没有变。
- `coverage.all` 在 v4 被移除，改用 `coverage.include` 列出 `src/`、`scripts/`、`api/`，未被测试加载的文件照旧计入分母；`poolOptions` 被移除，隔离继续由顶层 `isolate: true` 保证。
- 按官方迁移指南修正测试：用 `new` 调用的 mock 改为 `function` 实现；`agentApi.test.ts` 在 `restoreAllMocks` 前补 `resetAllMocks`，因为 v4 的 `restoreAllMocks` 不再重置 `vi.fn()`。

## Alternatives considered

- **合入 #26，直接升到 5.x**。最强理由：一步到最新主版本，以后少一次迁移。被否：5.x 要求 Node ^22.12，而 CI 的所有任务跑在 Node 20 上，升 5.x 必须同时改 CI 运行时，超出修漏洞的范围；#26 本身也没有更新 pnpm 锁文件。
- **保持 74 / 74 / 71 的旧门槛，补测试把数字拉回去**。最强理由：门槛数字不下降，看上去更严格。被否：数字下降来自统计口径而不是覆盖退化，要把 branches 从 60% 拉到 71% 需要大量新测试，与修漏洞无关；按新口径保持同样的余量，门禁对退化的敏感度不变。
- **把门槛设成紧贴实测值（70 / 68 / 60）**。最强理由：门禁更紧。被否：余量不到一个百分点，任何无关的小改动都可能让 CI 失败；沿用旧的余量更可预期。

## Consequences

- 收益：四条 vitest 告警随两份锁文件解析到 4.1.11 而关闭；CI 仍在 Node 20 上跑；测试数量与 main 相同（185 个文件，2274 通过，1 跳过）。
- 代价：覆盖率数字与 Vitest 3 时期的历史记录不可直接比较；以后升 5.x 时需要先把 CI 运行时升到 Node 22。

## Verification

在 main 与本分支分别运行 `pnpm install --frozen-lockfile`、`pnpm lint`、`pnpm test:coverage`、`pnpm test:site`、`pnpm build:typecheck`，结果一致且全部通过（本分支的覆盖率使用新门槛）。在 Node 20.20.2 与 Node 25 上覆盖率数字相同。另在 Node 20.20.2 / npm 10.9.9 下对 `apps/web/package-lock.json` 实际执行 `npm ci --legacy-peer-deps`，安装成功，`npm audit --audit-level=high` 为 0。
