# Simplification & Bug-Fix Note: 第二轮逐文件清理有害异常吞没、内部重复防御与死契约

Status: implemented

## Problem

在第一轮清理后，对全栈 671 个生产代码文件（后端 345 个 `.py`、前端 326 个 `.ts/.tsx`）做逐行审计与调用链核验，仍发现三类影响正确性与维护成本的问题：

1. **有害防御（吞异常伪造业务状态、击穿预算熔断、污染事务、阻断正常功能）**：
   - `agent/`：`file_ops/project.py` 在 `_refresh_verified_progress` 查询文件异常时未回滚事务且伪造 `artifacts = []`，把已验证 `done` 任务倒退改写为 `pending` 写入 Redis；`_current_title_source` 在 `FOR UPDATE` 写事务内无 savepoint 捕获 SQL 异常导致外层 `commit()` 失败；`file_ops/edit.py` 的 `attach_author_edits_for_agent` 遗漏 `begin_nested()` 与未完成披露；`SuggestService.generate_suggestions` 顶层 `except Exception` 吞掉 ¥15 日成本护栏 `APIException(429/503)`；`ToolContext.short_lived_session` 共享会话分支、`assembler._get_attached_library_materials` 与 `service._lookup_project_type/_is_first_ai_conversation` 异常缺 `rollback()`。
   - `flows/`：`exceptions.py` 的故障分类漏掉 `ERR_MATERIAL_TRIAL_COST_LIMIT`、`ERR_QUOTA_AI_DAILY_COST_EXCEEDED`、`ERR_AI_COST_BUDGET_UNAVAILABLE`、`ERR_MATERIAL_TRIAL_LIMIT_INVALID`，且 `story_aggregate_flow`、`character_entity_build_flow`、`relationship_tasks`、`handle_orphan_plots`、`StageExecutor._record_meta_failure` 在批次循环中吞掉预算熔断或篡改 `error_code`；`relationship_tasks` 产出角色名而入库期待角色 ID 导致关系记录 100% 静默丢弃；`handle_orphan_plots._execute_assignments` 循环 `flush()` 异常无 savepoint/`rollback()`；`StageExecutor.finalize` 吞掉终态写库失败仍广播 100% 完成导致后续超时误退费。
   - `api/` & `services/`：`points_service.redeem_for_pro` 与 `redemption_service.redeem_code` 在 `commit()` 后用 `suppress(Exception)` 包裹 `refresh()` 随后读取过期属性，导致扣分成功后抛 500；`auth_service` 密码 72 字节截断按字符切片且与 `verify_password` 不对称；`referral_service.validate_invite_code` 把 SQL 异常伪装成“邀请码无效”且漏 `rollback()`；`verification_service` 忽略发码状态重置失败；`batch_import_materials`、`_to_admin_feedback_item`、`auth/files` 激活埋点、`versions` 与 `files` 上传存在吞错伪造 200/400/404 或漏 `rollback()`。
   - `web/`：`chunkRecovery.lazyRoute` 在模块加载成功后裸调 `sessionStorage`，在受限存储环境下阻断全站懒加载路由；`authApi`、`materialsApi.upload`、`materialUploadValidation`、`feedbackApi` 裸调 `response.json()`、伪造归咎用户的错误码或直接向 UI 吐出英文 `ERR_*`；`dashboardInspirationSource` 永久缓存失败请求；`adminApi` 统计接口在响应损坏时伪造全 0 经营数据；多处组件（`ProjectSwitcher`、`Editor` 审阅完成与附加素材、`UserMenu` 签到、`AgentApiKeysPanel` 密钥复制等）吞掉报错渲染伪空态或点击无反馈；`ProjectContext.deleteProject` 在纯状态更新函数内触发跨状态与存储副作用且缺跨账号守卫。
2. **内部冗余防御、遮蔽/序列化缺陷与零消费计算**：
   - `natural_polish_service.py` 模块级同名 `_SENTENCE_CLOSERS` 二次赋值遮蔽导致中文闭合标点守卫退化；`character_tasks_v2`、`handle_orphan_plots`、`summary_based_aggregation` 将 JSON 字符串列当 `list[str]` 逐字符遍历，`filters.py` 将单字别名过滤绑在 `and logger` 上；`admin/subscriptions/{user_id}` 查出并裸序列化未消费的 `UsageQuota`（暴露内部成本字段）；`DiffReviewSplitView` 挂载悬空虚拟列表盲滚、`ImportMaterialDialog` 推荐目录受控值自相矛盾；`useInspirations` 每次挂载白发无人读取的 `/featured?limit=6` 请求；`voice.py` 3MB 音频双倍解码、`nodes.py` 双倍评估、`plot_tasks` 冗余校验任务、`stream_processor.history_buffer`、`useAgentStream` 未读状态、`ToolResultCard` 166 行 `response` 死分支、`MessageList` `streamingMessageId` 死链路，以及 `adminApi.ts` 与后端真实 Schema 不符的虚构多形态兼容分支与纯 SPA `typeof window` 守卫。
