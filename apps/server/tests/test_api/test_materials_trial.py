"""素材拆书免费试用：一个账号永久一次、前 3 章且不超过 1.5 万字；提交后不退还。"""

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
    assert before == {"available": True, "used": False, "max_chapters": 2, "max_characters": 15_000}

    response = await _upload(client, headers)
    assert response.status_code == 200
    job = db_session.get(IngestionJob, response.json()["job_id"])
    billing = json.loads(job.stage_progress)["billing"]
    assert billing["quota_mode"] == "trial"
    assert billing["chapter_limit"] == 2
    assert billing["character_limit"] == 15_000
    source_meta = json.loads(db_session.get(Novel, response.json()["novel_id"]).source_meta)
    assert source_meta["trial_chapter_limit"] == 2
    assert source_meta["trial_character_limit"] == 15_000
    quota_row = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == user.id)).one()
    assert quota_row.material_decompositions_used == 0

    after = (await client.get("/api/v1/subscription/quota", headers=headers)).json()["material_trial"]
    assert after == {"available": False, "used": True, "max_chapters": 2, "max_characters": 15_000}
    # One trial per account.
    assert (await _upload(client, headers)).status_code == 402
    # The trial book is readable in the library.
    library = await client.get("/api/v1/materials", headers=headers)
    assert library.status_code == 200
    assert [item["id"] for item in library.json()] == [response.json()["novel_id"]]


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code",
    [
        ErrorCode.MATERIAL_LLM_UNAVAILABLE,
        ErrorCode.MATERIAL_DISPATCH_FAILED,
        ErrorCode.MATERIAL_PROCESSING_TIMEOUT,
        ErrorCode.MATERIAL_DECOMPOSE_FAILED,
    ],
)
async def test_a_failed_trial_stays_used_and_visible(client: AsyncClient, db_session, monkeypatch, error_code):
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    user, headers = await _free_author(client, db_session)
    response = await _upload(client, headers)
    job = db_session.get(IngestionJob, response.json()["job_id"])

    IngestionJobsService().fail_job(db_session, job, error_code=error_code, stage="stage1", reason="x")

    trial = (await client.get("/api/v1/subscription/quota", headers=headers)).json()["material_trial"]
    assert trial["available"] is False
    assert trial["used"] is True
    assert (await _upload(client, headers)).status_code == 402
    library = (await client.get("/api/v1/materials", headers=headers)).json()
    assert [(item["id"], item["status"]) for item in library] == [(response.json()["novel_id"], "failed")]
    assert library[0]["error_message"] == error_code
    db_session.expire_all()
    assert db_session.exec(select(UsageQuota).where(UsageQuota.user_id == user.id)).one().material_trial_used_at


@pytest.mark.integration
async def test_deleting_the_trial_book_does_not_give_another_trial(client: AsyncClient, db_session, monkeypatch):
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    _user, headers = await _free_author(client, db_session)
    novel_id = (await _upload(client, headers)).json()["novel_id"]

    assert (await client.delete(f"/api/v1/materials/{novel_id}", headers=headers)).status_code == 200

    trial = (await client.get("/api/v1/subscription/quota", headers=headers)).json()["material_trial"]
    assert trial["used"] is True
    assert (await _upload(client, headers)).status_code == 402


@pytest.mark.integration
async def test_a_failed_trial_cannot_retry_for_free(client: AsyncClient, db_session, monkeypatch):
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    _user, headers = await _free_author(client, db_session)
    response = await _upload(client, headers)
    job = db_session.get(IngestionJob, response.json()["job_id"])
    IngestionJobsService().fail_job(
        db_session, job, error_code=ErrorCode.MATERIAL_LLM_UNAVAILABLE, stage="stage1", reason="x"
    )

    retry = await client.post(f"/api/v1/materials/{response.json()['novel_id']}/retry", headers=headers)

    assert retry.status_code == 402


@pytest.mark.integration
@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("TRIAL_MAX_CHAPTERS", 0),
        ("TRIAL_MAX_CHAPTERS", -1),
        ("TRIAL_MAX_CHAPTERS", 4),
        ("TRIAL_MAX_CHAPTERS", 20),
        ("TRIAL_MAX_CHARACTERS", 0),
        ("TRIAL_MAX_CHARACTERS", -5),
        ("TRIAL_MAX_CHARACTERS", 15_001),
        ("TRIAL_MAX_CHARACTERS", 300_000),
    ],
)
async def test_an_invalid_limit_closes_the_trial_instead_of_widening_it(
    client: AsyncClient, db_session, monkeypatch, setting, value
):
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    monkeypatch.setattr(material_settings, setting, value)
    _user, headers = await _free_author(client, db_session)

    trial = (await client.get("/api/v1/subscription/quota", headers=headers)).json()["material_trial"]

    assert trial["available"] is False
    assert (await _upload(client, headers)).status_code == 402


