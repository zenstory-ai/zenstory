# 后端测试

ZenStory 后端用 pytest 测试 API、服务、写作 Agent、数据模型和素材处理流程。从 `apps/server` 运行以下命令；CI 的分片与 PostgreSQL 专项入口见[仓库 E2E 与 CI](../../../docs/development/e2e.md)。

## 环境与快速检查

使用 Python 3.12 或 3.13 的项目虚拟环境；CI 的后端测试使用 3.12，API 镜像使用 3.13。安装仓库已声明的依赖：

```bash
cd apps/server
python3.12 -m venv .venv312
source .venv312/bin/activate  # Windows: .venv312\Scripts\activate
pip install -r requirements.txt
make env-check
```

`Makefile` 依次选择 `.venv312`、`.venv`、`venv`，也可传 `make VENV=<path> ...`。普通测试用临时 SQLite 库和 mock 外部调用；PostgreSQL、Redis 的专项测试需要隔离服务。`tests/real_llm` 需显式开启 `RUN_REAL_LLM_TESTS`，文档整理和常规回归不启用付费模型调用。

```bash
# 单文件回归，不生成覆盖率
pytest tests/test_api/test_auth.py -q --no-cov

# 单个用例
pytest tests/test_api/test_auth.py::test_register_success -q --no-cov

# 按当前配置运行全部测试
make test

# 只收集，不继承默认覆盖率和并行参数
pytest -o addopts='' --collect-only -q tests

# 按风险范围分组
make test-fast
make test-risk
make test-deep
```

`pytest.ini` 默认开启 xdist、按模块分配和覆盖率报告。小范围检查可传 `-n 0 --no-cov`；彻底替换默认参数用 `-o addopts=''`。`test-fast` 覆盖服务、模型、工具、核心与中间件；`test-risk` 选择鉴权、文件、订阅和素材 API；`test-deep` 覆盖 Agent 与流程。各 lane 是选定范围，不替代 CI 全部门禁。

## 目录与共享 Fixtures

| 目录 | 内容 |
| --- | --- |
| `test_api/` | API 和权限、配额、并发契约 |
| `test_services/` | 服务逻辑、版本、存储、导出和权益 |
| `test_agent/` | 上下文、工具、编排、SSE 和交付证据 |
| `test_flows/` | 素材处理的单元与集成测试 |
| `test_prompts/` | 提示模板与加载器 |
| `test_models/` | 数据模型与约束 |
| `test_core/`、`test_middleware/`、`test_utils/`、`test_scripts/` | 错误处理、中间件、工具和维护脚本 |
| `e2e/` | 后端端到端契约 |
| `real_llm/` | 需显式开启的真实模型测试 |

`conftest.py` 提供：

- `db_session`：每个测试独立会话，结束后清理测试库；额度计量与写作配置也接到隔离数据库。
- `client`：带 `ASGITransport` 的 `httpx.AsyncClient`，通过依赖覆盖使用 `db_session`，请求需 `await`。
- `writing_prompt_configs`：创建合成的项目写作配置，供需要启动写作工作流的测试使用。

另开数据库会话、后台任务或外部服务时，fixture 要同时管理其测试连接和清理。普通测试默认移除开发环境的 `REDIS_URL`；专项真实 Redis 连接遵循测试中的专用变量，不能接生产资源。

## 编写数据与 API 测试

项目通过 `owner_id` 关联作者。下面的 fixture 可供本文件中的服务测试使用：

```python
import pytest
from models import Project, User

@pytest.fixture
def test_project(db_session):
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password="fixture-only",
        email_verified=True,
    )
    db_session.add(user)
    db_session.commit()
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    return project
```

登录测试使用真实密码哈希；邮箱、模型和支付等外部服务在调用处替换。API 鉴权示例见 `tests/test_api/test_auth.py`，导出示例见 `tests/test_services/test_export_service.py`。注册成功测试还需配置邀请码策略并 mock 验证码发送，不能依赖开发机是否配置邮件服务。

```python
import pytest
from httpx import AsyncClient

@pytest.mark.integration
async def test_health(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
```

## Mock 外部依赖

在使用对象的模块中 patch 导入名称。验证码服务使用同步 Redis 辅助函数，经 `asyncio.to_thread` 调用；mock 这些函数，而非构造不存在的服务类：

```python
from services.features import verification_service

@pytest.mark.unit
async def test_verification_code(mocker):
    prefix = "services.features.verification_service"
    mocker.patch(f"{prefix}.get_verification_attempts", return_value=0)
    mocker.patch(f"{prefix}.get_verification_code", return_value="123456")
    mocker.patch(f"{prefix}.consume_verification_code", return_value=True)
    mocker.patch(f"{prefix}.reset_verification_attempts", return_value=True)

    result = await verification_service.verify_code("test@example.com", "123456")
    assert result == (True, None)
```

