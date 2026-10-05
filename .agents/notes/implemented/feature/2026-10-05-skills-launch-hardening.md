# Agent Note: 技能系统上线前加固（审核材料、下架、内容预算、同名技能、贡献积分）

Status: implemented

关联：`../architecture/2026-09-27-standard-skills-without-execution.md`（技能格式与渐进式加载）。

## Problem

上线前审查发现技能系统有几处会被用户直接碰到、或会放大成本与风险的缺口：

- **审核材料不全**：`PendingSkillResponse` 只有名称、描述、正文、分类。分享时复制到公共副本的 `references/`、`assets/` 资源、`tags`（触发词）、`skill_metadata` 审核者都看不到；审核页只显示渲染后的 markdown，link reference definition、HTML 注释这类隐藏内容看不见。核准后这些内容会原文进入其他用户的 agent（system prompt、`load_skill`、`read_skill_resource`），构成有前提的跨用户 prompt injection。
- **无法下架**：admin 只有 pending → approve / reject，已核准的公共技能出问题只能手改 SQL，也没有审计记录；`GET /skills`、`/skills/my-skills` 的已添加列表也不过滤 `status == "approved"`，手改状态后用户仍看得到全文。
- **技能正文没有 token 预算**：正文上限 5 万字符（约 6 万 tokens），显式选择 3 个就能把十几万 tokens 塞进 system prompt，被每轮工具迭代、每个角色重复发送；`load_skill` 也原样返回全文。
- **同名技能互相遮蔽**：目录只列名称，`find_active_skill` 按名称取第一个，同名的第二个技能模型永远载入不到。
- **贡献积分是空头承诺**：「贡献技能到公共库 +50」从未发放（全库没有 `skill_contribution` 的 `earn_points` 调用），送审（pending）当下卡片就显示已完成；「灵感投稿」「完善资料」两张卡同样没有任何发放路径。
- **输入校验与体验**：内容含 NUL 或分享分类超过 `VARCHAR(50)` 时 PostgreSQL 写库 500；任意分类字符串会进入公开分类列表；技能页增删改失败没有任何提示，表单无长度限制，编辑时清空描述不生效；发现页没有分页（第 21 个以后的公共技能看不到），搜索框每次按键（含输入法组字中间态）都发请求，而且列表每项都带 5 万字正文。

## Decision

1. **审核材料完整**（`api/admin/skills.py`、`api/admin/schemas.py`）：
   - `GET /api/admin/skills/pending` 的每项增加 `tags`、`skill_metadata`（解析后的 JSON 对象）、`resource_count`、`source`。
   - 新增 `GET /api/admin/skills/{id}/resources`，返回该公共技能全部资源的 `path / size / content` 原文（不论审核状态，仅超级用户）。
   - `SkillReviewPage` 展开后默认显示正文**原文**（`<pre>`，可切换渲染预览），并列出触发词、`skill_metadata` JSON、每个资源文件的原文（`<details>` 展开）；卡片上标注资源文件数。
