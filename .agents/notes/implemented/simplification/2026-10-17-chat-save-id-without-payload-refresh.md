# 历史保存返回 ID 不再重读整条消息

## Problem

`MessageManager._save_messages_with_session` 在提交后读取 `assistant_chat_message.id`。默认 `expire_on_commit=True` 会过期 ORM 属性，即使只取主键，也会 SELECT 整行 `ChatMessage`，重新加载正文、思考、工具 JSON 与有序展示 metadata。共享测试 fixture 关闭了 expiration，原有常规测试没有暴露这次回读。

本地真实 AgentService → StreamAdapter → SQLite 历史保存重放定位了该查询。2MiB 回复与256KiB思考的样本在流累加阶段峰值约14.7MB、commit阶段约37.3MB，而提交后取 ID 时达到约50.6MB；没有发现需要以减少内容或改变 JSON 格式来消除的业务要求。

## Decision

在既有 `session.commit()` 之前把 assistant ID 保存到局部变量，只有提交及既有 logging 成功后才返回。`ChatMessage.id` 使用客户端 `default_factory=generate_uuid`，构造时已生成；无助手行则仍返回 `None`。不关闭 expiration，不增加 flush、事务、缓存或异步边界，错误仍 rollback/rethrow。正文、思考、状态卡、steering、有序展示、routing、usage、JSON原始字符串、消息计数和时间戳都不变。

## Alternatives considered

- **关闭 session 的 expire_on_commit。** 优点是所有已知属性都能直接读取，也可以减少其他小行重读。未采用，因为扩大了整个 session 的一致性与生命周期契约；本问题只需要返回已经生成的一个字符串。
- **提交后只查询主键列。** 优点是坚持从数据库读取最终 ID，不需要依赖构造时的值。未采用，因为当前主键由客户端生成且没有主键改写 hook，额外查询没有业务价值。
- **改变 metadata JSON escaping/空格。** 优点是中文大消息的 JSON 分配可能进一步缩小。未采用，因为 API 原样返回这些嵌套 JSON 字符串，格式变化可被客户端观察；本批只消除不必要的重读，不改变任何存储或线缆字节。

## Consequences

收益是避免成功落库后为获取已知主键而重载整个大 payload；普通和强制空助手也少一次查询。代价是保留一个很小的局部字符串引用。ID若将来改为数据库生成或引入主键改写 hook，需要重新审查此决定，不能把当前客户端 UUID 前提当作通用 ORM 规则。

回归使用真实默认 expiration 的独立 Session，验证完整消息、无输出、强制空助手、steering、发送时间、原始 JSON，以及 commit 失败后 rollback/异常传播。旧实现两个针对助手回读的用例失败，两个已有行为用例通过。最终源码验证见下节。所有测量仅本地 Python3.14/SQLite，不证明生产 PostgreSQL 的 RSS、账单或已上线收益。

## Verification

- 新回归先在原实现得到2项预期整行SELECT失败、2项兼容行为通过；最终4项通过。相关MessageManager/service/历史回放/取消与shield补存/round outcome/SSE背压及退款保护共164项通过（1496项警告，无跳过）。标准agent/api/services及新测试Ruff通过；MessageManager的mypy与完整旧模块比较仍为13项既有诊断、无新增，不宣称整个后端类型检查干净。
- 完整旧模块来自基线`0cbca98a`，对照实际导入的新产品模块SHA-256`68a61268429864873bd755fb71ce4c9eb4df757324497bc74de1b0316d461c54`。普通/大聊天各3对交替traced和独立untraced重放：大聊天完整分配峰值中位数50,621,248→37,307,919字节（下降26.30%）；普通1,487,017→1,264,365字节。保留每次reset_peak之前的峰值及paused/final/call原始值，12条traced记录均可独立重算。
- 12对输出与持久化收据完全相同，包含完整规范化帧、正文、思考、有序display events、routing、usage、stop reason及原始JSON字符串。大样本metadata 14,096,608字节完全相同，未用解析后等价掩盖格式变化。原来每次为取ID产生的ChatMessage全列SELECT均从1次变为0次；既有会话日志等其他查询不变。
- Untraced中位耗时普通10.608→10.851ms（+0.244ms），大聊天246.412→239.576ms；均未触发事先约定的实质CPU回退门槛，不把小幅时间变化当作通用加速保证。性能重放未模拟取消，取消/退款/补存语义由上述已有回归及未改变的生命周期源码保护。
- 本批没有前端变更，沿用既有有效前端验证，不重复构建。私有性能脚本、原始失败、日志、源码哈希、独立审查和最终receipt保存在本地performance goal目录，不入Git；所有模型/网络调用为0。独立代码与架构审查结论在该目录记录，本批验收由本地提交前的最终检查执行。
