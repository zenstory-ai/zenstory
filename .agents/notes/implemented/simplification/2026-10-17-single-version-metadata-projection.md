# 单版本元数据不预载历史正文

## Problem

[版本列表投影](2026-10-17-version-list-metadata-projection.md)已省略无用历史正文，[单版本权限读取](2026-10-17-single-version-ownership-body-deferral.md)已省略无用当前正文；但 Web 按ID/最新版本的元数据响应仍读取整个 FileVersion.content，再丢弃。历史大base/delta成为请求分配峰值来源，与必须完整重建/交付正文的请求不同。

## Decision

仅 FileVersionService.get_version/get_latest_version 新增 keyword-only include_content=True，默认仍返回完整存储正文/diff；显式False沿用既有defer列投影，仅供2个Web元数据接口使用。Session.get保留identity map行为，不新增populate_existing；latest保留原排序/limit/query次数。正文、对比、Agent、创建/合并保存、AI undo、author保护、备份、回滚、快照及写锁均不改。

先补真实HTTP完整响应、查询次数、当前正文投影之后的历史正文列回归：旧实现3项仅因目标FileVersion SELECT含content失败，其余30项通过；包括3项cold默认service关闭session后仍能读完整base/delta。落地后补3项warm已完整载入对象不被卸载的兼容回归。最终17个相关模块290项通过、Ruff通过、API与service同2个既存mypy诊断（新增0）；实际源码36worker重测及独立检查通过，不重跑未受影响的前端。

默认完整指新查询保留原来的全文选择，而不是强制改写identity map状态。Session.get遇到已存在但此前deferred的对象时仍遵循既有行为，不承诺关闭session后可读取该字段；不能为此加入populate_existing或额外刷新。显式False的对象也只用于会话内构造不含正文的元数据响应。

## Alternatives considered

- **保持完整读取。** service接口最简单、默认内容消费者无需变化，但两个元数据接口不使用正文，已有显式投影模式可以复用。
- **把service默认改为不读正文。** 可覆盖更多读取，但版本合并、备份、undo与保护需要重建全文；保留默认完整和逐调用显式选择更安全。
- **新增独立DTO查询或缓存。** 能避免ORM deferred字段，但增加查询/授权层或生命周期，且已有投影已证明可保留identity map与原响应，无需新机制。

## Consequences

最终正常导入的实际源码对照：大历史base按ID峰值4,291,936→97,973 bytes（-97.72%）、latest4,285,507→91,826（-97.86%）；大历史delta两接口分别-97.39%/-97.56%，查询次数不变。必须完整输出历史正文的控制样本峰值基本不变，未裁剪任何正文。

普通元数据请求增加165/621 bytes，低于预先定义的显著回退门槛。另保留不利的untraced进程CPU中位数：普通全文+5.12%、普通重叠+6.49%、大base全文+5.41%，均未触发>10%且>10ms、至少2/3组的显著回退；不是延迟实测或生产收益承诺。此前当前正文优化的等大正文双并发+9.94%风险仍保留，不能用本批不同样本覆盖它。本地Python3.14/SQLite分配测量不是生产RSS/账单/容量证据，未测生产Python3.13/PostgreSQL。

## Verification

- 固定ID/时间戳临时SQLite、禁网和付费provider，冻结完整旧API/service对照正常导入的实际源码；普通/大存储base/大存储delta，各3组交替traced及独立untraced进程CPU。cold预检先通过完整响应/headers/SQL/default/warm/reconstruction，之后才运行36worker；没有为有利数字重测或新增候选。
- 完整原始bytes/status/同请求位置所有headers精确一致，fixture request/trace ID确定且逐请求唯一、不归一化响应；元数据SELECT仍4/4条，双端点重叠8条，目标FileVersion每请求1条且不含正文，无lazy N+1；必要全文接口仍5/7条、真实base/delta重建完整。drain/GC后的原始起点与全请求绝对峰值可独立重算，File/FileVersion弱引用清理后0。
- 发现阶段一次manifest路径错误在写文件前失败、一次checker把未修改的context行计入新增调用，原日志/JSON/checker均先保存再纠正；未改变候选/数据/门槛或重测。实际源码最终profile首轮及checker均通过。
- 原生LSP/ast-grep不可用，mypy不是全绿；独立代码/架构审查另存性能证据，不伪称无条件merge-ready。源码/测试/冻结模块/harness/checker均有SHA256绑定，完整结果在本地performance-goal `frontend-backend-local-20261017` 的 `version-metadata-projection-*`。仅本地验证与提交，无push/PR/部署/云变更。
