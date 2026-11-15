# Simplification & Bug-Fix Note: 清理吞错假成功、内部重复防御与零调用死代码

Status: implemented

## Problem

对照前后端全量代码审计，仓库存在三类过度防御与历史遗留负担：

1. **吞错伪装成空/成功状态或错误放行（Fail-Open）**：
   - 后端：`SessionLoader.attach_author_edits` 在 `select(File)` 异常时直接返回空字符串（宣称作者零改动、跳过 `_ReadSnapshots` 记录），且未回滚 `read_session`，在 PostgreSQL 上使后续 `attach_working_set` 连带触发 `InFailedSqlTransaction`；`summary_based_aggregation` 在 `_llm_judge_merge` / `_merge_cross_chunk_stories` 用宽泛 `except Exception` 吞掉瞬时模型错误与 `APIException`（试拆与日成本上限熔断），导致 `@api_task(retries=2)` 失效且超预算后继续请求后续批次；`writing_graph._agent_can_write_files` 用 `except Exception: return True` 包住仓内静态常量导入，异常时把只读角色当成可写角色；`redis_client.check_resend_cooldown` 与 `get_verification_attempts` 在 Redis 异常时返回 `False` / `0` 放行请求，`increment_verification_attempts` 则把超限与 Redis 异常混用同一个 `bool` 返回值。
   - 前端：`ProjectStatusDialog` 初次加载失败只写日志，展示空白可编辑表单与可点击保存按钮；`MessageInput` 的 `/` 技能菜单在接口报错时渲染「没有匹配的技能」；`InspirationGrid` 点击卡片拉取详情失败静默无反馈，`InspirationDetailPage` 并发切换时可能被旧请求覆盖状态；`useMaterialLibrary.loadPreview` 报错静默返回 `null`，且 `Editor.tsx` 用 `preview && previewEntityInfo` 门控导致首次加载态不可达并重复外层条件判断；`api.ts`（`exportApi.exportDrafts`、`fileApi.upload`、`fileApi.uploadDrafts`）与 `useExport.ts`（`exportDrafts`）在网关返回非 JSON 错误页（如 `502`）时硬编码伪造业务错误码（报「文件类型无效」或「没有可导出的正文」）。
2. **无人消费的校验计算、死管线与内部类型重复防御**：
   - 后端：素材上传与入库流程计算从未被读取的 `StoredObject.sha256`、`ChecksumResult.sha256_checksum`、`Chapter.content_hash`，并在 `novel_ingestion_v3_flow` 里对本地上传文件做冗余 `normalize_filename` + `shutil.copy2`；Neo4j 写入管线无任何读取端（配置类甚至缺失连接字段），却仍在阶段执行器、关系子流与结果构造器中维护多处 `neo4j_*` 状态分支；`get_material_summary` 仍在查询已退役的 `EventTimeline` 计数；多处内部已定型 SQLModel / dataclass 对象使用冗余 `getattr` / `hasattr` / `isinstance` 防御（如 `getattr(ch, "content", None)` 实际字段为 `original_content`）。
   - 前端：`naturalPolishApi` 保留后端从不裸反的字符串分支；`SkillReviewPage`、`CodeManagement`、`RedeemProModal`、`SkillsPage` 存在死分支与重复校验；`useThinkingVisibility` 每次挂载读取 `localStorage` 却无任何设置入口；多处纯客户端 SPA 代码保留无意义的 `typeof window === "undefined"` 分支，`onboardingPersona.ts` 保留已迁移至服务端 `onboardingPersonaApi.getState` 后的旧本地灰度时间窗门禁。
3. **零调用或仅测试自引用的僵尸模块与兼容桩**：
   - 后端：`QuotaService.consume_ai_conversation`、`single_flight_lock.py`、`mcp_tools._pending_empty_file_var` 平行上下文变量、`story_generation_flow.py` / `story_generation_tasks.py`、`FileProcessor`，以及 `upload.py`、`materials/helpers.py`、`materials/access.py`、`ingestion.py`、`features/export_service.py`、`subscription_service.py`、`points_service.py`、`stats_service.py` 中十余个零生产调用的旧辅助函数。
   - 前端：`subscriptionStore.ts`（未初始化时 `isPro()` 甚至误返 `true`）、`useQuota.ts`、`useThinkingVisibility.ts`、`SkillDiscoveryPage.tsx`、`memoryMonitor.ts`、`authz.ts`、`MobileChatInput.tsx`、`MobileSidebar.tsx`、`useVirtualizedTree.ts`、`fileTreeDrag.ts`、`useFileTreeDrag.ts`、`useModal.ts`、`useDelayedLoading.ts`、`ComingSoonState.tsx` 及三个未被引用的 `index.ts` barrel 文件和它们的孤儿测试。

