"""所有消耗免费 AI 消息的入口共用同一条网络规则；模型全部替身，不调用真实模型。"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import select

import agent.core.account_run_limit as limit
from core.error_codes import ERROR_MESSAGES, ErrorCode
from models import Project, User
from models.free_ai_network_quota import FreeAINetworkReservation
from models.subscription import SubscriptionPlan, UsageQuota, UserSubscription
from services.core.auth_service import hash_password
from services.features.natural_polish_service import NaturalPolishFailure, NaturalPolishResult

SERVER_ROOT = Path(__file__).resolve().parents[2]
IP = "93.184.216.34"
CODE = "ERR_QUOTA_FREE_NETWORK_DAILY_EXCEEDED"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class _Service:
    def __init__(self, *, ends_without_done: bool = False):
        self.started = 0
        self._ends_without_done = ends_without_done

    async def process_stream(self, **_kwargs):
        self.started += 1
        yield _sse("session_started", {"session_id": "synthetic-session"})
        if self._ends_without_done:
            return
        yield _sse("done", {"session_id": "synthetic-session"})


async def _member(client: AsyncClient, db_session) -> tuple[str, User, Project]:
    suffix = uuid4().hex[:8]
    user = User(
        username=f"network_api_{suffix}",
        email=f"network_api_{suffix}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    project = Project(name="Network", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    login = await client.post("/api/auth/login", data={"username": user.username, "password": "password123"})
    assert login.status_code == 200
    return login.json()["access_token"], user, project


def _stream(client: AsyncClient, token: str, project: Project, *, forwarded: str = IP):
    return client.post(
        "/api/v1/agent/stream",
        json={"project_id": str(project.id), "message": "写第五章"},
        headers={"Authorization": f"Bearer {token}", "X-Forwarded-For": forwarded},
    )


def _polish(client: AsyncClient, token: str, project: Project, *, forwarded: str = IP):
    return client.post(
        "/api/v1/editor/natural-polish",
        json={"project_id": str(project.id), "selected_text": "原始文本"},
        headers={"Authorization": f"Bearer {token}", "X-Forwarded-For": forwarded},
    )


def _used(db_session, user: User) -> int:
    db_session.expire_all()
    quota = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == user.id)).first()
    return quota.ai_conversations_used if quota else 0


def _active(db_session) -> list[FreeAINetworkReservation]:
    db_session.expire_all()
    return list(db_session.exec(
        select(FreeAINetworkReservation).where(FreeAINetworkReservation.released_at.is_(None))
    ).all())


async def _fill_seats(client, db_session):
    """三个账号用两个入口各发一条，占满名额。"""
    members = [await _member(client, db_session) for _ in range(3)]
    with (
        patch("api.agent.get_agent_service", return_value=_Service()),
        patch(
            "api.editor.natural_polish_service.natural_polish",
            new=AsyncMock(return_value=NaturalPolishResult(polished_text="改写后", model="test-model")),
        ),
    ):
        first = await _stream(client, members[0][0], members[0][2])
        second = await _polish(client, members[1][0], members[1][2])
        third = await _stream(client, members[2][0], members[2][2])
    assert [first.status_code, second.status_code, third.status_code] == [200, 200, 200]
    assert len(_active(db_session)) == 3
    return members


@pytest.mark.integration
async def test_both_entries_share_seats_and_refuse_the_fourth_account(client: AsyncClient, db_session):
    await _fill_seats(client, db_session)
    token, user, project = await _member(client, db_session)
    service = _Service()
    polish = AsyncMock()
    with (
        patch("api.agent.get_agent_service", return_value=service),
        patch("api.editor.natural_polish_service.natural_polish", new=polish),
    ):
        stream_refused = await _stream(client, token, project)
        polish_refused = await _polish(client, token, project)

    for refused in (stream_refused, polish_refused):
        assert refused.status_code == 402
        assert refused.json()["error_code"] == CODE
    assert service.started == 0
    polish.assert_not_awaited()
    assert _used(db_session, user) == 0
    # 被拒绝的请求放回了账号任务名额。
    slots = [await limit.acquire_account_run_slot(str(user.id)) for _ in range(2)]
    for slot in slots:
        await slot.release()


@pytest.mark.integration
async def test_refusal_copy_is_neutral_and_reveals_no_internals(client: AsyncClient, db_session):
    await _fill_seats(client, db_session)
    token, _user, project = await _member(client, db_session)
    response = await _stream(client, token, project)
    body = response.text
    assert IP not in body
    for internal in ("¥", "预算", "budget", "abuse", "作弊"):
        assert internal not in body
    assert ERROR_MESSAGES["zh"][ErrorCode.QUOTA_FREE_NETWORK_DAILY_EXCEEDED] == (
        "当前网络今日免费体验额度已用完，请明天再试或升级。"
    )
    assert "upgrade" in ERROR_MESSAGES["en"][ErrorCode.QUOTA_FREE_NETWORK_DAILY_EXCEEDED].lower()


@pytest.mark.integration
async def test_seated_account_continues_across_entries_after_seats_are_gone(client: AsyncClient, db_session):
    members = await _fill_seats(client, db_session)
    token, user, project = members[0]
    with patch(
        "api.editor.natural_polish_service.natural_polish",
        new=AsyncMock(return_value=NaturalPolishResult(polished_text="改写后", model="test-model")),
    ):
        response = await _polish(client, token, project)
    assert response.status_code == 200
    assert _used(db_session, user) == 2


@pytest.mark.integration
async def test_forged_forwarded_prefix_does_not_change_the_network(client: AsyncClient, db_session):
    await _fill_seats(client, db_session)
    token, user, project = await _member(client, db_session)
    service = _Service()
    with patch("api.agent.get_agent_service", return_value=service):
        response = await _stream(client, token, project, forwarded=f"8.8.8.8, 1.1.1.1, {IP}")
    assert response.status_code == 402
    assert response.json()["error_code"] == CODE
    assert service.started == 0
    assert _used(db_session, user) == 0


@pytest.mark.integration
async def test_trusted_address_change_is_a_different_network(client: AsyncClient, db_session):
    await _fill_seats(client, db_session)
    token, user, project = await _member(client, db_session)
    with patch("api.agent.get_agent_service", return_value=_Service()):
        response = await _stream(client, token, project, forwarded="93.184.216.99")
    assert response.status_code == 200
    assert _used(db_session, user) == 1


@pytest.mark.integration
async def test_failed_stream_refunds_message_and_seat_exactly_once(client: AsyncClient, db_session):
    token, user, project = await _member(client, db_session)
    with patch("api.agent.get_agent_service", return_value=_Service(ends_without_done=True)):
        response = await _stream(client, token, project)
    assert response.status_code == 200
    assert "quota_refunded" in response.text
    assert _used(db_session, user) == 0
    assert _active(db_session) == []
    rows = db_session.exec(select(FreeAINetworkReservation)).all()
    assert len(rows) == 1 and rows[0].released_at is not None


@pytest.mark.integration
async def test_busy_session_refunds_network_share(client: AsyncClient, db_session):
    from agent.core.steering import SteeringSessionBusyError

    class _Busy:
        async def process_stream(self, **_kwargs):
            raise SteeringSessionBusyError("synthetic")
            yield  # pragma: no cover

    token, user, project = await _member(client, db_session)
    with patch("api.agent.get_agent_service", return_value=_Busy()):
        response = await _stream(client, token, project)
    assert response.status_code == 409
    assert _used(db_session, user) == 0
    assert _active(db_session) == []


@pytest.mark.integration
@pytest.mark.parametrize(
    ("outcome", "expected_status", "used_after"),
    [
        (NaturalPolishFailure(stage="model_call", category="provider_error"), 502, 0),
        (RuntimeError("synthetic"), 500, 0),
        (NaturalPolishResult(polished_text="原始文本", model="test-model"), 200, 0),
    ],
)
async def test_polish_failure_or_no_change_refunds_the_network_share(
    client: AsyncClient, db_session, outcome, expected_status, used_after
):
    token, user, project = await _member(client, db_session)
    side_effect = outcome if isinstance(outcome, Exception) else None
    return_value = None if isinstance(outcome, Exception) else outcome
    with patch(
        "api.editor.natural_polish_service.natural_polish",
        new=AsyncMock(side_effect=side_effect, return_value=return_value),
    ):
        response = await _polish(client, token, project)
    assert response.status_code == expected_status
    assert _used(db_session, user) == used_after
    assert _active(db_session) == []


@pytest.mark.integration
async def test_pro_passes_on_a_full_network(client: AsyncClient, db_session):
    from datetime import timedelta

    from config.datetime_utils import utcnow

    await _fill_seats(client, db_session)
    token, user, project = await _member(client, db_session)
    plan = db_session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == "pro")).first()
    if plan is None:
        plan = SubscriptionPlan(
            name="pro", display_name="Pro", display_name_en="Pro",
            price_monthly_cents=4900, price_yearly_cents=39900,
            features={"ai_conversations_per_day": -1},
        )
        db_session.add(plan)
        db_session.commit()
    db_session.add(UserSubscription(
        user_id=user.id, plan_id=plan.id, status="active",
        current_period_start=utcnow() - timedelta(days=1),
        current_period_end=utcnow() + timedelta(days=30),
    ))
    db_session.commit()
    before = len(_active(db_session))
    with patch("api.agent.get_agent_service", return_value=_Service()):
        response = await _stream(client, token, project)
    assert response.status_code == 200
    assert len(_active(db_session)) == before


@pytest.mark.integration
async def test_reading_is_not_limited_on_a_full_network(client: AsyncClient, db_session):
    await _fill_seats(client, db_session)
    token, _user, _project = await _member(client, db_session)
    response = await client.get(
        "/api/v1/projects",
        headers={"Authorization": f"Bearer {token}", "X-Forwarded-For": IP},
    )
    assert response.status_code == 200


@pytest.mark.integration
async def test_railway_chain_counts_the_client_behind_any_edge_address(client: AsyncClient, db_session, monkeypatch):
    """Railway 的链路是「客户端, 边缘」：不同边缘地址上的同一客户端共用额度，不同客户端互不影响。"""
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    with patch(
        "api.editor.natural_polish_service.natural_polish",
        new=AsyncMock(return_value=NaturalPolishResult(polished_text="改写后", model="test-model")),
    ):
        for index in range(3):
            token, _user, project = await _member(client, db_session)
            response = await _polish(client, token, project, forwarded=f"{IP}, 84.17.44.{225 + index}")
            assert response.status_code == 200
        token, user, project = await _member(client, db_session)
        same_client = await _polish(client, token, project, forwarded=f"{IP}, 84.17.44.229")
        other_client = await _polish(client, token, project, forwarded="93.184.216.99, 84.17.44.225")
    assert same_client.status_code == 402
    assert same_client.json()["error_code"] == CODE
    assert other_client.status_code == 200
    assert _used(db_session, user) == 1


def test_every_charging_route_is_excluded_from_the_public_api_fallback():
    """生产 /api 回退经 Vercel 轮换出口到达，新的计费入口必须同时加入排除清单。"""
    from main import app

    contract = json.loads((SERVER_ROOT.parents[1] / "apps/web/content/site-routing.json").read_text(encoding="utf-8"))
    excluded = {f"/api/{path}" for path in contract["apiFallbackExcludedPaths"]}
    assert excluded == {"/api/v1/agent/stream", "/api/v1/editor/natural-polish"}
    assert excluded <= {route.path for route in app.routes if "POST" in getattr(route, "methods", ())}


def test_every_production_charge_passes_the_network_identity():
    """新入口如果直接预扣却不带网络信息，这里会失败。"""
    calls = []
    for root in ("api", "agent", "services", "flows", "core"):
        for path in (SERVER_ROOT / root).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                name = node.func.attr
                if name == "consume_ai_conversation" and path.name != "quota_service.py":
                    calls.append((path, "consume", set()))
                if name == "reserve_ai_conversation" and path.name != "quota_service.py":
                    calls.append((path, "reserve", {kw.arg for kw in node.keywords}))
                if name == "release_ai_conversation":
                    calls.append((path, "release", {kw.arg for kw in node.keywords}))
    assert {path.relative_to(SERVER_ROOT).as_posix() for path, _, _ in calls} == {"api/agent.py", "api/editor.py"}
    for path, name, keywords in calls:
        assert name != "consume", path
        required = {"client_ip", "reservation_id"} if name == "reserve" else {"reservation_id"}
        assert required <= keywords, (path, name)