3. **零生产调用模块、函数、模板、兼容垫片与旧路由**：
   - 后端：`flows/utils/helpers/{prompt_builder,index,scan,text}.py`、`services/subscription_service.py`、`services/utils/`、`migrations/add_soft_delete_to_project.sql`、`services/__init__.py` 零调用 `sys.modules` 垫片、`prompts/` 8 个工厂函数与 14 个 `.j2` 模板、`core/permissions.py` 死配额装饰器、12 个未引用 `ErrorCode`、`_start_flow_deployment` 无 `job_id` 危险回退、各模块内数十个零生产调用辅助函数，以及全栈零调用的 8 组旧 HTTP 路由（含 URL Query 明文传 JWT 的 `GET /api/auth/validate-token`）。
   - 前端：`CommonProviders.tsx` 空壳、`MobileTable`、重复的 `PageLoader`/`InlineLoader`、未用 `Skeleton`/`Modal`/`icons.ts`（44 个图标）导出、死 Hook/工具函数（`throttle`、`useGestures`、`useTouchSupport` 等）、14 个零调用 API 方法、`adminApi.ts` 56 个重复顶层导出函数，以及 `types/index.ts` 中 230+ 行重复过期类型与废弃 `AgentEnhancementEvent` 家族。

## Decision

已按四个互不重叠的有界模块分片完成修复、回归测试补齐与清理（保持 `prefect.yaml` 部署注册契约与数据库历史表 Schema 不变）：

1. **分片 1（`apps/server/agent/`）**：修复 `project.py`、`edit.py`、`suggest_service.py`、`assembler.py`、`service.py`、`mcp_tools.py` 的 savepoint/`rollback()`、成本护栏 `APIException` 透传与 `author_edits` 未完成披露（保留 `ContextAssembler.assemble` 越权返回空上下文的数据隔离契约与 `SuggestService.__init__` 无客户端降级契约）；移除双倍 `evaluate_agent_output`、`TOOL_USE` 增量死状态机、`history_buffer`、`AuthorScope` 空壳字段及零生产调用枚举/类/函数，并新增 `test_round2_agent_error_isolation.py` 回归覆盖。
2. **分片 2（`apps/server/flows/` + `prompts/`）**：补齐 `exceptions.py` 预算/试用熔断码识别（`is_llm_fatal_stop`）并在各子流/任务中透传熔断与全败错误；在 `build_character_relationships_task` 补齐角色名/别名→ID 映射并移除吞掉重试的批次 `except Exception`；为 `_execute_assignments` 增加 savepoint 隔离；让 `StageExecutor.finalize` 终态写库失败真实报错；修复 `character_tasks_v2` / `handle_orphan_plots` / `summary_based_aggregation` JSON 列表解析与 `filters.py` `and logger` 缺陷；移除冗余 `validate_plots_task`、重复读盘、死分支、4 个零调用 helper 模块、死任务及 8 个零调用 prompt 工厂与 14 个 `.j2` 模板。
3. **分片 3（`apps/server/{api,services,models,core,middleware,config,utils,main.py,migrations}`）**：修复兑换 `refresh` 后置 500、密码 UTF-8 72 字节对称截断、邀请码/任务板/验证码存储故障伪装与路由漏 `rollback()`；修复 `natural_polish_service` `_SENTENCE_CLOSERS` 遮蔽；移除 `admin/subscriptions/{user_id}` 裸 `UsageQuota` 暴露、`voice.py` 双倍解码与路由重复校验；删除零调用代码、兼容垫片、孤儿 SQL 文件及经确认的 8 组零生产调用 HTTP 路由。
4. **分片 4（`apps/web/`）**：为 `chunkRecovery` 补齐存储异常隔离；收敛 `authApi`、`materialsApi.upload`、`materialUploadValidation`、`feedbackApi` 错误解析与翻译；修复 `dashboardInspirationSource` 失败缓存、`ProjectContext` 纯 updater 副作用与跨账号竞态、各组件伪空态与静默点击失败、`DiffReviewSplitView` 悬空虚拟器与 `ImportMaterialDialog` 下拉受控值；按后端真实 FastAPI Schema 收紧 `adminApi.ts`（保留 `AdminPlan.effective_features`）并同步更新单测；移除 `useInspirations` 冗余请求、`useAgentStream` 未读状态、`ToolResultCard` `response` 死分支、`MessageList` `streamingMessageId` 死链路、纯 SPA `typeof window` 守卫以及全部零生产调用组件、函数、API 方法、图标重导出与重复类型。

## Alternatives considered

- **保留 8 组零调用旧 HTTP 路由与 `adminApi.ts` 虚构兼容分支不动。** 最强理由是无需调整旧路由单测与 `adminApi.test.ts` 的合成 Mock。未采用：`GET /api/auth/validate-token?token=...` 存在查询串泄露 Bearer JWT 的真实安全隐患，`adminApi.ts` 的全 0 默认回退与虚构字段既丢弃了真实的 `effective_features` 又掩盖后台契约故障，且用户已明确同意统一清理。
- **一并删改 `prefect.yaml` 中的 3 个子流 deployment。** 最强理由是与生产仅调度 `novel_ingestion_v3` 完全一致。未采用：按 `AGENTS.md` 规范，变更 Prefect deployment 必须先在线上暂停并排空对应 worker pool，脱离发布窗口单改配置会破坏部署门禁。

## Consequences

- 收益：消除任务板误回退、事务中毒、拆书预算熔断失效、人物关系静默全丢、去 AI 味中文闭合标点守卫被遮蔽、积分兑换偶发误报 500、受限浏览器懒加载白屏、上传弹窗裸露 `ERR_*` 等一批隐蔽生产缺陷，同时进一步削减数千行死代码、死模板与冗余网络/CPU 开销。
- 代价：直接请求已移除旧路由或旧内部辅助函数的历史单测需同步迁移或移除，`adminApi.test.ts` 需对齐后端真实 Pydantic Schema。
