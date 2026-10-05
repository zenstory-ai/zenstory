# Agent Note: 灵感、反馈、邀请码的管理员写操作补记 AdminAuditLog

Status: implemented

## Problem

codes、plans、points、prompts、skills、subscriptions、users 的管理员写操作都会写 `AdminAuditLog`，但以下端点只写 stdout 日志：

- `POST /api/admin/inspirations`：用 `session.get(Project, project_id)` 读取**任意用户**的项目，把所有文件复制成灵感；`source="official"` 时直接 `approved` 并公开。
- `PATCH /api/admin/inspirations/{id}`、`POST .../review`、`DELETE .../{id}`（硬删除）；
- `PATCH /api/admin/feedback/{id}/status`；
- `POST /api/admin/invites`（`ignore_max_limit=True`）。

管理员把别人的作品发布为官方模板之后，后台无法查到是谁、何时、发布了谁的项目。

## Decision

上述 6 个端点都调用 `admin_audit_service.log_action`，带 `request=http_request`（记录 IP 与 UA）：

| action | resource_type | 内容 |
| --- | --- | --- |
| `create_inspiration` | inspiration | new_value：灵感快照 + `project_id`、`project_owner_id`、`file_count` |
| `update_inspiration` | inspiration | old/new：`name`、`source`、`status`、`is_featured`、`sort_order`、`author_id` |
| `approve_inspiration` / `reject_inspiration` | inspiration | old/new 快照，new 含 `rejection_reason` |
| `delete_inspiration` | inspiration | old_value：快照 + `original_project_id` |
| `update_feedback_status` | feedback | old/new `{status}` |
| `create_invite_code` | invite_code | new_value：`code`、`max_uses`、`expires_at`、`ignore_max_limit` |

创建、更新、删除灵感与反馈状态用 `commit=False`，与业务写入同一事务提交。审核与邀请码的业务写入在 service 内部已提交，审计在其后单独提交。

## Alternatives considered

- **用中间件对所有 `/api/admin` 非 GET 请求自动记审计**。最强理由：一次覆盖，以后新增端点不会漏。被否：中间件拿不到旧值、资源 id 和业务含义（例如被发布项目的所有者），只能记 URL，审计价值低；现有端点都是显式记录，保持一致。
- **把审核与邀请码 service 改成不在内部提交，以便同一事务写审计**。最强理由：业务与审计原子一致。被否：这两个 service 还有其他调用方，改提交语义的影响面超出本次范围；审计失败时业务已提交，代价是少一条审计而不是数据不一致。

## Consequences

- 收益：发布他人项目为官方灵感、审核、删除、反馈处理、发放无上限邀请码都能在审计日志页按 action/resource 查到操作者、时间、IP 与前后值。
- 代价：审核与邀请码的审计不在业务事务内，审计写入失败时会返回 500 而业务已生效。灵感快照不含正文，只能追溯元数据。
