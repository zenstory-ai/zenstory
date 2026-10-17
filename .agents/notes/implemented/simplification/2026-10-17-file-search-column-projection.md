# 文件关键词搜索只加载结果所需列

## Problem

`FileSearchService._lexical_search` 使用 `select(File)` 读取关键词候选。后续排序、打分和结果组装只访问 `id`、`title`、`content`、`file_type`、`updated_at`，但 ORM 仍会把 `file_metadata`、层级、顺序、删除时间等列一并物化。项目文件的 `file_metadata` 可能较大；候选上限为 200 行，这些未使用值会增加查询传输、Python 对象物化和瞬时内存，而不会改变检索结果。

## Decision

- 在既有 `select(File)` 上使用 SQLAlchemy `load_only`，只加载 `id`、`title`、`content`、`file_type`、`updated_at`。
- 不改变项目、删除、文件类型和文本过滤，不改变 `updated_at` 候选排序、候选上限、打分、时间预算、截断或完整正文返回规则。
- 用真实 SQLite ORM 回归检查候选 SQL 不再选择大元数据列，且结果组装只执行一次查询，不因延迟列产生 N+1；同时覆盖大元数据、删除/跨项目/类型隔离和 `include_content` 两种输出。

## Alternatives considered

- **改成显式列元组查询。** 最强理由是 SQL 形状更直接，也完全不创建带延迟属性的 `File` 对象。未采用：现有打分和排序代码围绕 `File` 实例，元组会扩大改动面；`load_only` 是仓库已有模式，能用一行投影保持全部业务路径不变。
- **只延迟 `file_metadata`。** 最强理由是它通常是最大的未使用列，改动更窄。未采用：层级、顺序、创建/删除时间等列同样未使用，`load_only` 能明确记录本路径的数据契约，且不会增加查询数。

## Consequences

- 收益：减少每次关键词检索的数据库返回字节数和候选 ORM 物化内存，尤其是候选文件带大元数据时。可复跑的本地 disposable SQLite evaluator 从当前产品源码生成只移除 `load_only` 的基线，两边都调用真实 `_lexical_search`，并逐字段比较全部 `SearchResult`。200 个候选、7 次 fresh Session 的普通 256 B 元数据场景，中位 `tracemalloc` 峰值从 789,252 降到 754,346 字节（下降 4.42%），中位耗时从 24.284 ms 降到 20.197 ms（下降 16.83%）；128 KiB 压力场景中，峰值从 26,951,476 降到 753,192 字节（下降 97.21%），中位耗时从 103.808 ms 降到 25.166 ms（下降 75.76%）。该结果不代表生产 PostgreSQL RSS 或账单，普通场景的小幅收益也避免把压力数据误称为常态。
- 代价：若以后在此函数中访问未列出的 `File` 属性，SQLAlchemy 可能发出延迟加载查询；回归测试会用查询计数暴露这种 N+1，新增字段时需要同步投影。

## Verification

- 修改前新增真实 ORM 回归：`test_file_search_projection.py` 因候选 `SELECT` 含 `file.file_metadata` 而失败；失败输出保存在目标工作记录的 `backend-file-search-projection-red.log`。
- 修改后：`test_file_search_projection.py`、`test_file_search_service.py`、`test_file_search_guardrails.py` 共 6 项通过。回归使用 24 个带 64 KiB 元数据的真实候选，验证结果顺序和字段、删除/跨项目/文件夹/类型隔离、`include_content` 两种行为、候选 SQL 投影，以及结果组装全程仅一次 `file` 查询（没有延迟列 N+1）。
- `ruff check services/infra/file_search_service.py tests/test_services/test_file_search_projection.py` 通过；`git diff --check` 通过。
- evaluator 源码、stdout 和 JSON 分别保存在目标工作记录的 `backend-file-search-projection-benchmark.py`、`backend-file-search-projection-benchmark-actual.log`、`backend-file-search-projection-benchmark-actual.json`。它从当前 `file_search_service.py` 编译只移除投影块的基线，统一关闭时间预算，交替运行顺序，并比较十个 `SearchResult` 字段；普通与压力场景的全字段结果都一致。
