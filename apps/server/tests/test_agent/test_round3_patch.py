"""第三轮 deep review 的**补丁组**回归测试。

覆盖独立复核提出的、上一轮修复没有闭合到位的缺口：

- bug-02 消费端：writing_graph._format_file_inventory 硬编码类型名，
  script / document / snippet 被整类丢弃（数据源改了、渲染端没改）。
- C3 两侧键名不一致：router._extract_usage 产出 prompt/completion，
  runner 产出 input/output，_merge_usage 按字面键相加导致只有 total 被合并。
- offload 契约两侧不一致：工具恒进线程池，但 ToolContext 里仍放着请求级
  Session，多个并发工具线程会共用同一个 SQLAlchemy Session。
- stream_adapter 的 edit_file 全失败降级块变成死代码，前端拿到空白失败卡。
- （反证）agent 级 ERROR 事件是**终止**事件：适配器就地终止且不再发 done，
  前端 onError 里的 finalizeStream 因此是必要的，不能按“非终止”处理。
"""

import asyncio
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.graph.writing_graph import _format_file_inventory
from agent.stream_adapter import StreamAdapter
from agent.tools import mcp_tools
from agent.tools.mcp_tools import ToolContext
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


def test_format_file_inventory_includes_script_files():
    """短剧项目的正文是 script。渲染端漏掉它，交接后的 writer 看不到已写好的
    分集，会继续重复创建——这正是 bug-02 原文列出的症状。"""
    text = _format_file_inventory(
        {
            "outline": [{"id": "o1", "title": "总纲", "file_type": "outline"}],
            "draft": [],
            "script": [
                {"id": "s1", "title": "第1集", "file_type": "script"},
                {"id": "s2", "title": "第2集", "file_type": "script"},
            ],
            "character": [],
            "lore": [],
        }
    )

    assert "第1集(id=s1)" in text
    assert "第2集(id=s2)" in text
    assert "总纲(id=o1)" in text


def test_format_file_inventory_keeps_document_and_snippet_in_fallback_bucket():
    """document 是 create_file 的默认类型，snippet 是片段：任何一类被静默丢弃，
    Agent 都会对自己刚建的文件失明。"""
    text = _format_file_inventory(
        {
            "document": [{"id": "d1", "title": "设定备忘", "file_type": "document"}],
            "snippet": [{"id": "p1", "title": "灵感片段", "file_type": "snippet"}],
        }
    )

    assert "设定备忘(id=d1)" in text
    assert "灵感片段(id=p1)" in text


def test_format_file_inventory_keeps_unknown_new_types():
    """将来新增的 file_type 也必须进兜底桶，而不是从清单里凭空消失。"""
    text = _format_file_inventory({"storyboard": [{"id": "b1", "title": "分镜一"}]})
    assert "分镜一(id=b1)" in text


def test_format_file_inventory_labels_mixed_content_types():
    """draft 与 script 合并进「正文」时必须带类型标注，否则读不出是哪一类。"""
    text = _format_file_inventory(
        {
            "draft": [{"id": "d1", "title": "第一章", "file_type": "draft"}],
            "script": [{"id": "s1", "title": "第1集", "file_type": "script"}],
        }
    )

    content_line = next(line for line in text.splitlines() if line.startswith("正文:"))
    assert "第一章(id=d1)[正文]" in content_line
    assert "第1集(id=s1)[剧本]" in content_line


def test_format_file_inventory_empty_inventory_returns_empty_string():
    assert _format_file_inventory({"outline": [], "draft": []}) == ""


@pytest.mark.integration
def test_refresh_file_inventory_rows_carry_file_type(db_session: Session):
    """渲染端要按类型分桶标注，数据源的行字典必须自带 file_type。"""
    _, project = _make_user_project(db_session, "inv")
    db_session.add(
        File(project_id=project.id, title="第1集", file_type="script", content="正文")
    )
    db_session.commit()

    ToolContext.set_context(db_session, "u", project.id, "s")
    try:
        inventory = ToolContext.refresh_file_inventory()
    finally:
        ToolContext.clear_context()

    assert inventory is not None
    assert inventory["script"][0]["file_type"] == "script"

    # 端到端：数据源 → 渲染端，script 必须出现在 handoff 文本里
    assert "第1集" in _format_file_inventory(inventory)


# ------------------------------------------------------------------- C3：usage 键名统一


def test_router_extract_usage_uses_canonical_keys():
    from agent.graph.router import _extract_usage

    usage = _extract_usage(
        SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=120,
                completion_tokens=30,
                total_tokens=150,
                prompt_tokens_details=SimpleNamespace(cached_tokens=100),
            )
        )
    )

    # 缓存命中部分必须从 input_tokens 里扣出来，否则同一批 token 会被
    # 按输入价 + 缓存价重复计价（下游用量公式是相加）。
    assert usage == {
        "input_tokens": 20,
        "output_tokens": 30,
        "cache_read_tokens": 100,
        "total_tokens": 150,
    }
    assert "prompt_tokens" not in usage
    assert "completion_tokens" not in usage


