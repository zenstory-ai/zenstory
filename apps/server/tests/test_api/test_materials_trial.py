"""素材拆书免费试用：一个账号一本、只拆前 N 章；平台原因失败退还。"""

import io
import json
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import select

import api.materials.upload as materials_upload_api
from config.material_settings import material_settings
from core.error_codes import ErrorCode
from models import User
from models.material_models import IngestionJob
from models.subscription import UsageQuota
from services.core.auth_service import hash_password
from services.material.ingestion_jobs_service import IngestionJobsService


def _novel_bytes(chapters: int = 3) -> bytes:
    body = "这是一段用于测试的正文内容。" * 12
    return "\n".join(f"第{index}章 测试\n{body}" for index in range(1, chapters + 1)).encode("utf-8")


@pytest.fixture(autouse=True)
def stub_flow_dispatch(monkeypatch):
    async def _fake_start_flow_deployment(*args, **kwargs):
        return "flow-run-test"

    monkeypatch.setattr(materials_upload_api, "_start_flow_deployment", _fake_start_flow_deployment)


async def _free_author(client: AsyncClient, db_session) -> tuple[User, dict[str, str]]:
    suffix = uuid4().hex[:8]
    user = User(
        username=f"trial_{suffix}",
        email=f"trial_{suffix}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    login = await client.post("/api/auth/login", data={"username": user.username, "password": "password123"})
    return user, {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _upload(client: AsyncClient, headers: dict[str, str]):
    return await client.post(
        "/api/v1/materials/upload",
        files={"file": ("ref.txt", io.BytesIO(_novel_bytes()), "text/plain")},
        headers=headers,
    )


@pytest.mark.integration
async def test_trial_is_off_until_enabled(client: AsyncClient, db_session, monkeypatch):
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", False)
    _user, headers = await _free_author(client, db_session)

    quota = (await client.get("/api/v1/subscription/quota", headers=headers)).json()
    assert quota["material_trial"]["available"] is False
    assert (await _upload(client, headers)).status_code == 402
    assert (await client.get("/api/v1/materials", headers=headers)).status_code == 402


@pytest.mark.integration
async def test_free_author_gets_one_capped_trial_and_can_read_its_result(
    client: AsyncClient, db_session, monkeypatch
):
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    monkeypatch.setattr(material_settings, "TRIAL_MAX_CHAPTERS", 2)
    user, headers = await _free_author(client, db_session)

    before = (await client.get("/api/v1/subscription/quota", headers=headers)).json()["material_trial"]
    assert before == {"available": True, "used": False, "max_chapters": 2}

    response = await _upload(client, headers)
    assert response.status_code == 200
    job = db_session.get(IngestionJob, response.json()["job_id"])
    billing = json.loads(job.stage_progress)["billing"]
    assert billing["quota_mode"] == "trial"
    assert billing["chapter_limit"] == 2
    quota_row = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == user.id)).one()
    assert quota_row.material_decompositions_used == 0

    after = (await client.get("/api/v1/subscription/quota", headers=headers)).json()["material_trial"]
    assert after == {"available": False, "used": True, "max_chapters": 2}
    # One trial per account.
    assert (await _upload(client, headers)).status_code == 402
    # The trial book is readable in the library.
    library = await client.get("/api/v1/materials", headers=headers)
    assert library.status_code == 200
    assert [item["id"] for item in library.json()] == [response.json()["novel_id"]]


@pytest.mark.integration
async def test_platform_failure_gives_the_trial_back(client: AsyncClient, db_session, monkeypatch):
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    _user, headers = await _free_author(client, db_session)
    response = await _upload(client, headers)
    job = db_session.get(IngestionJob, response.json()["job_id"])

    IngestionJobsService().fail_job(
        db_session,
        job,
        error_code=ErrorCode.MATERIAL_LLM_UNAVAILABLE,
        stage="stage1",
        reason="upstream down",
    )

    trial = (await client.get("/api/v1/subscription/quota", headers=headers)).json()["material_trial"]
    assert trial["available"] is True
    assert (await _upload(client, headers)).status_code == 200


@pytest.mark.unit
def test_trial_switch_is_the_documented_environment_variable(monkeypatch):
    """运维按 MATERIAL_TRIAL_ENABLED / MATERIAL_TRIAL_MAX_CHAPTERS 打开试用（设置类带 MATERIAL_ 前缀）。"""
    from config.material_settings import MaterialSettings

    monkeypatch.setenv("MATERIAL_TRIAL_ENABLED", "true")
    monkeypatch.setenv("MATERIAL_TRIAL_MAX_CHAPTERS", "12")

    settings = MaterialSettings()

    assert settings.TRIAL_ENABLED is True
    assert settings.TRIAL_MAX_CHAPTERS == 12
