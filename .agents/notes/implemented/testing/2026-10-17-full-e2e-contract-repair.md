# Full E2E 契约修复

## Problem

`39062687687` 的 full Chromium/Firefox 回归出现 298 个失败实例。已确认多组用例依赖旧文案/样式、共享且受配额限制的数据、未同步认证导航以及不存在的 Linux 视觉基线；无效项目路径的实际状态仍需查明。默认绿灯不能替代 full 证据。

## Decision

按 `docs/plans/2026-10-17-full-release-repair.md` 分族保留红灯再修复：复用既有语义/API helper、每测试自有数据和确定性 SSE；仅在 trace/响应证明缺陷后修改产品状态机。视觉采用固定 Linux Playwright 环境与确定性数据，逐图审阅。不新增依赖、不削弱断言/阈值、不增加重试/超时或扩大跳过。发布同时要求最终精确 main SHA 的 push CI/E2E 与 manual full。

搜索空结果/加载分支未绑定既有 outside-click ref，导致点击外部不关闭；补回同一 ref 并统一该下拉框的测试/状态语义，不新增监听器或改变搜索算法。先补空/加载分支的内外点击回归，再最小修复。

错误恢复用例改为真实项目/正文与浏览器边界故障注入，保留断流、HTTP 503/429、损坏 JSON、认证失败、读取中断、重试及坏帧覆盖。原「自动 backoff」并非产品契约：项目 GET 仅显式重试，生成 POST 不自动重发；更名后断言用户点击才增加一次请求。原 65 秒等待超出测试自身 60 秒限制，改注入真实 `ERR_AGENT_RUN_TIMEOUT` 终态（后端规定 retryable=false），验证提示、编辑器保留和可继续输入，而不捏造自动重发入口。截断响应使用真实 agent/stream 路径，既有错误的 agent/chat 拦截不再遗漏真实外呼。

CI 复用既有 seeder 创建 TEST_USERS.pool 已声明的五个免费账户，限回环 `zenstory_test` 库；不提高免费额度或删除项目。CI 专用入口在 DNS/连接边界拒绝非回环外呼，生产入口不变；固定 mock 不能替代真实模型验证，也不应使用假 key 向真实模型发送请求。

原文件移动组也假设了尚不存在的拖拽 UI：桌面/手机文件树均未接入 move，当前和上次生产 tag 的桌面节点均无 drag handlers。保留定向拖拽红灯原件，五个用例准确更名为既有 API move 的响应、幂等、树读取与刷新持久化覆盖；不扩写文件拖拽新功能、不把 API 验证声称为 UI 能力。前十七个文件操作用例继续验证真实已有交互。

大文档组存在同类旧模式假设：`Editor.tsx` 当前、main 和 `prod-v2026.10.17.1` 均明确所有文档使用 SimpleEditor，仓库没有 VirtualizedEditor/chunk-container 实现。相关用例准确改为真实大正文输入、跨文件切换及完整内容/API 回读；保留原内容规模与既有性能、滚动和保存阈值，不引入虚拟化新功能，也不声称验证了虚拟化。

本机实际存在指向 127.0.0.1 的 HTTP(S)_PROXY，仅限制回环连接不足以防止代理转发 provider 请求。CI 专用 guard 在 SDK 导入前同时删除大小写 HTTP/HTTPS/ALL_PROXY；补环境代理回归，先红再修。现有日志没有 provider SDK 请求记录，但不据此推断绝对零外呼；最终隔离证据须来自关闭该路径后的运行。

独立 designer 逐图首轮 8/10 接受，指出两张消息态 fixture 完成一轮后仍回 `used:0`，会把不真实额度写进基线。仅修该视觉 fixture：完成模拟回合后 quota 返回 `used:1` 并等待 badge 更新，再重生成并独立复核两张；不修改产品计费。独立审阅同时确认四张旧 Chromium Darwin 图来自 live/conditional 流程，状态/尺寸不兼容且缺消息态，移出当前基线。Linux 是本次 canonical 视觉门禁；未新增跨 OS 跳过或放宽阈值，不声称已覆盖 macOS 视觉渲染。

独立审阅进一步要求并发测试必须证明真实 stale token 409 与显式审阅，不能接受任一正文/状态。当前前端无删除会话 UI，三个虚构交互从 browser E2E 移除；既有后端以真实持久 session/messages 验证删除和 ledger 保留，不声称 UI 覆盖。

完整发布证据仍未完成；本决策描述已采用的方法，不把候选视觉图、定向绿灯或独立静态审阅当最终 full/main 发布批准。

project fixture 按稳定 parallelIndex 选择已 seed 的账号，每测试清理精确 ID；认证 helper 每调用真实登录，不复用未知新鲜度的 token。两个 skills suite 仍保留既有专用合成账号与 test-owned 前缀清理，单 runtime 固定 worker=1；CI 分片各自独立 DB，因此保留分片并行，不宣称本地共享 skills 账号可多 worker 运行。

