# 版本元数据的权限读取不预载当前全文

## Problem

[版本行投影](2026-10-17-version-list-metadata-projection.md)已避免预载整页历史正文，但权限helper仍通过session.get读取当前File.content。Web后续页/Agent版本列表只需要所有权、删除状态和标题；Web第一页必须比较最新版本和当前正文，设置current_version_number。禁网真实ASGI/ORM造数仅增大当前正文、保留123个小历史版本，定位到无用body hydration；请求后File对象GC释放，不是已证明的常驻泄漏。

## Decision

两个既有权限helper增加keyword-only include_content=True，默认完整读取不变；仅Web版本offset>0和Agent版本列表传False，使用SQLAlchemy defer省略File.content。Web第一页继续eager读取及既有base/diff重建。Agent for_write分支保持PG key-share/populate_existing与SQLite refresh，不因可选参数少读写入需要的内容。

实际实现使用Session.get(..., options=...)，不改成显式select，保留identity-map复用；已有全文不强制卸载。无缓存、迁移、依赖、新结果类型。权限/key范围/删除检查、错误码状态、标题分页、auto-save、完整版本和写作上下文不变。

## Alternatives considered

- **全局默认省略正文。** 代码更少，也节省其他不读正文的路径；但共享helper还有预览/恢复/写入调用，不能改变默认detached正文可用性及写锁刷新，故不采用。
- **第一页也defer、再lazy-load。** 调用统一，但第一页确实消费正文，会增加一次查询而无收益，故保留eager control。
- **metadata分支显式select。** 与service投影模式一致，发现原型验证了收益；实际用get选项取得相同冷Session投影，同时保留identity-map命中不重复查的语义。
- **另写metadata权限查询。** 不改共享helper签名；但会复制授权/key范围/删除规则并有分叉风险，显式可选读取字段更小。

## Consequences

纯元数据请求不再为不使用的大正文分配内存，第一页不承诺收益。选项只控制查询时加载列，不是硬内存上限，不删除已有Session内容。性能仅本地Python3.14/SQLite，不能换算生产PostgreSQL RSS/账单；未推送部署。

旧实现新增SQL回归6项因仍选file.content失败，6项已有行为/默认兼容通过；随后增加2项warm identity-map复用保护。最终源码验证见下节；独立审查收据随本地批次保存。

## Verification

- 14个相关模块226项通过（4142项既有警告、无跳过），包括原metadata列表/current marker、权限/Agent版本、写入并发预条件、service、restore/额度/snapshot及file ops。8组冷Session真实HTTP继续逐字检查响应，新增File SELECT列/次数、3项默认web/Agent/write的detached正文兼容及2项warm identity-map复用；FileVersion查询仍为原5/2次，无逐行lazy N+1。
- 标准agent/api/services和变更测试Ruff通过。最初226通过后Ruff指出新增import顺序，原日志保留；仅排序import后重新226通过，所有性能证据绑定修正后的最终源码。两API mypy与原源码均为13项既有错误、无新增，不是整个后端类型干净。
- 冻结完整7f77a859基线API模块，对照实际导入的产品模块：versions.py SHA-256 `576c5deba504bdaf366aee745a0339933462cec86cf71560c7039b0ea34718a1`；agent_api.py `0f8a18cd83d8b87c14df70e96af9947788d7d42190c5575cb8c3a25172140beb`。最终产品不是发现阶段显式select的算法副本，而是实际Session.get loader选项实现。
- 每类普通/大当前正文fixture分别3组交替traced与独立untraced对照，共24个worker原始runs、每run覆盖四路径。大当前正文2,097,157字符/6,291,471 UTF-8字节，历史仍为123个小版本：Web后续页峰值4,456,066→293,745B（-93.41%），Agent列表4,457,830→298,941B（-93.29%），两个重叠后续页8,761,645→399,033B（-95.45%）。第一页control4,484,181→4,484,077B，仍完整加载正文，不宣称收益。普通四路径仅增加69/271/155/664B，无实质回退。
- 每条traced请求为未切段的完整峰值，保留start/absolutepeak/current等原始值，独立checker重算。完整status/body bytes和content headers完全相同、不做语义规范化；SELECT总数第一页8、后续5、Agent7、两个后续10均不变，metadata SQL确实省略content、第一页仍选取，所观测File弱引用在响应后GC均释放。网络/模型0。
- CPU没有触发预先定义的“超过10%且超过10ms、至少2/3对”的实质回退：普通中位差-0.29至+0.06ms，第一页control+0.05ms；大正文metadata本地中位快3.69至6.83ms，不换算生产吞吐。最终checker和所有source/support/frozen/harness哈希、原始run、失败和source receipts保留在本地performance-goal目录，不入Git。
- 不重复未变的前端/build验证；不宣称已执行生产PostgreSQL/云端或真实付费生成。PG锁/刷新分支源码保持，真正staging/生产验证需另获授权。独立代码/架构审查完成才本地commit；本批不push、PR、合并或发布。

独立代码审查五文件0发现，正式建议COMMENT仅因LSP诊断工具不可用；架构审查CLEAR。按事先声明的226项测试、Ruff、mypy零新增及实际源码性能门槛接受本地批次，保留工具/既有类型诊断缺口，不宣称无条件APPROVE、merge-ready或LSP通过。

## Follow-up

本条保留仅元数据列表调用的原决策；另行实测的[单版本只读权限读取](2026-10-17-single-version-ownership-body-deferral.md)将已有显式选项用于5个丢弃当前正文的调用。历史正文/对比输出保持完整；等大正文双并发的不利样本另行记录，不追认成本条原收益。
