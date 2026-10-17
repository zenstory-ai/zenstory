# 版本列表按元数据读取，不提前载入整页历史正文

## Problem

Web 与 Agent 的版本列表契约均为 metadata-only，但 FileVersionService.get_versions 查询整行，最多加载100份完整正文/diff。Web仅第一页需要重建最新一版以判断 current_version_number，后续页和Agent列表都不使用历史正文。禁网、本地真实ASGI+合成SQLite重放中，50个大base版本列表约13.6MB traced峰值，两个重叠请求约27MB；GC后所观测ORM对象均释放，并非已证明的常驻泄漏。

## Decision

get_versions 已新增 keyword-only include_content=True，保留现有默认完整ORM读取及detached消费行为。仅两个metadata路由传False，并通过SQLAlchemy defer(content)省去历史正文列。保持所有版本记录、字段/顺序/分页/总数/auto-save过滤、权限与key范围、预览/比较/恢复、额度/事务行为不变。Web第一页照常执行 get_content_at_version：原有整行目标SELECT会载入其需要的正文，再使用既有base/diff链，不能增加每行lazy query。

修改前新增冷Session/真实HTTP SQL投影回归，旧实现8项只因SELECT包含content失败，默认service的detached正文兼容性1项通过；新实现9项通过，锁定两路由含当前标记/后续页、auto-save过滤、完整响应、SQL次数与默认service返回正文的兼容性。冻结三模块旧源码，以真实ASGI/ORM新旧对照测量普通base+diff和大base样本；seeding在控制进程，采样用新进程，避免把造数据的RSS高水位当API常驻内存。

## Alternatives considered

- **全局默认defer正文。** 改动最少且现有业务调用都只是metadata；但会改变service默认完整对象契约，离开Session后读取正文可能失败，保留默认兼容更安全。
- **只在API重新写metadata SELECT。** 不改service签名，但复制排序/分页/过滤规则，未来可能分叉；在所属service增加明确读取选项更小。
- **删旧历史/截短正文/缩小页长。** 节省更多，但改变用户数据或交付内容，直接排除。
- **同时省略当前File.content。** 后续页/Agent列表确实不需要它，但第一页需要比较，ownership helper还有写/恢复调用。作为独立候选测量，不混改共享权限读取。

## Consequences

元数据列表避免不使用的历史payload；第一页仍为安全标记加载必要最新正文与差分链，预览恢复仍按需读取全部需要的数据，不减少写作上下文或输出。增加一个内部可选读取参数，没有新依赖、缓存、迁移或云操作。仍需用真实SQL证明无N+1，收益只报告本地合成分配，不换算生产RSS/账单。

## Verification

- 13个相关模块191项通过：版本列表、current marker、预览/比较/恢复、Agent元数据与更新、service/额度/snapshot/并发回归。完整agent/api/services Ruff通过，预先声明 evaluate-version-list.sh exit0。已有前端与导出源码未变，不重复其有效测试。
- 真实HTTP冷Session回归对Web/Agent × offset0/2 × auto-save false/true检查完整响应golden、metadata SELECT省略content、会话关闭；Web最新为delta时版本SQL仍5条，后续页/Agent仍2条，无逐行lazy N+1。默认service仍完整载入content，关闭Session后读取原始base/diff可用。
- 独立新进程真实ASGI/ORM对照使用固定ID/时间戳临时SQLite，造数在控制进程，冻结三个旧模块完整源码加载为baseline，不复制查询算法。普通样本123版（每10版base，其余真实diff）：Web首页峰值551,928→328,153 bytes（-40.54%）；后续页522,589→293,621（-43.81%）；Agent522,684→297,799（-43.03%）。
- 压力样本123个131,072汉字强制base（支持的历史形态，不代表平均用户）：Web首页13,836,933→790,954 bytes（-94.28%），后续页13,630,644→526,269（-96.14%），Agent13,630,905→527,158（-96.13%）；两个同时在途后续页请求27,165,104→958,228（-96.47%）。各路由新旧完整body SHA256/bytes与Content-Type/Length相同，成功状态相同。每类5次采样中位数；普通并发不作固定改善门禁，因为相同代码的control本就有约8%调度波动。
- 同时记录本地RSS原始值：大base独立进程预热后baseline208,912,384 vs candidate187,777,024 bytes；仅一次进程对，且本地Python3.14/SQLite不同于生产3.13/PostgreSQL，不当作稳定生产RSS/成本收益。主门禁为分配峰值与精确行为。
- 同配置mypy旧/新均为15个既存诊断（2个文件），新增0；不是干净类型通过。defer沿用inspiration的InstrumentedAttribute类型桥，不新增抑制或依赖。
- 初次落地误把Web读取选项加到logging kwargs，4个实际SQL回归及时失败，修正为service调用后191全通过，未放宽断言；失败日志保留。早期mixed probe一次失败来自合成auth对象commit后过期的DetachedInstanceError，修正仅是临时fixture持有方式，不计产品故障/收益。
- 只本地验证/commit，无push、PR、部署、云变更、付费模型调用；完整冻结源码、no-change control、各次样本与门禁日志在本地performance-goal `frontend-backend-local-20261017`。
- 独立code-review覆盖5文件，代码/安全/兼容性/查询/实测均0发现。该review lane因没有LSP diagnostics工具，将正式建议标为COMMENT（无修改要求），不是无条件APPROVE；leader同样未发现该工具，按事先声明的真实测试+Ruff+mypy 0-new差分门禁接受此本地批次，保留类型诊断缺口，不安装新工具或掩盖既存错误。

## Follow-up

本记录当时将当前File.content的省略保留为独立候选，未混入版本行投影。后续经独立当前正文测量，按[版本元数据权限读取不预载当前全文](2026-10-17-version-ownership-body-deferral.md)实施：helper默认完整、Web第一页需要的正文和写锁刷新仍保留，仅纯元数据列表省略无用当前正文。本记录原有FileVersion投影、默认完整service及历史兼容决定不变。

本条保留列表投影的原决策；[单版本元数据投影](2026-10-17-single-version-metadata-projection.md)另行测量并将相同显式模式用于Web按ID/最新元数据的单行历史记录，service默认完整及所有内容消费者仍保持，不追认成原列表批次的收益。
