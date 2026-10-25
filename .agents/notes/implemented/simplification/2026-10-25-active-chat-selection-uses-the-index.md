# Agent Note: active 会话按索引选择，历史只保存到已解析的会话

## Problem

审计 D-09 发现 service、loader、suggest 和 chat API 都查询全部 active 会话并修复多活跃状态。迁移 `20260307_230000_add_unique_active_chat_session_index` 已禁止同一作者/作品多活跃；[模型声明](../architecture/2026-10-25-chat-session-active-index-declared-on-model.md)也使新测试库一致。保存消息时另有一个无 session_id 就选取或创建 active 会话的路径，生产入口已先解析 ID，此回退会掩盖调用接线错误并把历史存进别的会话。

## Decision

- 在既有 `services/chat_session_service.py` 增加 `latest_active_chat_session`，沿用 updated_at/created_at/id 倒序，以 LIMIT 1 选择；service、loader、suggest 和 chat API 复用，不再在读取时修复多活跃。
- 保留 PG advisory lock、新会话建立、显式切换到已有 archived 会话时先停用旧会话并 flush 再激活的顺序。未知旧 runtime ID 继续回退到现有 active，避免历史看似丢失；suggest 的 DB 历史回退保留。
- MessageManager 的保存链要求显式 session_id，空 ID 在写入前拒绝；现有 project/user 归属检查和 inactive 会话保存继续有效。无 ID loader 查询仍保留供兼容调用。
- 修改前只读核对：staging 和 production 都有同名 unique index，列为 user_id/project_id，条件 is_active=true；production revision 为 `20261024_120000`，迁移链包含上述迁移。staging 由 create_all 建表，没有 alembic_version。私有证据位于本次 Codex 交接输出，不包含业务行。

## Alternatives considered

- **保留修复循环**：未迁移的旧库可以自动恢复。但 hosted 已核实索引，运行中无法产生该状态；每次读历史执行写修复增加副作用。未迁移数据库仍由迁移负责。
- **保留无 ID 保存回退**：隐藏调用方遗漏 ID，减少报错。但可能串会话；主入口已经解析 ID，应在保存前暴露缺失合同。
- **删掉 suggest DB 回退**：前端通常传 recent_messages，可减少查询。但接口仍允许省略，已有实际服务调用和覆盖，继续保留兼容。

## Consequences

active 查询和排序只有一个实现，读取不再修改其它会话。无索引旧 SQLite 的回归仍验证稳定选最新，但不再期待自动停用；有索引库和 PG 并发回归验证唯一性与切换顺序。遗漏 session_id 的调用现在显式失败，不会悄悄写到当前 active。此变更不新增迁移。
