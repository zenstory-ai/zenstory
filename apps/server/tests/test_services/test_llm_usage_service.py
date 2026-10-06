"""Usage extraction and the never-raising ledger recorder."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, select

from models import Chapter, LLMUsageEvent, Novel, User
from services.usage import llm_usage_service as svc
from services.usage.llm_usage_service import LLMUsageAttribution, UsageTokens, extract_usage_tokens


def _user(db_session: Session, name: str = "meter") -> User:
    user = User(username=name, email=f"{name}@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _events(db_session: Session) -> list[LLMUsageEvent]:
    db_session.expire_all()
    return list(db_session.exec(select(LLMUsageEvent)).all())


# ---------------------------------------------------------------------------
# extract_usage_tokens
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prefers_deepseek_cache_fields():
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 50,
        "prompt_cache_hit_tokens": 700,
        "prompt_cache_miss_tokens": 300,
        "prompt_tokens_details": {"cached_tokens": 999},
    }
    assert extract_usage_tokens(usage) == UsageTokens(cache_hit=700, cache_miss=300, output=50)


@pytest.mark.unit
def test_hit_only_deepseek_field_derives_miss_from_prompt():
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=5, prompt_cache_hit_tokens=40)
    assert extract_usage_tokens(usage) == UsageTokens(cache_hit=40, cache_miss=60, output=5)


@pytest.mark.unit
def test_falls_back_to_prompt_tokens_details_cached_tokens():
    usage = SimpleNamespace(
        prompt_tokens=1200,
        completion_tokens=80,
        prompt_tokens_details=SimpleNamespace(cached_tokens=1000),
    )
    assert extract_usage_tokens(usage) == UsageTokens(cache_hit=1000, cache_miss=200, output=80)


@pytest.mark.unit
def test_reads_openai_agents_usage_shape():
    # openai-agents Usage: input_tokens is the whole prompt; output includes reasoning.
    usage = SimpleNamespace(
        input_tokens=500,
        output_tokens=300,
        input_tokens_details=SimpleNamespace(cached_tokens=128),
        output_tokens_details=SimpleNamespace(reasoning_tokens=200),
    )
    assert extract_usage_tokens(usage) == UsageTokens(cache_hit=128, cache_miss=372, output=300)


@pytest.mark.unit
def test_real_agents_usage_object():
    from agents.usage import InputTokensDetails, OutputTokensDetails, Usage

    usage = Usage(
        requests=1,
        input_tokens=900,
        input_tokens_details=InputTokensDetails(cached_tokens=600),
        output_tokens=40,
        output_tokens_details=OutputTokensDetails(reasoning_tokens=10),
        total_tokens=940,
    )
    assert extract_usage_tokens(usage) == UsageTokens(cache_hit=600, cache_miss=300, output=40)


@pytest.mark.unit
def test_missing_bool_and_mock_values_count_as_zero():
    assert extract_usage_tokens(None) == UsageTokens()
    assert extract_usage_tokens({"prompt_tokens": True, "completion_tokens": "7"}) == UsageTokens()
    # MagicMock attributes are not numbers and must not turn into fake tokens.
    assert extract_usage_tokens(MagicMock()) == UsageTokens()
    # Cached count larger than the prompt never produces a negative miss.
    assert extract_usage_tokens({"prompt_tokens": 10, "prompt_tokens_details": {"cached_tokens": 50}}) == UsageTokens(
        cache_hit=50, cache_miss=0, output=0
    )


# ---------------------------------------------------------------------------
# record_llm_usage
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_record_writes_one_priced_row(db_session: Session):
    user = _user(db_session)
    written = svc.record_llm_usage(
        LLMUsageAttribution(user_id=user.id, source="suggest", project_id="p-1", correlation_id="run-1"),
        model="deepseek-flash",
        usage={"prompt_tokens": 100, "completion_tokens": 20, "prompt_cache_hit_tokens": 60},
        # Monday 10:00 Beijing.
        occurred_at=datetime(2026, 10, 5, 2, 0),
    )
    assert written is True
    [event] = _events(db_session)
    assert (event.user_id, event.source, event.project_id, event.correlation_id) == (
        user.id,
        "suggest",
        "p-1",
        "run-1",
    )
    assert (event.cache_hit_tokens, event.cache_miss_tokens, event.output_tokens) == (60, 40, 20)
    assert event.price_band == "peak"
    assert event.pricing_version == svc.PRICING_VERSION
    assert event.occurred_at == datetime(2026, 10, 5, 2, 0)
    assert event.is_backfilled is False


@pytest.mark.integration
def test_record_skips_without_user_or_tokens(db_session: Session):
    user = _user(db_session)
    usage = {"prompt_tokens": 10, "completion_tokens": 1}
    assert svc.record_llm_usage(None, model="m", usage=usage) is False
    assert svc.record_llm_usage(LLMUsageAttribution(user_id=None, source="agent"), model="m", usage=usage) is False
    assert svc.record_llm_usage(LLMUsageAttribution(user_id=user.id, source="agent"), model="m", usage={}) is False
    assert _events(db_session) == []


@pytest.mark.integration
def test_record_swallows_database_errors(monkeypatch):
    def broken_factory():
        raise RuntimeError("db down")

    monkeypatch.setattr(svc, "_session_factory", broken_factory)
    assert (
        svc.record_llm_usage(
            LLMUsageAttribution(user_id="u", source="agent"),
            model="m",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )
        is False
    )


@pytest.mark.integration
def test_record_closes_session_when_commit_fails(monkeypatch):
    session = MagicMock()
    session.commit.side_effect = RuntimeError("constraint")
    monkeypatch.setattr(svc, "_session_factory", lambda: session)
    ok = svc.record_llm_usage(
        LLMUsageAttribution(user_id="u", source="agent"),
        model="m",
        tokens=UsageTokens(output=3),
    )
    assert ok is False
    session.close.assert_called_once()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_record_async_writes_off_loop(db_session: Session):
    user = _user(db_session, "meter_async")
    ok = await svc.record_llm_usage_async(
        LLMUsageAttribution(user_id=user.id, source="router"),
        model="deepseek-flash",
        usage={"prompt_tokens": 7, "completion_tokens": 3},
    )
    assert ok is True
    [event] = _events(db_session)
    assert (event.source, event.cache_miss_tokens, event.output_tokens) == ("router", 7, 3)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_record_async_skips_and_swallows(monkeypatch):
    assert await svc.record_llm_usage_async(None, model="m", usage={"completion_tokens": 1}) is False
    assert await svc.record_llm_usage_async(
        LLMUsageAttribution(user_id="u", source="agent"), model="m", usage={}
    ) is False

    def boom(*_args, **_kwargs):
        raise RuntimeError("extract failed")

    monkeypatch.setattr(svc, "extract_usage_tokens", boom)
    assert await svc.record_llm_usage_async(
        LLMUsageAttribution(user_id="u", source="agent"), model="m", usage={"completion_tokens": 1}
    ) is False


# ---------------------------------------------------------------------------
# Material flows
# ---------------------------------------------------------------------------


@pytest.fixture
def clear_owner_cache():
    svc._novel_owner_cache.clear()
    svc._chapter_novel_cache.clear()
    yield
    svc._novel_owner_cache.clear()
    svc._chapter_novel_cache.clear()


@pytest.mark.integration
def test_material_usage_is_billed_to_novel_owner(db_session: Session, clear_owner_cache):
    owner = _user(db_session, "novel_owner")
    novel = Novel(user_id=owner.id, title="Book")
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)
    chapter = Chapter(novel_id=novel.id, chapter_number=1, title="c1")
    db_session.add(chapter)
    db_session.commit()
    db_session.refresh(chapter)

    usage = {"prompt_tokens": 50, "completion_tokens": 900}
    assert svc.record_material_llm_usage(model="deepseek-flash", usage=usage, novel_id=novel.id)
    assert svc.record_material_llm_usage(model="deepseek-flash", usage=usage, chapter_id=chapter.id)
    # Second chapter call hits the cache (no DB read needed).
    assert svc._chapter_novel_cache[chapter.id] == novel.id
    assert svc._novel_owner_cache[novel.id] == owner.id

    events = _events(db_session)
    assert len(events) == 2
    assert {event.user_id for event in events} == {owner.id}
    assert {event.source for event in events} == {"material"}
    assert {event.correlation_id for event in events} == {f"novel:{novel.id}"}


@pytest.mark.integration
def test_material_usage_skips_unknown_or_missing_targets(db_session: Session, clear_owner_cache):
    usage = {"prompt_tokens": 5, "completion_tokens": 5}
    assert svc.record_material_llm_usage(model="m", usage=usage) is False
    assert svc.record_material_llm_usage(model="m", usage=usage, novel_id=987654) is False
    assert svc.record_material_llm_usage(model="m", usage=usage, chapter_id=987654) is False
    assert svc.record_material_llm_usage(model="m", usage={}, novel_id=1) is False
    assert _events(db_session) == []


@pytest.mark.integration
def test_material_usage_swallows_errors(clear_owner_cache):
    def broken_factory():
        raise RuntimeError("db down")

    assert (
        svc.record_material_llm_usage(
            model="m",
            usage={"completion_tokens": 1},
            novel_id=1,
            session_factory=broken_factory,
        )
        is False
    )
