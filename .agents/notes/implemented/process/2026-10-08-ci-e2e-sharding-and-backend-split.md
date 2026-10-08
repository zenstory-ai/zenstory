# Agent Note: E2E 八分片与后端测试拆分并行

Status: implemented

## Problem

合并到 main 后，CI（约 9 分钟）与 E2E（约 26–29 分钟）并行，E2E 是发布关键路径，修复到上线整体约 50–60 分钟。2026-10-08 的实测：

- E2E push：两个 chromium 分片，CI 下 `workers: 1`、`fullyParallel: true`、重试 2 次。b997aa1 一次中分片 1 跑 377 个测试 15.5 分钟、分片 2 跑 376 个测试 19.8 分钟；0211072 一次中分片 2 达 26.1 分钟。慢文件：projects.spec.ts 4.4–10.7 分钟、points.spec.ts 4.1–4.3、security.spec.ts 2.8–3.6、responsive-pages-mocked 2.3–2.5、admin.spec.ts 2.0、mobile 1.4–1.6、accessibility 1.4–1.5、material-library 1.3。每个分片还要约 2.3 分钟准备（Setup backend 约 1.2 分钟、安装浏览器约 0.4 分钟）。
- Playwright 1.58 的 `--shard` 按测试数量把文件顺序（字母序）切成连续区间，不按时长平衡；points.spec.ts 与 projects.spec.ts 相邻（列表第 420–486 个测试）。
- CI backend-test 约 8.7 分钟：安装依赖 0.8 分钟，`pytest --cov=. ... -v` 6.2–6.4 分钟（5132 个收集、4847 通过、285 跳过，4 vCPU `-n auto`），之后串行跑 PostgreSQL 回归约 0.5 分钟、flow 测试、Prefect 部署注册。按 b997aa1 日志中 xdist 每行时间戳估算，tests/test_api 约占单元测试时间 60%，其中 `test_a*` 至 `test_f*` 文件约 470 worker 秒，其余 API 文件约 370，其他目录合计约 490。
- `workflow_dispatch` 的 nightly 套件矩阵只有 `1/1`，但运行守卫只接受 `1/2`，因此手动 nightly 实际什么都不跑却显示成功；schedule 的 Firefox 作业和第二分片也只做准备后直接退出。

## Decision

E2E（`.github/workflows/e2e.yml`）：push、`default`、`full` 三条整套测试的车道改为 8 个分片 `1/8`…`8/8`（`full` 仍加 Firefox）；schedule/nightly、release、smoke 只跑固定文件列表，统一使用单个 chromium `1/1` 作业，nightly 守卫改为 `1/1`。schedule 不再生成只会空跑的 Firefox 与额外分片作业。报告产物名仍用 `strategy.job-index`，保持唯一；`e2e-summary` 读取矩阵聚合结果，校验逻辑不变。未加浏览器或后端缓存：仓库没有已固定的 `actions/cache`，`--with-deps` 的系统包安装仍需执行，收益只有十几秒。

CI（`.github/workflows/ci.yml`）：

- `backend-test` 改为三分片矩阵：`api-a-f` 跑 `tests/test_api/test_[a-f]*.py`；`api-g-z` 跑 `tests/test_api --ignore-glob=` 同一个 glob；`other` 跑 `tests --ignore=tests/test_api`。新增目录自动落入 `other`，三者按构造恰好覆盖原来的单次运行。每个分片 `COVERAGE_FILE=.coverage.<shard>`、`--cov-fail-under=0`、`-q`（抵消 pytest.ini 的 `-v`，保留 `--durations=15`），并上传 `backend-coverage-<shard>` 产物（`if-no-files-found: error`）。
- `backend-coverage` 在全部分片成功后下载产物，确认恰有 3 份数据，`coverage combine` 后生成 `coverage.xml` 供 Codecov，并以 `coverage report --fail-under=80` 执行 80% 门槛。
- `backend-integration` 与分片并行，运行 ruff、串行 PostgreSQL 回归、flow 测试和 Prefect 部署注册，步骤与命令不变。
- `ci-summary` 需要并校验 `backend-test`、`backend-coverage`、`backend-integration`；`scripts/ci/check-workflow-results.mjs` 用导出的 `ciJobExpectations` 把三者都设为 backend 或 CI 变更时必需、否则必须跳过。detect-changes 字面输出、main 不取消、生产晋级对 exact-SHA 的 `ci-summary`/`e2e-summary` 要求都不变。