真实卡片名称来自项目响应，不来自创作灵感；回访场景用唯一 API-owned 名称与真实 aria-label。Dashboard pathname 切换既有行为是 main.scrollTop 重置，不继续声称 window 滚动位置保留。保留专门 UI 创建测试，不扩写滚动记忆产品功能。

当前及最近生产的 ProjectEditor 仅把 file query 作为选择契约，不支持用 session query 选历史会话；测试准确覆盖额外未知参数不干扰有效文件，并直接收集/断言 dialog 与 pageerror 防止 XSS 假绿。

认证刷新测试覆盖实际支持的非空 access 被拒绝、refresh 仍有效且缓存过期路径，要求 401、真实 refresh POST 与恢复后的 token/保护页。缺失 access 的既有清会话策略不改；不以登录页作为刷新成功。语音失败用真实 APIException/code 的安全提示与一次 recognize 请求证明命中目标路径，不用任意 alert 放行。

正文上传用例每例创建并清理唯一 owned 项目，缺少 folder/chooser 属于失败，不因共享数据缺失跳过。Footer matrix 共同断言保存/选择/滚动；浏览器级 pinch 的 CDP 路径只在 Chromium step 中报告，Firefox 显式记录覆盖边界，不以测试标题混称。

CI 实跑证明 version-list 外层在 loading 时已经存在，不能用它代替数据就绪。版本预览/滚动单测等待真实 rows/action 出现后再交互，不加重试或延长超时，保留精确 scrollTop 断言。

冻结 Firefox trace 中，API fixture 对 127.0.0.1 登录成功，但浏览器默认 localhost 的两次 /auth/me 连接一直 pending，页面因此停在 AuthProvider loading。Playwright 两种 Vite 启动路径都显式沿用 fixture 的 E2E_API_URL（默认 127.0.0.1），消除测试环境主机分歧；不通过加超时、伪造验证时间或跳过认证掩盖。

CI trace 进一步证明导航 URL 改变不代表项目组件已提交：back 用例须等待真实文件树，再验证返回后的 reset。退出用例固定 Dashboard 的真实 account panel 和回首页契约；版本历史复用 owned 项目/文件，避免自有登录和重复 hard navigation 互相中断，不改既有版本数/正文/回滚/冲突断言。

后续运行证据纠正退出终态：Dashboard 内部 navigate 首页并不是完整系统终态，AuthContext 的 logout 事件最终跳 `/login`。测试要求真实登录页和四项凭据清除。首作语言 matrix 同时设置浏览器 context 的 zh/en locale，保留请求头精确断言；无编辑历史要求实际 dialog 空状态与 API count=0，不接受“空或至少一个”的并集。

最终 main `3c03449d` 的 full `39090867679` 为 2098 passed / 1 failed / 53 skipped / 4 flaky，仍不能发布。历史对比的全页 contains 选择器误命中项目按钮（owned 项目名含 compare），且列表 loading 时的同步 count 让部分用例空跑。改为精确文件列表响应、已渲染行数、dialog 内对比按钮、成功 compare API 与真实 diff；至少两版本的前提改为断言，不再条件略过。订阅 mock 切换后只 reload 同一已认证 billing 页面，移除第二次 login 对未提交页面的打断。

跨会话持久化改为先注册精确 rename/logout/login 响应，再操作真实 UI 并等待 Dashboard / Login 提交，不手工删 token。Accessibility 复用既有 owned fixture 与精确清理，删除自造重试、任意项目借用及 onboarding storage 伪造；键盘创建场景仍走真实键盘登录/创建并登记 owned ID。

滚动时间原来由 Node Date.now 包含两次 Playwright RPC，Firefox 一次实测 104ms 超过 100ms。未改源码的双引擎诊断显示驱动 31–46ms 时，同页滚动与一帧为 11–19.8ms。滚动两项改在同一 browser performance.now 与 RAF 内测量，保留原 100ms 和约 50ms 阈值，并增加实际 1000px 移动或 top→bottom 断言；不把驱动通信当编辑器性能，也不提高阈值。

## Alternatives considered

- 仅依据默认检查发布：现有必需 CI 已通过，交付更快；但用户要求完整复跑且 full 存在未验证行为，不能将失败作废。
- 批量替换旧选择器并接受全部截图：可快速减少表面红灯；但无法证明定位正确或视觉真值，可能把缺陷写成期望，因此按根因与实际页面契约逐项修复。
- 重建测试框架或提高全局 timeout/retry：可以集中隐藏竞争或减轻慢机波动；但增加新耦合且掩盖真实准备失败，本次使用现有工具和明确状态边界。

## Consequences

收益是 full 重新覆盖真实业务断言，失败可以归因，发布证据绑定同一源。成本是需要逐图检查和一次最终 main full 运行；付费模型和浏览器不支持能力仍必须单独说明，不能声称完整 live-provider 验证。
