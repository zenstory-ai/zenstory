"""Round3 回归测试：Agent API 的配额与限流（缺陷 #27）。

背景：/api/v1/agent/suggest 会真正调用远端 LLM（上下文组装 + acomplete），
但历史实现里全部防护只有一行 verify_project_access —— 既不计 AI 对话额度、
也不限流，任何已登录用户（包括额度已耗尽的免费账号）都能无限刷厂商账单。

本文件同时把「该 router 下所有会触发 LLM 的端点都必须有按用户限流」
这条不变量钉成守卫用例，避免以后新增端点再次漏掉。
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from api.agent import (
    STEER_RATE_LIMIT_MAX_REQUESTS,
    STREAM_RATE_LIMIT_MAX_REQUESTS,
    SUGGEST_DAILY_MAX_REQUESTS,
    SUGGEST_RATE_LIMIT_MAX_REQUESTS,
    SUGGEST_RATE_LIMIT_WINDOW_SECONDS,
)
from api.agent import (
    router as agent_router,
)
from models import Project, UsageQuota, User
from services.core.auth_service import hash_password
from services.quota_service import quota_service

FREE_AI_CONVERSATION_LIMIT = 20  # 无订阅计划时 quota_service 的兜底日限额


async def _make_user_and_project(
    client: AsyncClient,
    db_session: Session,
    name: str,
) -> tuple[User, Project, str]:
    """建用户 + 登录 + 建项目，返回 (user, project, access_token)。"""
    user = User(
        username=name,
        email=f"{name}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    login_response = await client.post(
        "/api/auth/login",
        data={"username": name, "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name=f"{name}-project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    return user, project, token


def _set_quota_used(db_session: Session, user_id: str, used: int) -> UsageQuota:
    """写入一条已用 used 次的 AI 对话额度记录。"""
    now = datetime.utcnow()
    quota = UsageQuota(
        user_id=user_id,
        period_start=now,
        period_end=now + timedelta(hours=24),
        ai_conversations_used=used,
        last_reset_at=now,
    )
    db_session.add(quota)
    db_session.commit()
    return quota


def _read_quota_used(db_session: Session, user_id: str) -> int:
    db_session.expire_all()
    quota = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == user_id)).first()
    return quota.ai_conversations_used if quota else 0


def _llm_backed_suggest_service(suggestions: list[str]) -> MagicMock:
    """构造一个「持有 LLM 客户端」的 SuggestService 替身（即真实付费路径）。"""
    service = MagicMock()
    service.llm = MagicMock()  # 非 None 表示真的会打远端 LLM
    service.generate_suggestions = AsyncMock(return_value=suggestions)
    return service


@pytest.mark.integration
async def test_suggest_still_available_when_ai_quota_exhausted(client: AsyncClient, db_session: Session):
    """建议不占 AI 对话额度：额度耗尽时 /suggest 照常可用，额度也不被改动。"""
    user, project, token = await _make_user_and_project(client, db_session, "round3_suggest_quota_out")
    _set_quota_used(db_session, user.id, FREE_AI_CONVERSATION_LIMIT)

    service = _llm_backed_suggest_service(["继续写第二章"])
    with patch("agent.suggest_service.get_suggest_service", return_value=service):
        response = await client.post(
            "/api/v1/agent/suggest",
            json={"project_id": str(project.id), "count": 3},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    service.generate_suggestions.assert_awaited_once()
    assert _read_quota_used(db_session, user.id) == FREE_AI_CONVERSATION_LIMIT


@pytest.mark.integration
async def test_suggest_does_not_consume_ai_conversation_quota(client: AsyncClient, db_session: Session):
    """真实 LLM 路径下 /suggest 也不扣对话额度（前端每轮都会自动触发它）。"""
    user, project, token = await _make_user_and_project(client, db_session, "round3_suggest_quota_charge")
    _set_quota_used(db_session, user.id, 3)

    service = _llm_backed_suggest_service(["继续写第二章", "补充角色动机", "设计剧情反转"])
    with (
        patch("agent.suggest_service.get_suggest_service", return_value=service),
        patch.object(quota_service, "reserve_ai_conversation") as spy_consume,
    ):
        response = await client.post(
            "/api/v1/agent/suggest",
            json={"project_id": str(project.id), "count": 3},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["suggestions"] == ["继续写第二章", "补充角色动机", "设计剧情反转"]
    spy_consume.assert_not_called()
    assert _read_quota_used(db_session, user.id) == 3


@pytest.mark.integration
async def test_suggest_daily_cap_returns_429(client: AsyncClient, db_session: Session, monkeypatch):
    """独立的每日上限：同一账号当天超过上限后 429，LLM 不再被调用。"""
    import api.agent as agent_api
    import middleware.rate_limit as rate_limit_module
    from middleware.rate_limit import _rate_limit_store

    _user, project, token = await _make_user_and_project(client, db_session, "round3_suggest_daily_cap")
    service = _llm_backed_suggest_service(["继续写第二章"])
    today = rate_limit_module.beijing_date(rate_limit_module.utcnow()).isoformat()
    user_key = f"agent_suggest_daily:user_{_user.id}:beijing_day:{today}"
    with patch("agent.suggest_service.get_suggest_service", return_value=service):
        # 预先把当日窗口填满（小时窗口不受影响）。
        import time as _time

        _rate_limit_store[user_key] = [_time.time()] * agent_api.SUGGEST_DAILY_MAX_REQUESTS
        blocked = await client.post(
            "/api/v1/agent/suggest",
            json={"project_id": str(project.id), "count": 3},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert blocked.status_code == 429
    service.generate_suggestions.assert_not_called()


@pytest.mark.integration
async def test_suggest_does_not_charge_when_llm_unavailable(client: AsyncClient, db_session: Session):
    """没有 LLM 客户端时只返回固定兜底文案，不产生成本，也不该扣额度。"""
    user, project, token = await _make_user_and_project(client, db_session, "round3_suggest_no_llm")
    _set_quota_used(db_session, user.id, 3)

    with (
        patch("agent.suggest_service._service", None),
        patch(
            "agent.suggest_service.get_llm_client",
            side_effect=ValueError("DEEPSEEK_API_KEY is required"),
        ),
    ):
        response = await client.post(
            "/api/v1/agent/suggest",
            json={"project_id": str(project.id), "count": 3},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["suggestions"] == [
        "写下一章的情节发展",
        "完善角色的人物动机",
        "设计一个情节转折点",
    ]
    assert _read_quota_used(db_session, user.id) == 3


@pytest.mark.integration
async def test_suggest_fallback_remains_available_when_quota_is_exhausted(client: AsyncClient, db_session: Session):
    """A local fixed fallback has no provider cost and must not require quota."""
    user, project, token = await _make_user_and_project(client, db_session, "round3_suggest_no_llm_quota_out")
    _set_quota_used(db_session, user.id, FREE_AI_CONVERSATION_LIMIT)

    with (
        patch("agent.suggest_service._service", None),
        patch(
            "agent.suggest_service.get_llm_client",
            side_effect=ValueError("DEEPSEEK_API_KEY is required"),
        ),
    ):
        response = await client.post(
            "/api/v1/agent/suggest",
            json={"project_id": str(project.id), "count": 3},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["suggestions"] == [
        "写下一章的情节发展",
        "完善角色的人物动机",
        "设计一个情节转折点",
    ]
    assert _read_quota_used(db_session, user.id) == FREE_AI_CONVERSATION_LIMIT


@pytest.mark.integration
async def test_suggest_rate_limited_per_user(client: AsyncClient, db_session: Session):
    """同一账号超过窗口内的请求数上限后返回 429，且限流主体是用户而非 IP。"""
    _user_a, project_a, token_a = await _make_user_and_project(client, db_session, "round3_suggest_rl_a")
    _user_b, project_b, token_b = await _make_user_and_project(client, db_session, "round3_suggest_rl_b")

    # 走「无 LLM 客户端」的兜底路径，把额度维度排除掉，单独验证限流。
    with (
        patch("agent.suggest_service._service", None),
        patch(
            "agent.suggest_service.get_llm_client",
            side_effect=ValueError("DEEPSEEK_API_KEY is required"),
        ),
    ):
        for index in range(SUGGEST_RATE_LIMIT_MAX_REQUESTS):
            response = await client.post(
                "/api/v1/agent/suggest",
                json={"project_id": str(project_a.id), "count": 3},
                headers={"Authorization": f"Bearer {token_a}"},
            )
            assert response.status_code == 200, f"第 {index + 1} 次请求不应被限流"

        blocked = await client.post(
            "/api/v1/agent/suggest",
            json={"project_id": str(project_a.id), "count": 3},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert blocked.status_code == 429

        # 同一客户端（同一 IP）的另一个账号不受影响：限流按 user_id 计。
        other_user = await client.post(
            "/api/v1/agent/suggest",
            json={"project_id": str(project_b.id), "count": 3},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert other_user.status_code == 200


@pytest.mark.integration
async def test_stream_rejects_concurrent_run_for_same_chat_session(client: AsyncClient, db_session: Session):
    """One chat session has one ordered writer; another run receives 409."""
    from agent.core.steering import (
        cleanup_steering_queue_async,
        create_steering_queue_async,
    )

    user, project, token = await _make_user_and_project(client, db_session, "round3_stream_session_busy")
    session_id = "round3-stream-session-busy"
    await create_steering_queue_async(
        session_id,
        user.id,
        run_id="already-running",
        exclusive_run=True,
    )
    try:
        response = await client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": str(project.id),
                "message": "第二个并发请求",
                "session_id": session_id,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        await cleanup_steering_queue_async(session_id)

    assert response.status_code == 409
    assert response.json()["error_code"] == "ERR_SESSION_BUSY"
    assert _read_quota_used(db_session, user.id) == 0


@pytest.mark.integration
async def test_stream_busy_resolved_session_returns_409_and_refunds(client: AsyncClient, db_session: Session):
    """没带 session_id、后端解析到的活跃会话仍被占用时：409 ERR_SESSION_BUSY，额度退回。

    修复前 SteeringSessionBusyError 在流开始后才抛出，前端只看到泛用的
    「生成回复时发生错误」，无论重试多少次都一样。
    """
    from agent.core.steering import (
        cleanup_steering_queue_async,
        create_steering_queue_async,
    )
    from models import ChatSession

    user, project, token = await _make_user_and_project(client, db_session, "round3_stream_resolved_busy")
    chat_session = ChatSession(user_id=user.id, project_id=project.id, is_active=True)
    db_session.add(chat_session)
    db_session.commit()
    db_session.refresh(chat_session)
    await create_steering_queue_async(
        chat_session.id,
        user.id,
        run_id="still-finalizing",
        exclusive_run=True,
    )
    try:
        response = await client.post(
            "/api/v1/agent/stream",
            json={"project_id": str(project.id), "message": "继续写"},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        await cleanup_steering_queue_async(chat_session.id)

    assert response.status_code == 409
    assert response.json()["error_code"] == "ERR_SESSION_BUSY"
    assert _read_quota_used(db_session, user.id) == 0


@pytest.mark.unit
def test_all_llm_endpoints_have_user_scoped_rate_limit():
    """守卫用例：agent router 下所有触发 LLM 的端点都必须挂按用户限流依赖。

    只有 /health（无鉴权、无 LLM、无 DB 的探活接口）豁免。
    """
    llm_endpoints = {
        "/api/v1/agent/stream": STREAM_RATE_LIMIT_MAX_REQUESTS,
        "/api/v1/agent/suggest": SUGGEST_RATE_LIMIT_MAX_REQUESTS,
        "/api/v1/agent/steer": STEER_RATE_LIMIT_MAX_REQUESTS,
    }

    routes_by_path = {route.path: route for route in agent_router.routes}
    # 端点清单本身也要守住：新增端点必须显式在这里表态
    assert set(routes_by_path) == set(llm_endpoints) | {"/api/v1/agent/health"}

    for path, expected_max in llm_endpoints.items():
        dependencies = routes_by_path[path].dependant.dependencies
        rate_limiters = {
            dep.call.rate_limit_window_seconds: dep.call
            for dep in dependencies
            if getattr(dep.call, "rate_limit_key", None) is not None
        }
        assert rate_limiters, f"{path} 缺少按用户限流依赖"
        hourly = rate_limiters.get(3600)
        assert hourly is not None, f"{path} 缺少小时级按用户限流"
        assert hourly.rate_limit_max_requests == expected_max

    # /suggest 不扣对话额度，必须另有独立的每日上限。
    suggest_limiters = [
        dep.call
        for dep in routes_by_path["/api/v1/agent/suggest"].dependant.dependencies
        if getattr(dep.call, "rate_limit_key", None) is not None
    ]
    daily = [limiter for limiter in suggest_limiters if getattr(limiter, "rate_limit_period", None) == "beijing_day"]
    assert len(daily) == 1
    assert daily[0].rate_limit_max_requests == SUGGEST_DAILY_MAX_REQUESTS
    assert daily[0].rate_limit_window_seconds == 86400


@pytest.mark.unit
def test_suggest_rate_limit_window_is_hourly():
    """限流窗口按小时计，防止误配成秒级导致形同虚设。"""
    assert SUGGEST_RATE_LIMIT_WINDOW_SECONDS == 3600
