# Agent Note: 技能对齐标准 SKILL.md 格式，运行时只做渐进式加载、不执行脚本

Status: implemented

## Problem

zenstory 的「技能」不是标准 Agent Skill，而且三套实现并存、机制与标准相反：

- **文件技能（半死代码）**：`agent/skills/loader.py` + `matcher.py` + `builtin/*.md`（13 个，zenstory 原生 `## Triggers / ## Instructions` 格式）。`match_skills` 没有任何调用方；`builtin/*.md` 只剩 `stream_adapter` 用量记录的兜底和手动脚本 `scripts/migrate_skills.py` 在读。`loader.py:34` 的 `USER_SKILLS_DIR` 读的是**服务器**进程的 `~/.zenstory/skills`，在多租户 SaaS 下没有意义。
- **数据库技能（实际生效路径）**：`UserSkill` / `PublicSkill` / `UserAddedSkill`，每条技能是 `name / description / triggers / instructions` 四个字段的单段文本，没有 `references/`、`assets/` 这样的多文件结构，无法导入或导出标准 SKILL.md 包。
- **全量预注入**：`context_injector.py:19-20` 每轮把最多 8 个技能、共 4000 字符的完整指令塞进 system prompt；超出预算的技能被列为「需显式调用」，模型只知道名字，只能让用户在消息开头手打技能名。这与标准的渐进式披露（progressive disclosure）相反。
- **显式选择靠文本前缀**：`explicit_resolver.py` 从用户消息前缀里解析技能名，文件头注释已承认这是前端没有发结构化字段的权宜之计。
- **用量统计靠模型自报**：模型要在回复开头写 `[使用技能: X]`（`context_injector.py:180`），再由 `stream_adapter` 解析文本，模型漏写或写错时统计就失真。

标准 skill 支持 `scripts/` 并由 agent 在代码执行环境里运行，但 zenstory 后端（Railway 上的 FastAPI）没有沙箱，不能执行用户或社区提供的代码。

## Decision

格式完全对齐开放的 Agent Skills 标准；服务端运行时只做「按需加载指令和参考文件」，在任何路径上都不执行技能携带的代码。

1. **格式与解析**：`agent/skills/package.py` 解析标准 `SKILL.md`（frontmatter 必填 `name`、`description`，可选 `license`、`compatibility`、`metadata`、`allowed-tools`），并兼容旧的 zenstory 原生格式（`# 标题 / ## Triggers / ## Instructions`，识别代码围栏）。zenstory 自有字段放在 `metadata.zenstory.*`（`display_name`、`triggers`、`category`）。`name` 不强制标准的小写连字符规则（技能名多为中文），只校验长度；导出时另生成 ASCII slug 作为标准 `name`，显示名写回 `metadata.zenstory.display_name`。frontmatter 用拒绝 anchor/alias 的 `SafeLoader` 子类解析，frontmatter 上限 16 KiB，`metadata` 限制深度 10、节点 1000、序列化后 16 KiB，日期转为 ISO 字符串，其他非 JSON 类型拒收。
2. **存储**：`UserSkill` / `PublicSkill` 仍是技能主表，新增 `skill_metadata`（JSON 文本，存 frontmatter 的其余字段），`description` 上限放宽到 1024。新表 `skill_resource`（`user_skill_id` 或 `public_skill_id` 二选一，`path`、`content`、`size`）存附属文件，只收 `references/` 或 `assets/` 下的 `.md .txt .json .yaml .yml .csv`；每个技能最多 20 个文件，单个 64 KiB，合计 256 KiB；指令上限 50,000 字符。路径做 NFC 规范化，拒绝 `..`、绝对路径、反斜杠、盘符以及 Unicode 控制/格式字符。迁移为 `b7e4c2a9d1f3`。创建、更新接口与导入使用同一组长度上限。
3. **渐进式加载**：
   - L1：`context_injector.build_skill_catalog` 只把全部启用技能（用户自建 + 已添加且已审核的公共技能，自建优先）的名称与描述写进 system prompt，每条描述截到 300 字符，整份目录约 8000 字符；超出的技能不进目录但仍可按名称加载，目录注明这一点。名称（忽略大小写与空白）重复的技能在目录里标注 `(id: …)`，工具按 id 优先匹配（见 `2026-10-05-skills-launch-hardening.md`）。原来的「8 个 / 4000 字符」全量注入与《技能参考手册》已删除。
   - L2：工具 `load_skill(name)` 返回技能正文与资源清单；正文受每请求的技能内容 token 预算限制，超出时截断并给出续读提示。
   - L3：工具 `read_skill_resource(name, path, offset)` 返回单个资源文件；`path="SKILL.md"` 读技能正文，内容超出预算时按 `offset` 分段返回。
   - 两个工具的描述都声明技能内容只是参考资料，不能凌驾系统规则或覆盖用户指令。发给浏览器的 `tool_result` 只含技能名、路径与大小，不含正文；模型拿到的是完整结果。
   - 用量统计来自 `load_skill` 调用（`matched_trigger="load_skill"`），同一请求内同一技能只记一次，写入用独立数据库会话，失败不影响请求会话。`[使用技能: X]` 文本标记及其解析、剥离逻辑已删除；`skill_matched` 事件改由 `load_skill` 结果触发，每个流内按技能去重。
