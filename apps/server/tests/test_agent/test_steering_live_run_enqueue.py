"""POST /agent/steer 只在本轮还会消费引导时才确认 queued（F-28）。

旧实现把「owner 检查」与「入队」拆成两步：
- 本轮最后一次 drain（finally 里兜底取出、写进历史）之后、释放队列之前到达的
  引导，接口回 200 queued，随后被释放脚本连同 msgs 一起 DEL；
- 释放之后才到达的 RPUSH 会重建 msgs（TTL 1 h），注入同一 session 的下一轮。

现在入队与「仍有未封口的存活 run」在同一个原子单元里判定（Redis Lua / 内存
manager 锁），本轮最后一次 drain 同时封口；封口后或释放后的引导一律 404，
前端退回一般发送。两个后端跑同一组用例；Redis 用本机临时起的真实 redis-server
（或 CI 的 ZENSTORY_TEST_REDIS_URL），Lua 脚本真实执行。
"""

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from models import ChatMessage, Project, User
from services.core.auth_service import hash_password

pytestmark = pytest.mark.usefixtures("writing_prompt_configs")

LATE_TEXT = "最后一段别写成大团圆"


@pytest.fixture(scope="module")
def real_redis():
    """本测试独占的 Redis：CI 用 ZENSTORY_TEST_REDIS_URL，本机临时起 unix socket 实例。"""
    import redis

    url = os.getenv("ZENSTORY_TEST_REDIS_URL")
    if url:
        assert urlparse(url).hostname in {"localhost", "127.0.0.1", "::1"}
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2)
        assert client.ping()
        try:
            yield client
        finally:
            client.close()
        return
    executable = shutil.which("redis-server")
    if not executable:
        pytest.skip("local Redis executable unavailable")
    with tempfile.TemporaryDirectory(prefix="zs-steer-", dir="/tmp") as directory:
        socket = Path(directory) / "steer.sock"
        log = Path(directory) / "redis.log"
        with log.open("w") as output:
            process = subprocess.Popen(
                [executable, "--port", "0", "--unixsocket", str(socket),
                 "--save", "", "--appendonly", "no"],
                stdout=output, stderr=subprocess.STDOUT,
            )
        client = redis.Redis(unix_socket_path=str(socket), decode_responses=True, protocol=2,
                             socket_connect_timeout=0.2, socket_timeout=2)
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    assert client.ping()
                    break
                except redis.ConnectionError:
                    assert process.poll() is None and time.monotonic() < deadline, log.read_text()
                    time.sleep(0.01)  # startup readiness only
            yield client
        finally:
            try:
                if process.poll() is None:
                    client.shutdown(nosave=True)
            except redis.ConnectionError:
                pass
            finally:
                client.close()
                process.wait(timeout=5)


@pytest.fixture(params=["memory", "redis"])
def steering_backend(request, monkeypatch):
    import agent.core.steering as st

    if request.param == "redis":
        client = request.getfixturevalue("real_redis")
        monkeypatch.setenv("REDIS_URL", "redis://steering-test")
        monkeypatch.setattr("services.infra.redis_client.get_redis_client", lambda: client)
    else:
        client = None
        monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(st, "_redis_health_checked_at", 0.0)
    monkeypatch.setattr(st, "_redis_is_healthy", False)
    monkeypatch.setattr(st, "_redis_health_url", None)
    return request.param, client


def _leftover(backend, client, session_id: str) -> list[str]:
    """该 session 释放后残留的任何 steering 状态（应为空）。"""
    import agent.core.steering as st

    if backend == "memory":
        return [session_id] if session_id in st._queue_manager._queues else []
    return list(client.keys(f"steering:*:{session_id}"))


