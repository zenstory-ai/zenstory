"""
Skill MD Service - Generates SKILL.md documentation for AI agents.

Uses route introspection to auto-generate endpoint tables from FastAPI routers,
eliminating manual sync drift between documentation and actual API surface.
"""

import os

from services.skill_md_routes import EndpointInfo, extract_agent_endpoints
from utils.logger import get_logger

logger = get_logger(__name__)

# NOTE: Keep in sync with require_agent_rate_limit() params in agent_api.py and vector_search.py
RATE_LIMITS = {
    "read": "2000/hour",
    "write": "1000/hour",
    "search": "500/hour",
    "context": "500/hour",
}

# Agent Skills spec (agentskills.io): only name/description/license/compatibility/metadata/
# allowed-tools are valid top-level keys, and metadata is a string-to-string map. Legacy
# fields (version, api_base, triggers, ...) therefore live under `metadata` as strings.
_DESCRIPTIONS = {
    "zh": (
        "ZenStory（AI 辅助小说写作工作台）的 Agent API 使用说明：用 API Key 读取和管理用户在 ZenStory 中的"
        "小说项目、章节草稿、大纲、人物设定、世界观与素材，并提供混合搜索与写作上下文。"
        "当用户提到 ZenStory、自己存放在 ZenStory 的小说项目，或需要读写其中的章节、大纲、人物、设定时使用。"
    ),
    "en": (
        "Documents the ZenStory Agent API: reads and manages a user's novel projects in ZenStory "
        "(chapter drafts, outlines, characters, lore, materials) with an API key, plus hybrid search "
        "and AI-assembled writing context. Use when the user mentions ZenStory or a novel project stored "
        "there, or asks to read or write its chapters, outlines, characters or world-building."
    ),
}
_DEFAULT_API_BASE = "https://api.zenstory.ai/api/v1"
_CAPABILITIES = "project_management, file_crud, file_tree, version_history, hybrid_search, writing_context"
_FILE_TYPES = "outline, draft, character, lore, material"


def _rate_limit_str() -> str:
    return ", ".join(f"{k}={v}" for k, v in RATE_LIMITS.items())


_TABLE_HEADERS = {
    "zh": "| 端点 | 方法 | 描述 | 所需权限 |\n|------|------|------|----------|",
    "en": "| Endpoint | Method | Description | Required Scope |\n|----------|--------|-------------|----------------|",
}


def _get_agent_routers():
    """Lazy-import agent API routers to avoid circular imports."""
    from api.agent_api import router as agent_router
    from api.vector_search import router as search_router
    return [agent_router, search_router]


_cached_endpoints: list[EndpointInfo] | None = None


def _get_cached_endpoints() -> list[EndpointInfo]:
    """Get endpoints with caching to avoid repeated imports."""
    global _cached_endpoints
    if _cached_endpoints is None:
        _cached_endpoints = []
        for router in _get_agent_routers():
            _cached_endpoints.extend(extract_agent_endpoints(router))
    return _cached_endpoints


def _build_endpoint_table(endpoints: list[EndpointInfo], lang: str = "zh") -> str:
    header = _TABLE_HEADERS.get(lang, _TABLE_HEADERS["zh"])
    rows = [f"| `{ep.path}` | {ep.method} | {ep.summary} | {ep.scope} |" for ep in endpoints]
    return header + "\n" + "\n".join(rows)


