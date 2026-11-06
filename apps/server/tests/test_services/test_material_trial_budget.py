"""Lifetime trial cost: reserved before every provider request, never reset, not bypassed by Pro."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import object_session
from sqlmodel import select

from agent.core import llm_failover, llm_providers
from core.error_codes import ErrorCode
from core.error_handler import APIException
from flows.utils.clients.llm import MaterialLLMClient
from flows.utils.helpers.exceptions import LLMNonRetryableError
from models import SubscriptionPlan, User, UserSubscription
from models.ai_cost_budget import AICostDailyBudget, AICostReservation
from models.material_models import IngestionJob, Novel
from models.subscription import UsageQuota
from services.material.ingestion_jobs_service import IngestionJobsService, material_job_scope
from services.usage import cost_budget as budget
from services.usage import model_call_guard

NOW = datetime(2026, 11, 6, 2, tzinfo=UTC)  # Friday, 10:00 Beijing: peak prices.


@pytest.fixture(autouse=True)
def fresh_owner_cache(monkeypatch):
    """Novel ids repeat across test databases; the process cache would keep old owners."""
    from services.usage import llm_usage_service

    monkeypatch.setattr(llm_usage_service, "_novel_owner_cache", {})
    monkeypatch.setattr(llm_usage_service, "_chapter_novel_cache", {})


def _account(session, *, used=True, charged=0) -> User:
    account = User(username=f"u{id(session)}x{charged}", email=f"u{id(session)}x{charged}@example.com", hashed_password="x")
    session.add(account)
    session.flush()
    session.add(UsageQuota(
        user_id=account.id,
        period_start=NOW.replace(tzinfo=None),
        period_end=(NOW + timedelta(days=30)).replace(tzinfo=None),
        material_trial_used_at=NOW.replace(tzinfo=None) if used else None,
        material_trial_charged_units=charged,
    ))
    session.commit()
    return account


def _trial_novel(session, account, *, status="processing", mode="trial") -> Novel:
    novel = Novel(user_id=account.id, title="t", source_meta="{}")
    session.add(novel)
    session.flush()
    job = IngestionJob(novel_id=novel.id, source_path="x", status=status, total_chapters=3, processed_chapters=0)
    IngestionJobsService.set_billing(job, quota_charged=True, quota_mode=mode, chapter_limit=3, character_limit=15_000)
    session.add(job)
    session.commit()
    return novel


def _pro(session, account) -> None:
    plan = SubscriptionPlan(
        name=f"pro_{account.id[:6]}", display_name="Pro", display_name_en="Pro", price_monthly_cents=1,
        price_yearly_cents=1, features={},
    )
    session.add(plan)
    session.flush()
    session.add(UserSubscription(
        user_id=account.id, plan_id=plan.id, status="active",
        current_period_start=NOW.replace(tzinfo=None),
        current_period_end=(NOW + timedelta(days=30)).replace(tzinfo=None),
    ))
    session.commit()


def _payload(maximum=4096, content="写作"):
    return {"model": "deepseek-flash", "messages": [{"role": "user", "content": content}], "max_tokens": maximum}


def _charged(session, account) -> int:
    session.expire_all()
    return session.exec(select(UsageQuota).where(UsageQuota.user_id == account.id)).one().material_trial_charged_units


def _reserve(account, payload=None, *, output_limit=4096):
    with budget.material_trial_call(output_limit=output_limit):
        return budget.reserve_model_call(payload or _payload(), user_id=account.id, now=NOW)


def test_reservation_is_charged_then_settled_to_actual_usage_once(db_session):
    account = _account(db_session)
    call = _reserve(account)
    reserved = _charged(db_session, account)
    assert reserved > 4096 * 800

    budget.settle_model_call(call, {"prompt_tokens": 100, "completion_tokens": 100})
    actual = 100 * 200 + 100 * 800
    assert _charged(db_session, account) == actual
    budget.settle_model_call(call, {"prompt_tokens": 9, "completion_tokens": 9})
    assert _charged(db_session, account) == actual


@pytest.mark.parametrize("usage", [None, {}, {"completion_tokens": 0}])
def test_unknown_usage_keeps_the_upper_bound(db_session, usage):
    account = _account(db_session)
    call = _reserve(account)
    reserved = _charged(db_session, account)

    budget.settle_model_call(call, usage)

    assert _charged(db_session, account) == reserved


def test_budget_refuses_before_any_reservation_and_does_not_touch_the_daily_bucket(db_session):
    account = _account(db_session, charged=budget.MATERIAL_TRIAL_BUDGET_UNITS - 1)

    with pytest.raises(APIException) as refused:
        _reserve(account)

    assert refused.value.error_code == ErrorCode.MATERIAL_TRIAL_COST_LIMIT
    assert _charged(db_session, account) == budget.MATERIAL_TRIAL_BUDGET_UNITS - 1
    assert db_session.exec(select(AICostReservation)).all() == []
    assert db_session.exec(select(AICostDailyBudget)).all() == []


def test_concurrent_reservations_cannot_exceed_the_lifetime_budget(db_session):
    account = _account(db_session)
    uid = account.id

    def reserve(_):
        request = _payload(4096, "字" * 40_000)
        try:
            with budget.material_trial_call(output_limit=4096):
                return budget.reserve_model_call(request, user_id=uid, now=NOW)
        except APIException as error:
            assert error.error_code == ErrorCode.MATERIAL_TRIAL_COST_LIMIT
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        calls = list(pool.map(reserve, range(30)))

    granted = [call for call in calls if call is not None]
    assert 0 < len(granted) < 30
    assert _charged(db_session, account) <= budget.MATERIAL_TRIAL_BUDGET_UNITS
    assert len(db_session.exec(select(AICostReservation)).all()) == len(granted)


def test_output_limit_is_on_the_payload_and_replaces_other_bounds(db_session):
    account = _account(db_session)
    request = _payload(64_000)
    request["max_completion_tokens"] = 64_000

    _reserve(account, request)

    assert request["max_tokens"] == 4096
    assert "max_completion_tokens" not in request


def test_a_trial_account_without_its_used_mark_is_refused(db_session):
    account = _account(db_session, used=False)

    with pytest.raises(APIException) as refused:
        _reserve(account)

    assert refused.value.error_code == budget.UNAVAILABLE_CODE
    assert _charged(db_session, account) == 0


def test_a_trial_account_without_a_quota_row_is_refused(db_session):
    account = User(username="noquota", email="noquota@example.com", hashed_password="x")
    db_session.add(account)
    db_session.commit()

    with pytest.raises(APIException) as refused:
        _reserve(account)

    assert refused.value.error_code == budget.UNAVAILABLE_CODE


def test_unpriced_model_and_stream_are_refused_even_after_upgrading(db_session):
    account = _account(db_session)
    _pro(db_session, account)

    for request in (
        {**_payload(), "model": "unpriced"},
        {**_payload(), "stream": True},
    ):
        with pytest.raises(APIException) as refused:
            _reserve(account, request)
        assert refused.value.error_code == budget.UNAVAILABLE_CODE
    assert _charged(db_session, account) == 0


def test_upgrading_mid_trial_keeps_the_lifetime_budget_without_a_daily_charge(db_session):
    account = _account(db_session)
    _pro(db_session, account)

    call = _reserve(account)

    assert call is not None
    assert _charged(db_session, account) > 0
    assert db_session.exec(select(AICostDailyBudget)).all() == []
    budget.settle_model_call(call, {"prompt_tokens": 100, "completion_tokens": 100})
    assert _charged(db_session, account) == 100 * 200 + 100 * 800


def test_paid_call_outside_a_trial_is_untouched(db_session):
    account = _account(db_session)
    _pro(db_session, account)

    assert budget.reserve_model_call(_payload(), user_id=account.id, now=NOW) is None
    assert _charged(db_session, account) == 0


def test_free_call_outside_a_trial_does_not_touch_the_trial_budget(db_session):
    account = _account(db_session)

    call = budget.reserve_model_call(_payload(), user_id=account.id, now=NOW)

    assert call is not None
    assert _charged(db_session, account) == 0
    assert db_session.exec(select(AICostDailyBudget)).one().charged_units > 0


def test_trial_call_also_keeps_the_free_daily_backstop(db_session):
    account = _account(db_session)
    db_session.add(AICostDailyBudget(
        user_id=account.id, day=datetime(2026, 11, 6).date(), charged_units=budget.FREE_DAILY_BUDGET_UNITS,
    ))
    db_session.commit()

    with pytest.raises(APIException) as refused:
        _reserve(account)

    assert refused.value.error_code == budget.COST_LIMIT_CODE
    assert _charged(db_session, account) == 0


# ---------------------------------------------------------------------------
# The real client path: every provider request reserves; nothing retries inside the SDK.
# ---------------------------------------------------------------------------


class _Endpoint:
    def __init__(self, failures=0):
        self.calls = []
        self.failures = failures
        self.chat = SimpleNamespace(completions=self)
        self.retry_settings = []

    def with_options(self, **options):
        self.retry_settings.append(options)
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self.failures:
            raise TimeoutError("synthetic")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="stop")],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=10,
                model_dump=lambda: {"prompt_tokens": 10, "completion_tokens": 10},
            ),
            model=kwargs["model"],
        )


def _client(monkeypatch, *, primary_failures=0, primary="deepseek", backup=None):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", primary)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic")
    monkeypatch.setenv("MINIMAX_API_KEY", "synthetic")
    monkeypatch.delenv("LLM_PRIMARY_MODEL", raising=False)
    monkeypatch.delenv("LLM_BACKUP_MODEL", raising=False)
    if backup:
        monkeypatch.setenv("LLM_BACKUP_PROVIDER", backup)
    else:
        monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    endpoints = {"primary": _Endpoint(primary_failures), "backup": _Endpoint()}
    llm_failover.reset_failover_state()

    def get_client(provider, **_kwargs):
        return model_call_guard.install_sync_cost_guard(_rewrap(endpoints[provider.slot]))

    monkeypatch.setattr(llm_providers, "get_sync_client", get_client)
    return endpoints, MaterialLLMClient()


def _rewrap(endpoint):
    """A fresh wrapper per call so the guard installs on an unguarded object."""
    completions = SimpleNamespace(create=endpoint.create)
    wrapper = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    wrapper.with_options = lambda **options: (endpoint.retry_settings.append(options), SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=endpoint.create))
    ))[1]
    return wrapper


def _job_of(session, novel) -> int:
    return session.exec(select(IngestionJob.id).where(IngestionJob.novel_id == novel.id)).first()


def _call(client, novel, *, job_id=None, **kwargs):
    job_id = job_id or _job_of(object_session(novel), novel)
    with material_job_scope(job_id):
        return client.chat_completion([{"role": "user", "content": "x"}], usage_novel_id=novel.id, **kwargs)


def test_trial_call_caps_payload_turns_thinking_off_and_disables_sdk_retries(db_session, monkeypatch):
    account = _account(db_session)
    novel = _trial_novel(db_session, account)
    endpoints, client = _client(monkeypatch)

    _call(client, novel)

    request = endpoints["primary"].calls[0]
    assert request["max_tokens"] == 4096
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert endpoints["primary"].retry_settings == [{"max_retries": 0}]
    # This path uses the real clock: peak or off-peak input and output prices for 10 + 10 tokens.
    assert _charged(db_session, account) in {10 * 200 + 10 * 800, 10 * 100 + 10 * 400}


def test_merged_settings_may_use_the_higher_output_limit_but_no_more(db_session, monkeypatch):
    account = _account(db_session)
    novel = _trial_novel(db_session, account)
    endpoints, client = _client(monkeypatch)

    _call(client, novel, trial_output_tokens=8192)
    _call(client, novel, trial_output_tokens=100_000)

    assert [call["max_tokens"] for call in endpoints["primary"].calls] == [8192, 8192]


def test_failover_headroom_cannot_exceed_the_output_limit(db_session, monkeypatch):
    """MiniMax cannot turn thinking off, so the failover adds headroom; the payload stays capped."""
    account = _account(db_session)
    novel = _trial_novel(db_session, account)
    endpoints, client = _client(monkeypatch, primary_failures=1, backup="minimax")

    _call(client, novel)

    assert endpoints["backup"].calls[0]["max_tokens"] == 4096
    reservations = db_session.exec(select(AICostReservation)).all()
    assert len(reservations) == 2  # primary (unknown, retained) + backup


def test_task_retry_reserves_again_and_exhaustion_stops_before_the_provider(db_session, monkeypatch):
    account = _account(db_session, charged=budget.MATERIAL_TRIAL_BUDGET_UNITS - 1)
    novel = _trial_novel(db_session, account)
    endpoints, client = _client(monkeypatch)

    with pytest.raises(LLMNonRetryableError) as refused:
        _call(client, novel)

    assert refused.value.error_code == ErrorCode.MATERIAL_TRIAL_COST_LIMIT
    assert endpoints["primary"].calls == []


def test_budget_survives_a_failed_job_and_a_deleted_book(db_session, monkeypatch):
    account = _account(db_session)
    novel = _trial_novel(db_session, account)
    _endpoints, client = _client(monkeypatch)
    _call(client, novel)
    spent = _charged(db_session, account)

    job = IngestionJobsService().get_latest_by_novel(db_session, novel.id)
    IngestionJobsService().fail_job(
        db_session, job, error_code=ErrorCode.MATERIAL_LLM_UNAVAILABLE, stage="x", reason="x"
    )
    novel = db_session.get(Novel, novel.id)
    novel.deleted_at = NOW.replace(tzinfo=None)
    db_session.commit()

    assert _charged(db_session, account) == spent


@pytest.mark.parametrize("status", ["failed", "completed", "completed_with_errors"])
def test_late_worker_of_a_finished_trial_job_sends_nothing(db_session, monkeypatch, status):
    account = _account(db_session)
    novel = _trial_novel(db_session, account, status=status)
    endpoints, client = _client(monkeypatch)

    with pytest.raises(LLMNonRetryableError):
        _call(client, novel)

    assert endpoints["primary"].calls == []
    assert _charged(db_session, account) == 0


def _trial_a_then_paid_b(session, account):
    """Trial A timed out while its old worker lives on; the upgraded author retried as paid B."""
    novel = _trial_novel(session, account, status="failed")
    job_a = _job_of(session, novel)
    paid = IngestionJob(novel_id=novel.id, source_path="x", status="processing", total_chapters=3, processed_chapters=0)
    paid.created_at = datetime(2030, 1, 1)
    IngestionJobsService.set_billing(paid, quota_charged=True, chapter_limit=3)
    session.add(paid)
    session.commit()
    _pro(session, account)
    return novel, job_a, paid.id


def test_paid_retry_of_a_trial_book_uses_paid_rules(db_session, monkeypatch):
    account = _account(db_session)
    novel, _job_a, job_b = _trial_a_then_paid_b(db_session, account)
    endpoints, client = _client(monkeypatch)

    _call(client, novel, job_id=job_b, max_tokens=64_000)

    assert endpoints["primary"].calls[0]["max_tokens"] == 64_000
    assert _charged(db_session, account) == 0


def test_late_trial_worker_is_refused_while_its_paid_retry_runs(db_session, monkeypatch):
    """The novel's latest job is paid, but this call belongs to the finished trial job."""
    account = _account(db_session)
    novel, job_a, job_b = _trial_a_then_paid_b(db_session, account)
    endpoints, client = _client(monkeypatch)

    with pytest.raises(LLMNonRetryableError):
        _call(client, novel, job_id=job_a, max_tokens=64_000)
    assert endpoints["primary"].calls == []

    _call(client, novel, job_id=job_b)
    assert len(endpoints["primary"].calls) == 1