4. **显式选择**：`AgentRequest.selected_skill_ids`（最多 3 个，接受 `GET /skills` 返回的 id）。后端校验归属后把对应技能的正文（合计不超过显式选择的 token 份额，超出截断并提示续读）与资源清单注入本轮 system prompt，记录 `matched_trigger="selected"` 的用量，并在流开始时发出 `skill_matched`；这些技能之后再被 `load_skill` 时不重复记录或广播。前端点选技能（侧栏、技能详情、输入框 `/` 菜单）会在输入框上方生成可移除的标签，发送后清空；消息前缀解析（`explicit_resolver.py`）已删除。
5. **脚本与 `allowed-tools`**：导入时 `scripts/` 下的文件与非文本文件一律丢弃，并在导入响应的 `warnings` 里逐条列出。`allowed-tools` 在标准中是「预先授权的工具」，zenstory 没有工具审批流程，因此只解析、存储、原样导出，不影响运行时的工具集。
6. **导入导出 API**（`api/skills.py`，逻辑在 `services/skill_package_service.py`）：`POST /skills/import`（`.zip` 或 `.md`，上限 1 MiB；有 `Content-Length` 时在解析请求体之前就拒绝超限上传；解析在工作线程中进行）、`GET /skills/{id}/export`、资源的列表/读取/写入/删除。zip 导入拒绝路径穿越、符号链接、加密条目、声明总大小超过 4 MiB、实际读取超限、重复条目（含 NFC 同形），优先使用根目录的 `SKILL.md`。资源写入对父技能行加锁，唯一约束冲突返回 409。已添加的公共技能只读，且须处于已审核状态；他人的技能一律 404；`GET /skills`、`GET /skills/my-skills` 的已添加列表同样只返回 approved 的公共技能。分享到公共库时复制资源与 `skill_metadata`，审核接口返回这些内容的原文供管理员核对。
7. **内置技能**：13 个内置技能改为 `agent/skills/builtin/<id>/SKILL.md` 标准目录，正文与原文件逐字节一致。`services/builtin_skill_seed.py` 按「名称 + `source == "official"`」幂等写入 `PublicSkill`（默认只补缺，`force` 才覆盖），`scripts/migrate_skills.py` 是它的命令行入口；应用启动时不自动执行，以免覆盖管理员在线上改过的官方技能。
8. **删除的代码**：`matcher.py`、`explicit_resolver.py`、`schemas.py`、`user_skill_service.py`，以及 `loader.py` 中读取服务器 `~/.zenstory/skills` 与项目目录的逻辑和 `SkillCache`。

## Alternatives considered

- **维持现状，只修补全量注入的预算问题**。最强理由：改动最小，现有前端、统计和测试都不用动。被否：预算天花板和「需显式调用」分区是全量注入这个机制本身带来的，调参数只会移动天花板；而且仍然不能导入、导出标准技能。
- **完整实现标准 skill（含脚本执行），接入托管沙箱**（E2B、Modal、Vercel Sandbox 这类 microVM 服务，或 WASM/Pyodide）。最强理由：与 Claude/Codex 等生态完全等价，外部技能开箱即用。被否（至少第一阶段）：需要新基础设施、新的计费和滥用面，还要设计网络/凭证隔离；小说写作场景里脚本能做的事几乎都能用一方工具覆盖。保留为第二阶段选项：只允许官方技能携带脚本，在无网络、无数据库凭证的沙箱里运行，通过 API 回写文件；社区技能永远不能携带脚本。
- **不执行脚本，但在服务端受限的 Python 子进程里跑**（seccomp、资源限制、临时目录）。最强理由：不依赖外部服务，延迟低。被否：进程级限制在同一台持有数据库凭证的机器上不算隔离边界，出一次逃逸就是全量数据泄露。

## Consequences

- 收益：技能数量不再受 prompt 预算限制；system prompt 只含元数据，更短、更稳定，也更利于缓存；用量统计来自工具调用，准确可靠；可以导入、导出标准 SKILL.md 包，与外部生态互通；删掉一套死代码，只剩一个技能来源。
- 代价：多一次工具往返，模型需要先调 `load_skill` 才能拿到指令，DeepSeek 模型是否会稳定地主动调用需要评估，必要时靠 description 写法和显式选择兜底；需要一次数据迁移（`skill_resource` 表与 `skill_metadata` 列）和前端改动（技能标签、导入导出、资源文件编辑）；用量记录的 `confidence` 在新机制下恒为 1.0，统计面板与 `GET /skills/stats/{project_id}` 已不再给出「平均置信度」（数据库列保留、不做迁移），面板的「总触发次数」改称「总使用次数」（响应字段仍叫 `total_triggers`）；依赖脚本的外部技能导入后功能不完整，只能作为参考。

## Verification

- 单元测试：标准 frontmatter 和旧原生格式都能解析；超长 description、非文本资源、路径穿越、超限 zip、YAML alias（billion laughs）都会被拒绝。`name` 不强制标准的小写连字符规则（zenstory 技能名多为中文），只校验长度；导出时另生成符合标准的 slug。
- `allowed-tools` 无运行时效果：导入一个声明了任意工具名的技能后，本轮工具列表与未导入时完全相同。
- 集成测试：system prompt 只含 L1 元数据；`load_skill` / `read_skill_resource` 会写入用量记录；`selected_skill_ids` 能注入 L2。
- 删除后 `rg "match_skills|USER_SKILLS_DIR|使用技能:" apps/server --glob '!venv'` 应无命中（测试除外）。
