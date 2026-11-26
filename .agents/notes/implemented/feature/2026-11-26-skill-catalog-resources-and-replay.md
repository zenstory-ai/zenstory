# Agent Note: 技能目录触发提示、资源目录扩展、内容预算放宽与服务端冷回放水合

Status: implemented

关联：[2026-09-27-standard-skills-without-execution](../architecture/2026-09-27-standard-skills-without-execution.md)（标准 SKILL.md 渐进式披露、永不执行脚本）、[2026-10-11-official-skill-text-hidden-and-identity-rule](2026-10-11-official-skill-text-hidden-and-identity-rule.md)（官方技能正文对用户隐藏、每轮最多加载 3 个不同技能）。

## Problem

1. **L1 技能目录信息量偏窄**：
   - `ActiveSkill` 与 `SkillContextInjector` 一直未读取 `UserSkill.triggers` / `PublicSkill.tags`，作者为技能配置的触发场景在系统提示词目录里完全不可见。
   - `CATALOG_DESCRIPTION_MAX_CHARS = 300` 过容易截断完整的方法定位说明，`CATALOG_MAX_CHARS = 8000` 在启用较多技能时也偏紧。
2. **标准技能包资源目录白名单偏窄**：
   - `ALLOWED_RESOURCE_ROOTS` 仅允许 `references/` 与 `assets/`，而社区和写作技能包中常见 `examples/`（范例）与 `templates/`（模板）目录，导入时会被当成目录外文件静默丢弃并告警，前端资源编辑器也不允许直接创建这两个目录下的文件。
3. **单轮技能内容 token 预算偏紧**：
   - `SKILL_CONTENT_TOKEN_BUDGET = 12_000`、`SELECTED_SKILLS_TOKEN_BUDGET = 8_000` 在中文 `cl100k_base` 估算（汉字约 1.5–2.2 token/字）下，一份 ~7,000 字的完整写作方法就容易被提前截断，迫使模型额外发起 `read_skill_resource(path="SKILL.md", offset=...)` 续读往返。
4. **冷回放场景丢失历史已加载技能正文**：
   - 落库在 `ChatMessage.tool_calls` 的技能结果卡片遵循前端安全口径（`stream_adapter._client_safe_skill_payload`），不落库技能正文；当 Redis `wire_prefix_cache` 冷启动或失效、需要从数据库重建跨轮历史时，此前已加载的技能只能附上 `_SKILL_CONTENT_NOT_KEPT` 提示让模型重新调用，增加冗余工具轮次。

## Decision

1. **L1 目录透出 `triggers` 并放宽截断上限**（`agent/skills/active_skills.py`、`agent/skills/context_injector.py`）：
   - `ActiveSkill` 新增 `triggers: tuple[str, ...] = ()`，由纯函数 `parse_skill_triggers` 从 `UserSkill.triggers` 与 `PublicSkill.tags`（公共技能触发词列）安全解析 JSON 字符串数组，格式损坏或非字符串元素静默忽略并按大小写折叠去重保序。
   - `CATALOG_DESCRIPTION_MAX_CHARS` 从 `300` 提高到 `600`，`CATALOG_MAX_CHARS` 从 `8000` 提高到 `10_000`。
   - `_catalog_line` 在技能存在触发词时追加紧凑的 `（触发场景：…）`，过滤掉已在渲染描述中原样出现的词以避免重复噪声，并受 `CATALOG_MAX_TRIGGERS_PER_SKILL = 6` 与 `CATALOG_TRIGGERS_MAX_CHARS = 120` 约束。数据库查询保持 `created_at, id` / `added_at, id` 确定性排序，维持系统提示词 KV 前缀缓存稳定。
   - 同步更新前端 `skills.json` 中英文 `form.triggersHint` 文案，如实说明标签同时作为触发场景提示供 AI 参考。
2. **扩展标准资源目录白名单**（`agent/skills/package.py`、`apps/web/src/components/skills/SkillResourcesSection.tsx`、`apps/web/public/locales/{zh,en}/skills.json`）：
   - `ALLOWED_RESOURCE_ROOTS` 扩展为 `("references/", "assets/", "examples/", "templates/")`，同步更新路径校验错误文案、zip 导入忽略目录警告、前端 `RESOURCE_PATH_PREFIXES` 与中英文提示/报错文案。`_locate_skill_md` 自动通过 `*ALLOWED_RESOURCE_ROOTS` 将 `examples/SKILL.md`、`templates/SKILL.md` 视为普通资源文件而非冲突入口。
   - 维持「永不执行技能脚本」边界：`scripts/` 及非白名单扩展名照旧拒绝/忽略并告警。