## Decision

分四个有界批次完成修复与清理，补齐行为回归测试，数据库历史表与可空列定义保持兼容（不执行破坏性 `DROP TABLE/COLUMN` 迁移）：

1. **Batch A（后端吞错与 Fail-Open 修复）**：
   - `SessionLoader.attach_author_edits`：查询异常时回滚 `session` 防止污染后续 `attach_working_set`，并将待核对文件标记为差异未算出（复用 `<author_edits>` 的未完成披露语义），不伪装成零改动。
   - `summary_based_aggregation`：`summary_based_story_aggregation` 仅捕获 `(LLMOutputError, ValidationError, KeyError)`、`_llm_judge_merge` 仅捕获 `(LLMOutputError, ValueError, TypeError)` 并降级为不合并；让 `APIException`（成本/配额熔断）与瞬时传输/供应商异常向上抛出，恢复 `@api_task` 重试与预算硬停。
   - `writing_graph._agent_can_write_files`：直接导入 `AGENT_TOOL_NAME_MAP` 与 `FILE_WRITE_TOOL_NAMES`，移除 `try/except return True`。
   - `redis_client` 与 `verification_service`：以单次查询的 `get_resend_cooldown_ttl -> int | None` 替代双次查询的 `check_resend_cooldown`，消除冷却到期瞬间的二次查询竞态并在 Redis 异常（`None`）时拒绝发送；`get_verification_attempts` 与 `increment_verification_attempts` 统一返回 `int | None`，由 `verification_service.verify_code` 区分 Redis 故障（`verification_verify_error`）、并发超限（`verification_too_many_attempts`）与剩余次数提示。
2. **Batch B（前端吞错、竞态与错误码伪造修复）**：
   - `ProjectStatusDialog`：区分初次加载失败与空项目记忆，加载失败展示明确错误提示与重试按钮，并在恢复成功前禁用空白覆盖保存。
   - `MessageInput`：`/` 技能列表拉取失败时展示加载失败与重试入口，不再冒充「没有匹配的技能」。
   - `InspirationGrid` + `InspirationDetailPage`：卡片点击拉取详情失败时弹出真实错误提示；详情页按请求序号忽略已被新请求取代的过期响应，避免并发切换误报加载失败。
   - `useMaterialLibrary` + `MaterialPreview` + `Editor.tsx`：预览拉取前先写入 `previewEntityInfo` 使加载骨架屏可达，失败时清理状态并弹出错误提示，移除 `Editor.tsx` 重复的外层 `preview &&` 判断。
   - `api.ts` + `useExport.ts`：`exportApi.exportDrafts`、`fileApi.upload` 与 `fileApi.uploadDrafts` 统一走 `apiErrorFromPayload` 解析非 2xx 响应，网关非 JSON 异常按真实 HTTP 状态报错而非硬编码伪造 `ERR_EXPORT_NO_DRAFTS` / `ERR_FILE_TYPE_INVALID`；移除 `useExport.ts`（`exportDrafts`）未消费的 `error` 状态与 3 秒自动清理 `setTimeout`（`adminApi.ts` 与 `feedbackApi.ts` 已具备契约测试覆盖的 `apiErrorFromPayload` / 归一化逻辑，保持不变）。