@pytest.mark.unit
def test_a_malformed_environment_limit_does_not_stop_the_service_or_widen_the_trial(monkeypatch):
    from config.material_settings import MaterialSettings, trial_limits

    monkeypatch.setenv("MATERIAL_TRIAL_MAX_CHAPTERS", "twenty")
    settings = MaterialSettings(_env_file=None)

    assert trial_limits(settings) is None


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
async def test_trial_accepts_a_whole_book_and_keeps_only_the_first_chapters(
    client: AsyncClient, db_session, monkeypatch, capturing_storage
):
    """150 章、约 42 万字的整本：试拆只检查、只存、只派发前 3 章。"""
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    _user, headers = await _free_author(client, db_session)
    whole_book = _long_novel(150, 2_800)
    assert len(whole_book) > MAX_TEXT_CHARACTERS

    response = await _upload_text(client, headers, whole_book)

    assert response.status_code == 200, response.text
    stored_text, _encoding = decode_novel_bytes(capturing_storage.stored[0])
    stored_chapters = split_novel_text(stored_text)
    assert len(stored_chapters) == 3
    assert stored_chapters == split_novel_text(whole_book)[:3]
    meta = json.loads(db_session.get(Novel, response.json()["novel_id"]).source_meta)
    assert meta["chapter_count"] == 3
    assert meta["source_chapter_count"] == 150
    assert meta["char_count"] == len(stored_text)
    library = (await client.get("/api/v1/materials", headers=headers)).json()
    assert library[0]["trial_chapter_limit"] == 3
    assert library[0]["source_chapter_count"] == 150


@pytest.mark.integration
@pytest.mark.parametrize(
    "text",
    [
        pytest.param(_long_novel(20, 15_000), id="twenty_chapters_holding_300k"),
        pytest.param(_long_novel(1, 20_000), id="one_oversized_chapter"),
        pytest.param("楔子\n" + "序" * 15_100 + "\n第1章 开始\n" + "正文" * 100, id="oversized_preface"),
    ],
)
async def test_trial_refuses_over_the_character_limit_before_storage_and_charge(
    client: AsyncClient, db_session, monkeypatch, capturing_storage, text
):
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    _user, headers = await _free_author(client, db_session)

    response = await _upload_text(client, headers, text)

    assert response.status_code == 400
    payload = response.json()
    assert payload["error_code"] == ErrorCode.FILE_CONTENT_TOO_LONG
    assert payload["error_detail"]["limit"] == 15_000
    assert payload["error_detail"]["char_count"] > 15_000
    assert capturing_storage.stored == []
    trial = (await client.get("/api/v1/subscription/quota", headers=headers)).json()["material_trial"]
    assert trial["available"] is True
    assert db_session.exec(select(Novel)).all() == []


@pytest.mark.integration
@pytest.mark.parametrize(("extra", "status"), [(0, 200), (1, 400)])
async def test_trial_limit_counts_unicode_characters_at_the_exact_boundary(
    client: AsyncClient, db_session, monkeypatch, capturing_storage, extra, status
):
    """按 Unicode 字符数，不按字节；emoji 算 1 个字符，换行统一成一个字符。"""
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    _user, headers = await _free_author(client, db_session)
    heading = "第1章 开始\r\n"
    body = "😀" * (15_000 - len("第1章 开始\n") + extra)

    response = await _upload_text(client, headers, heading + body)

    assert response.status_code == status, response.text
    if status == 200:
        stored_text, _encoding = decode_novel_bytes(capturing_storage.stored[0])
        assert len(stored_text) == 15_000


@pytest.mark.integration
async def test_second_trial_account_on_a_network_is_refused_until_the_next_beijing_day(
    client: AsyncClient, db_session, monkeypatch, capturing_storage
):
    from services.material import trial_network

    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    first_day = datetime(2026, 11, 6, 15, 59)  # 23:59 Beijing
    monkeypatch.setattr(trial_network, "utcnow", lambda: first_day)
    _first, first_headers = await _free_author(client, db_session)
    second, second_headers = await _free_author(client, db_session)
    network = {"X-Forwarded-For": "93.184.216.34"}

    assert (await _upload(client, first_headers | network)).status_code == 200
    stored_before = len(capturing_storage.stored)
    refused = await _upload(client, second_headers | network)

    assert refused.status_code == 402
    assert refused.json()["error_code"] == ErrorCode.MATERIAL_TRIAL_NETWORK_LIMIT
    assert len(capturing_storage.stored) == stored_before
    db_session.expire_all()
    quota = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == second.id)).first()
    assert quota is None or quota.material_trial_used_at is None

    monkeypatch.setattr(trial_network, "utcnow", lambda: first_day + timedelta(minutes=2))
    assert (await _upload(client, second_headers | network)).status_code == 200


