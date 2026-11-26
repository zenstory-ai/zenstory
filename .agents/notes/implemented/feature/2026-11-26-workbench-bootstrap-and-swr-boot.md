# 工作台聚合 Bootstrap 启动接口与用户隔离 SWR 缓存

## Problem

在跨洋访问 `app.zenstory.ai`（Vercel）→ `api.zenstory.ai`（Railway）时，直接打开或刷新 `/project/:projectId` 存在明显的三段串行启动瀑布：

1. **认证阻塞**：`AuthContext` 仅在 5 分钟 `localStorage` TTL 内允许乐观渲染；超过 5 分钟刷新标签页会阻塞在 `/api/auth/me` 完成前，导致受保护路由与工作台请求无法并行发起。
2. **工作台多跳串行请求**：认证放行后依次发起 `GET /api/v1/projects`、`GET /api/v1/projects/{id}/file-tree`、`GET /api/v1/files/{lastFileId}`、`GET /api/v1/chat/session/{id}/recent?limit=50`；不仅增加了跨洋 RTT 次数，也一次性水合了过多聊天历史。
3. **连接预热与重复刷新白屏**：`index.html` 缺少静态 `preconnect` / `dns-prefetch`，且刷新页面时缺少安全的用户级 SWR 即时绘制，用户在网络往返期间只能看到加载占位。

同时必须满足三项硬约束：
- Vercel 比 Railway 早约 25 分钟上线，前端必须兼容旧后端（`/bootstrap` 返回 404/405 时无感回退）。
- 严格保护 `AGENTS.md` 中的未保存草稿恢复与版本冲突契约，绝不基于陈旧缓存静默覆盖草稿或误报草稿已保护。
- 严格隔离账号态，登出、401/403 或切换账号时立即清空所有会话缓存。

## Decision

1. **后端聚合引导接口 `GET /api/v1/projects/{project_id}/bootstrap`**：
   - 新增可选参数 `file_id: str | None = None` 与 `chat_limit: int = Query(15, ge=1, le=50)`。
   - 复用 `get_current_active_user` 与 `verify_project_ownership`（与 `GET /api/v1/projects/{id}` 保持完全一致的 401/403/404 与超管语义）。
   - 复用共享实现 `build_project_file_tree(..., include_content=False)` 返回不含正文的文件树元数据；按「有效且属于当前项目的非文件夹 `file_id` → 最近更新的可编辑文件 → `null`」返回 `initial_file`（含完整 `content` 与 `updated_at` 并发版本令牌）；复用 `load_active_session_recent_messages` 以只读方式返回 `chat_session`、经 `client_tool_calls` + `client_message_metadata` 瘦身后的最近 `chat_limit` 条 `recent_messages`，并通过 `limit + 1` 单次查询计算 `has_more_messages`（不额外执行 `COUNT(*)`，也不会自动新建会话）。
2. **静态与构建感知的预连接（`apps/web/index.html` + `apps/web/vite.config.ts`）**：
   - 在 `index.html` `<head>` 中静态写入 `<link rel="preconnect" href="https://api.zenstory.ai" crossorigin />` 与 `<link rel="dns-prefetch" href="https://api.zenstory.ai" />`。
   - `apiPreconnectPlugin` 在生产构建保留静态标签不重复注入；显式 HTTPS staging 构建自动改写为 staging 域名；本地开发、HTTP E2E 与隔离 Preview 构建则剥离，避免误连生产域名。
3. **非阻塞会话态认证启动（`AuthContext.tsx` + `apiClient.ts` + `workbenchSwrCache.ts`）**：
   - 除既有 5 分钟 `localStorage` 缓存外，在 `sessionStorage` 写入不含任何 token/密钥的轻量用户信息快照（`zenstory:auth-session-user:v1`）。
   - 当 `access_token` 存在且命中有效缓存或同标签页快照（并与 `localStorage.user` 的 `id` 交叉核对一致）时，首帧直接以 `loading = false` 放行，使 `/bootstrap` 与后台 `/api/auth/me` 并行执行。
   - 若后台 `/api/auth/me` 返回 401（刷新失败或无 `refresh_token`）或 403，或触发登出/切换账号，`clearAuthStorage` 与 `AuthContext` 立即清除 `sessionStorage` 用户快照与全部工作台 SWR 缓存，受保护路由立刻回退到登录页。
