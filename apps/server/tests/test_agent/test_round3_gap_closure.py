"""Round-3 缺口闭合回归：并发 run 的 steering 归属。

背景（bug-07 未闭合的部分）：
`POST /agent/steer` 的请求体只有 session_id，队列因此只能按 chat session 寻址，
无法把一条引导消息定向投递给某个 run。能够并且必须保证的不变量是：

    **已经结束的 run 不得吞掉本该由仍在生成的 run 消费的引导消息。**

上一轮修复只给 finally 里那段「历史已落库 → 队列被 cleanup」的窄窗口做了交还
（`_release_run_and_requeue_steering`），却漏掉了更宽的
「工作流结束 → 保存历史」窗口：service.py 在保存历史前会无条件 drain 一次队列，
drain 出来的消息直接随本轮历史落库。结果是同一 chat session 里仍在流式生成的
run B 永远收不到这条引导，历史里反而凭空多出一条挂在已结束轮次上的用户消息。

本文件同时锁死两层：
1. 生产路径（`AgentService.process_stream`）：保存历史前入队的 steering，有并发
   run 时必须回到队列、且不得写进本轮历史；没有并发 run 时必须照旧落库。
2. 原语层（`agent.core.steering.has_other_active_runs_async`）：内存与 Redis 两
   套后端必须给出同一个答案——这个模块反复出问题的根源就是两侧语义漂移。
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, select

from models import ChatMessage, File, Project, User
from services.core.auth_service import hash_password
from tests.test_agent.test_round3_steering import _FakeRedis

pytestmark = pytest.mark.usefixtures("writing_prompt_configs")

CHAPTER = "第四章 荒原的信使\n" + "他把信塞进石缝里。" * 20
LATE_TEXT = "结尾再收一下，别烂尾"
_ONE_DAY = 24 * 3600


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def gap_user_with_project(db_session: Session):
    """本文件专用的用户 + 项目 + 草稿（邮箱与其它测试文件隔离）。"""
    user = User(
        email="round3_gap_closure@example.com",
        username="round3gapclosure",
        hashed_password=hash_password("password123"),
        name="Round3 Gap Closure User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name="Round3 Gap Closure Project",
        description="缺口闭合回归项目",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    draft = File(
        title="第一章",
        content="旧内容",
        file_type="draft",
        project_id=project.id,
        user_id=user.id,
    )
    db_session.add(draft)
    db_session.commit()

    return {"user": user, "project": project}


@pytest.fixture
def gap_service():
    """带 mock context assembler 的 AgentService。"""
    from agent.schemas.context import ContextData
    from agent.service import AgentService

    assembler = MagicMock()
    assembler.assemble.return_value = ContextData(items=[], context="", token_estimate=0)
    return AgentService(context_assembler=assembler)


@pytest.fixture
def memory_steering(monkeypatch):
    """强制走内存回退后端。"""
    return _force_memory_steering(monkeypatch)


def _force_redis_steering(monkeypatch) -> tuple[object, _FakeRedis]:
    """强制走 Redis 后端，并接到同一个 fake 上。"""
    import agent.core.steering as st

    fake = _FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://fake:6379/0")
    monkeypatch.setattr(
        "services.infra.redis_client.get_redis_client", lambda: fake, raising=True
    )
    monkeypatch.setattr(st, "_redis_health_checked_at", 0.0)
    monkeypatch.setattr(st, "_redis_is_healthy", False)
    return st, fake


@pytest.fixture
def redis_steering(monkeypatch):
    return _force_redis_steering(monkeypatch)


@pytest.fixture(params=["memory", "redis"])
def steering_backend(request, monkeypatch):
    """生产路径用例在两个后端上各跑一遍：语义漂移是这个模块的历史顽疾。"""
    if request.param == "redis":
        return _force_redis_steering(monkeypatch)[0]
    return _force_memory_steering(monkeypatch)


def _force_memory_steering(monkeypatch):
    import agent.core.steering as st

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(st, "_redis_health_checked_at", 0.0)
    monkeypatch.setattr(st, "_redis_is_healthy", False)
    return st


def _session_id_from_events(events: list[str]) -> str:
    started = [e for e in events if "event: session_started" in e]
    assert started, "缺少 session_started 事件"
    return json.loads(started[0].split("data:", 1)[1].strip())["session_id"]


def _rows(db_session: Session, session_id: str) -> list[ChatMessage]:
    # 被测代码的补偿保存/追加走独立 session，必须显式 expire 才能读到最新副本。
    db_session.rollback()
    db_session.expire_all()
    return db_session.exec(
        select(ChatMessage).where(ChatMessage.session_id == session_id)
    ).all()


async def _run_stream_with_pre_save_steering(
    gap_service,
    project,
    user,
    db_session: Session,
    concurrent_run_id: str | None,
):
    """跑一轮 process_stream，在工作流结束前（即保存历史前的兜底 drain 之前）
    模拟用户 POST /agent/steer。concurrent_run_id 非空时先注册一个仍在生成的
    并发 run。返回 (session_id, events)。"""
    from agent.core.steering import (
        create_steering_queue_async,
        get_steering_queue_for_user_async,
    )
    from agent.core.workflow_events import StreamEvent, StreamEventType

    captured: dict[str, str] = {}

    def fake_workflow(writing_state, **_kwargs):
        async def _stream():
            sid = writing_state["session_id"]
            captured["session_id"] = sid
            if concurrent_run_id:
                # 同一 chat session 的另一个 stream run，仍在生成中。
                await create_steering_queue_async(
                    sid, str(user.id), run_id=concurrent_run_id
                )
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": CHAPTER})
            # 用户此刻 POST /agent/steer：本 run 已经不会再把它喂给模型了。
            queue = await get_steering_queue_for_user_async(sid, str(user.id))
            await queue.add(LATE_TEXT)
            yield StreamEvent(
                type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"}
            )

        return _stream()

    bind = db_session.get_bind()
    events: list[str] = []
    with (
        patch("agent.service.run_writing_workflow_streaming", side_effect=fake_workflow),
        patch("agent.service.create_session", side_effect=lambda: Session(bind)),
    ):
        async for event in gap_service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="写第四章",
            session=db_session,
        ):
            events.append(event)

    return _session_id_from_events(events), captured


# ---------------------------------------------------------------------------
# 生产路径：保存历史前那段窗口
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPreSaveDrainHandsBackToConcurrentRun:
    """「工作流结束 → 保存历史」窗口里到达的 steering 的归属。"""

    async def test_steering_arriving_before_pre_save_drain_is_handed_back(
        self, gap_service, gap_user_with_project, db_session: Session, steering_backend
    ):
        """并发 run B 仍在生成时，本 run 保存历史前 drain 出来的 steering 必须
        回到队列，并且不得写进本轮历史。"""
        from agent.core.steering import (
            cleanup_steering_queue_async,
            get_steering_queue_for_user_async,
        )

        project = gap_user_with_project["project"]
        user = gap_user_with_project["user"]
        other_run_id = "gap-closure-concurrent-run-b"

        session_id, captured = await _run_stream_with_pre_save_steering(
            gap_service, project, user, db_session, concurrent_run_id=other_run_id
        )
        try:
            queue = await get_steering_queue_for_user_async(session_id, str(user.id))
            still_pending = [m.content for m in await queue.peek()]
            assert LATE_TEXT in still_pending, (
                "仍在生成的 run B 必须还能消费到这条引导；"
                "先结束的 run A 不得把它吞掉"
            )

            rows = _rows(db_session, session_id)
            assert not any(r.content == LATE_TEXT for r in rows), (
                "交还给并发 run 的消息不应同时挂到已结束轮次的历史里"
            )
            assert any(
                r.role == "assistant" and CHAPTER in (r.content or "") for r in rows
            ), "本轮正文仍必须正常落库"
        finally:
            if captured.get("session_id"):
                await cleanup_steering_queue_async(
                    captured["session_id"], run_id=other_run_id
                )

    async def test_pre_save_drain_still_persists_when_no_concurrent_run(
        self, gap_service, gap_user_with_project, db_session: Session, steering_backend
    ):
        """没有并发 run 时行为必须与修复前完全一致：消息随本轮历史落库。"""
        project = gap_user_with_project["project"]
        user = gap_user_with_project["user"]

        session_id, _ = await _run_stream_with_pre_save_steering(
            gap_service, project, user, db_session, concurrent_run_id=None
        )

        rows = _rows(db_session, session_id)
        assert any(
            r.role == "user" and r.content == LATE_TEXT for r in rows
        ), "无并发 run 时 /agent/steer 已确认 queued 的消息必须随本轮历史落库"

    async def test_stale_concurrent_run_does_not_strand_steering(
        self, gap_service, gap_user_with_project, db_session: Session, redis_steering
    ):
        """并发持有者是崩溃残留的僵尸时，消息不能被交还给「不存在的 run」而
        滞留在队列里空转，必须照常落库。

        只在 Redis 后端验：跨 worker 才可能留下僵尸持有者（worker 被 SIGKILL /
        部署重启，finally 永远不执行）；内存后端的持有记录与进程同生共死，
        进程还活着就说明持有者也还活着，僵尸场景在那里根本不可达。
        僵尸不算活跃持有者这条不变量在两个后端的原语层都锁在
        TestHasOtherActiveRunsParity 里。
        """
        st, fake = redis_steering
        from agent.core.steering import (
            cleanup_steering_queue_async,
            create_steering_queue_async,
            get_steering_queue_for_user_async,
        )
        from agent.core.workflow_events import StreamEvent, StreamEventType

        project = gap_user_with_project["project"]
        user = gap_user_with_project["user"]
        zombie_run_id = "gap-closure-zombie-run"

        captured: dict[str, str] = {}

        def fake_workflow(writing_state, **_kwargs):
            async def _stream():
                sid = writing_state["session_id"]
                captured["session_id"] = sid
                await create_steering_queue_async(
                    sid, str(user.id), run_id=zombie_run_id
                )
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": CHAPTER})
                queue = await get_steering_queue_for_user_async(sid, str(user.id))
                await queue.add(LATE_TEXT)
                # 把僵尸持有者的心跳拨回一天前（worker 被 SIGKILL 的效果）。
                fake.age_runs(st._runs_key(sid), _ONE_DAY, member=zombie_run_id)
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"}
                )

            return _stream()

        bind = db_session.get_bind()
        events: list[str] = []
        try:
            with (
                patch(
                    "agent.service.run_writing_workflow_streaming",
                    side_effect=fake_workflow,
                ),
                patch("agent.service.create_session", side_effect=lambda: Session(bind)),
            ):
                async for event in gap_service.process_stream(
                    project_id=str(project.id),
                    user_id=str(user.id),
                    message="写第四章",
                    session=db_session,
                ):
                    events.append(event)

            session_id = _session_id_from_events(events)
            rows = _rows(db_session, session_id)
            assert any(
                r.role == "user" and r.content == LATE_TEXT for r in rows
            ), "僵尸持有者不算「仍在生成的 run」，消息必须落库而不是滞留队列"
        finally:
            if captured.get("session_id"):
                await cleanup_steering_queue_async(
                    captured["session_id"], run_id=zombie_run_id
                )


# ---------------------------------------------------------------------------
# 原语层：两个后端的语义必须一致
# ---------------------------------------------------------------------------


async def _probe_matrix(st) -> dict[str, bool]:
    """在当前后端上跑同一组场景，返回场景名 -> 是否「还有别的活跃 run」。"""
    results: dict[str, bool] = {}

    # 1. 从未建过队列的 session
    results["unknown_session"] = await st.has_other_active_runs_async(
        "gap-probe-unknown", "runA"
    )

    # 2. 只有自己一个持有者
    sid_solo = "gap-probe-solo"
    await st.create_steering_queue_async(sid_solo, "user-1", run_id="runA")
    results["only_self"] = await st.has_other_active_runs_async(sid_solo, "runA")
    await st.cleanup_steering_queue_async(sid_solo, run_id="runA")

    # 3. 还有一个并发 run
    sid_pair = "gap-probe-pair"
    await st.create_steering_queue_async(sid_pair, "user-1", run_id="runA")
    await st.create_steering_queue_async(sid_pair, "user-1", run_id="runB")
    results["other_run_active"] = await st.has_other_active_runs_async(sid_pair, "runA")
    # 从 run B 的视角看同样成立（对称）
    results["other_run_active_symmetric"] = await st.has_other_active_runs_async(
        sid_pair, "runB"
    )
    await st.cleanup_steering_queue_async(sid_pair, run_id="runB")
    results["after_other_released"] = await st.has_other_active_runs_async(
        sid_pair, "runA"
    )
    await st.cleanup_steering_queue_async(sid_pair, run_id="runA")

    # 4. 并发持有者是僵尸（心跳早已过期）
    sid_zombie = "gap-probe-zombie"
    await st.create_steering_queue_async(sid_zombie, "user-1", run_id="runA")
    await st.create_steering_queue_async(sid_zombie, "user-1", run_id="zombie")
    _age_hold(st, sid_zombie, "zombie", _ONE_DAY)
    results["stale_other_run"] = await st.has_other_active_runs_async(sid_zombie, "runA")
    await st.cleanup_steering_queue_async(sid_zombie, run_id="runA")

    # 5. 空 run_id（调用方没有持有身份）一律视为「没有别人」，不做误判
    results["empty_run_id"] = await st.has_other_active_runs_async(sid_pair, "")

    return results


def _age_hold(st, session_id: str, run_id: str, seconds: float) -> None:
    """把某个持有者的心跳往前拨（模拟 worker 被 SIGKILL 后再没有心跳）。

    两个后端的持有者表示不同：内存是 SteeringQueueEntry.active_runs，Redis 是
    runs ZSET。哪边有这个 session 就拨哪边。
    """
    entry = st._queue_manager._queues.get(session_id)
    if entry is not None and run_id in entry.active_runs:
        entry.active_runs[run_id] -= seconds
        return

    from services.infra.redis_client import get_redis_client

    runs = get_redis_client().store.get(st._runs_key(session_id))
    assert isinstance(runs, dict) and run_id in runs, "两个后端都找不到该持有者"
    runs[run_id] -= seconds


_EXPECTED_PROBE_MATRIX = {
    "unknown_session": False,
    "only_self": False,
    "other_run_active": True,
    "other_run_active_symmetric": True,
    "after_other_released": False,
    "stale_other_run": False,
    "empty_run_id": False,
}


@pytest.mark.unit
class TestHasOtherActiveRunsParity:
    """has_other_active_runs_async：内存与 Redis 两套实现不得语义漂移。"""

    async def test_memory_backend_matrix(self, memory_steering):
        assert await _probe_matrix(memory_steering) == _EXPECTED_PROBE_MATRIX

    async def test_redis_backend_matrix(self, redis_steering):
        st, _fake = redis_steering
        assert await _probe_matrix(st) == _EXPECTED_PROBE_MATRIX

    async def test_redis_probe_does_not_delete_session_keys(self, redis_steering):
        """探测只读判定，不能像回收脚本那样把 owner/msgs 删掉——本 run 还在用。"""
        st, fake = redis_steering
        sid = "gap-probe-keep-keys"
        queue = await st.create_steering_queue_async(sid, "user-1", run_id="runA")
        await queue.add("别删我")

        assert await st.has_other_active_runs_async(sid, "runA") is False
        assert fake.store.get(st._owner_key(sid)) == "user-1"
        assert [m.content for m in await queue.peek()] == ["别删我"]

        await st.cleanup_steering_queue_async(sid, run_id="runA")


@pytest.mark.unit
async def test_memory_handback_holds_session_lifetime_through_append(memory_steering):
    """最后一个并发 run 的 cleanup 不能插进 holder 判定与消息入队之间。"""
    st = memory_steering
    sid = "gap-atomic-handback-memory"
    user_id = "user-1"

    await st.create_steering_queue_async(sid, user_id, run_id="runA")
    queue = await st.create_steering_queue_async(sid, user_id, run_id="runB")
    await st.cleanup_steering_queue_async(sid, run_id="runA")

    original_add = queue.add
    cleanup_task: asyncio.Task | None = None

    async def add_while_run_b_exits(content: str):
        nonlocal cleanup_task
        cleanup_task = asyncio.create_task(
            st.cleanup_steering_queue_async(sid, run_id="runB")
        )
        # 让 cleanup 开始竞争 manager lock。原子 hand-back 此刻仍持有该锁，
        # 所以 cleanup 必须等待消息完成入队。
        await asyncio.sleep(0)
        assert cleanup_task is not None and not cleanup_task.done()
        return await original_add(content)

    queue.add = add_while_run_b_exits
    accepted = await st.requeue_steering_if_other_active_async(
        sid,
        "runA",
        user_id,
        [LATE_TEXT],
    )

    assert accepted is True
    # primitive 返回后、尚未让 cleanup 再次调度前，消息必须仍挂在可寻址 entry 上，
    # 而不是旧实现那样写进已从 manager 删除的 detached queue。
    entry = st._queue_manager._queues.get(sid)
    assert entry is not None
    assert [message.content for message in entry.queue._messages] == [LATE_TEXT]

    assert cleanup_task is not None
    await cleanup_task


@pytest.mark.unit
async def test_handback_falls_back_when_last_concurrent_run_already_exited(
    steering_backend,
):
    """cleanup 先赢得竞态时原子原语返回 False，调用方据此保留历史兜底。"""
    st = steering_backend
    sid = "gap-atomic-handback-cleanup-wins"
    user_id = "user-1"

    await st.create_steering_queue_async(sid, user_id, run_id="runA")
    await st.create_steering_queue_async(sid, user_id, run_id="runB")
    await st.cleanup_steering_queue_async(sid, run_id="runA")
    await st.cleanup_steering_queue_async(sid, run_id="runB")

    assert (
        await st.requeue_steering_if_other_active_async(
            sid,
            "runA",
            user_id,
            [LATE_TEXT],
        )
        is False
    )
