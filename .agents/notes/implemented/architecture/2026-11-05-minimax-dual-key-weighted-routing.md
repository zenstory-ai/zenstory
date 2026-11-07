# Agent Note: MiniMax 双凭证加权路由

Status: implemented

关联：[模型供应商主备切换](2026-10-20-llm-primary-backup-failover.md)。

后续：第 2 条（每个 run 独立分桶）和第 4 条（Key 问题仍按供应商处理）已调整，见 [会话粘性与单 Key 冷却](2026-11-07-minimax-key-sticky-routing-and-cooldown.md)。

## Problem

MiniMax 只有一个凭证入口。单个账号额度接近上限时，无法把新任务按稳定比例分给第二个账号；如果把两个 Key 伪装成两个 provider，又会错误引入跨 Key 重试、会话固定和熔断，改变现有供应商 failover 与成本语义。

## Decision

1. `MINIMAX_API_KEY` 继续是必需的主凭证；可选 `MINIMAX_SECONDARY_API_KEY` 增加次凭证，`MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT` 设置次凭证的任务比例，未配置或非法时用 20，允许 0 到 100。没有次凭证时行为与原单 Key 部署一致。
2. 写作请求优先用服务端生成的 `agent_run_id` 做稳定 SHA-256 分桶；同一 run 内的路由、写作、工具轮和质检调用固定到同一凭证。内部可信会话 id 是没有 run 上下文时的次级稳定键，不使用客户端可控的 request id。
3. 没有可信上下文的同步素材调用按每次模型调用使用进程内加锁轮转；100 次完整周期中按配置百分比分配，避免进程首次解析配置后所有调用永久落到同一个 Key。
4. 两个 Key 仍是同一个 MiniMax provider 槽位。Key 选择发生在 provider 构造时，不新增跨 Key 重试，不改变 DeepSeek 主备顺序、会话固定、熔断、计价、成本守卫或取消语义。
5. OpenAI client cache 继续包含真实 `api_key` 作为内部键，因此两个凭证的连接池隔离。日志只增加 `key_slot=primary|secondary`，不写 Key、摘要、哈希或分桶值。

## Alternatives considered

- **把两个 MiniMax Key 配成主备 provider**。最强理由：可以直接复用现有 failover。被否：额度耗尽会触发跨 Key 重试，同一生成可能多次计费，也会把凭证故障错误升级为供应商熔断。
- **每次 `configured_providers()` 随机选 Key**。最强理由：实现最短，长期大致满足比例。被否：同一 run 的连续模型调用会漂移，破坏前缀缓存，也让问题难以复现。
- **只在进程启动时随机选 Key**。最强理由：单进程内绝对稳定。被否：无上下文的素材调用会全部落到一个账号，少量实例时长期比例严重失真。
- **按 token 或实际费用动态平衡**。最强理由：更接近额度消耗的真实 80/20。被否：需要跨进程用量状态和调用后反馈，扩大配置、存储及故障面；当前目标明确按任务路由。

## Consequences

- 收益：同一写作任务稳定使用一个 MiniMax 账号，多个任务长期按配置比例分摊；单 Key 配置无需迁移。
- 收益：双 Key 不参与 provider failover，DeepSeek 主备行为、成本预算与 SSE 取消保持原合同。
- 代价：比例按任务数而非 token 或费用；不同任务体量差异会让实际额度消耗偏离配置比例。
- 代价：无可信上下文的轮转状态只在单进程内；多个 worker 各自从周期起点开始，但每个进程的完整周期仍满足配置比例。