@pytest.fixture
def steer_user_with_project(db_session: Session):
    user = User(
        email=f"steer_live_{uuid4().hex[:8]}@example.com",
        username=f"steerlive{uuid4().hex[:8]}",
        hashed_password=hash_password("password123"),
        name="Steer Live",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="Steer Live", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return user, project


@pytest.fixture
def plain_service():
    from unittest.mock import MagicMock

    from agent.schemas.context import ContextData
    from agent.service import AgentService

    assembler = MagicMock()
    assembler.assemble.return_value = ContextData(items=[], context="", token_estimate=0)
    return AgentService(context_assembler=assembler)


async def _steer(session_id: str, user: User) -> int:
    """直接调用 POST /agent/steer 的处理函数，返回 HTTP 状态码。"""
    from api.agent import SteeringRequest, inject_steering
    from core.error_handler import APIException

    try:
        await inject_steering(
            SteeringRequest(session_id=session_id, message=LATE_TEXT),
            current_user=user,
            _rate_limit=0,
        )
    except APIException as exc:
        return exc.status_code
    return 200


@pytest.mark.unit
async def test_steer_after_final_drain_is_never_acknowledged_then_dropped(
    steering_backend, plain_service, steer_user_with_project, db_session: Session
):
    """最后一次 drain 之后、释放之前到达的引导：要么落库，要么非 200；释放后也非 200 且不留状态。"""
    import agent.service as service_module
    from agent.core.workflow_events import StreamEvent, StreamEventType

    backend, client = steering_backend
    user, project = steer_user_with_project
    real_cleanup = service_module.cleanup_steering_queue_async
    statuses: dict[str, int] = {}

    async def steer_around_release(session_id, run_id=None):
        statuses["before_release"] = await _steer(session_id, user)
        await real_cleanup(session_id, run_id=run_id)
        statuses["after_release"] = await _steer(session_id, user)
        statuses["session"] = session_id

    async def mock_stream():
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "雪落下来。"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    bind = db_session.get_bind()
    with (
        patch("agent.service.run_writing_workflow_streaming", return_value=mock_stream()),
        patch("agent.service.create_session", side_effect=lambda: Session(bind)),
        patch("agent.service.cleanup_steering_queue_async", steer_around_release),
    ):
        async for _event in plain_service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="写一段",
            session=db_session,
        ):
            pass

    session_id = statuses["session"]
    db_session.rollback()
    db_session.expire_all()
    rows = db_session.exec(select(ChatMessage).where(ChatMessage.session_id == session_id)).all()
    persisted = any(r.role == "user" and r.content == LATE_TEXT for r in rows)

    assert statuses["before_release"] != 200 or persisted, (
        f"[{backend}] /agent/steer 回了 200 queued，但消息既没进本轮历史也没有消费者"
    )
    assert statuses["before_release"] == 404 and not persisted
    assert statuses["after_release"] == 404
    assert _leftover(backend, client, session_id) == []


@pytest.mark.unit
async def test_enqueue_requires_an_unsealed_live_run(steering_backend):
    """入队与存活判定原子：live run 才收；封口（最后一次 drain）后、释放后、只剩僵尸时一律拒收。"""
    import agent.core.steering as st

    backend, client = steering_backend
    session_id = f"steer-live-{uuid4().hex}"
    run_id = uuid4().hex

    # 没有任何 run 登记过：不再给占位队列回 200。
    with pytest.raises(KeyError):
        await st.enqueue_steering_for_user_async(session_id, "owner", "无人消费")
    assert _leftover(backend, client, session_id) == []

    await st.create_steering_queue_async(session_id, "owner", run_id=run_id, exclusive_run=True)
    with pytest.raises(PermissionError):
        await st.enqueue_steering_for_user_async(session_id, "intruder", "越权")
    msg = await st.enqueue_steering_for_user_async(session_id, "owner", "第一条")
    assert msg.content == "第一条"

    drained = await st.drain_and_seal_steering_async(session_id, run_id)
    assert [m.content for m in drained] == ["第一条"]
    with pytest.raises(KeyError):
        await st.enqueue_steering_for_user_async(session_id, "owner", "封口之后")
    # 封口不释放独占槽位：同一 session 的新一轮仍是 busy。
    assert await st.has_active_runs_async(session_id) is True

    await st.cleanup_steering_queue_async(session_id, run_id=run_id)
    with pytest.raises(KeyError):
        await st.enqueue_steering_for_user_async(session_id, "owner", "释放之后")
    assert _leftover(backend, client, session_id) == []


@pytest.mark.unit
async def test_sealed_holder_does_not_receive_handed_back_steering(steering_backend):
    """多持有者遗留路径：已封口的 run 不再算「其它存活 run」，交还时不把消息塞给它。"""
    import agent.core.steering as st

    backend, client = steering_backend
    session_id = f"steer-live-{uuid4().hex}"
    run_a, run_b = uuid4().hex, uuid4().hex
    await st.create_steering_queue_async(session_id, "owner", run_id=run_a)
    await st.create_steering_queue_async(session_id, "owner", run_id=run_b)

    await st.drain_and_seal_steering_async(session_id, run_b)
    assert await st.requeue_steering_if_other_active_async(
        session_id, run_a, "owner", ["交还"]
    ) is False

    await st.cleanup_steering_queue_async(session_id, run_id=run_a)
    await st.cleanup_steering_queue_async(session_id, run_id=run_b)
    assert _leftover(backend, client, session_id) == []
