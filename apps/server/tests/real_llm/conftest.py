import os
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from models import Project, User
from models.agent_api_key import AgentApiKey
from services.agent_auth_service import generate_api_key, hash_api_key
from services.core.auth_service import hash_password

REAL_LLM_FLAG = "RUN_REAL_LLM_TESTS"
REAL_TEST_PASSWORD = "password123"


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _load_dotenv_if_exists() -> None:
    candidate_paths = [
        Path(__file__).resolve().parents[2] / ".env",
        Path(__file__).resolve().parents[4] / ".env",
    ]

    for env_path in candidate_paths:
        if not env_path.exists():
            continue
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def _require_keys(*env_keys: str) -> None:
    missing = [key for key in env_keys if not os.getenv(key)]
    if missing:
        joined = ", ".join(missing)
        pytest.skip(f"Missing required env keys: {joined}")


@pytest.fixture(scope="session", autouse=True)
def _prepare_real_llm_env():
    _load_dotenv_if_exists()


@pytest.fixture(scope="session")
def real_llm_enabled():
    if not _is_truthy(os.getenv(REAL_LLM_FLAG)):
        pytest.skip(f"Set {REAL_LLM_FLAG}=1 to run real LLM tests")


@pytest.fixture
def require_deepseek_key(real_llm_enabled):
    _require_keys("DEEPSEEK_API_KEY")


@pytest.fixture(autouse=True)
def _real_llm_uses_test_db(monkeypatch, db_session: Session):
    """The agent service opens its own sessions (history, file saves, outcomes): keep them on the test DB."""
    import database
    from tests.conftest import TestSessionLocal

    def _test_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(database, "create_session", TestSessionLocal)
    monkeypatch.setattr(database, "get_session", _test_get_session)
    monkeypatch.setattr("agent.service.create_session", lambda: Session(db_session.get_bind()))


@pytest.fixture
def real_user_project(db_session: Session, writing_prompt_configs):
    suffix = uuid.uuid4().hex[:8]
    username = f"real_llm_{suffix}"
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password(REAL_TEST_PASSWORD),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name=f"Real LLM Project {suffix}",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    return {
        "user": user,
        "project": project,
        "username": username,
    }


@pytest.fixture
async def real_auth_context(
    client: AsyncClient, real_user_project: dict[str, str]
):
    login_response = await client.post(
        "/api/auth/login",
        data={
            "username": real_user_project["username"],
            "password": REAL_TEST_PASSWORD,
        },
    )
    assert login_response.status_code == 200

    token = login_response.json()["access_token"]
    return {
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
        "user": real_user_project["user"],
        "project": real_user_project["project"],
    }


@pytest.fixture
def real_agent_api_key(db_session: Session, real_user_project: dict[str, str]):
    user = real_user_project["user"]
    project = real_user_project["project"]

    plain_key = generate_api_key()
    key_hash = hash_api_key(plain_key)
    api_key = AgentApiKey(
        user_id=user.id,
        key_prefix="eg_",
        key_hash=key_hash,
        name="Real LLM Chat Key",
        scopes=["chat"],
        project_ids=[project.id],
        is_active=True,
    )
    db_session.add(api_key)
    db_session.commit()
    db_session.refresh(api_key)

    return {
        "entity": api_key,
        "plain_key": plain_key,
        "project": project,
    }
