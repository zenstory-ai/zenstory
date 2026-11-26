# Agent Note: 技能目录触发提示、资源目录扩展、取消人为正文截断与服务端冷回放水合

Status: implemented

关联：[2026-09-27-standard-skills-without-execution](../architecture/2026-09-27-standard-skills-without-execution.md)（标准 SKILL.md 渐进式披露、永不执行脚本）、[2026-10-11-official-skill-text-hidden-and-identity-rule](2026-10-11-official-skill-text-hidden-and-identity-rule.md)（官方技能正文对用户隐藏、每轮最多加载 3 个不同技能）。

## Problem

1. **L1 技能目录信息量偏窄**：
   - `ActiveSkill` 与 `SkillContextInjector` 一直未读取 `UserSkill.triggers` / `PublicSkill.tags`，作者为技能配置的触发场景在系统提示词目录里完全不可见。
   - `CATALOG_DESCRIPTION_MAX_CHARS = 300` 过容易截断完整的方法定位说明（而技能包入库上限本身是 `MAX_DESCRIPTION_CHARS = 1024`），`CATALOG_MAX_CHARS = 8000` 在启用较多技能时也偏紧。
2. **标准技能包资源目录白名单偏窄**：
   - `ALLOWED_RESOURCE_ROOTS` 仅允许 `references/` 与 `assets/`，而社区和写作技能包中常见 `examples/`（范例）与 `templates/`（模板）目录，导入时会被当成目录外文件静默丢弃并告警，前端资源编辑器也不允许直接创建这两个目录下的文件。
3. **运行时人为 token 截断破坏完整写作方法**：
   - 早期 `SKILL_CONTENT_TOKEN_BUDGET` / `SELECTED_SKILLS_TOKEN_BUDGET` 在中文 `cl100k_base` 估算下会把合法的完整技能（上传上限已设为 5 万字符 / 单资源 64 KiB）切碎，迫使模型额外发起多次 `read_skill_resource(path="SKILL.md", offset=...)` 往返，且显式选择的长技能也会在首轮丢失后半段方法。
4. **冷回放场景丢失历史已加载技能正文**：
   - 落库在 `ChatMessage.tool_calls` 的技能结果卡片遵循前端安全口径（`stream_adapter._client_safe_skill_payload`），不落库技能正文；当 Redis `wire_prefix_cache` 冷启动或失效、需要从数据库重建跨轮历史时，此前已加载的技能只能附上 `_SKILL_CONTENT_NOT_KEPT` 提示让模型重新调用，增加冗余工具轮次。

## Decision

1. **L1 目录透出 `triggers` 并对齐描述上限**（`agent/skills/active_skills.py`、`agent/skills/context_injector.py`）：
   - `ActiveSkill` 新增 `triggers: tuple[str, ...] = ()`，由纯函数 `parse_skill_triggers` 从 `UserSkill.triggers` 与 `PublicSkill.tags`（公共技能触发词列）安全解析 JSON 字符串数组，格式损坏或非字符串元素静默忽略并按大小写折叠去重保序。
   - `CATALOG_DESCRIPTION_MAX_CHARS` 提高到 `1024`（与 `MAX_DESCRIPTION_CHARS` 对齐，所有合法技能描述均不被截断），`CATALOG_MAX_CHARS` 提高到 `16_000`。
   - `_catalog_line` 在技能存在触发词时追加紧凑的 `（触发场景：…）`，过滤掉已在渲染描述中原样出现的词以避免重复噪声，并受 `CATALOG_MAX_TRIGGERS_PER_SKILL = 6` 与 `CATALOG_TRIGGERS_MAX_CHARS = 120` 约束。数据库查询保持 `created_at, id` / `added_at, id` 确定性排序，维持系统提示词 KV 前缀缓存稳定。
   - 同步更新前端 `skills.json` 中英文 `form.triggersHint` 文案，如实说明标签同时作为触发场景提示供 AI 参考。
2. **扩展标准资源目录白名单**（`agent/skills/package.py`、`agent/tools/tool_inputs.py`、`apps/web/src/components/skills/SkillResourcesSection.tsx`、`apps/web/public/locales/{zh,en}/skills.json`）：
   - `ALLOWED_RESOURCE_ROOTS` 扩展为 `("references/", "assets/", "examples/", "templates/")`，同步更新路径校验错误文案、zip 导入忽略目录警告、`ReadSkillResourceInput.path` 工具参数描述、前端 `RESOURCE_PATH_PREFIXES` 与中英文提示/报错文案。`_locate_skill_md` 自动通过 `*ALLOWED_RESOURCE_ROOTS` 将 `examples/SKILL.md`、`templates/SKILL.md` 视为普通资源文件而非冲突入口。
   - 维持「永不执行技能脚本」边界：`scripts/` 及非白名单扩展名照旧拒绝/忽略并告警。
3. **移除运行时人为技能截断并守住核心安全不变量**（`agent/skills/content_budget.py`、`agent/tools/mcp_tools.py`、`agent/tools/tool_schemas.py`、`agent/service.py`、`agent/core/message_manager.py`）：
   - 对齐主流 Coding Agent（Claude Code / Codex）做法：依靠入库硬上限（`SKILL.md` ≤ 50,000 字符、单资源 ≤ 64 KiB、总资源 ≤ 256 KiB）+ `MAX_LOADED_SKILLS_PER_REQUEST = 3` / `MAX_SELECTED_SKILLS = 3` + 轮间压缩控制上下文体积，不再对 `load_skill`、`read_skill_resource`、显式选择技能（`_resolve_selected_skills`）或冷回放做人为 token 截断，一律返回完整文本（`truncated: False`）。
   - `read_skill_resource` 保留向后兼容的 `offset` 参数（校验 `0 <= offset <= len(content)`，返回 `content[offset:]`），并在 `offset > 0` 时在返回 `data` 中附带 `"offset": offset`；同时更新 `load_skill` / `read_skill_resource` 的工具描述去掉过时的截断提示。
   - **关键安全不变量 100% 保持不变**：`SkillContentBudget.admit_skill` 继续强制每轮最多加载 `MAX_LOADED_SKILLS_PER_REQUEST = 3` 个不同技能，`resolve_selected_skills` 继续强制最多 `MAX_SELECTED_SKILLS = 3` 个，且官方技能正文对浏览器/API 保持 100% 隐藏。
