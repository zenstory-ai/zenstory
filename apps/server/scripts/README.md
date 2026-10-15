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