回归：`scripts/ci/coverage-artifact-contract.test.mjs` 检查分片按构造划分测试树、每个分片的覆盖率文件和产物名唯一、合并任务的份数与矩阵一致、门槛与 Codecov XML 来自合并数据；`check-workflow-results.test.mjs` 检查 ci.yml 中每个作业都在 `ci-summary` 的 needs 和校验器里，且合并任务被跳过时判失败；`workflows.test.mjs` 检查 E2E 分片列表连续、固定文件车道的守卫接受 `1/1`。

## Alternatives considered

- 按用户最初设想用 6 个 E2E 分片：作业更少，免费组织 20 个并发作业的余量更大。但按测试数切分时第 4 片恰好同时包含 points 与 projects，估算测试时间 9.6 分钟（慢的一次达 16 分钟），加准备约 12–18 分钟，达不到 8–10 分钟目标；8 片时两者大多分开，估算最大分片约 6.5 分钟（慢的一次约 9 分钟），墙钟约 9–11 分钟。
- 用 `PWTEST_SHARD_WEIGHTS` 做加权分片：能用更少作业平衡时长；但它是 Playwright 内部测试用环境变量，权重绑定测试数量，新增测试后会悄悄失衡，因此不用。
- 把 CI 的 Playwright `workers` 从 1 调高：不增加 runner 就能并行；但所有测试共用一个后端和同一个种子用户，这正是当初设为 1 的原因，风险超出本次范围。
- 后端只按目录拆成 test_api / test_agent / 其余：最直观；但 test_api 单独约占 60%，仍是最长分片，所以对 test_api 按文件名首字母再拆一次，并用同一个 glob 的正反两面保证不漏不重。
- 删除重复的 flow 测试步骤（tests/test_flows 已包含在主套件里）：能省一次运行；但它在并行的 `backend-integration` 中、不在关键路径上，且以 `--maxfail=1` 单独提供 flow 证据，因此保留。

## Consequences

收益：预计 E2E 墙钟由 26–29 分钟降到约 9–11 分钟，CI 由约 9 分钟降到约 5–5.5 分钟（最长后端分片约 2 分钟测试加 1.5–2 分钟准备，再加约 1 分钟合并），修复到上线时间随之缩短约 15–20 分钟；手动 nightly 不再空跑，schedule 不再生成空跑作业；单元测试日志不再逐条打印 5000 行。

代价：每次后端相关推送多用 3 个 runner（各自安装依赖、启动 PostgreSQL/Redis），E2E 由 2 个作业变为 8 个，每个都要约 2.3 分钟准备，总 runner 分钟增加；backend、frontend、CI 同时变更时 CI 与 E2E 峰值约 22–24 个作业，超过免费组织 20 个并发上限的作业会短暂排队。各分片终端会打印各自不完整的覆盖率表，以 `backend-coverage` 的合并结果为准。E2E 分片按测试数切分，慢文件位置变化会改变最长分片，需要按 "Slow test file" 输出复核。

## Verification

`node --test scripts/ci/*.test.mjs`（全部通过），并逐一做变更反证：改动 API 补集 glob、删除 `ci-summary` 中的 `backend-integration`、给 `other` 多加一个 `--ignore`、把合并份数改为 2、把分片门槛改为 80、把 nightly 守卫改为 `1/8`、删掉 `8/8`，相应测试都失败。`yamllint .github/workflows/ .github/actions/` 通过。本地仅做收集：`pytest --collect-only` 全量 5132 个，三个分片 895 + 821 + 3416 = 5132，节点 ID 排序后与全量完全一致。用两个小测试文件验证：pytest-cov 7.1 在 `-n` 下写出的正是 `COVERAGE_FILE` 指定的文件；`coverage combine <dir>` 合并后 TOTAL 语句数为 29307，与 CI 全量报告一致（未执行文件已计入）；`coverage xml` 也会执行 `.coveragerc` 的 fail_under，所以显式传 `--fail-under=0`，门槛只由 `coverage report --fail-under=80` 执行。实际耗时以合并后第一次 main 推送的 CI/E2E 运行为准。
