# TXT 导出复用已渲染的大纲状态

## Problem

包含大纲时，service 已读取、过滤和排序所有大纲，API 却为选择文件名再次读取全文。真实本地 ASGI/SQLite 对照确认每次大纲 SELECT 为2次；第二次查询不改变交付内容。此前[文本副本优化](2026-10-17-export-text-allocation-copies.md)明确将此项留待单独验证。

## Decision

现有格式化正文提取为私有函数，返回 `(完整文本, 是否实际包含非空大纲)`；公开 `export_drafts_to_txt` 保持原参数、默认值、文档和内置 str 返回。API 使用同次渲染的布尔值选择原文件名，删除第二次查询。格式、Python Unicode 空白过滤、排序、BOM、权限、套餐、空白 first_work 处理均保持，不持有额外 ORM 对象。文件名描述本次内容，不新增事务或全项目原子快照保证。

先增加真实 HTTP 精确字节和 SQL 次数回归：旧代码4项仅在查询2次而非1次的断言失败，默认不含大纲的控制项通过；所有完整输出和 public str/default 断言先通过。最终7个导出模块72项通过、Ruff通过、mypy旧/新同3个既存SQLModel诊断（新增0）；冻结完整旧模块与实际导入新模块的24次真实ASGI/ORM对照及独立重算检查通过。未重跑未受影响的前端验证。

## Alternatives considered

- **保留重复查询。** 逻辑直接且无接口调整，但重复加载所有大纲全文；已有最小私有函数方案可复用状态，公共 API 不必改变。
- **路由预读取并传入所有大纲。** 可复用现有函数，但延长 ORM 全文存活至响应编码，可能扩大峰值；布尔结果已足够。
- **结果类、str 子类或缓存。** 可暴露更多状态或减少重复工作，但改变公共类型或增加生命周期机制；当前只需一个内部布尔值。
- **SQL trim/存在性查询。** 返回数据更少，但仍多一次查询，且数据库 trim 与 Python Unicode strip 不同；不能替代已经确定的渲染状态。

## Consequences

收益以确定的全文查询/装载2→1为主；最终本地大纲样本耗时中位数22.531→16.143ms（约-28.35%）；单请求分配峰值19,008,402→19,008,554 bytes（无收益），不声称生产RSS/账单下降。两个重叠请求样本峰值28,557,418→24,568,081 bytes（约-13.97%），受线程调度影响，仅补充记录，不承诺固定并发容量。前一测量脚本误将上一响应残留计入下一控制组起点；已保留旧证据并修正生命周期，禁止使用被污染的3KB控制数据。

## Verification

- 24次对照覆盖普通/大纲全文约5.5MB响应、3组交替traced及单独untraced、默认/含大纲/两请求重叠。完整响应bytes/status/content headers/Unicode文件名、BOM、默认公开str、过滤/排序均一致；大纲SELECT2→1、全文ORM装载26→13，默认0→0；清理后File弱引用0。
- 每段直接await、两次事件循环drain后GC，记录原始起点和绝对最大值；独立脚本重新计算完整请求峰值，未将上一响应释放误算成下一请求收益。首次72项通过后Ruff发现测试import排序，保留失败日志，仅排序import后重跑最终72项与静态检查；输出断言和门槛未放宽。
- Python3.14/SQLite本地证据，未测生产Python3.13/PostgreSQL或真实RSS。原生LSP/ast-grep工具不可用；不伪造干净类型检查或上线就绪结论。独立代码/架构审查结果另存performance-goal证据。
- 证据：本地performance-goal `frontend-backend-local-20261017` 中 `export-query-final-profile.{py,json,log,md}`、`export-query-evidence-check.py`、source gate和red日志；源码、测试、冻结模块与测量脚本均有SHA256绑定。