def test_router_extract_usage_without_usage_returns_none():
    from agent.graph.router import _extract_usage

    assert _extract_usage(SimpleNamespace()) is None
    assert _extract_usage(SimpleNamespace(usage=SimpleNamespace())) is None


@pytest.mark.asyncio
async def test_router_usage_and_agent_usage_merge_into_one_key_family():
    """router 的用量与 runner 的用量必须落在同一族键上再相加。

    键名不统一时两族并存：只有 total_tokens 被相加，而下游
    下游用量汇总取 input_tokens 时读到的是 runner 那一份，
    router 的 prompt_tokens 被静默丢弃 —— total ≠ input+output+cache。
    """
    adapter = StreamAdapter()

    await _drain(
        adapter,
        StreamEvent(
            type=StreamEventType.ROUTER_DECIDED,
            data={
                "initial_agent": "writer",
                # router 侧（Chat Completions 原生键名，经 _extract_usage 归一后的形态）
                "routing_usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "cache_read_tokens": 5,
                    "total_tokens": 125,
                },
            },
        ),
    )
    await _drain(
        adapter,
        StreamEvent(
            type=StreamEventType.MESSAGE_END,
            data={
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 900,
                    "output_tokens": 80,
                    "cache_read_tokens": 15,
                    "total_tokens": 995,
                },
            },
        ),
    )

    usage = adapter.get_last_message_metadata()["usage"]
    assert usage == {
        "input_tokens": 1000,
        "output_tokens": 100,
        "cache_read_tokens": 20,
        "total_tokens": 1120,
    }
    assert (
        usage["total_tokens"]
        == usage["input_tokens"] + usage["output_tokens"] + usage["cache_read_tokens"]
    )


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
    """SQLite 部署下 ToolContext 里若放的是请求级 Session，
    get_session() 第一分支会把它交给工作线程；SDK 同一 turn 的多个 tool_call
    是并发 asyncio Task，两个以上线程会同时在同一个 Session 上 flush/commit。
    """
    request_session = _ProbeSession(-1)
    created: list[_ProbeSession] = []

    def factory() -> _ProbeSession:
        session = _ProbeSession(len(created))
        created.append(session)
        return session

    ToolContext.set_context(
        session=request_session,
        user_id="u1",
        project_id="p1",
        session_id="s1",
        create_session_func=factory,
    )
    try:
        seen: list[int] = []

        def probe(_args):
            session = ToolContext.get_session()
            seen.append(id(session))
            time.sleep(0.05)  # 让两个工具的执行窗口真实重叠
            return {"ok": True}

        await asyncio.gather(
            asyncio.to_thread(
                mcp_tools._run_sync_tool_with_owned_session_cleanup, probe, {}
            ),
            asyncio.to_thread(
                mcp_tools._run_sync_tool_with_owned_session_cleanup, probe, {}
            ),
        )
    finally:
        ToolContext.clear_context()

    assert len(seen) == 2
    # 1) 谁都不许拿到请求级 Session
    assert id(request_session) not in seen
    # 2) 两个并发工具线程各自持有独立 Session
    assert seen[0] != seen[1]
    assert len(created) == 2
    assert all(session.closed for session in created)


@pytest.mark.asyncio
async def test_sync_tool_thread_falls_back_to_context_session_without_factory():
    """没有 create_session_func 时自建无从谈起，只能沿用调用方给的 session
    （测试与内部脚本的典型用法），不能因为隔离逻辑把它变成 RuntimeError。"""
    request_session = _ProbeSession(-1)
    ToolContext.set_context(
        session=request_session, user_id="u1", project_id="p1", session_id="s1"
    )
    try:

        def probe(_args):
            return ToolContext.get_session()

        got = await asyncio.to_thread(
            mcp_tools._run_sync_tool_with_owned_session_cleanup, probe, {}
        )
    finally:
        ToolContext.clear_context()

    assert got is request_session


def test_offload_switches_are_same_source():
    """service.py 决定「ToolContext 里放不放 session」时必须用与
    mcp_tools 同一个判据，否则两个开关会在 SQLite 上错配。"""
    import inspect

    from agent.service import AgentService

    source = inspect.getsource(AgentService.process_stream)
    assert "session=None if _should_offload_tool_execution() else session" in source


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

    ToolContext.set_context(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="s1",
        create_session_func=factory,
    )
    try:
        results = await asyncio.gather(
            mcp_tools.create_file(
                {"title": "并发一", "file_type": "draft", "content": "甲"}
            ),
            mcp_tools.create_file(
                {"title": "并发二", "file_type": "draft", "content": "乙"}
            ),
        )
    finally:
        ToolContext.clear_context()

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
    assert tool_result.data["error"]
    assert "未找到待替换文本" in tool_result.data["error"]
    assert "锚点不唯一" in tool_result.data["error"]
    # data 必须保留：failed_edits 是用户判断「哪几处没改成」的唯一依据
    assert tool_result.data["data"] is not None
    assert tool_result.data["data"]["all_failed"] is True


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
