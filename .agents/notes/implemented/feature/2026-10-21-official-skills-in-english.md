# Agent Note: 英文界面下官方技能显示英文名称与简介

Status: implemented

## Problem

2026-10-21 英文新用户诊断：Skills → Discover 的 17 张卡片全部是中文标题和简介，外壳（「Official」「Add to My Skills」）才是英文。英文作者看不懂任何一个技能，也就不会添加。

同一天运营改版了官方技能：现在生产上有 14 个官方技能。4 个短剧技能转为官方，新增「短篇写作」「去AI味」；下架对话生成、场景描写、冲突设计、反转设计、代入感强化；其余 8 个的简介重写，带上了作者常用的说法。`PublicSkill` 只有单一语言的 `name` / `description`，官方内容由运营在数据库里维护（见 `docs/agent-writing-guidance.md`）。

## Decision

- **新增 `config/official_skill_translations.py`**：以数据库里存的中文名为键，按 2026-10-21 改版后的生产目录给 14 个官方技能配英文名称和简介。英文简介跟随新的中文简介，「作者说……时用」改写成英文的「Use it when you ask to …」。
- **在哪些接口生效**：`Accept-Language` 为英文时，下列接口对 `source == "official"` 的技能返回英文名称和简介。
  - 公共技能列表与详情：`/api/v1/public-skills`。
  - 我的技能：`/api/v1/skills`、`/api/v1/skills/my-skills`。
  - 作者自己改过名（`custom_name`）的已添加技能保留作者的名字。
- **英文搜索**：公共技能搜索在英文界面下也匹配这些英文名称和简介。
- **不变的部分**：
  - 技能正文（模型加载的 instructions）不翻译。
  - 数据库行、id、计数和用户收藏都不动。
  - 社区技能保留作者原文。
  - 不在表里的官方技能（新增或改名）显示数据库原文。
- **约束后续更新**：`AGENTS.md`（`CLAUDE.md` 引用它）和 `docs/agent-writing-guidance.md` 规定，任何官方技能目录改动（新增、改名、上下架、改写名称或简介）都在同一批里更新这张表。

## Alternatives considered

- **给 `public_skill` 加 `name_en` / `description_en` 列，由运营维护**。最强理由：翻译和中文内容放在一起，改名也不会失配。没有采用：staging 没有 Alembic 记录，加列要手工执行 DDL，否则读技能的查询会失败；现有的官方内容更新脚本也只改 description / instructions，要一并扩展审批流程。14 条固定文案放在代码里，评审和回滚都更简单。
- **前端按技能名在 locales 里翻译**。最强理由：后端不用改。没有采用：技能出现在 Discover、我的技能、工作区技能栏、对话里的技能选择等多处，每处都要接；搜索也在服务端，只做前端翻译时英文搜不到。

## Consequences

- 收益：英文作者能看懂、搜到并添加官方技能；中文界面完全不变。
- 代价：
  - 官方技能改名、新增或改写简介时，要按上面的约束同步更新这张表，否则英文界面显示中文原文或过时的英文简介。没有运行时检查兜底：曾考虑在英文读者碰到缺失翻译时记警告日志，但那要等真实用户访问才会触发，而且只能事后发现，所以改为在改版流程里约束。
  - React Query 缓存的技能列表在切换语言后要等下次刷新才换语言。
  - 社区技能对英文作者仍是原文。

## Verification

`cd apps/server && pytest tests/test_api/test_public_skills.py tests/test_api/test_skills.py -q --no-cov`：英文读者看到英文名和简介、能用英文搜到；社区技能不变；中文读者不变。