3. **放宽单轮技能内容 token 预算并守住安全不变量**（`agent/skills/content_budget.py`）：
   - `SKILL_CONTENT_TOKEN_BUDGET` 从 `12_000` 提高到 `24_000`，`SELECTED_SKILLS_TOKEN_BUDGET` 从 `8_000` 提高到 `16_000`。
   - **保持 `MAX_LOADED_SKILLS_PER_REQUEST = 3` 不变**，且官方技能正文对浏览器/API 保持 100% 隐藏。
4. **新增服务端冷回放水合纯辅助函数**（`agent/skills/replay.py`，经 `agent/skills/__init__.py` 导出）：
   - 提供 `index_active_skills` 与 `rehydrate_skill_tool_result(active_skills_by_id, session, tool_name, result_data, *, remaining_budget)` 及 `SKILL_REPLAY_TOKEN_BUDGET`。
   - 仅当持久化卡片中的 `skill_id` 仍存在于当前用户的 `active_skills_by_id`（由 `load_active_skills` 构造，强制用户归属、`is_active=True` 及 `PublicSkill.status == "approved"`）时，才按需回填 `load_skill` 的 `instructions` + 最新 `resources`，或 `read_skill_resource` 的 `content`（支持 `path="SKILL.md"`、NFC 归一化资源路径及 `offset`），并原地扣减共享 `SkillContentBudget`。
   - 技能被删除/停用/下架、缺少 `skill_id`（绝不按 `skill_name` 猜测，防止同名顶替）、资源已删、`offset` 非法或越界、或回放预算耗尽时返回 `None`，让 `agent/loop/history.py` 在集成阶段体面回退到既有的 `_SKILL_CONTENT_NOT_KEPT` 说明。
   - 本批刻意不修改 `agent/loop/history.py`，待并行压缩分支合并后用 ~10 行接线调用，避免合并冲突。

## Alternatives considered

- **直接把技能正文写入数据库 `ChatMessage.tool_calls` 并在 `client_tool_calls` 中剥离**：
  - 最强理由：冷回放无需额外查库，历史卡片与当时模型看到的内容逐字节一致。
  - 没采用：违反官方技能正文不落入聊天记录与前端安全口径的既定契约，且技能被用户停用/删除/下架或编辑修正后，历史快照仍会继续向模型暴露过期或已撤销的正文。
- **在冷回放找不到 `skill_id` 时按 `skill_name` 回退匹配**：
  - 最强理由：旧版落库的 `read_skill_resource` 卡片未保存 `skill_id`，按名匹配可覆盖存量历史。
  - 没采用：同名技能在删除自建技能后会被公共技能静默顶替，存在跨技能串扰与权限语义失真风险；缺失 `skill_id` 时返回 `None` 回退到 `_SKILL_CONTENT_NOT_KEPT` 更安全。
- **同步放宽 `MAX_LOADED_SKILLS_PER_REQUEST` 或引入项目级技能、`allowed-tools` 运行时限制与脚本执行**：
  - 最强理由：能力更接近本地 CLI 编码 agent 的完整技能规范。
  - 没采用：产品决策明确排除项目级技能、前端调用控制/参数替换、`allowed-tools` 运行时拦截与脚本执行；每轮 3 个技能的硬上限是防批量倾倒官方技能的核心护栏，不得放宽。

## Consequences

- **收益**：
  - 模型在 L1 目录即可看到更完整的技能描述与去重后的触发场景，选技能更准且保持前缀缓存顺序稳定。
  - 外部标准技能包与作者自定义技能可使用 `examples/` 与 `templates/` 目录存放写作范例与模板，前后端校验和文案一致。
  - 常规长技能一次 `load_skill` 即可完整读取，减少不必要的分段 `offset` 续读往返。
  - 服务端冷回放水合逻辑以独立、可测、受预算与权限约束的纯辅助模块落地，官方技能正文仍绝不进入持久化卡片或浏览器响应。
- **代价与后续**：
  - L1 目录上线后因描述上限提高与新增触发场景提示，现有活跃会话的系统提示词前缀缓存会在发布后刷新一次。
  - `agent/loop/history.py` 对 `rehydrate_skill_tool_result` 的接线以及 `stream_adapter._client_safe_skill_payload` 对 `read_skill_resource` 补充 `skill_id` / `offset` 留待并行轨道合并后的集成步骤完成。
