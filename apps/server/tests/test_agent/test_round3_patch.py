"""第三轮 deep review 的**补丁组**回归测试。

覆盖独立复核提出的、上一轮修复没有闭合到位的缺口：

- C3 键名不一致：上游产出 prompt/completion 别名、runner 产出 input/output 时，
  _merge_usage 按字面键相加导致只有 total 被合并。
- offload 契约两侧不一致：工具恒进线程池，但 ToolContext 里仍放着请求级
  Session，多个并发工具线程会共用同一个 SQLAlchemy Session。
- stream_adapter 的 edit_file 全失败降级块变成死代码，前端拿到空白失败卡。
- （反证）agent 级 ERROR 事件是**终止**事件：适配器就地终止且不再发 done，
  前端 onError 里的 finalizeStream 因此是必要的，不能按“非终止”处理。
"""

import asyncio
import json
import time
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.stream_adapter import StreamAdapter
from agent.tools import mcp_tools
from agent.tools.tool_context import AgentRequestContext
from models import File, Project, User

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- helpers


def _make_user_project(db_session: Session, tag: str) -> tuple[User, Project]:
    suffix = uuid4().hex[:8]
    user = User(
        email=f"r3p-{tag}-{suffix}@example.com",
        username=f"r3p_{tag}_{suffix}",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(name=f"R3P {tag} {suffix}", owner_id=user.id, project_type="screenplay")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return user, project


async def _drain(adapter: StreamAdapter, event: StreamEvent) -> list:
    return [sse async for sse in adapter._process_workflow_event(event)]  # noqa: SLF001


def _mcp_result(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}


# --------------------------------------------------------- bug-02 消费端：handoff 清单渲染


# ------------------------------------------------------------------- C3：usage 键名统一


@pytest.mark.asyncio
async def test_merge_usage_normalizes_legacy_alias_keys():
    """即便某个上游仍在发 prompt/completion 别名，也必须折叠到规范键上相加，
    绝不能让两族键名在同一个 dict 里并存。"""
    adapter = StreamAdapter()

    await _drain(
        adapter,
        StreamEvent(
            type=StreamEventType.MESSAGE_END,
            data={"usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}},
        ),
    )
    await _drain(
        adapter,
        StreamEvent(
            type=StreamEventType.MESSAGE_END,
            data={"usage": {"input_tokens": 30, "output_tokens": 6, "total_tokens": 36}},
        ),
    )

    usage = adapter.get_last_message_metadata()["usage"]
    assert usage == {"input_tokens": 40, "output_tokens": 10, "total_tokens": 50}


# ------------------------------------------------- offload 契约：工作线程绝不共用请求 Session


class _ProbeSession:
    """只记录身份的假 Session。"""

    def __init__(self, index: int) -> None:
        self.index = index
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_sync_tool_thread_never_reuses_request_session(monkeypatch):
    """即便 AgentRequestContext 同时带了 session 与 create_session_func，
    session_scope() 仍必须为每次工作线程调用建独立短生命周期 Session 并在退出时关闭，
    绝不让并发工具线程共享请求级 Session。
    """
    request_session = _ProbeSession(-1)
    created: list[_ProbeSession] = []

    def factory() -> _ProbeSession:
        session = _ProbeSession(len(created))
        created.append(session)
        return session

    req_ctx = AgentRequestContext(
        session=request_session,
        user_id="u1",
        project_id="p1",
        session_id="s1",
        create_session_func=factory,
    )
    try:
        seen: list[int] = []

        def probe():
            with req_ctx.session_scope() as session:
                seen.append(id(session))
                time.sleep(0.05)  # 让两个工具的执行窗口真实重叠
                return {"ok": True}

        await asyncio.gather(
            asyncio.to_thread(probe),
            asyncio.to_thread(probe),
        )
    finally:
        req_ctx.clear()

    assert len(seen) == 2
    # 1) 谁都不许拿到请求级 Session
    assert id(request_session) not in seen
    # 2) 两个并发工具线程各自持有独立 Session
    assert seen[0] != seen[1]
    assert len(created) == 2
    assert all(session.closed for session in created)


@pytest.mark.asyncio
async def test_sync_tool_thread_falls_back_to_context_session_without_factory():
    """没有 create_session_func 时自建无从谈起，沿用调用方注入的 session 且不关闭。"""
    request_session = _ProbeSession(-1)
    req_ctx = AgentRequestContext(
        session=request_session, user_id="u1", project_id="p1", session_id="s1"
    )
    try:

        def probe():
            with req_ctx.session_scope() as session:
                return session

        got = await asyncio.to_thread(probe)
    finally:
        req_ctx.clear()

    assert got is request_session
    assert request_session.closed is False


def test_service_never_passes_request_session_into_tool_context():
    """service.py 始终传 session=None + create_session_func，工具工作线程各自建独立 Session。"""
    import inspect

    from agent.service import AgentService

    source = inspect.getsource(AgentService.process_stream)
    assert "session=None," in source
    assert "create_session_func=create_session" in source


@pytest.mark.asyncio
@pytest.mark.integration
async def test_concurrent_tools_use_distinct_sessions_on_sqlite(db_session: Session, monkeypatch):
    """真实工具（create_file）在 SQLite 下并发执行时也不得共用请求 Session。"""
    import database

    user, project = _make_user_project(db_session, "concurrent")
    engine = db_session.get_bind()
    sessions: list[Session] = []

    def factory() -> Session:
        session = Session(engine)
        sessions.append(session)
        return session

    monkeypatch.setattr(database, "create_session", factory)

    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="s1",
        create_session_func=factory,
    )
    try:
        results = await asyncio.gather(
            mcp_tools.create_file(
                {"title": "并发一", "file_type": "draft", "content": "甲"},
                request_context=req_ctx,
            ),
            mcp_tools.create_file(
                {"title": "并发二", "file_type": "draft", "content": "乙"},
                request_context=req_ctx,
            ),
        )
    finally:
        req_ctx.clear()

    payloads = [json.loads(r["content"][0]["text"]) for r in results]
    assert all(p.get("status") == "success" for p in payloads), payloads
    # 工具线程各自建 session，一个都没有复用请求级的 db_session
    assert len(sessions) >= 2
    assert all(session is not db_session for session in sessions)

    db_session.expire_all()
    titles = {
        f.title
        for f in db_session.exec(select(File).where(File.project_id == project.id)).all()
    }
    assert {"并发一", "并发二"} <= titles


# ------------------------------------------------ edit_file 全失败：错因与 data 都不能丢


@pytest.mark.asyncio
async def test_edit_file_all_failed_emits_error_text_and_data():
    """工具层已把 status 置成 error，适配器的降级块不能再以 status=='success'
    为前置条件——否则它是死代码，前端拿到既没 data 也没 error 的空白失败卡。"""
    adapter = StreamAdapter()

    result_payload = {
        "status": "error",
        "data": {
            "id": "f1",
            "title": "第一章",
            "all_failed": True,
            "edits_applied": 0,
            "failed_edits": [
                {"index": 0, "error": "未找到待替换文本"},
                {"index": 1, "error": "锚点不唯一"},
            ],
        },
    }

    events = await _drain(
        adapter,
        StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "name": "edit_file",
                "tool_use_id": "call-1",
                "result": _mcp_result(result_payload),
            },
        ),
    )

    tool_result = next(e for e in events if e.type.value == "tool_result")
    assert tool_result.data["status"] == "error"
    assert tool_result.data["error"] == "此次修改未能生效，文件未改动。"
    # 原始给模型看的 error 文案绝不透传给前端
    assert "未找到待替换文本" not in tool_result.data["error"]
    assert "锚点不唯一" not in tool_result.data["error"]
    # data 必须保留：file meta 与脱敏后的 failed_edits 是用户判断「哪几处没改成」的依据
    assert tool_result.data["data"] is not None
    assert tool_result.data["data"]["id"] == "f1"
    assert tool_result.data["data"]["title"] == "第一章"
    assert tool_result.data["data"]["all_failed"] is True
    assert len(tool_result.data["data"]["failed_edits"]) == 2
    assert all("error" not in item for item in tool_result.data["data"]["failed_edits"])