4. **服务端冷回放水合接入 `load_loop_history` 与前端安全卡片补齐**（`agent/skills/replay.py`、`agent/loop/history.py`、`agent/stream_adapter.py`）：
   - `stream_adapter._client_safe_skill_payload("read_skill_resource", ...)` 补充持久化 `skill_id`（及存在且 `> 0` 时的 `offset`），依然严格剔除 `content`，确保官方技能正文绝不进入 SSE 或聊天历史 HTTP 响应。
   - `replay.py` 提供 `index_active_skills` 与 `rehydrate_skill_tool_result(active_skills_by_id, session, tool_name, result_data, *, call_arguments=None)`：仅当持久化卡片中的 `skill_id` 仍存在于当前用户的 `active_skills_by_id`（由 `load_active_skills` 构造，强制用户归属、`is_active=True` 及 `PublicSkill.status == "approved"`）时，完整回填 `load_skill` 的 `instructions` + 最新 `resources`，或 `read_skill_resource` 的 `content`（支持 `path="SKILL.md"`、NFC 归一化资源路径及优先 `result_data["offset"]`、缺失时回退配对 `call_arguments["offset"]`）。
   - `agent/loop/history.py` 的 `load_loop_history` 仅对最近 `SKILL_REPLAY_RECENT_TURNS = 3` 个用户轮次（连续 steering user 行合并算同一轮）内的成功 `load_skill` / `read_skill_resource` 调用执行水合：惰性加载 `index_active_skills(load_active_skills(session, user_id))` 至多一次（无近期技能调用时零额外查库）；水合成功时直接作为内存模型输入的 `payload["data"]` 且不附 `_SKILL_CONTENT_NOT_KEPT`；技能被删除/停用/下架、缺少 `skill_id`、资源已删、`call_arguments`/`offset` 非法越界、或属于更早轮次时返回 `None` 并体面回退到 `_SKILL_CONTENT_NOT_KEPT`。绝不修改落库的 `ChatMessage.tool_calls` / `display_events` 或 `client_tool_calls()`。

## Alternatives considered

- **继续保留 24k / 16k token 的运行时截断**：
  - 最强理由：限制单次工具返回体积，防止极端长技能瞬间占用过多上下文。
  - 没采用：技能上传本身已有 50k 字符 / 64 KiB 硬上限，叠加单轮 3 个技能门禁与 300k 轮间压缩后风险完全可控；人为截断在中文 `cl100k_base` 虚高估算下频繁切碎合法写作方法，损害写作质量并引入多余工具轮次。
- **直接把技能正文写入数据库 `ChatMessage.tool_calls` 并在 `client_tool_calls` 中剥离**：
  - 最强理由：冷回放无需额外查库，历史卡片与当时模型看到的内容逐字节一致。
  - 没采用：违反官方技能正文不落入聊天记录与前端安全口径的既定契约，且技能被用户停用/删除/下架或编辑修正后，历史快照仍会继续向模型暴露过期或已撤销的正文。
- **对全会话所有历史轮次都无条件水合技能正文**：
  - 最强理由：无论多少轮前加载过的技能都不必重调。
  - 没采用：长会话尚未触发压缩前若包含早期探索阶段加载的多个无关技能，冷回放时全部注回会膨胀输入并稀释注意力；限定最近 3 个用户轮次（`SKILL_REPLAY_RECENT_TURNS = 3`）兼顾多轮续写连贯性与上下文简洁。
- **在冷回放找不到 `skill_id` 时按 `skill_name` 回退匹配**：
  - 最强理由：旧版落库的 `read_skill_resource` 卡片未保存 `skill_id`，按名匹配可覆盖存量历史。
  - 没采用：同名技能在删除自建技能后会被公共技能静默顶替，存在跨技能串扰与权限语义失真风险；缺失 `skill_id` 时返回 `None` 回退到 `_SKILL_CONTENT_NOT_KEPT` 更安全。

## Consequences

- **收益**：
  - 模型在 L1 目录即可看到完整技能描述（≤ 1024 字无截断）与去重后的触发场景，选技能更准且保持前缀缓存顺序稳定。
  - 外部标准技能包与作者自定义技能可使用 `examples/` 与 `templates/` 目录存放写作范例与模板，前后端校验、工具入参描述与文案完全一致。
  - 合法长度（≤ 50k 字符 / ≤ 64 KiB）的技能与参考文件在显式选择、`load_skill`、`read_skill_resource` 和冷回放中均一次拿到全文，消除截断与分页往返，同时守住单轮 3 技能门禁与官方技能正文对用户不可见红线。
  - Redis `wire_prefix_cache` 冷启动或失效时，最近 3 轮内已加载的启用技能自动从 DB 安全回填，减少模型重复调技能的额外往返；没有技能调用的会话零额外数据库查询。
- **代价**：
  - L1 目录上线后因描述上限提高与新增触发场景提示，现有活跃会话的系统提示词前缀缓存会在发布后刷新一次。
  - 同时启用 3 个接近 50k 字符上限的技能并在同一轮全部加载时，单轮上下文会更早逼近 300k 轮间压缩阈值，由既有轮间压缩机制自动收敛。
