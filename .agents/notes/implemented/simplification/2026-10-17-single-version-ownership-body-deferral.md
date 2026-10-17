# 单版本只读接口不为权限检查加载当前全文

## Problem

[版本列表权限读取优化](2026-10-17-version-ownership-body-deferral.md)提供了显式 `include_content=False`，但 Web 单版本元数据、历史正文、对比、最新元数据和 Agent 单版本正文5个调用仍按默认加载当前 File 全文。它们不使用当前正文，所需历史正文来自 FileVersion 重建；无必要的宽 Unicode 全文分配放大单请求及重叠请求峰值。

## Decision

仅5个只读权限调用使用已有 `include_content=False`。不改 helper、默认值、ORM identity map、任何 FileVersion 查询/重建/对比/输出、写入/回滚/配额或 for_write/PostgreSQL锁分支。复用已证实机制，不新增延迟响应、缓存或抽象。先补真实ASGI快照/差量重建、完整响应、冷session及SQL列回归；旧实现8项仅在最后权限查询包含file.content的断言失败，响应/headers/查询次数/持久化和重建结果先通过，22项跨用户、软删除、缺版本、Agent scope/allowlist保护通过。实施后14个相关模块256项通过、Ruff通过、API mypy旧/新同13个既存诊断（新增0）；默认detach/warm identity/write/回滚/配额/snapshot/并发保护保持。

## Alternatives considered

- **继续默认读取当前全文。** 调用最直接、保留现状，但5个调用丢弃返回对象，不需要内容；已有显式选项可准确描述需求。
- **修改默认值/批量所有读写调用。** 可能覆盖更多机会，但服务默认全文和写锁/恢复需要内容；本批保持所有未测调用不变。
- **同时投影 FileVersion.content。** 元数据接口还存在历史载荷开销，等大历史控制样本已证明这一点；但这是另一原因，涉及service默认兼容，单独跟踪，避免混淆收益。

## Consequences

最终实际源码36次真实ASGI/SQLite对照，当前约210万字符、目标历史很小时5接口分配峰值下降97.10%–97.93%；例如Web历史正文4,281,760→119,798 bytes，Agent正文4,291,737→124,605 bytes，两个Web重叠请求8,554,849→201,718 bytes。查询次数不变，当前File正文列被排除。

等大的目标历史正文仍必须完整重建和输出，单请求正文/Agent峰值基本不变；等大正文双并发中位峰值反而42,159,122→46,350,378 bytes（+9.94%），候选最大值也更高。三组交替测量上下波动明显，调度相关是解释而非已证实根因，不能排除生产并发内存回退。保留此不利结果，不重测寻找好看的数据，不减少输出换取数字。上线前仍需另获授权后验证生产运行时代表性并发内存；本批仅本地提交，不声称生产RSS、容量、账单或PostgreSQL收益。

## Verification

- 冻结完整旧API模块，对照正常导入的实际修改源码；普通、等大目标正文、当前大正文/较小历史3类样本，3组交替traced和单独untraced，全部响应bytes/status/同一端点所有headers精确一致。快照和真实delta重建、完整对比均未改变；File查询每请求1次且不含正文、无lazy N+1，清理后File弱引用0。
- 所有整请求分配峰值均用drain/GC后起点及完整绝对最大值独立重算。普通样本增加356–2,151 bytes，无预先定义的显著回退；untraced无显著CPU回退。未来若这些调用使用当前正文，应重新评估需求，不能靠未察觉的lazy读取。
- 测量脚本首轮因随机request/trace标识未满足全headers一致而拒绝；以确定且每请求唯一的fixture标识重测，未归一化或删除headers。随后修正把不同请求唯一标识也要求相同的checker断言，同端点全headers检查仍严格。只更新checker哈希，原始测量数据不变；独立架构审查核对差异。首轮原始完整日志被覆盖无法恢复，原始JSON和匹配其哈希的重构脚本已明确标注保存，不伪称完整失败日志仍在。
- Python3.14/SQLite，不是生产Python3.13/PostgreSQL；原生LSP/ast-grep不可用，mypy不是全绿。独立代码/架构审查和源码/测试/冻结模块/脚本SHA256及全部原始样本，存于本地performance-goal `frontend-backend-local-20261017` 的 `single-version-owner-*`。未重跑无关前端验证。
