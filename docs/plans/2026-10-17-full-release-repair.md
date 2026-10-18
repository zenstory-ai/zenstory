# Full E2E 修复与发布追踪

用户已授权继续修复此前全量回归失败，并在完整验证通过后继续原正式发布流程。修复基线 `02049f03429c19db0f3d785afebd17085d3889db`；上次失败 run `39062687687`：Chromium/Firefox 共 298 failed、22 flaky，不等同于 298 个独立产品问题。

## Tasks

| ID | 状态 | 范围 | 完成条件 |
| --- | --- | --- | --- |
| R1 | 进行中 | 失败去重及未审日志归因 | 每个失败归入实际根因；未知行为保留独立条目，失败原件不删除 |
| R2 | 修复中 | skills / voice / performance / large-document 旧定位与数据准备 | 使用实际语义/test ID，原业务/性能阈值不削弱；双浏览器定向通过 |
| R3 | 修复中 | 认证、Firefox 导航与共享数据 | 先证明竞争/数据原因，稳定等待真实状态；不吞导航异常、不盲重试、不放宽超时 |
| R4 | 修复中 | chat / concurrent / deep-link | 独立合成项目、真实 API 准备、可控 SSE；无效/越权项目明确错误或安全跳转；无真实付费调用 |
| R5 | 10 图独立接受，待最终比较 | Linux 视觉基线 | 固定 Playwright 镜像、确定性项目/消息和时钟，逐图人工/独立视觉检查，不批量接受实际图掩盖差异 |
| R6 | 等待用户登录，其他继续 | Vercel 403 与发布准备 | 仅认证只读诊断；复核正确项目、身份、source；不改账号/权限/配置来绕过错误 |
| R7 | 审阅返修中 | 独立代码/架构审阅与本地回归 | 单测、集成、lint/token/i18n/typecheck/build 通过；本次修复族首轮通过、无新增/扩大 skip/fixme/only；不增加 timeout/retry、不降低业务/性能/视觉阈值；重试后通过仍视作待修 |
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

## Independent review corrections

- 精确源码 `f4ae0b8` 的独立 code-reviewer 为 REQUEST CHANGES、architect 为 BLOCK；产品三处最小修复本身无 blocker，主要缺口是测试断言和共享 fixture，不把静态审阅当运行批准。
- C1：并发第二次 stale save 必须 409、明确 review controls、原服务端正文保留；删除原状态/正文并集断言。
- C2：当前产品无删除会话 UI；移除三个虚构的 browser deletion/confirmation 用例（每引擎少 3），不补占位断言。真实持久 session+3 messages 删除、计数归零及 ledger 保留由既有后端测试覆盖，定向 fresh 2/2 通过。
- C3：navigation/projects/security/performance 不得任意清共享项目或复用 projects[0]；改为明确 owner+唯一 ID，清理仅限自己的数据。pool 使用 stable parallelIndex，不以重启后的 workerIndex modulo 复用账号。skills 两套测试仍使用既有专用合成账号及 test-owned 前缀清理，所以每个 runtime 默认固定 worker=1；CI 分片仍并行且各自有独立数据库，不宣称本地多 worker 的共享 skills 数据隔离。
- C4：认证 helper 每次真实 API 登录，不无限缓存旋转前 token、不伪造新 validation 时间；deep link 只能实际自动恢复原 URL/file，禁止测试手动导航补救。
- C5：未配置语音应验证实际 recognize 返回的配置错误；不为旧测试新增未请求的 preflight 产品行为。录音硬件可用确定性音频源替代，保留真实 MediaRecorder/normalize 路径并明确不验证硬件/真实 ASR。
- C6：补 asyncio 外呼边界回归，DNS+directIP 都必须在原始 IO 之前拒绝；guard 5/5 通过。最终 `f4ae0b8` 前端单测再次 fresh 4741 passed / 1 existing skipped，完整 typecheck/build 通过。
- 发布仍以 operator 的精确 SHA full/0 flaky 收据为必需门禁；本修复不扩写 production promotion 流水线，自动化加强留作后续独立范围。

## Current verification batch