邮件发送的底层调用是 `services.infra.email_client.resend.Emails.send`，应用服务常在自己的导入名称上 mock `send_verification_email`。Agent 回归按实际调用层替换模型，参考 `tests/test_agent/test_service.py`；SSE 流式事件需要可异步迭代的 mock，不能用普通消息对象冒充流。

## 测试标记

| 标记 | 用途 |
| --- | --- |
| `unit` | 隔离的单元测试 |
| `integration` | API、服务组合或外部基础设施集成 |
| `e2e` | 完整链路契约 |
| `real_llm` | 真实模型调用，仍须显式环境开关 |
| `slow` | 慢速用例 |
| `asyncio` | 异步用例；仓库配置为 `asyncio_mode=auto` |

```bash
pytest -m unit
pytest -m integration
pytest -m "not slow"
```

## 最佳实践

### 1. 测试隔离

每个测试独立准备数据，不依赖其他测试的执行顺序：

```python
# ❌ 不好：依赖测试顺序
def test_step_1():
    global.user = create_user()

def test_step_2():
    assert global.user is not None

# ✅ 好：每个测试独立
def test_create_user(db_session: Session):
    user = create_user(db_session)
    assert user.id is not None

def test_query_user(db_session: Session):
    user = create_user(db_session)
    found = db_session.get(User, user.id)
    assert found is not None
```

### 2. 使用唯一测试数据

避免 UNIQUE 约束冲突：

```python
# ❌ 不好：可能冲突
def test_1():
    User(username="test", email="test@example.com")

def test_2():
    User(username="test", email="test@example.com")  # 冲突!

# ✅ 好：使用唯一数据
def test_1():
    User(username="user1", email="user1@example.com")

def test_2():
    User(username="user2", email="user2@example.com")
```

### 3. 测试命名

测试名称说明触发条件和预期结果：

```python
# ❌ 不好
def test_1():
    pass

# ✅ 好
def test_user_registration_with_duplicate_email_returns_400():
    pass
```

### 4. AAA 模式

Arrange-Act-Assert 模式：

```python
def test_user_update(db_session: Session):
    # Arrange: 准备测试数据
    user = create_user(db_session, username="oldname")

    # Act: 执行测试操作
    user.username = "newname"
    db_session.commit()

    # Assert: 验证结果
    db_session.refresh(user)
    assert user.username == "newname"
```

### 5. 测试边界情况

在实际 API 或服务入口验证不存在的资源、空值、越权和并发冲突，断言具体状态或异常类型。请求验证由 API schema 负责，不能用数据库模型构造代替。参考 `tests/test_api/test_files.py` 和 `tests/test_models/test_file_model.py`。

### 6. 使用 Fixture 复用代码

```python
# ❌ 不好：重复代码
def test_1():
    user = User(username="test", email="test@example.com")
    db_session.add(user)
    db_session.commit()

def test_2():
    user = User(username="test2", email="test2@example.com")
    db_session.add(user)
    db_session.commit()

# ✅ 好：使用 fixture
@pytest.fixture
def test_user(db_session: Session):
    def _create(username, email):
        user = User(username=username, email=email)
        db_session.add(user)
        db_session.commit()
        return user
    return _create
```

### 7. 异步测试

使用 `@pytest.mark.asyncio` 和 `async/await`：

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_async_endpoint(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
```

### 8. 密码和敏感数据测试

```python
# ✅ 好：使用 hash_password
from services.core.auth_service import hash_password

def test_user_password():
    hashed = hash_password("mypassword")
    assert verify_password("mypassword", hashed)

# ❌ 不好：硬编码密码
assert user.hashed_password == "plaintext_password"
```

## 调试与覆盖率

```bash
pytest -s                # 显示输出
pytest -vv              # 详细结果
pytest --tb=short        # 简短失败堆栈
pytest -x               # 首个失败后停止
pytest --pdb -n 0        # 在单进程中调试
pytest --cov=. --cov-report=html --cov-report=term
pytest --cov=. --cov-report=xml
```

HTML 报告位于 `htmlcov/index.html`。CI 合并后端分片后执行整体覆盖率 `--fail-under=80`；单次本地 pytest 的默认门槛为 0，不代表 CI 已通过。覆盖率只说明执行范围，不能代替具体行为断言。

## 常见问题

- 数据库连接错误：核对是否使用 `db_session`，额外会话是否连接同一测试库。
- 异步测试失败：核对 `async/await` 和 fixture 类型；需要显式事件循环行为时参考相邻测试。
- Mock 不生效：核对调用模块实际绑定的导入名称，避免 patch 了定义模块却漏掉使用方。
- 数据污染：结束后台任务并关闭额外会话；不要让测试资源指向开发或生产数据。

Mock 路径对照：

```python
# ❌ 不好
mocker.patch("module.Class", ...)

# ✅ 好
mocker.patch("actual.import.path.module.Class", ...)
```

项目规则见[根 AGENTS.md](../../../AGENTS.md)，写作 Agent 契约见[子系统指南](../agent/AGENTS.md)。
