# Scripts

## release_preflight_check.py

检查发布配置和 Alembic head。

### 用法

```bash
cd apps/server
python scripts/release_preflight_check.py --strict
```

检查项：
- `ENVIRONMENT`（strict 模式要求 production/staging）
- `JWT_SECRET_KEY` 强度
- `ALLOW_LEGACY_UNTYPED_TOKENS` / `ALLOW_LEGACY_REFRESH_WITHOUT_JTI`
- `CORS_ORIGINS`
- `RATE_LIMIT_BACKEND`
- Alembic heads 是否唯一


## update_official_skill_content.py

数据库中已存在的官方 skill 内容维护：默认只验证并回滚，`--apply --backup` 才提交。按当前 ID、名称、来源、状态及旧描述/指令进行 CAS，不创建/重命名技能，不改用户引用或社区内容。详见[写作配置维护](../../../docs/agent-writing-guidance.md)。脚本不内置提示词或官方技能默认内容。


## eval_review_effort.py

审稿人 / 返修 writer 思考强度对照评估（**付费 DeepSeek 调用**，低峰价每次约 ¥0.04）。临时 SQLite 库，固定路由和第一个 writer（写入埋好 5 个客观缺陷的一章 / 一集），真实跑自动质检 → 审稿 → 返修，按档位设置 `AGENT_REVIEWER_REASONING_EFFORT` / `AGENT_REWORK_REASONING_EFFORT`。只需要 `DEEPSEEK_API_KEY`，不读 `.env`、不连其他数据库；`--budget-cny` 累计成本到上限即停。

```bash
cd apps/server
DEEPSEEK_API_KEY=... python scripts/eval_review_effort.py --repeats 5 --budget-cny 10 --out /tmp/review-effort.json
```

结论见 `.agents/notes/implemented/process/2026-10-20-reviewer-rework-reasoning-effort.md`。