2. **下架**：新增 `POST /api/admin/skills/{id}/unpublish`（只接受 approved，否则 409），状态改为新值 `unpublished`（`public_skill.status` 是 `VARCHAR(20)`，没有 CHECK/enum 约束，不需要迁移），原因写入 `rejection_reason`，同一事务写 `AdminAuditLog(action="unpublish_skill")`。公共库列表、技能目录、`load_skill`、导出与资源读取本来就只认 approved；`GET /skills`、`/skills/my-skills` 的已添加查询补上 `PublicSkill.status == "approved"`。作者的 `UserSkill` 保留分享链接，`SkillResponse.share_status` 返回 `pending / approved / unpublished`，技能卡片显示「审核中 / 已公开 / 已下架」；下架后再次分享返回「已分享」，不能直接重投同一技能。驳回仍按原逻辑清掉链接，所以驳回状态不在这里显示。后台审核页增加「已下架」筛选和「下架」按钮（可填原因）。
3. **技能内容 token 预算**（`agent/skills/content_budget.py`）：一次请求里交给模型的技能内容（显式选择区块 + `load_skill` + `read_skill_resource`）合计不超过 `SKILL_CONTENT_TOKEN_BUDGET = 12_000` tokens（用 `estimate_text_tokens` 估算），其中显式选择区块最多占 `SELECTED_SKILLS_TOKEN_BUDGET = 8_000`，从最短的技能开始平分，短技能用不完的份额留给长技能。余额放在 `ToolContext`（`skill_content_budget`，线程安全），`AgentService` 用显式选择已占用的 token 初始化。超出部分截断：`load_skill` 返回 `truncated / next_offset / total_chars / continue_hint`，显式选择区块追加续读提示；`read_skill_resource` 新增 `offset` 参数，`path="SKILL.md"` 表示技能正文，资源与正文都按预算分段返回。预算用完后两个工具返回明确错误，提示基于已加载内容完成或让用户下一条消息继续。
4. **同名技能用 id 区分**：目录里名称（忽略大小写与空白）重复的技能标注 `(id: …)`，并在目录头说明同名时传 id；`find_active_skill` 先匹配 id、再匹配名称；按名称命中且有同名技能时，`load_skill` 结果附带 `same_name_skills`（id、名称、描述摘要）。工具描述同步说明。
5. **贡献积分**：`approve_skill` 对 `source == "community"` 且有作者的技能，在同一事务里调用 `points_service.award_skill_contribution`：先锁作者 `user` 行，再以 `(user_id, "skill_contribution", source_id=PublicSkill.id)` 查重，同一公共技能只发一次，发放额记入审计日志。积分卡「已完成」改为「有已核准的社区投稿或已领过贡献积分」。「灵感投稿」「完善资料」两张卡从 `get_earn_opportunities` 移除（`POINTS_CONFIG` 数值保留），等真正接入发放再加回。下架不收回已发积分。
6. **输入校验**（`api/skills.py`、`agent/skills/package.py`）：创建/编辑技能的文本字段与资源内容在 pydantic `BeforeValidator` 里清除 NUL（在长度校验之前，只剩 NUL 的名称回 422）；导入路径在 UTF-8 解码与 `_validate_parsed_skill` 里清除 NUL（覆盖 YAML `"\0"` 转义）。`ShareSkillRequest.category` 限定为 `writing / character / worldbuilding / plot / style`，其他值回 422。审核/下架原因限制 500 字符。编辑时描述传空字符串即清空（存为 NULL）。
7. **导入上传上限**：`_ImportUploadRoute` 仍按 `Content-Length` 在解析表单前拒收；端点读取前再用 `UploadFile.size` 检查、只读 `MAX_ZIP_BYTES + 1` 字节，`parse_skill_upload` 按实际字节数再查一次，超限一律 413（不再因扩展名不同而回 400）。没有 `Content-Length` 的分块请求在表单解析阶段仍会先落到临时文件，全局请求体上限由 ASGI 中间件另行处理（不在本次改动范围）。
8. **前端技能页**：创建/编辑/添加/移除/删除/批量删除失败时 `toast.error`，优先显示 `ApiError` 已翻译的后端信息（含 `error_detail` 原因），否则用 `skills:errors.*` 文案；表单 `maxLength` 与字数提示使用 `config/skills.ts` 的 `SKILL_FIELD_LIMITS`（与后端常量一致），触发词超过 50 个或单个超过 100 字时本地提示。发现页按 `PUBLIC_SKILLS_PAGE_SIZE = 20` 分页、底部「加载更多」（按 id 去重）；搜索框 300ms 防抖，输入法组字期间（`compositionstart` 到 `compositionend`）不发请求。
9. **公共技能列表只带预览**：`GET /public-skills` 的 `instructions` 只回传前 `LIST_INSTRUCTIONS_PREVIEW_CHARS = 500` 个字符，并以 `instructions_truncated` 标记；`GET /public-skills/{id}` 仍回传全文。发现页展开被截断的技能时再取详情。