4. **前端单次 `/bootstrap` 消费、404/405 回退与安全 SWR 缓存（`workbenchBootstrap.ts`、`ProjectContext.tsx`、`ProjectEditor.tsx`、`useFileTree.ts`、`Editor.tsx`、`ChatPanel.tsx`）**：
   - `ProjectEditor` 挂载即通过 `QueryClient` 作用域启动单次 `startProjectBootstrap`（`StrictMode` 下自动去重），把结果一次性分发给 `ProjectContext.hydrateProject`、`useFileTree`、`preloadedFileCache`（供 `Editor`）和 `ChatPanel`，消除首屏重复请求。
   - 当 `/bootstrap` 返回旧后端 `404`（非 `ERR_PROJECT_NOT_FOUND`）或 `405` 时，自动并行回退到既有 `projectApi.get` + `fileApi.getTree` + `getRecentMessages` + `fileApi.get`，确保 Vercel 先于 Railway 发布期间零故障。
   - `ChatPanel` 默认展示 `/bootstrap` 的 15 条最近消息；若 `has_more_messages` 为真，顶部提供「加载更早的对话」按钮按需加载更多历史（50 → 100），保持滚动位置稳定。
   - `sessionStorage` 按 `(userId, projectId)` 存有界 SWR 缓存（单条上限 350KB，最多 4 个项目；超限优先省略大正文并捕获配额异常）。若 `localStorage` 中已存在该文件的未保存草稿恢复快照（`editorDraftRecovery`），则跳过 SWR 正文预绘，等待权威 `/bootstrap` 返回后再执行草稿恢复/冲突判定；无未保存草稿时先渲染 SWR 正文，待 `/bootstrap` 返回新版本后通过 `WORKBENCH_FRESH_FILE_EVENT` 复用 `Editor` 的三方合并/冲突保护逻辑静默对齐，不发额外 `GET /files/{id}`。

## Alternatives considered

### 1. 仅在前端并行发起现有 4 个接口，不新增后端 `/bootstrap`
- **最强论据**：零后端改动、无跨服务发布协调成本，且可在前端用 `Promise.all` 并行掉一部分串行等待。
- **未采用原因**：在跨洋高延迟及 HTTP 连接竞争下，4 个独立鉴权/数据库事务请求仍需重复执行 JWT 校验、项目归属查询与多次数据库往返，且无法根据 `lastOpenedFile` 失效自动回退到最近编辑文件，也无法提供精确的 `has_more_messages` 标志来安全收缩首屏聊天历史体积。

### 2. 将工作台 SWR 缓存写入 `localStorage` 或 IndexedDB 以跨标签页复用
- **最强论据**：关闭并重新打开浏览器后也能直接命中缓存，首屏命中率高于 `sessionStorage`。
- **未采用原因**：小说正文属于作者私密创作内容，长期落盘到 `localStorage` 会增加多账号共享设备上的泄露面，且与 `editorDraftRecovery` 争抢有限的 `localStorage` 配额（可能导致未保存草稿写入失败）。将 SWR 限定在 `sessionStorage`、设定 15 分钟 TTL 与体积上限，并在登出/401/切号时全量清理，既能覆盖同标签页刷新与项目切回，又绝不侵占草稿保护的存储空间。

### 3. 直接用 SWR 缓存文件触发 `Editor` 的草稿恢复，等 `/bootstrap` 回来再二次弹窗
- **最强论据**：存在未保存草稿时也能更早画出编辑器正文区域。
- **未采用原因**：若服务器版本已在其他端或后台 AI 写入中推进到 `T2`，而 SWR 仍停留在 `T1`，先对 `T1` 跑恢复逻辑会误判为版本一致并自动把草稿套入编辑器，随后 `T2` 到达又突然切换为冲突态，造成闪跳甚至误导作者。遇到未保存草稿快照时跳过 SWR 正文预绘、仅预绘项目壳与文件树并等待权威 `/bootstrap`，严格守住冲突恢复的单一事实源。

## Consequences

- **Benefits**：
  - 跨洋刷新 `/project/:projectId` 时，TCP/TLS 预热提前到 HTML 解析阶段，认证不再阻塞受保护路由，首屏项目/文件树/当前文件/最近 15 条对话合并为 1 次 `/bootstrap` 往返；重复加载通过用户隔离 SWR 缓存即时绘制。
  - 首屏聊天水合量从 50 条降到 15 条，并保留完整的「加载更早的对话」能力。
  - 对 Vercel 先于 Railway 部署完全兼容，且 100% 保留既有认证安全边界与未保存草稿/版本冲突保护。
- **Costs**：
  - 后端新增一个聚合只读端点与响应契约，后续若修改文件树或最近消息字段需继续通过共享辅助函数保持一致。
  - 前端新增 `workbenchBootstrap` 与 `workbenchSwrCache` 协调层，维护时需保持登出/401/切号清理路径与草稿恢复跳过条件不被绕过。