- 源码/运行分阶段：所有 author 先完成修复并报告 ready，再冻结源码串行运行 browser batches；运行期间不生成默认 HTML report、不修改 UI/test 文件。开发态 Vite 实际记录了 playwright-report HTML full reload 与源码 CSS HMR，故受影响中间轮只用于归因，不作最终源验证。
- 已独立接受 Linux 10/10 精确字节（8 张不变，2 张消息态正确为 1/3），本地独立提交 `ff8f10ac`，尚未 push。`--update-snapshots=all` 仅限定那两项，用于真实替换阈值 100 内的额度数字差异，未放宽阈值。
- 严格并发首轮 4 项均已验证真实 409/token 与响应中的 winning content；独立 API 持久正文断言位于按钮后，仍需最终运行。最后按钮因 allReviewed 为「应用更改」而非「完成审阅」需匹配同一条件控件；不移除控件或冲突断言。自动登录回跳原 file URL 两引擎已实际通过。
- 卡片用例不能将创作灵感当项目名称：真实创建响应为 `name:我的小说, description:null`，灵感另属初始创作 intent。回访卡片改为唯一 API-owned 名称和真实打开按钮语义；UI 创建本身仍单独测实际 POST/导航。
- Dashboard 的真实滚动容器为 main，既有 pathname 切换逻辑重置 scrollTop；旧 window.scrollY 保存断言不成立。准确覆盖实际重置契约，不新增未请求的滚动记忆功能。
- 不临时新增一条必红拖拽测试再删除：当前及上次生产源码无拖拽处理器，已有真实失败和静态边界已足够说明只验证 API move。最终 17 UI + 5 API 场景照常运行。

- 最终源码准备：补技能选择 button 的 test ID/aria-pressed，并真实验证单选/全选状态；语音录到 0:01 后才主动停止，55 秒自动截止用浏览器时钟驱动，不再用时长文本代替截止验证。保留 mobile/native-WAV 的 fake microphone source，live ASR 未覆盖。
- 聊天连续性覆盖真实 `session_started → 下一 POST session_id`，捕获实际请求体验证两次请求；固定模型回复不是记忆证明。文件事件/工具结果用例按真实 UI 协议命名，不声称执行了后端工具或生成 diff。
- 新增选择语义后产品相关 5 个文件单测 fresh 170/170、完整 typecheck/build 通过；全 lint 0 error/3 既有 warning、token/i18n 检查通过。额外 strict E2E compile 找到两个仅类型问题（network Response/APIResponse 区分、窗口 fixture 状态可选声明），已按真实类型修正，不改变运行期容错。

- `663c7081` 独立架构静态 CLEAR，代码复审又指出 4 处弱断言，已按实际契约补齐：不支持 session query 的文件链接兼容，不声称历史会话深链；恶意 URL 直接收集 dialog/pageerror 并要求均为空；语音两个错误必须命中一次真实形状的 recognize response 和明确安全提示；刷新必须真实 401→refresh POST→恢复 token/保护页，不能接受 login。
- 认证缺失 access 的既有策略是清会话；有效 refresh 的支持路径是 access 被拒绝且缓存验证过期。测试采用该路径，不扩写产品恢复策略，也不将清空两 token 的用例称为 TTL 验证。
- 首个冻结候选浏览器 99 项：80 passed、18 既有 capability/manual skipped、1 failed，源码收据 clean/stable。仅 55 秒录音测试先跳时钟却未等 native 音频 chunk，再等待瞬态 spinner；已改为真实录到 0:01、0:54 仍录音、0:55 自动停止且一次识别完成的终态断言。没有降低门槛或将失败计为通过。

- `0dd4f83f` 最后审阅 delta 双引擎 47 passed / 18 既有 capability/manual skipped / 0 failed / 0 flaky（含 setup），clean/stable 收据；真实刷新、安全深链和语音错误/55 秒截止通过。代码独立复审 APPROVE、架构 CLEAR（静态）。草稿 PR #259 已提交，分支 full 16 shard 正在运行。
- skip 逐项审计发现正文上传仍借用任意项目并可能跳过，改为每例 owned fixture、实际 filechooser 和准确 API 字节断言；footer 两引擎共同覆盖保存/选择/滚动，Chromium 的 CDP pinch 单独标记 step，Firefox 不再以标题声称已测 pinch。没有新增 skip 或修改产品。

