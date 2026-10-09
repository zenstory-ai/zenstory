"""素材拆书免费试用：一个账号一本、只拆前 N 章；平台原因失败退还。"""

import io
import json
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import select

import api.materials.upload as materials_upload_api
from api.materials.constants import MAX_TEXT_CHARACTERS
from config.material_settings import material_settings
from core.error_codes import ErrorCode
from models import User
from models.material_models import IngestionJob, Novel
from models.subscription import SubscriptionPlan, UsageQuota, UserSubscription
from services.core.auth_service import hash_password
from services.infra.upload_storage import StoredObject
from services.material.ingestion_jobs_service import IngestionJobsService
from services.material.novel_text import decode_novel_bytes, split_novel_text


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


# ---------------------------------------------------------------------------
# 整本网文：试拆只看前 N 章（字数上限、存储、派发都只针对前 N 章）
# ---------------------------------------------------------------------------

class _CapturingStorage:
    """Stand-in for the upload storage that keeps what the upload stored."""

    def __init__(self):
        self.stored: list[bytes] = []

    def put_material(self, *, owner_id, timestamp, original_name, content):
        self.stored.append(content)
        return StoredObject(f"/tmp/fake/{owner_id}_{len(self.stored)}.txt", len(content), "sha")

    def delete(self, *args, **kwargs):
        return None

    def close(self):
        return None


@pytest.fixture
def capturing_storage(monkeypatch):
    storage = _CapturingStorage()
    monkeypatch.setattr(materials_upload_api, "_storage", lambda: storage)
    return storage