class SkillMdService:
    """Service for generating SKILL.md documentation."""

    def __init__(self):
        self.app_name = os.getenv("APP_NAME", "zenstory API")
        self.app_version = os.getenv("APP_VERSION", "1.0.0")
        self.api_base = os.getenv("API_BASE_URL", _DEFAULT_API_BASE)

    def _cli_login_command(self) -> str:
        """`npx zenstory login`, plus --api-base when this server is not the hosted one."""
        base = self.api_base.rstrip("/")
        if base == _DEFAULT_API_BASE:
            return "npx zenstory login"
        return f"npx zenstory login --api-base {base}"

    def generate_skill_md(self, lang: str = "zh") -> str:
        """
        Generate SKILL.md documentation.

        Args:
            lang: Language for documentation ("zh" for Chinese, "en" for English)

        Returns:
            Complete SKILL.md content as string
        """
        if lang == "en":
            return self._generate_english()
        return self._generate_chinese()

    def _get_endpoints(self) -> list[EndpointInfo]:
        """Extract endpoints from all agent API routers (cached)."""
        endpoints = _get_cached_endpoints()

        if len(endpoints) < 8:
            logger.warning(
                "SKILL.md: only %d endpoints found, expected at least 8",
                len(endpoints),
            )
        return endpoints

    def _generate_chinese(self) -> str:
        endpoints = self._get_endpoints()
        endpoint_table = _build_endpoint_table(endpoints, "zh")

        return f'''---
name: zenstory
description: "{_DESCRIPTIONS["zh"]}"
metadata:
  display_name: "ZenStory 小说写作平台"
  version: "{self.app_version}"
  api_base: "{self.api_base}"
  auth_method: "api_key"
  auth_header: "X-Agent-API-Key"
  auth_prefix: "eg_"
  rate_limit: "{_rate_limit_str()}"
  triggers: "写小说, 小说创作, 角色创建, 大纲规划, 世界观设定, write a novel, create characters, story outline, world building, draft chapters"
  capabilities: "{_CAPABILITIES}"
  file_types: "{_FILE_TYPES}"
  cli: "npx zenstory"
---

# ZenStory 小说写作平台

AI 辅助的小说写作工作台，提供智能对话、文件管理、语义搜索等功能。

## 推荐：使用 ZenStory 命令行工具（`zenstory`）

编程 Agent（Claude Code、Codex、OpenClaw 等）推荐直接使用官方 CLI 和标准 Agent Skill，无需手写 HTTP 请求：

```bash
{self._cli_login_command()}
# 出现 "Paste API key:" 时粘贴密钥（输入不回显，不会进入 shell 历史；本地以 0600 权限保存）
npx zenstory skill install   # 安装 Skill 到 Claude Code（--target codex|openclaw|<目录>）
npx zenstory whoami          # 检查登录状态与读写权限
```

请由用户本人在终端里运行登录命令并在提示时粘贴密钥，不要把密钥写在命令行参数里，也不要贴进 AI 对话。
如果密钥已经被贴进对话，请到「设置 → Agent」重新生成密钥后再登录。

## 认证说明

所有 API 请求需要在请求头中携带有效的 API Key：

```
X-Agent-API-Key: eg_your_api_key_here
```

API Key 格式为 `eg_` 前缀加上 64 位十六进制字符。在平台设置中生成和管理 API Key。

**权限范围 (Scopes)**：
- `read` - 读取项目和文件
- `write` - 创建和更新内容

## API 端点列表

{endpoint_table}

## 文件类型说明

| 类型 | 用途 | 关键字段 |
|------|------|----------|
| outline | 大纲，支持层级嵌套 | title, content, parent_id, metadata |
| draft | 正文草稿 | title, content, parent_id, metadata |
| character | 人物设定 | title, content, metadata(traits, relationships) |
| lore | 世界观设定 | title, content, metadata(category) |
| material | 写作素材 | title, content, metadata(source, tags) |

## 使用示例

### 1. 获取项目列表

```bash
curl -H "X-Agent-API-Key: eg_your_key" \\
  "{self.api_base}/agent/projects"
```

### 2. 创建新项目

```bash
curl -X POST "{self.api_base}/agent/projects" \\
  -H "X-Agent-API-Key: eg_your_key" \\
  -H "Content-Type: application/json" \\
  -d '{{
    "name": "我的科幻小说",
    "description": "一部关于星际旅行的科幻作品",
    "project_type": "novel"
  }}'
```

### 3. 创建章节草稿

```bash
curl -X POST "{self.api_base}/agent/projects/{{project_id}}/files" \\
  -H "X-Agent-API-Key: eg_your_key" \\
  -H "Content-Type: application/json" \\
  -d '{{
    "title": "第一章：启程",
    "file_type": "draft",
    "content": "晨光透过窗帘的缝隙洒进房间..."
  }}'
```

### 4. 获取写作上下文

```bash
curl -H "X-Agent-API-Key: eg_your_key" \\
  "{self.api_base}/agent/projects/{{project_id}}/writing-context?file_id={{file_id}}"
```

### 5. 语义搜索

```bash
curl -X POST "{self.api_base}/agent/projects/{{project_id}}/search" \\
  -H "X-Agent-API-Key: eg_your_key" \\
  -H "Content-Type: application/json" \\
  -d '{{
    "query": "主角的冒险经历",
    "top_k": 5
  }}'
```

## 适用范围

**适用于：** 小说创作、短篇故事、剧本写作、人物设定、世界观构建、大纲规划
**不适用于：** 通用问答、代码生成、图像创作、非虚构写作、数据分析

## 完整工作流示例

### 工作流 1：创建新小说项目

```
1. POST /agent/projects                          → 创建新项目（响应 folders 字段列出自动创建的默认文件夹）
2. POST /agent/projects/{{id}}/files             → 创建大纲文件 (file_type=outline, parent_id=大纲文件夹)
3. POST /agent/projects/{{id}}/files             → 创建人物设定 (file_type=character, parent_id=角色文件夹)
4. POST /agent/projects/{{id}}/files             → 创建世界观设定 (file_type=lore, parent_id=设定文件夹)
5. GET  /agent/projects/{{id}}/writing-context   → 获取写作上下文
6. POST /agent/projects/{{id}}/files             → 创建章节 (file_type=draft, parent_id=正文文件夹, order=章节序号)
```

默认文件夹 id 可预测：`{{project_id}}-lore-folder`、`-character-folder`、`-material-folder`、`-outline-folder`、
`-draft-folder`（剧本项目为 `-script-folder`）。已有文件可用 `POST /agent/files/{{file_id}}/move` 移入文件夹。

### 工作流 2：续写已有章节

```
1. GET  /agent/projects/{{id}}/files?file_type=draft&fields=id,title  → 列出草稿
2. GET  /agent/files/{{file_id}}                                        → 读取当前内容
3. GET  /agent/projects/{{id}}/writing-context?file_id={{file_id}}     → 获取相关上下文
4. POST /agent/projects/{{id}}/search  query="角色关系"                 → 搜索人物关系
5. PUT  /agent/files/{{file_id}}                                        → 更新草稿内容
```

### 工作流 3：撤销一次修改

```
1. GET  /agent/files/{{file_id}}/versions                    → 版本历史（最新在前，不含正文）
2. GET  /agent/files/{{file_id}}/versions/{{n}}               → 查看第 n 版正文
3. POST /agent/files/{{file_id}}/versions/{{n}}/rollback      → 恢复到第 n 版（历史不丢；版本额度未满时生成一个新版本）
```

## 错误处理

API 返回标准化的错误响应格式：

```json
{{
  "detail": "错误描述信息",
  "error_code": "ERROR_CODE",
  "request_id": "req_xxx"
}}
```

常见错误码：

| 错误码 | HTTP 状态码 | 描述 |
|--------|-------------|------|
| `AUTH_UNAUTHORIZED` | 401 | 缺少认证信息 |
| `AUTH_TOKEN_INVALID` | 401 | API Key 无效 |
| `AUTH_TOKEN_EXPIRED` | 401 | API Key 已过期 |
| `NOT_AUTHORIZED` | 403 | 权限不足 |
| `NOT_FOUND` | 404 | 资源不存在 |
| `VALIDATION_ERROR` | 422 | 请求参数验证失败 |
| `RATE_LIMIT_EXCEEDED` | 429 | 请求频率超限 |

## 速率限制

按端点类型分级限速：

| 类型 | 限制 |
|------|------|
| 读取 (read) | {RATE_LIMITS["read"]} |
| 写入 (write) | {RATE_LIMITS["write"]} |
| 搜索 (search) | {RATE_LIMITS["search"]} |
| 上下文 (context) | {RATE_LIMITS["context"]} |

---

如有问题或建议，请联系技术支持或访问 API 文档：{self.api_base.rsplit("/api/v1", 1)[0]}/docs
'''

    def _generate_english(self) -> str:
        endpoints = self._get_endpoints()
        endpoint_table = _build_endpoint_table(endpoints, "en")

        return f'''---
name: zenstory
description: "{_DESCRIPTIONS["en"]}"
metadata:
  display_name: "ZenStory Novel Writing Platform"
  version: "{self.app_version}"
  api_base: "{self.api_base}"
  auth_method: "api_key"
  auth_header: "X-Agent-API-Key"
  auth_prefix: "eg_"
  rate_limit: "{_rate_limit_str()}"
  triggers: "write a novel, novel writing, create characters, story outline, world building, draft chapters, 写小说, 小说创作, 角色创建, 大纲规划"
  capabilities: "{_CAPABILITIES}"
  file_types: "{_FILE_TYPES}"
  cli: "npx zenstory"
---

# ZenStory Novel Writing Platform

AI-assisted novel writing workbench with file management, semantic search, and writing context.

## Recommended: use the ZenStory CLI (`zenstory`)

Coding agents (Claude Code, Codex, OpenClaw, ...) should prefer the official CLI and standard Agent Skill over hand-written HTTP calls:

```bash
{self._cli_login_command()}
# paste the key at the "Paste API key:" prompt (hidden input, kept out of shell history; stored locally, mode 0600)
npx zenstory skill install   # install the skill for Claude Code (--target codex|openclaw|<dir>)
npx zenstory whoami          # check login state and read/write scopes
```

The user should run the login command in their own terminal and paste the key when prompted. Never put the key
on the command line or paste it into an AI chat; if it was already pasted into a chat, regenerate it in
Settings → Agent and log in again.

## Authentication

All API requests require a valid API Key in the request header:

```
X-Agent-API-Key: eg_your_api_key_here
```

The API Key format is `eg_` prefix followed by 64 hexadecimal characters. Generate and manage API Keys in platform settings.

**Available Scopes**:
- `read` - Read projects and files
- `write` - Create and update content

## API Endpoints

{endpoint_table}

## File Types

| Type | Purpose | Key Fields |
|------|---------|------------|
| outline | Story structure with hierarchy | title, content, parent_id, metadata |
| draft | Main content editing | title, content, parent_id, metadata |
| character | Character profiles | title, content, metadata(traits, relationships) |
| lore | World-building entries | title, content, metadata(category) |
| material | Writing materials | title, content, metadata(source, tags) |

## Usage Examples

### 1. List Projects

```bash
curl -H "X-Agent-API-Key: eg_your_key" \\
  "{self.api_base}/agent/projects"
```

### 2. Create New Project

```bash
curl -X POST "{self.api_base}/agent/projects" \\
  -H "X-Agent-API-Key: eg_your_key" \\
  -H "Content-Type: application/json" \\
  -d '{{
    "name": "My Sci-Fi Novel",
    "description": "A story about interstellar travel",
    "project_type": "novel"
  }}'
```

### 3. Create Chapter Draft

```bash
curl -X POST "{self.api_base}/agent/projects/{{project_id}}/files" \\
  -H "X-Agent-API-Key: eg_your_key" \\
  -H "Content-Type: application/json" \\
  -d '{{
    "title": "Chapter 1: The Journey Begins",
    "file_type": "draft",
    "content": "Morning light streamed through the curtains..."
  }}'
```

### 4. Get Writing Context

```bash
curl -H "X-Agent-API-Key: eg_your_key" \\
  "{self.api_base}/agent/projects/{{project_id}}/writing-context?file_id={{file_id}}"
```

### 5. Semantic Search

```bash
curl -X POST "{self.api_base}/agent/projects/{{project_id}}/search" \\
  -H "X-Agent-API-Key: eg_your_key" \\
  -H "Content-Type: application/json" \\
  -d '{{
    "query": "protagonist adventure",
    "top_k": 5
  }}'
```

## Scope

**Suitable for:** Novel writing, short stories, screenwriting, character design, world building, outline planning
**Not suitable for:** General Q&A, code generation, image creation, non-fiction writing, data analysis

## Complete Workflow Examples

### Workflow 1: Create a New Novel Project

```
1. POST /agent/projects                          → Create new project (response `folders` lists the default folders)
2. POST /agent/projects/{{id}}/files             → Create outline file (file_type=outline, parent_id=outline folder)
3. POST /agent/projects/{{id}}/files             → Create character profiles (file_type=character, parent_id=character folder)
4. POST /agent/projects/{{id}}/files             → Create lore entries (file_type=lore, parent_id=lore folder)
5. GET  /agent/projects/{{id}}/writing-context   → Get writing context
6. POST /agent/projects/{{id}}/files             → Create chapter (file_type=draft, parent_id=draft folder, order=chapter number)
```

Default folder ids are predictable: `{{project_id}}-lore-folder`, `-character-folder`, `-material-folder`,
`-outline-folder`, `-draft-folder` (`-script-folder` for screenplays). Move existing files with `POST /agent/files/{{file_id}}/move`.

### Workflow 2: Continue an Existing Chapter

```
1. GET  /agent/projects/{{id}}/files?file_type=draft&fields=id,title  → List drafts
2. GET  /agent/files/{{file_id}}                                        → Read current content
3. GET  /agent/projects/{{id}}/writing-context?file_id={{file_id}}     → Get relevant context
4. POST /agent/projects/{{id}}/search  query="character relationships"  → Search character relations
5. PUT  /agent/files/{{file_id}}                                        → Update draft content
```

### Workflow 3: Undo a Change

```
1. GET  /agent/files/{{file_id}}/versions                    → Version history (newest first, no content)
2. GET  /agent/files/{{file_id}}/versions/{{n}}               → Content of version n
3. POST /agent/files/{{file_id}}/versions/{{n}}/rollback      → Restore version n (history is kept; adds a new version unless the per-file version quota is full)
```

## Error Handling

API returns standardized error responses:

```json
{{
  "detail": "Error description",
  "error_code": "ERROR_CODE",
  "request_id": "req_xxx"
}}
```

Common error codes:

| Error Code | HTTP Status | Description |
|------------|-------------|-------------|
| `AUTH_UNAUTHORIZED` | 401 | Missing authentication |
| `AUTH_TOKEN_INVALID` | 401 | Invalid API Key |
| `AUTH_TOKEN_EXPIRED` | 401 | API Key expired |
| `NOT_AUTHORIZED` | 403 | Insufficient permissions |
| `NOT_FOUND` | 404 | Resource not found |
| `VALIDATION_ERROR` | 422 | Request validation failed |
| `RATE_LIMIT_EXCEEDED` | 429 | Rate limit exceeded |

## Rate Limiting

Tiered rate limits by endpoint type:

| Type | Limit |
|------|-------|
| Read (read) | {RATE_LIMITS["read"]} |
| Write (write) | {RATE_LIMITS["write"]} |
| Search (search) | {RATE_LIMITS["search"]} |
| Context (context) | {RATE_LIMITS["context"]} |

---

For questions or suggestions, contact support or visit API docs: {self.api_base.rsplit("/api/v1", 1)[0]}/docs
'''


# Singleton instance
skill_md_service = SkillMdService()