@pytest.mark.integration
async def test_trial_network_seat_is_not_released_when_the_first_trial_fails(
    client: AsyncClient, db_session, monkeypatch, capturing_storage
):
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    monkeypatch.setattr(materials_upload_api, "_start_flow_deployment", _dispatch_unavailable)
    _first, first_headers = await _free_author(client, db_session)
    _second, second_headers = await _free_author(client, db_session)
    network = {"X-Forwarded-For": "93.184.216.35"}

    assert (await _upload(client, first_headers | network)).status_code == 503
    monkeypatch.setattr(materials_upload_api, "_start_flow_deployment", _dispatch_accepted)

    assert (await _upload(client, second_headers | network)).status_code == 402


@pytest.mark.integration
async def test_a_missing_client_network_skips_only_the_network_rule(
    client: AsyncClient, db_session, monkeypatch, capturing_storage
):
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    _first, first_headers = await _free_author(client, db_session)
    _second, second_headers = await _free_author(client, db_session)
    private = {"X-Forwarded-For": "10.0.0.1"}

    assert (await _upload(client, first_headers | private)).status_code == 200
    assert (await _upload(client, second_headers | private)).status_code == 200
    # The account rule still holds.
    assert (await _upload(client, first_headers | private)).status_code == 402


@pytest.mark.integration
async def test_a_percent_encoded_upload_path_is_refused(client: AsyncClient, db_session, monkeypatch):
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    _user, headers = await _free_author(client, db_session)

    response = await client.post(
        "/api/v1/materials/%75pload",
        files={"file": ("ref.txt", io.BytesIO(_novel_bytes()), "text/plain")},
        headers=headers,
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 派发失败也已用掉试拆
# ---------------------------------------------------------------------------

async def _dispatch_unavailable(*args, **kwargs):
    return None


async def _dispatch_accepted(*args, **kwargs):
    return "flow-run-test"


@pytest.mark.integration
async def test_dispatch_failure_after_commit_uses_the_trial_and_keeps_a_failed_book(
    client: AsyncClient, db_session, monkeypatch
):
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    _user, headers = await _free_author(client, db_session)

    monkeypatch.setattr(materials_upload_api, "_start_flow_deployment", _dispatch_unavailable)
    assert (await _upload(client, headers)).status_code == 503
    monkeypatch.setattr(materials_upload_api, "_start_flow_deployment", _dispatch_accepted)
    assert (await _upload(client, headers)).status_code == 402

    library = (await client.get("/api/v1/materials", headers=headers)).json()

    assert [item["status"] for item in library] == ["failed"]
    assert library[0]["error_message"] == ErrorCode.MATERIAL_DISPATCH_FAILED


@pytest.mark.integration
async def test_an_uncommitted_trial_transaction_does_not_use_the_trial(
    client: AsyncClient, db_session, monkeypatch, capturing_storage
):
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    user, headers = await _free_author(client, db_session)

    def fail_after_claim(*_args, **_kwargs):
        raise RuntimeError("synthetic database failure")

    monkeypatch.setattr(IngestionJobsService, "set_billing", fail_after_claim)
    assert (await _upload(client, headers)).status_code == 500

    db_session.expire_all()
    quota = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == user.id)).first()
    assert quota is None or quota.material_trial_used_at is None
    assert db_session.exec(select(Novel)).all() == []


@pytest.mark.integration
async def test_a_failed_trial_book_can_be_retried_on_the_paid_path_after_upgrading(
    client: AsyncClient, db_session, monkeypatch
):
    """失败码在退款集合里时，付费用户仍能重试，不会一直得到 503。"""
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    user, headers = await _free_author(client, db_session)
    response = await _upload(client, headers)
    job = db_session.get(IngestionJob, response.json()["job_id"])
    IngestionJobsService().fail_job(
        db_session, job, error_code=ErrorCode.MATERIAL_LLM_UNAVAILABLE, stage="stage1", reason="x"
    )
    _grant_pro(db_session, user)

    retry = await client.post(f"/api/v1/materials/{response.json()['novel_id']}/retry", headers=headers)

    assert retry.status_code == 200, retry.text


@pytest.mark.integration
async def test_a_trial_book_retried_after_upgrading_stays_capped_to_its_stored_chapters(
    client: AsyncClient, db_session, monkeypatch
):
    monkeypatch.setattr(material_settings, "TRIAL_ENABLED", True)
    monkeypatch.setattr(material_settings, "TRIAL_MAX_CHAPTERS", 2)
    user, headers = await _free_author(client, db_session)
    response = await _upload(client, headers)
    job = db_session.get(IngestionJob, response.json()["job_id"])
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
