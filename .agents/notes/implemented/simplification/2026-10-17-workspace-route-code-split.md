# 工作台按项目路由加载

## Problem

App 顶层直接导入 Layout、Sidebar、Editor、ChatPanel，并包含只属于项目编辑页的 ProjectEditor；公共首页和登录页因此也加载这组工作台依赖。上一轮生产构建入口 JS 约 1.05 MB；仅把代码挪到另一个立即导入的文件不会减少实际初始下载。

## Decision

新增实际 App 模块加载回归，旧实现会执行 4 个工作台模块而失败；新实现导入公共路由时不执行它们。完整 ProjectEditor 及其局部 helper 原样移到 pages/ProjectEditor.tsx，通过既有 lazyRoute/chunkRecovery 在同一个项目 route 位置加载。移动前后 helper 与组件函数体的标准化哈希相同。不分别按移动 tab 卸载 Editor/ChatPanel，不调整 provider/auth/onboarding/error boundary 层级、文件恢复/版本令牌、SSE 或 FirstWorkOpenTracker。

保持 ProtectedRoute → ProtectedProviders → ProjectEditor 层级与既有 Suspense/PageLoader。App guards/providers/last-file 与 chunk/draft/streaming 回归保持通过；没有云操作、付费调用、新依赖或上线。

## Alternatives considered

- **给单个 Editor/ChatPanel 各加 lazy。** 改动几行，但会在工作台内形成多个独立加载边界，额外打断原同时挂载关系；项目路由整体是现有页面切分模式，更能保持内部生命周期。
- **只拆 manualChunks。** 能缩小入口文件，但静态 import 闭包仍需下载全部工作台依赖，不能证明首屏传输减少。
- **按移动 tab 条件挂载。** 可省初始工作，但会丢失未保存编辑器/聊天与文件状态，违反 DESIGN 的 mounted/inert 契约。

## Consequences

公共入口不再静态加载工作台模块。项目页冷首次进入新增一个约 452.7 kB（gzip 约 121.8 kB）的工作台 chunk 边界，沿用 PageLoader 和既有 chunk 失败恢复保护；内部编辑器/聊天仍同时挂载。不是删除功能或缩减模型输入。尚未测量真实用户冷首开耗时，不把构建大小或本地浏览器正确性换算成生产 TTI。

## Verification

- Vite manifest 遍历入口的全部静态 imports（包含共享 vendor，不只比较 index 文件）：JS 原始总量 1,258,920→698,566 bytes，逐文件 gzip（同一压缩设置）总量 377,648→228,250 bytes，下降 39.56%；ProjectEditor 是独立 dynamic entry，不在初始静态闭包内。两边初始 CSS 都是同一 hash 的 `index-B4i45TxI.css`。
- 公共 App 模块加载回归在旧源码上因 Layout/Sidebar/Editor/ChatPanel 四个工厂均执行而失败；新实现通过。完整前端 Vitest 298 files，4,676 passed、1 skipped；ESLint、token/i18n 检查、TypeScript 通过。
- 本地 Chromium 对构建产物的 17 个独立场景通过：登录页与未登录项目跳转不请求工作台模块，唯一一次模拟登录后按需加载；桌面/手机 SSE 与刷新后历史顺序、同轮二次 AI 写入、作者冲突处理、删除后的脏稿保留；chunk 恢复比较、普通重载恢复和移动 tab 保持原 textarea 节点。API/SSE 均为 mock，外部访问被阻止。
- 新浏览器用例首次因测试写成不存在的 `login-button` 而超时；纠正为既有 `login-submit` 后单独通过，原始失败 trace 与其余 13 项成功记录保留，未改产品或放宽超时。另 3 项恢复/移动生命周期通过。
- 原始构建、manifest 对照、函数体等价、测试与浏览器 JSON/trace 归档于本地 OMX performance-goal `frontend-backend-local-20261017`。独立 reviewer 0 findings；本批只本地 commit。
