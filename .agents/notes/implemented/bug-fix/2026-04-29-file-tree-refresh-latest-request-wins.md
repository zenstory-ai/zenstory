# Agent Note: 文件树刷新只采纳最新一次请求的结果，并为全树查询加活跃文件索引

Status: implemented

## Problem

AI 创建或更新章节后，左侧文件树不显示新文件，直到整页刷新。一次 Agent 运行期间聊天流会多次触发文件树刷新，各视图并发发出 `getTree()`，谁最后返回就采纳谁；大项目下 `/file-tree` 每次重建整棵树、耗时更长，让"旧响应覆盖新响应"的窗口更宽。

## Decision

`components/sidebar/FileTreePane.tsx` 与 `components/MobileFileTree.tsx` 各持有 `loadAbortControllerRef` 和递增的 `loadRequestIdRef`：发起新加载前 must abort 上一次在途请求；响应返回后若 signal 已 abort 或 requestId 不是最新，则丢弃；abort 错误是预期控制流，never 清空当前树。`fileApi.getTree(projectId, options?: RequestInit)` 接受可选 `AbortSignal`，不需要它的调用点保持原样。后端 `apps/server/database.py` 在 `init_db()` 用 `CREATE INDEX IF NOT EXISTS ix_file_project_active ON file (project_id) WHERE is_deleted = false` 覆盖全树刷新的访问模式，SQLite 与 PostgreSQL 都建。

来源：3ef311e（初始提交；设计稿 `docs/plans/2026-04-04-file-tree-refresh-race-fix-design.md`，已落地）

## Alternatives considered

仓库历史里只能看到当前做法，未见其他备选被记录。设计稿把"流式层多次触发刷新"列为根因之一，但采用的是响应侧守卫而非减少触发次数，理由未写明。

## Consequences

- 收益：树的最终状态总是最新一次成功响应；大项目全树查询走部分索引。
- 代价：每个文件树视图各自维护 abort/requestId 样板，新增文件树视图 must 复制同一守卫；索引在 `init_db()` 里以裸 SQL 维护，与 Alembic 迁移并行存在，是两套 schema 来源。

## Verification

`cd apps/web && pnpm exec vitest run src/components/sidebar` 中的文件树测试断言过期响应不覆盖新树；`grep -n ix_file_project_active apps/server/database.py` 确认索引 SQL 仍接在启动路径上。