def _long_novel(chapters: int, chars_per_chapter: int) -> str:
    sentence = "雾港的灯一盏盏亮起来，他把旧信折好放回怀里。"
    body = (sentence * (chars_per_chapter // len(sentence) + 1))[:chars_per_chapter]
    return "\n".join(f"第{index}章 雾港\n{body}" for index in range(1, chapters + 1))


async def _upload_text(client: AsyncClient, headers: dict[str, str], text: str):
    return await client.post(
        "/api/v1/materials/upload",
        files={"file": ("whole-book.txt", io.BytesIO(text.encode("utf-8")), "text/plain")},
        headers=headers,
    )


def _grant_pro(db_session, user: User) -> None:
    plan = db_session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == "pro")).first()
    if plan is None:
        plan = SubscriptionPlan(
            name="pro",
            display_name="Pro",
            display_name_en="Pro",
            price_monthly_cents=4900,
            price_yearly_cents=39900,
            features={"materials_library_access": True, "material_decompositions": 5},
            is_active=True,
        )
        db_session.add(plan)
        db_session.commit()
        db_session.refresh(plan)
    now = datetime.utcnow()
    db_session.add(
        UserSubscription(
            user_id=user.id,
            plan_id=plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
    )
    db_session.commit()


@pytest.mark.integration
async def test_trial_accepts_a_whole_book_over_the_limit_and_keeps_only_the_first_chapters(
    client: AsyncClient, db_session, monkeypatch, capturing_storage
):
    """150 章、约 42 万字的整本：试拆只检查、只存、只派发前 20 章。"""
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    monkeypatch.setattr(material_settings, "TRIAL_MAX_CHAPTERS", 20)
    _user, headers = await _free_author(client, db_session)
    whole_book = _long_novel(150, 2_800)
    assert len(whole_book) > MAX_TEXT_CHARACTERS

    response = await _upload_text(client, headers, whole_book)

    assert response.status_code == 200, response.text
    stored_text, _encoding = decode_novel_bytes(capturing_storage.stored[0])
    stored_chapters = split_novel_text(stored_text)
    assert len(stored_chapters) == 20
    assert stored_chapters == split_novel_text(whole_book)[:20]
    meta = json.loads(db_session.get(Novel, response.json()["novel_id"]).source_meta)
    assert meta["chapter_count"] == 20
    assert meta["source_chapter_count"] == 150
    assert meta["char_count"] == len(stored_text)
    library = (await client.get("/api/v1/materials", headers=headers)).json()
    assert library[0]["trial_chapter_limit"] == 20
    assert library[0]["source_chapter_count"] == 150


@pytest.mark.integration
async def test_trial_rejects_first_chapters_over_the_limit_with_the_count_and_keeps_the_trial(
    client: AsyncClient, db_session, monkeypatch, capturing_storage
):
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    monkeypatch.setattr(material_settings, "TRIAL_MAX_CHAPTERS", 2)
    _user, headers = await _free_author(client, db_session)

    response = await _upload_text(client, headers, _long_novel(5, 160_000))

    assert response.status_code == 400
    payload = response.json()
    assert payload["error_code"] == ErrorCode.FILE_CONTENT_TOO_LONG
    assert payload["error_detail"]["trial_chapters"] == 2
    assert payload["error_detail"]["char_count"] > payload["error_detail"]["limit"]
    assert capturing_storage.stored == []
    trial = (await client.get("/api/v1/subscription/quota", headers=headers)).json()["material_trial"]
    assert trial["available"] is True


@pytest.mark.integration
async def test_paid_upload_keeps_the_whole_book_limit(
    client: AsyncClient, db_session, monkeypatch, capturing_storage
):
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    user, headers = await _free_author(client, db_session)
    _grant_pro(db_session, user)
    whole_book = _long_novel(150, 2_800)

    response = await _upload_text(client, headers, whole_book)

    assert response.status_code == 400
    assert response.json()["error_detail"]["char_count"] == len(whole_book)
    assert "trial_chapters" not in response.json()["error_detail"]
    assert capturing_storage.stored == []


@pytest.mark.integration
async def test_a_just_upgraded_author_with_an_unused_trial_gets_the_whole_book_on_the_paid_path(
    client: AsyncClient, db_session, monkeypatch, capturing_storage
):
    """页面还缓存着免费状态时，浏览器照样上传原文件；服务端按当前权益走付费、拆整本，不占试拆。"""
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    monkeypatch.setattr(material_settings, "TRIAL_MAX_CHAPTERS", 20)
    user, headers = await _free_author(client, db_session)
    _grant_pro(db_session, user)
    whole_book = _long_novel(30, 2_000)

    response = await _upload_text(client, headers, whole_book)

    assert response.status_code == 200, response.text
    stored_text, _encoding = decode_novel_bytes(capturing_storage.stored[0])
    assert len(split_novel_text(stored_text)) == 30
    meta = json.loads(db_session.get(Novel, response.json()["novel_id"]).source_meta)
    assert meta["chapter_count"] == 30
    assert "trial_chapter_limit" not in meta
    billing = json.loads(db_session.get(IngestionJob, response.json()["job_id"]).stage_progress)["billing"]
    assert billing.get("quota_mode") != "trial"
    assert "chapter_limit" not in billing
    quota = (await client.get("/api/v1/subscription/quota", headers=headers)).json()
    assert quota["material_decompositions"]["used"] == 1
    assert quota["material_trial"]["used"] is False


@pytest.mark.integration
async def test_whole_book_is_rejected_while_the_trial_is_off(
    client: AsyncClient, db_session, monkeypatch, capturing_storage
):
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", False)
    _user, headers = await _free_author(client, db_session)

    response = await _upload_text(client, headers, _long_novel(150, 2_800))

    assert response.status_code == 402
    assert capturing_storage.stored == []


# ---------------------------------------------------------------------------
# 退还过的失败试拆不是素材库里的书
# ---------------------------------------------------------------------------

async def _dispatch_unavailable(*args, **kwargs):
    return None


async def _dispatch_accepted(*args, **kwargs):
    return "flow-run-test"


@pytest.mark.integration
async def test_refunded_trial_attempts_do_not_reappear_once_the_trial_is_used(
    client: AsyncClient, db_session, monkeypatch
):
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    _user, headers = await _free_author(client, db_session)

    monkeypatch.setattr(materials_upload_api, "_start_flow_deployment", _dispatch_unavailable)
    for _ in range(2):
        assert (await _upload(client, headers)).status_code == 503
    monkeypatch.setattr(materials_upload_api, "_start_flow_deployment", _dispatch_accepted)
    accepted = await _upload(client, headers)
    assert accepted.status_code == 200

    library = await client.get("/api/v1/materials", headers=headers)

    assert library.status_code == 200
    assert [item["id"] for item in library.json()] == [accepted.json()["novel_id"]]


@pytest.mark.integration
async def test_a_trial_book_retried_after_upgrading_stays_capped_to_its_stored_chapters(
    client: AsyncClient, db_session, monkeypatch
):
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    monkeypatch.setattr(material_settings, "TRIAL_MAX_CHAPTERS", 2)
    user, headers = await _free_author(client, db_session)
    response = await _upload(client, headers)
    job = db_session.get(IngestionJob, response.json()["job_id"])
    # A non-refundable failure: the trial stays used.
    IngestionJobsService().fail_job(
        db_session, job, error_code=ErrorCode.MATERIAL_DECOMPOSE_FAILED, stage="stage1", reason="x",
    )
    _grant_pro(db_session, user)

    retry = await client.post(f"/api/v1/materials/{response.json()['novel_id']}/retry", headers=headers)

    assert retry.status_code == 200, retry.text
    new_job = db_session.get(IngestionJob, retry.json()["job_id"])
    billing = json.loads(new_job.stage_progress)["billing"]
    assert billing["chapter_limit"] == 2
    assert billing.get("quota_mode") != "trial"
