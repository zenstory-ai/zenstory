"""Admin Usage & cost endpoints: Beijing-day windows, CNY cost, sorting, auth."""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from models import LLMUsageEvent, User
from services.core.auth_service import hash_password
from services.usage import admin_usage_service
from services.usage.llm_usage_service import UsageTokens, build_usage_event

# Wednesday 2026-10-07 10:00 Beijing.
NOW = datetime(2026, 10, 7, 2, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(admin_usage_service, "utcnow", lambda: NOW)


def _user(db_session: Session, name: str, *, admin: bool = False) -> User:
    user = User(
        username=name,
        email=f"{name}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_superuser=admin,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


async def _headers(client: AsyncClient, username: str) -> dict[str, str]:
    response = await client.post("/api/auth/login", data={"username": username, "password": "password123"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _event(
    db_session: Session,
    user: User,
    at: datetime,
    *,
    hit: int = 0,
    miss: int = 0,
    out: int = 0,
    source: str = "agent",
    model: str = "deepseek-flash",
    price_band: str | None = None,
    pricing_version: str | None = None,
) -> None:
    event = build_usage_event(
        user_id=user.id,
        source=source,
        model=model,
        tokens=UsageTokens(cache_hit=hit, cache_miss=miss, output=out),
        occurred_at=at,
    )
    if price_band is not None:
        event.price_band = price_band
    if pricing_version is not None:
        event.pricing_version = pricing_version
    db_session.add(event)
    db_session.commit()


@pytest.fixture
def seeded(db_session: Session):
    admin = _user(db_session, "usage_admin", admin=True)
    alice = _user(db_session, "alice")
    bob = _user(db_session, "bob")
    carol = _user(db_session, "carol")
    # Today 09:00 Beijing during the National Day holiday: 1M cache hits = 0.02 CNY.
    _event(db_session, alice, datetime(2026, 10, 7, 1, 0), hit=1_000_000)
    # Exactly 00:00 Beijing today (off-peak): 1000 output = 0.004 CNY.
    _event(db_session, alice, datetime(2026, 10, 6, 16, 0), out=1000, source="router")
    # 23:59:59 Beijing yesterday, Tuesday off-peak: 1M output = 4 CNY.
    _event(db_session, bob, datetime(2026, 10, 6, 15, 59, 59), out=1_000_000, source="material")
    # 00:00 Beijing on Oct 1, the first day of the 7-day window: 1M miss = 1 CNY.
    _event(db_session, carol, datetime(2026, 9, 30, 16, 0), miss=1_000_000, source="suggest")
    # One second earlier is Sep 30 Beijing, outside every window.
    _event(db_session, carol, datetime(2026, 9, 30, 15, 59, 59), miss=5_000_000)
    return {"admin": admin, "alice": alice, "bob": bob, "carol": carol}


@pytest.mark.integration
async def test_usage_endpoints_require_superuser(client: AsyncClient, db_session: Session, seeded):
    for path in (
        "/api/admin/usage/summary",
        "/api/admin/usage/users",
        f"/api/admin/usage/users/{seeded['alice'].id}/daily",
    ):
        assert (await client.get(path)).status_code == 401
    headers = await _headers(client, "alice")
    for path in (
        "/api/admin/usage/summary",
        "/api/admin/usage/users",
        f"/api/admin/usage/users/{seeded['alice'].id}/daily",
    ):
        assert (await client.get(path, headers=headers)).status_code == 403


@pytest.mark.integration
async def test_summary_today_counts_from_beijing_midnight(client: AsyncClient, db_session: Session, seeded):
    headers = await _headers(client, "usage_admin")
    response = await client.get("/api/admin/usage/summary", params={"window": "today"}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["window"] == "today"
    assert body["timezone"] == "Asia/Shanghai"
    assert (body["period_start"], body["period_end"]) == ("2026-10-07", "2026-10-07")
    assert body["prices"]["peak"] == {"cache_hit": "0.04", "cache_miss": "2", "output": "8"}
    assert body["pricing_version"]
    assert body["totals"] == {
        "users": 1,
        "calls": 2,
        "cache_hit_tokens": 1_000_000,
        "cache_miss_tokens": 0,
        "output_tokens": 1000,
        "cost_cny": "0.0240",
        "peak_cost_cny": "0.0000",
        "offpeak_cost_cny": "0.0240",
    }
    assert [(row["source"], row["cost_cny"]) for row in body["by_source"]] == [
        ("agent", "0.0200"),
        ("router", "0.0040"),
    ]
    assert [(row["date"], row["users"], row["calls"]) for row in body["daily"]] == [("2026-10-07", 1, 2)]


@pytest.mark.integration
async def test_summary_yesterday_and_seven_days(client: AsyncClient, db_session: Session, seeded):
    headers = await _headers(client, "usage_admin")
    yesterday = (
        await client.get("/api/admin/usage/summary", params={"window": "yesterday"}, headers=headers)
    ).json()
    assert yesterday["period_start"] == "2026-10-06"
    assert yesterday["totals"]["cost_cny"] == "4.0000"
    assert yesterday["totals"]["offpeak_cost_cny"] == "4.0000"
    assert yesterday["totals"]["users"] == 1

    week = (await client.get("/api/admin/usage/summary", params={"window": "7d"}, headers=headers)).json()
    assert (week["period_start"], week["period_end"]) == ("2026-10-01", "2026-10-07")
    assert [row["date"] for row in week["daily"]] == [f"2026-10-0{day}" for day in range(1, 8)]
    by_date = {row["date"]: row for row in week["daily"]}
    assert by_date["2026-10-01"]["cost_cny"] == "1.0000"
    assert by_date["2026-10-03"] == {
        "date": "2026-10-03",
        "users": 0,
        "calls": 0,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
        "output_tokens": 0,
        "cost_cny": "0.0000",
        "peak_cost_cny": "0.0000",
        "offpeak_cost_cny": "0.0000",
    }
    assert week["totals"]["users"] == 3
    assert week["totals"]["calls"] == 4
    assert week["totals"]["cost_cny"] == "5.0240"
    assert [row["source"] for row in week["by_source"]] == ["material", "suggest", "agent", "router"]


@pytest.mark.integration
async def test_summary_rejects_unknown_window(client: AsyncClient, db_session: Session, seeded):
    headers = await _headers(client, "usage_admin")
    response = await client.get("/api/admin/usage/summary", params={"window": "30d"}, headers=headers)
    assert response.status_code == 422


@pytest.mark.integration
async def test_users_sorted_by_cost_with_search_and_pages(client: AsyncClient, db_session: Session, seeded):
    headers = await _headers(client, "usage_admin")
    body = (await client.get("/api/admin/usage/users", params={"window": "7d"}, headers=headers)).json()
    assert body["total"] == 3
    assert [item["username"] for item in body["items"]] == ["bob", "carol", "alice"]
    alice = body["items"][2]
    assert alice["calls"] == 2
    assert (alice["peak_cost_cny"], alice["offpeak_cost_cny"], alice["cost_cny"]) == ("0.0000", "0.0240", "0.0240")
    assert alice["email"] == "alice@example.com"
    assert alice["last_used_at"] == "2026-10-07T01:00:00Z"

    by_calls = (
        await client.get("/api/admin/usage/users", params={"window": "7d", "sort": "calls"}, headers=headers)
    ).json()
    assert [item["username"] for item in by_calls["items"]] == ["alice", "bob", "carol"]

    by_tokens = (
        await client.get("/api/admin/usage/users", params={"window": "7d", "sort": "tokens"}, headers=headers)
    ).json()
    # alice 1,001,000 tokens; bob and carol 1,000,000 each, bob first by cost.
    assert [item["username"] for item in by_tokens["items"]] == ["alice", "bob", "carol"]

    searched = (
        await client.get("/api/admin/usage/users", params={"window": "7d", "search": "CAROL@"}, headers=headers)
    ).json()
    assert [item["username"] for item in searched["items"]] == ["carol"]
    by_id = (
        await client.get(
            "/api/admin/usage/users", params={"window": "7d", "search": seeded["bob"].id}, headers=headers
        )
    ).json()
    assert [item["username"] for item in by_id["items"]] == ["bob"]

    first = (
        await client.get("/api/admin/usage/users", params={"window": "7d", "page_size": 2}, headers=headers)
    ).json()
    second = (
        await client.get(
            "/api/admin/usage/users", params={"window": "7d", "page_size": 2, "page": 2}, headers=headers
        )
    ).json()
    assert (first["total"], second["total"], second["page"]) == (3, 3, 2)
    assert [item["username"] for item in first["items"] + second["items"]] == ["bob", "carol", "alice"]


@pytest.mark.integration
async def test_users_tie_break_is_stable(client: AsyncClient, db_session: Session, seeded):
    twins = [_user(db_session, f"twin{index}") for index in range(3)]
    for twin in twins:
        _event(db_session, twin, datetime(2026, 10, 7, 1, 30), miss=10)
    headers = await _headers(client, "usage_admin")
    ids = []
    for page in (1, 2, 3):
        body = (
            await client.get(
                "/api/admin/usage/users",
                params={"window": "today", "search": "twin", "page_size": 1, "page": page},
                headers=headers,
            )
        ).json()
        ids.extend(item["user_id"] for item in body["items"])
    assert ids == sorted(twin.id for twin in twins)


@pytest.mark.integration
async def test_users_window_today_excludes_yesterday(client: AsyncClient, db_session: Session, seeded):
    headers = await _headers(client, "usage_admin")
    body = (await client.get("/api/admin/usage/users", params={"window": "today"}, headers=headers)).json()
    assert [item["username"] for item in body["items"]] == ["alice"]


@pytest.mark.integration
async def test_user_daily_zero_fills_and_splits_sources(client: AsyncClient, db_session: Session, seeded):
    headers = await _headers(client, "usage_admin")
    alice = seeded["alice"]
    body = (
        await client.get(f"/api/admin/usage/users/{alice.id}/daily", params={"days": 14}, headers=headers)
    ).json()
    assert (body["user_id"], body["username"], body["days"]) == (alice.id, "alice", 14)
    assert (body["period_start"], body["period_end"]) == ("2026-09-24", "2026-10-07")
    assert len(body["daily"]) == 14
    assert body["daily"][-1]["calls"] == 2
    assert sum(row["calls"] for row in body["daily"]) == 2
    assert "users" not in body["totals"]
    assert body["totals"]["cost_cny"] == "0.0240"
    assert {row["source"] for row in body["by_source"]} == {"agent", "router"}

    carol = seeded["carol"]
    thirty = (
        await client.get(f"/api/admin/usage/users/{carol.id}/daily", params={"days": 30}, headers=headers)
    ).json()
    assert len(thirty["daily"]) == 30
    # 30 days reach back past Sep 30, so both of carol's rows count: 1M + 5M miss off-peak.
    assert thirty["totals"]["cost_cny"] == "6.0000"
    seven = (await client.get(f"/api/admin/usage/users/{carol.id}/daily", headers=headers)).json()
    assert seven["days"] == 7
    assert seven["totals"]["cost_cny"] == "1.0000"


@pytest.mark.integration
async def test_user_daily_validates_days_and_user(client: AsyncClient, db_session: Session, seeded):
    headers = await _headers(client, "usage_admin")
    alice = seeded["alice"]
    assert (
        await client.get(f"/api/admin/usage/users/{alice.id}/daily", params={"days": 5}, headers=headers)
    ).status_code == 422
    assert (await client.get("/api/admin/usage/users/missing/daily", headers=headers)).status_code == 404


@pytest.mark.unit
def test_window_period_rejects_unknown_window():
    with pytest.raises(ValueError):
        admin_usage_service.window_period("month", NOW)  # type: ignore[arg-type]



@pytest.mark.unit
def test_cost_units_widen_token_columns_to_bigint_on_postgresql():
    """int4 * weight overflows on PostgreSQL ("integer out of range") without the cast."""
    from sqlalchemy import select
    from sqlalchemy.dialects import postgresql

    query = select(
        admin_usage_service._peak_units_expr(admin_usage_service.EVENTS),
        admin_usage_service._offpeak_units_expr(admin_usage_service.EVENTS),
    )
    sql = str(query.compile(dialect=postgresql.dialect())).upper()
    for column in ("CACHE_HIT_TOKENS", "CACHE_MISS_TOKENS", "OUTPUT_TOKENS"):
        assert f"CAST(LLM_USAGE_EVENT.{column} AS BIGINT)" in sql
    # Both bands, three columns each: no token column is multiplied uncast.
    assert sql.count("AS BIGINT)") == 6


@pytest.mark.integration
async def test_cost_of_multi_million_token_row(client: AsyncClient, db_session: Session, seeded):
    headers = await _headers(client, "usage_admin")
    # 2026-10-06 is a holiday: 6M output at weight 400 is 2.4e9 units, past int4.
    _event(db_session, seeded["bob"], datetime(2026, 10, 6, 2, 0), out=6_000_000)
    body = (
        await client.get("/api/admin/usage/users", params={"window": "yesterday"}, headers=headers)
    ).json()
    bob = next(item for item in body["items"] if item["username"] == "bob")
    # Both calls are off-peak: 24 + 4 CNY.
    assert bob["cost_cny"] == "28.0000"


@pytest.mark.integration
def test_legacy_holiday_peak_is_corrected_without_mutating_ledger(db_session: Session):
    legacy = _user(db_session, "legacy_holiday")
    unknown = _user(db_session, "unknown_version")
    weekday = _user(db_session, "legacy_weekday")

    # Old pricing tagged the National Day weekday as peak. Query-time cost must
    # be corrected consistently without rewriting the append-only row.
    _event(
        db_session,
        legacy,
        datetime(2026, 10, 7, 7, 0),  # 15:00 Beijing
        out=1_000_000,
        source="agent",
        price_band="peak",
        pricing_version="deepseek-flash-2026-10",
    )
    # Unknown versions retain their stored band even on the holiday.
    _event(
        db_session,
        unknown,
        datetime(2026, 10, 7, 7, 0),
        hit=1_000_000,
        source="router",
        price_band="peak",
        pricing_version="unrecognized-version",
    )
    # The same known legacy version remains peak on a normal weekday.
    _event(
        db_session,
        weekday,
        datetime(2026, 9, 30, 7, 0),  # Wednesday 15:00 Beijing
        out=1_000_000,
        price_band="peak",
        pricing_version="deepseek-flash-2026-10",
    )

    summary = admin_usage_service.get_usage_summary(db_session, "today", now=NOW)
    assert summary["totals"]["cost_cny"] == "4.0400"
    assert (summary["totals"]["peak_cost_cny"], summary["totals"]["offpeak_cost_cny"]) == (
        "0.0400",
        "4.0000",
    )
    assert summary["daily"][0]["cost_cny"] == "4.0400"
    assert {row["source"]: row["cost_cny"] for row in summary["by_source"]} == {
        "agent": "4.0000",
        "router": "0.0400",
    }

    users = admin_usage_service.list_user_usage(db_session, "today", now=NOW)
    by_user = {row["username"]: row for row in users["items"]}
    assert by_user["legacy_holiday"]["cost_cny"] == "4.0000"
    assert by_user["legacy_holiday"]["offpeak_cost_cny"] == "4.0000"
    assert by_user["unknown_version"]["peak_cost_cny"] == "0.0400"

    detail = admin_usage_service.get_user_daily_usage(db_session, legacy, 7, now=NOW)
    assert detail["totals"]["cost_cny"] == "4.0000"
    assert detail["daily"][-1]["offpeak_cost_cny"] == "4.0000"

    normal_day = datetime(2026, 9, 30, 8, 0, tzinfo=UTC)
    normal_summary = admin_usage_service.get_usage_summary(db_session, "today", now=normal_day)
    assert normal_summary["totals"]["peak_cost_cny"] == "8.0000"

    # Read-only correction: stored evidence remains intact.
    stored = db_session.exec(select(LLMUsageEvent).where(LLMUsageEvent.user_id == legacy.id)).one()
    assert stored.price_band == "peak"
    assert stored.pricing_version == "deepseek-flash-2026-10"
