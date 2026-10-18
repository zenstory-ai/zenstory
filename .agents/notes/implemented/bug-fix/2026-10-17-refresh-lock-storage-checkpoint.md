# 刷新锁释放前的存储任务检查点

## Problem

本说明补充 [原刷新并发决策](./2026-10-13-refresh-rotation-concurrency.md)，不改变其服务端重放规则。真实 Firefox 双标签回归证明：两个原请求均 401，第二个标签已排队等待 Web Lock；第一个刷新 200 后用新 access 重放成功，但约 2.5ms 后第二个标签仍读取旧 refresh，再次 POST 并被拒绝。预定压力诊断在第 4 个 Firefox 首轮失败即停止，保留 trace，不将重试计作成功。

Mozilla 说明 localStorage 使用任务内快照，写入在任务结束的稳定点批量提交；Web Locks 则在回调 Promise 完成后释放，不能据此推断存储已经跨进程可见。来源：[Mozilla 1740144 c25](https://bugzilla.mozilla.org/show_bug.cgi?id=1740144#c25)、[Web Locks §2.4](https://w3c.github.io/web-locks/#locks)。本次 trace 与该机制一致；不将其表述为所有浏览器的存储保证。

## Decision

仅在既有原生 Web Lock 分支中，锁回调在 `finally` 跨过一次真正的下一任务，再结束并释放同一把锁。采用一次性 MessageChannel，交付后关闭两端；不使用微任务、轮询、定时退避、广播凭据或新的持久化协调器。成功结果在检查点后还须通过既有 `resolveOwnedAuthSession` 归属检查，期间注销、换用户或换登录 family 必须返回失败，不再写入凭据。无 Web Locks 的既有分支不变。

以可控 MessageChannel、锁队列及暂存存储写入补确定性单测，先红后绿：检查点前不得释放锁或让后继进入；随后两个原请求成功且只有一次刷新；检查点期间注销/换用户/换 family 不恢复旧会话；401 清除也在释放前经过检查点。保留真实双标签单次刷新断言，固定次数双引擎压力检查，失败即停。

## Alternatives considered

- **显式 owner/follower 完成协议**：可直接表达交接，但消息与存储的次序仍需检查点，且新增迟到订阅、相关性、过期和清理规则。仅在最小修复被实跑否定后再考虑。
- **IndexedDB 或 worker 协调器**：可提供更强的共享事务语义，但扩大凭据存储和登录/注销生命周期的安全面，不适合本次局部排序缺陷。
- **微任务或零延时定时器**：前者不能结束任务；后者可能被后台标签限速并延长持锁。选择不依赖经过时长的 MessageChannel 任务。

## Consequences

不增加依赖、刷新 POST 重试、服务端迁移或云配置；保持原会话归属与重放保护。增加一次任务交接及端口生命周期，需要单测和真实 Firefox 压力回归共同验证。确定性单测 14/14，API client 系列 74/74；完整前端 4,747 passed / 1 既有 skipped，构建及静态检查通过。精确候选 `c0d67a47` 的原生双标签四场景在 Chromium/Firefox 各固定 20 轮首轮全通过（161 incl setup，retries=0，无 flaky），且 local owned runner 启用 init 后僵尸保持为 0。最终 main 同源码 full 与 provider 发布门槛仍需完成，这些结果不是生产发布批准。