## Alternatives considered

- **分享时拒收带资源的技能，或核准时才复制资源**。最强理由：不用改审核页，直接消除「审核看不到资源」这一缺口。被否：资源是标准技能包的一部分，拒收会让导入的外部技能无法分享；核准时复制仍然要求审核者先看过资源，问题没有消失，只是挪了位置。
- **下架复用 `rejected` 状态**。最强理由：不引入新状态值，前后端少改一处枚举。被否：`share_skill` 对 rejected 允许作者直接重投，作者端也会被清掉分享链接，看不到「已下架」；状态列是无约束的 `VARCHAR(20)`，新增 `unpublished` 不需要迁移，语义更清楚，审计记录也能区分「从未上架」与「上架后撤下」。
- **技能预算按表面分别设上限（显式选择一份、每次 `load_skill` 一份）而不是一次请求合计**。最强理由：实现简单，不需要在 `ToolContext` 里共享余额，模型也不会因为前面读得多而读不到后面的技能。被否：工具结果同样留在对话里每轮重发，分面上限挡不住「反复带 offset 续读」把整篇 5 万字读进上下文；合计上限才能真正封住单条消息的成本放大，截断时给出续读提示保证内容仍可分段获取（下一条消息重新计额）。
- **同名技能在创建/导入/添加时自动改名或拒绝**。最强理由：从源头消除歧义，目录和工具都不用改。被否：已添加的公共技能没有可改名的入口（`custom_name` 没有写接口），官方与社区技能同名很常见，强制改名会改变用户看到的名字；目录只给重复项标 id，对无重复的用户目录完全不变，也不破坏 prompt 缓存。
- **把三张无发放路径的积分卡都补上发放**。最强理由：保留对用户的激励承诺。被否：灵感投稿受功能开关控制、完善资料没有清晰的「完成」定义（只看头像），在上线前用小改动做对并防刷的把握不大；先把承诺撤下，技能贡献这张有明确审核节点的卡先做对。

## Consequences

- 收益：审核者核准前能看到会进入 agent 的全部原文，出问题的技能可以在后台一键下架并留下审计；单条消息的技能内容成本有硬上限，不会因为 3 个超长技能而撑爆上下文；同名技能都能被模型载入；贡献积分真实到账且不能靠反复送审刷分；NUL 与非法分类不再产生 500；技能页失败有反馈，发现页能浏览全部公共技能，搜索不再在输入法组字时连发请求，列表响应变小。
- 代价：超长技能需要模型多次调用 `read_skill_resource` 分段读取，单条消息里读不完的部分要等下一条消息；显式选择的长技能在 system prompt 里只有开头部分；预算按估算 token 计，与模型真实计数有偏差；「灵感投稿」「完善资料」积分卡暂时消失；下架后作者不能直接重投同一技能，只能新建；`GET /public-skills` 的 `instructions` 在列表里变成预览，其他消费方需要全文时必须改调详情接口；被驳回的分享仍然不在作者的技能卡上显示原因。

## Verification

- 后端：`tests/test_api/test_admin_skills_review.py`（审核材料、资源原文、下架与审计、核准发积分且幂等、送审不算完成）、`tests/test_api/test_skill_packages_api.py`（NUL 清除、分类白名单、清空描述、无 Content-Length 的超大上传 413）、`tests/test_api/test_public_skills.py`（列表预览 / 详情全文）、`tests/test_agent/test_skill_tools.py`（同名 id、截断与分段续读、预算与显式选择共享）、`tests/test_agent/test_service.py`（显式选择区块受预算限制并带续读提示）、`tests/e2e/test_points_contract_e2e.py`（积分卡列表）。
- 前端：`src/pages/__tests__/SkillsPage.test.tsx`（错误 toast、maxLength、清空描述、分享状态、加载更多、防抖与输入法、展开取详情）、`src/pages/admin/__tests__/SkillReviewPage.test.tsx`（原文、触发词、元数据、资源、下架）、`src/lib/__tests__/adminApi.test.ts`。