def test_a_call_without_its_job_on_a_trial_book_is_refused(db_session, monkeypatch):
    account = _account(db_session)
    novel = _trial_novel(db_session, account)
    endpoints, client = _client(monkeypatch)

    with pytest.raises(LLMNonRetryableError) as refused:
        client.chat_completion([{"role": "user", "content": "x"}], usage_novel_id=novel.id)

    assert refused.value.error_code == ErrorCode.MATERIAL_TRIAL_LIMIT_INVALID
    assert endpoints["primary"].calls == []


def test_a_call_bound_to_another_novels_job_is_refused(db_session, monkeypatch):
    account = _account(db_session)
    novel = _trial_novel(db_session, account)
    other = _trial_novel(db_session, account)
    endpoints, client = _client(monkeypatch)

    with pytest.raises(LLMNonRetryableError):
        _call(client, novel, job_id=_job_of(db_session, other))

    assert endpoints["primary"].calls == []


def test_a_paid_book_needs_no_bound_job(db_session, monkeypatch):
    account = _account(db_session, used=False)
    _pro(db_session, account)
    novel = Novel(user_id=account.id, title="t", source_meta="{}")
    db_session.add(novel)
    db_session.commit()
    endpoints, client = _client(monkeypatch)

    client.chat_completion([{"role": "user", "content": "x"}], usage_novel_id=novel.id)

    assert len(endpoints["primary"].calls) == 1


def test_a_paid_book_is_unchanged(db_session, monkeypatch):
    account = _account(db_session, used=False)
    _pro(db_session, account)
    novel = Novel(user_id=account.id, title="t", source_meta="{}")
    db_session.add(novel)
    db_session.flush()
    job = IngestionJob(novel_id=novel.id, source_path="x", status="processing", total_chapters=1, processed_chapters=0)
    IngestionJobsService.set_billing(job, quota_charged=True)
    db_session.add(job)
    db_session.commit()
    endpoints, client = _client(monkeypatch)

    _call(client, novel)

    assert endpoints["primary"].calls[0]["max_tokens"] == client.max_tokens
    assert "extra_body" not in endpoints["primary"].calls[0]
    assert endpoints["primary"].retry_settings == []