3. **Batch C（后端冗余哈希、死管线与零调用代码清理）**：
   - 移除未消费的 `StoredObject.sha256`、`ChecksumResult.sha256_checksum`（保留 `NovelsService.get_by_content_hash` 实际使用的 `md5_checksum`）、`Chapter.content_hash` 运行时计算，以及 `novel_ingestion_v3_flow` 中的冗余 `normalize_filename` + `shutil.copy2`（保留 ORM 可空列定义以兼容既有数据库表）。
   - 移除无读取端的 Neo4j 客户端、链接任务、配置项、`stage_executor` / `relationship_flow` / `result_builder` 中的 `neo4j_*` 分支及 `requirements.txt` 中的 `neo4j` 依赖；删除死子流 `story_generation_flow.py` / `story_generation_tasks.py`；移除 `get_material_summary` 中已退役的 `EventTimeline` 计数查询。
   - 清理内部定型对象上的冗余 `getattr` / `hasattr` / `isinstance` 分支，统一 `mcp_tools.py` 至 `_PendingEmptyFileState`，删除零生产调用的死模块与旧辅助函数，并将 `tests/conftest.py` 中 `flows.database_session` 的导入提前到 `REDIS_URL` 清理之前，避免模块级 `load_dotenv()` 在首轮测试时把本地 `.env` 的 `REDIS_URL` 重新注入测试进程。
4. **Batch D（前端冗余防御与不可达模块清理）**：
   - 移除 `naturalPolishApi`、`SkillReviewPage`、`CodeManagement`、`RedeemProModal`、`SkillsPage` 中的死分支与重复校验；移除无设置入口的 `useThinkingVisibility`；在 `i18n-helpers.ts` 提供共用 `switchLocale` 收敛 `Dashboard.tsx` 与 `SettingsDialog.tsx` 的语言切换实现；清理纯浏览器路径中无意义的 `typeof window` 分支与 `onboardingPersona.ts` 已废弃的本地灰度门禁函数（保留 `OnboardingPersonaPage` 使用的本地读写辅助函数）。
   - 删除零生产引用的前端模块（`subscriptionStore.ts`、`useQuota.ts`、`useThinkingVisibility.ts`、`SkillDiscoveryPage.tsx`、`memoryMonitor.ts`、`authz.ts`、`MobileChatInput.tsx`、`MobileSidebar.tsx`、`useVirtualizedTree.ts`、`fileTreeDrag.ts`、`useFileTreeDrag.ts`、`useModal.ts`、`useDelayedLoading.ts`、`ComingSoonState.tsx` 及 3 个未引用 `index.ts` barrel）及其孤儿单测；将 `useQuota.sharedCache.test.tsx` 迁移为验证真实消费者使用的 `subscriptionQueryKeys.quota()` 与 `useAiMessageQuota()`，并同步更新 `2026-11-14-unified-quota-query-cache.md`。

## Alternatives considered

- **只修吞错（Category 1），保留所有零调用模块与 `getattr` 防御以减少改动面。** 最强理由是改动行数最少。未采用：零调用代码（如未初始化即判定 `isPro() === true` 的 `subscriptionStore`、缺配置字段必崩的 `Neo4jClient`、绕开 `reserve/refund` 合同的 `consume_ai_conversation`）会持续误导后续开发与测试，且用户已明确授权一次性全量处理。
- **连同数据库历史死表（`EventTimeline`、`Outline`、`GeneratedContent` 等）和 `Chapter.content_hash` 列一起写 Alembic `DROP` 迁移。** 最强理由是模型与数据库表彻底一致。未采用：线上 PostgreSQL 删表/删列属于不可逆存储变更，超出代码清理边界且影响回滚安全；仅移除未使用的业务代码与运行时无效计算即可消除维护与运行成本。
- **重做 `adminApi.ts` 的全部外层响应归一化函数。** 最强理由是减少字段转换代码。未采用：`adminApi.ts` 的归一化函数由现有后台契约测试明确锁定（兼容分页包装与可空字段），保留这些显式边界转换比静默删除契约保障更稳妥。

## Consequences

- 收益：消除会话加载事务污染、素材聚合吞掉预算熔断与瞬时重试、只读角色导入异常放行写权限、Redis 故障放行验证码核验/重发，以及前端多处把接口故障伪装成空状态或伪造业务错误码的缺陷；删去数千行零调用死代码、Neo4j 死管线与每次上传/入库的多余哈希与文件拷贝。
- 代价：依赖已删除兼容桩或死模块的旧单测需同步迁移至真实生产入口或一并移除；内部受控契约不再靠 `getattr` 掩盖拼写错误的字段名，字段变更会在测试期直接暴露。
