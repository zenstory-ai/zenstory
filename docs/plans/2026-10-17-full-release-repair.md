# Full E2E 修复与发布追踪

用户已授权继续修复此前全量回归失败，并在完整验证通过后继续原正式发布流程。修复基线 `02049f03429c19db0f3d785afebd17085d3889db`；上次失败 run `39062687687`：Chromium/Firefox 共 298 failed、22 flaky，不等同于 298 个独立产品问题。

## Tasks

| ID | 状态 | 范围 | 完成条件 |
| --- | --- | --- | --- |
| R1 | 进行中 | 失败去重及未审日志归因 | 每个失败归入实际根因；未知行为保留独立条目，失败原件不删除 |
| R2 | 修复中 | skills / voice / performance / large-document 旧定位与数据准备 | 使用实际语义/test ID，原业务/性能阈值不削弱；双浏览器定向通过 |
| R3 | 修复中 | 认证、Firefox 导航与共享数据 | 先证明竞争/数据原因，稳定等待真实状态；不吞导航异常、不盲重试、不放宽超时 |
| R4 | 修复中 | chat / concurrent / deep-link | 独立合成项目、真实 API 准备、可控 SSE；无效/越权项目明确错误或安全跳转；无真实付费调用 |
| R5 | 修复中 | Linux 视觉基线 | 固定 Playwright 镜像、确定性项目/消息和时钟，逐图人工/独立视觉检查，不批量接受实际图掩盖差异 |
| R6 | 等待用户登录，其他继续 | Vercel 403 与发布准备 | 仅认证只读诊断；复核正确项目、身份、source；不改账号/权限/配置来绕过错误 |
| R7 | 待验证 | 独立代码/架构审阅与本地完整回归 | 单测、集成、lint/token/i18n/typecheck/build 通过；本次修复族首轮通过、无新增/扩大 skip/fixme/only；不增加 timeout/retry、不降低业务/性能/视觉阈值；重试后通过仍视作待修 |
| R8 | 待执行 | PR 与最终 main 发布证据 | 正常 PR、无 admin bypass；最终 main/tag 候选同一完整 SHA 的 main-push CI/E2E summary 成功，并有同 SHA manual full Chromium/Firefox 成功；记录 run ID/attempt/head SHA，不重复触发已通过同源工作流 |
| R9 | 待执行 | 正式发布与观察 | 有迁移则 tag 前完成备份、兼容升级及 readback；涉及 worker 则 tag 前 pause/drain，tag 触发原流程部署后，replacement heartbeat 且旧 deployment REMOVED 才 resume；观察 Actions、provider source 与只读 smoke |

## Cleanup plan and constraints

1. 本计划作者为根代理；先由独立 reviewer 审阅边界后开始源码修复，完成后再独立 code-reviewer/architect 复审，不由作者自批。
2. 先保留已有失败及定向红灯复现，再每次只修一个根因；优先已有语义控制、API fixture 和页面模式，不新增依赖或通用框架。
3. 仅在确有重复和状态契约时复用认证/合成数据 helper；不以全局拦截 goto、强制等待、吞异常或全局重试掩盖行为。
4. 真实产品缺陷须先补针对性回归；最小修复并保护草稿、文件版本、配额退款、鉴权、SSE 取消和 billing flush。
5. 并行 ownership 不重叠；需要修改他人文件先向根代理报告。根代理负责证据整合、视觉真值、最终执行门禁。
6. 每测试使用唯一且 test-owned 的 API fixture，仅清理自己的数据。已关闭的付费/未支持浏览器能力不能假装验证：既有 skip 逐项核对原因及替代覆盖，不允许本次必需行为因缺项目等 fixture 条件跳过；不接受未解释 skip 或 flaky 作为发布证据。
7. 普通回归禁止任何真实模型请求，不仅禁止计费。所有生成/识别/工具结果采用明确的确定性 SSE/mock；意外 provider 外呼应 fail-closed，不能通过假 key 发往真实 provider，也不声称已验证 live models。共享 fixture/视觉策略变动先写 testing 决策说明。

## Independent plan review

架构 reviewer 首轮指出最终 main/full SHA 绑定、worker 发布顺序、模型禁外呼与条件跳过四个缺口，已逐项补入 R5/R7/R8/R9 和约束 6–7，复核后才进入实现。

## Evidence

- 原始报告与 13 份失败 artifacts：`/Users/pite/.codex/scheduled-jobs/zenstory-release-20261017-1228/`。
- 本次日志、截图与机器收据：`/Users/pite/.codex/outputs/zenstory/full-release-repair-20261017/`。
- 本地使用独立 worktree；测试合成数据、假 provider keys、禁用向量与真实支付。生产写入仅在所有前置门禁通过后按原授权/runbook 执行。

## Progress

- 独立计划审阅首轮提出四项修正，补齐后结论 CLEAR，随后开始三条不重叠实现 lane；不是把作者方案直接视为批准。
- 原 Firefox trace 证明连续 hard navigation 打断 lazy import，触发 chunk recovery 后再反向中止导航；认证 helper 已改为静态同源文档写认证后，单次进入 dashboard 并等待真实可交互契约。仍单独核实用户快速导航时的 lazy recovery 产品风险。
- 发现既有 `TEST_USERS.pool` 未由 seed 脚本创建；本地五个免费合成账号已补齐，真实 auth API 5/5 登录验证。CI 准备仍须随修复同步，不把仅本地数据修复当最终交付。
- Linux runner 与浏览器固定在同一 Playwright 1.58.0 noble 镜像，单 worker、retries=0、共享运行锁、私有 auth 输出；本地 flag 修正后两引擎代表 smoke 5/5 通过。旧配置首次失败保留，未修改测试期望放行。
- Vercel 当前 `whoami` 明确报告 token 无效，已异步请用户通过其本机 `vercel login` 恢复；不索取或改写 token，不阻塞本地测试修复。

- 已修复并锁定两个实际产品缺陷：导出 pending 禁用（Header 61/61）及搜索空/加载分支 outside-click（新增回归先红，组件 29/29 绿）。浏览器仍需按最终代码复验。
- CI 同一 seed/guard 源码已在独立本地库执行，五个 pool 账号真实登录 5/5、health=200，egress guard 3/3 行为测试及 Ruff 通过。生产入口不变。
- 原错误恢复用例存在未实现的自动 backoff 假设及 65 秒等待超出 60 秒测试限制；改为真实显式重试/真实超时终态契约，详见 testing note。未增加 retry/timeout 或放宽阈值。

- 前端完整单测 fresh：303 files / 4741 passed / 1 existing skipped；lint 0 errors / 3 existing warnings，token/i18n keys 检查通过，tsc/Vite/org/docs 构建通过。
- 错误恢复 11 场景×2 引擎全部通过（23 tests incl setup，retries=0）；CI workflow contracts 21/21、guard 3/3 和 Ruff 通过。
- 10 张 Linux 确定性视觉候选全部生成并由作者逐张查看，已记录哈希；独立 designer 审阅及无更新比较仍待完成，不把候选自动视为批准。
- 文件拖拽 UI 与 VirtualizedEditor 模式在当前及上一生产版本均未实现。对应旧测试改为准确命名的既有 API move / SimpleEditor 大正文完整性覆盖，不扩写未请求功能、不将 API/简单编辑器覆盖声称为拖拽/虚拟化。深链接未知/越权页面的红灯另行调查，不能归入这些旧假设。