- `86b5724d` 上传/中英 footer/10 图无更新比较，两引擎 31/31 首轮通过、无 skip/flaky。PR 首次 CI 的 frontend-test 4741 passed / 1 failed / 1 既有 skipped；失败 DOM 显示历史列表仍 loading，旧测试只等外层即同步取 rows。改为等待真实 rows/action，不重跑原失败放行、不动产品。

- PR `56ac88d9` CI 已成功。冻结 files 首轮 42 passed / 3 failed：两例为空状态旧文案，另一例 Firefox 在 AuthProvider loading；trace 表明 fixture 登录 127.0.0.1 成功、浏览器 localhost 的 /auth/me 一直 pending，测试启动现统一 API host。分支 full 又复现未提交项目页面就 back、Dashboard logout 控件错误、versions 自有 double-hard-navigation；分别等待真实项目就绪、使用实际 account panel 退出契约、复用 owned 版本 fixture，均不修改产品/超时/重试。

- `fbcc2861` 67 项定向为 63 passed / 4 failed：导航 reset、空目录及大多数版本用例已通过；退出真实终态为 AuthContext 事件触发的 `/login`，不能只据 Dashboard 的 `navigate("/")` 推断；无编辑版本接口明确 0，应等实际空状态而不是旧 OR 断言；另有一次本地 Vite index.css pending 导致 setup DOM load 30 秒，保留 trace 待后续同用例实跑验证，不声称已定位产品根因。
- 首作 Firefox 测试必须让每例 context locale 与 zh/en 业务语言一致，保留 exact Accept-Language 断言，不在 mock 改写 header。跨标签认证单次 flaky 已用预定 20×2 首轮压力诊断复现（20 Chromium + 3 Firefox 通过，第 4 Firefox 失败即停，16 未跑），已有真实失败 trace，正在调查锁后 token 交接，不能视作重试后通过。

- 跨标签认证已有确定性单测先红后绿（14/14，API client 3 文件 74/74），按架构独立评审的最小方案保持同一 Web Lock 跨一次 MessageChannel 任务检查点，并在返回成功前重验会话归属；没有新增协调协议/依赖/网络重试，无锁分支不变。真实双引擎压力回归仍待完成。
- 本地测试容器发现 764 个 PPID=1 的浏览器僵尸进程，缺少 init 回收；已保留进程清单，并仅重建 owned Playwright 容器为同镜像/挂载/端口加 `--init`，僵尸降为 0，web/API 200。此前 CSS pending/30 秒 setup 超时不作为产品修复证明，须在正常回收的运行环境复验。

- 精确候选 `c0d67a47` fresh：303 前端单测文件 / 4,747 passed / 1 既有 skipped，完整 typecheck/build、lint 0 error/3 既有 warning、token/i18n 检查通过。独立 code-reviewer APPROVE、architect 静态 CLEAR。
- 原生双标签四场景×20 轮×两引擎全部首轮通过：161/161 incl setup、retries=0、无 flaky。含单次 POST 恢复两个原请求及 logout/other-user/new-family 不恢复旧会话。先前真实 Firefox 失败不再复现；新 note 移至 implemented，旧决策未反写。全量 main / provider 门槛仍未完成。

## Exact-main tail repair

PR #259 已合并为 `3c03449d`，精确 push CI/E2E/CodeQL 成功（默认 E2E 836 passed / 242 opt-in/既有 skipped / 0 failed/flaky），随后仅一次 dispatch full `39090867679`：2098 passed / 1 failed / 53 skipped / 4 flaky；该轮保留为失败证据，不放行。

- [x] 归因历史 compare 全局选择器、跨会话/Accessibility 未提交导航、订阅 reload 后重复登录及滚动 RPC 计时。
- [x] 最小修复对应 5 个 E2E 文件，产品源码/重试/超时/阈值/skip 不改；版本至少两行改为断言，移除隐藏比较的条件空跑。
- [ ] 冻结双引擎相关文件实跑及独立审阅；新 PR 正常合并后，再按新精确 main push→full 顺序验证。
- [ ] Vercel 登录尚未恢复；生产备份/迁移、worker pause/drain 与 provider gate 仍须在 full 首轮通过后完成，未打 tag 或改生产。
