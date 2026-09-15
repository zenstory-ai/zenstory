# Agent Note: API 路由保持薄，跨入口共享的不变量只在 services 层实现一次

Status: implemented

## Problem

同一条业务规则（文件的 parent 必须是 folder、哪些 file_type 算正文、布尔参数怎么强转）有多个入口：REST 路由、Agent 工具、后台任务。每个入口各写一份时写法必然不一致。深度 review 实证过三例：Agent 侧漏掉"parent 必须是 folder"导致文件在侧栏不可见；文件清单硬编码 5 种类型丢掉 script/document，模型被告知"正文暂无"而反复重建已存在的分集；`recursive="false"` 被真值判断触发整棵子树软删除。

## Decision

`apps/server/api/` 下的路由处理函数只做参数解析、鉴权依赖和调用 `services/`；业务逻辑 must 放在 `services/`。凡是被多于一个入口依赖的不变量 must 抽成共享实现，入口只调用不复制：`services/file_tree_rules.py` 的 `validate_parent_assignment` / `is_descendant_of` 同时被 `api/files.py` 和 `agent/tools/file_ops/crud.py` 使用；`agent/constants.py` 的 `CONTENT_FILE_TYPES` / `INVENTORY_FILE_TYPES` / `coerce_bool` 被 `mcp_tools.py`、`context/assembler.py`、`graph/writing_graph.py` 共用。新增入口时 never 在入口内重写这些规则。

来源：3ef311e（CLAUDE.md 的 service layer 规则）、ea85c83（共享实现）

## Alternatives considered

- **各入口就地修复各自的缺陷**。最强理由：改动最小、风险局部。被否：ea85c83 的 review 记录把"同一不变量在多入口各写一遍且写法不一致"识别为反复出现的根因，就地修复只闭合当次症状。

## Consequences

- 收益：一条规则改一处，REST 与 Agent 行为一致；回归测试可以只针对共享函数写。
- 代价与已知上限："薄"是方向而非现状——`api/files.py` 仍有 1700 余行，没有脚本度量路由层厚度；共享模块的位置分裂在 `services/` 与 `agent/constants.py` 两处，何时合并未定。

## Verification

`grep -rln file_tree_rules apps/server --include='*.py'` 应同时命中 `api/files.py` 与 `agent/tools/file_ops/crud.py`。