@pytest.mark.asyncio
async def test_edit_file_success_still_carries_data():
    """没有 all_failed 时不受影响：仍然是 success + 完整 data。"""
    adapter = StreamAdapter()

    events = await _drain(
        adapter,
        StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "name": "edit_file",
                "tool_use_id": "call-2",
                "result": _mcp_result(
                    {"status": "success", "data": {"id": "f1", "edits_applied": 2}}
                ),
            },
        ),
    )

    tool_result = next(e for e in events if e.type.value == "tool_result")
    assert tool_result.data["status"] == "success"
    assert tool_result.data["data"]["edits_applied"] == 2


@pytest.mark.asyncio
async def test_non_edit_tool_error_still_hides_data():
    """非 edit_file 的失败仍然不带 data（避免把半截结果当成功数据渲染）。"""
    adapter = StreamAdapter()

    events = await _drain(
        adapter,
        StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "name": "query_files",
                "tool_use_id": "call-3",
                "result": _mcp_result({"status": "error", "error": "boom", "data": {"x": 1}}),
            },
        ),
    )

    tool_result = next(e for e in events if e.type.value == "tool_result")
    assert tool_result.data["status"] == "error"
    assert tool_result.data["data"] is None
    assert tool_result.data["error"] == "boom"


# ----------------------------------------- 反证：agent 级 ERROR 事件确实是终止事件


@pytest.mark.asyncio
async def test_error_event_terminates_stream_without_done():
    """复核意见认为 agent 级 ERROR 之后工作流仍会继续吐正文、随后还有 done，
    因此前端 onError 里的 finalizeStream 会抢跑。实测不成立：
    适配器一见 ERROR 就置 _fatal_stream_error 并就地终止，
    后续事件一个都不转发，也不会发 done。这条断言把该不变量钉死——
    它一旦变化，前端 onError 的 finalize 语义就必须同步重新设计。
    """
    adapter = StreamAdapter()

    async def _events():
        yield StreamEvent(type=StreamEventType.ERROR, data={"error": "boom"})
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "交接后继续写的正文"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    sse_events = [sse async for sse in adapter.process_workflow_events(_events())]
    types = [e.type.value for e in sse_events]

    assert "error" in types
    assert "done" not in types
    assert not any("交接后继续写的正文" in json.dumps(e.data, ensure_ascii=False) for e in sse_events)


def test_error_event_is_marked_fatal():
    """同一不变量的另一半：ERROR 分支必须置位 _fatal_stream_error。"""
    import inspect

    source = inspect.getsource(StreamAdapter._process_workflow_event)
    error_branch = source.split("StreamEventType.ERROR")[1][:400]
    assert "_fatal_stream_error = True" in error_branch
