"""第三轮收尾：C1 契约修复引入的 token 成本回归。

bug03 的修复把 create_file 的剧本分集幂等复用分支从「返回 content=''」改成
「返回真实 content + reused_existing + original_content_length」，数据丢失是堵住了，
但 mcp_tools 的序列化层会把这段**整集正文**（实测一集 8000 字）原样写进回给模型的
tool result 文本里，每次分集复用都白烧几千 token。

这里同时钉住两件事：
1. 复用分支回给模型的 tool result 不再携带正文（省 token 有效）；
2. 省略正文之后，StreamAdapter 依旧能判定「目标文件原本非空」并在截断补全时
   拒绝整体覆盖（bug03 的保护没被削弱）。
"""

import json

import pytest
from sqlmodel import Session

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.openai_agents.events import mcp_text_result
from agent.stream_adapter import StreamAdapter, StreamAdapterConfig
from agent.tools.mcp_tools import ToolContext, create_file
from models import File, Project, User

EPISODE_TITLE = "第7集 潮落"
EPISODE_BODY = "旧正文。" * 2000  # 8000 字，与线上实测的一集体量一致
TRUNCATED_PROSE = "（新版开场 300 字）"


@pytest.fixture
def test_user(db_session: Session) -> User:
    user = User(
        email="round3_token_cost@example.com",
        username="round3_token_cost",
        hashed_password="hashed_password",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def screenplay_project(db_session: Session, test_user: User) -> Project:
    project = Project(
        name="Round3 Token Cost",
        owner_id=test_user.id,
        project_type="screenplay",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    script_folder = File(
        id=f"{project.id}-script-folder",
        project_id=project.id,
        title="剧本",
        file_type="folder",
        order=0,
        parent_id=None,
    )
    db_session.add(script_folder)
    db_session.commit()
    return project


@pytest.fixture
def redirect_stream_save_session(monkeypatch):
    """StreamAdapter 落库走 database.create_session，测试里改指向内存库。"""
    import database
    from tests.conftest import TestSessionLocal

    def _get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(database, "get_session", _get_session)
    monkeypatch.setattr(database, "create_session", TestSessionLocal)
    monkeypatch.setattr(database, "is_postgres", False)
    yield


def _seed_episode(db_session: Session, project: Project, body: str = EPISODE_BODY) -> str:
    existing = File(
        project_id=project.id,
        title=EPISODE_TITLE,
        content=body,
        file_type="script",
        parent_id=f"{project.id}-script-folder",
        order=7,
    )
    db_session.add(existing)
    db_session.commit()
    db_session.refresh(existing)
    return existing.id


def _tool_result_text(payload: dict) -> str:
    """取出 MCP payload 里真正发给模型的那段文本。"""
    return payload["content"][0]["text"]


async def _reuse_episode(project: Project, title: str = EPISODE_TITLE) -> dict:
    return await create_file(
        {
            "title": title,
            "file_type": "script",
            "parent_id": f"{project.id}-script-folder",
        }
    )


@pytest.mark.asyncio
async def test_reused_episode_tool_result_drops_the_body(
    db_session: Session, test_user: User, screenplay_project: Project
):
    """复用分支回给模型的文本里不得再出现整集正文。"""
    file_id = _seed_episode(db_session, screenplay_project)
    ToolContext.set_context(db_session, test_user.id, screenplay_project.id, None)

    text = _tool_result_text(await _reuse_episode(screenplay_project))
    parsed = json.loads(text)
    data = parsed["data"]

    assert data["id"] == file_id, "没有走到幂等复用分支"
    # 正文片段一个都不许出现，长度也必须远小于正文本身
    assert "旧正文。" not in text
    assert EPISODE_BODY not in text
    assert data["content"] == ""
    assert data["content_elided"] is True
    assert len(text) < len(EPISODE_BODY) // 10


@pytest.mark.asyncio
async def test_reused_episode_tool_result_keeps_guard_fields(
    db_session: Session, test_user: User, screenplay_project: Project
):
    """省略正文的同时，覆盖保护赖以判断的显式字段必须原样保留。"""
    _seed_episode(db_session, screenplay_project)
    ToolContext.set_context(db_session, test_user.id, screenplay_project.id, None)

    data = json.loads(_tool_result_text(await _reuse_episode(screenplay_project)))["data"]

    assert data["reused_existing"] is True
    assert data["original_content_length"] == len(EPISODE_BODY)


@pytest.mark.asyncio
async def test_reused_empty_episode_is_untouched(
    db_session: Session, test_user: User, screenplay_project: Project
):
    """复用的目标文件本来就是空的时候没有正文可省，契约保持原样。"""
    _seed_episode(db_session, screenplay_project, body="")
    ToolContext.set_context(db_session, test_user.id, screenplay_project.id, None)

    data = json.loads(_tool_result_text(await _reuse_episode(screenplay_project)))["data"]

    assert data["reused_existing"] is True
    assert data["original_content_length"] == 0
    assert data["content"] == ""
    assert "content_elided" not in data


@pytest.mark.asyncio
async def test_explicit_content_on_new_file_still_echoes(
    db_session: Session, test_user: User, screenplay_project: Project
):
    """用户显式带 content 建新文件时回显 content 是既有行为，不在省略范围内。"""
    ToolContext.set_context(db_session, test_user.id, screenplay_project.id, None)

    payload = await create_file(
        {
            "title": "人物小传",
            "file_type": "character",
            "content": "赵四，四十岁，退伍侦察兵。",
        }
    )
    data = json.loads(_tool_result_text(payload))["data"]

    assert data["content"] == "赵四，四十岁，退伍侦察兵。"
    assert "reused_existing" not in data
    assert "content_elided" not in data


def _collect_sse_text(sse_events: list) -> str:
    """把 SSE 事件里所有回到对话流的文本拼起来。"""
    chunks: list[str] = []
    for event in sse_events:
        data = getattr(event, "data", None)
        if not isinstance(data, dict):
            continue
        for key in ("text", "content", "delta"):
            value = data.get(key)
            if isinstance(value, str):
                chunks.append(value)
    return "".join(chunks)


async def _run_streaming_turn(
    project: Project,
    user_id: str,
    *,
    close_file: bool,
) -> list:
    """跑一轮「复用分集 + 流式写正文」，close_file=False 模拟模型漏写 </file>。"""
    adapter = StreamAdapter(
        StreamAdapterConfig(project_id=project.id, user_id=user_id)
    )
    tool_payload = await _reuse_episode(project)
    result_text = _tool_result_text(tool_payload)

    body = f"<file>{EPISODE_TITLE}\n{TRUNCATED_PROSE}"
    if close_file:
        body += "</file>"

    events = [
        StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "tool_use_id": "call_round3_token",
                "name": "create_file",
                "result": mcp_text_result(result_text),
            },
        ),
        StreamEvent(type=StreamEventType.TEXT, data={"text": body}),
        StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"}),
    ]

    async def _gen():
        for event in events:
            yield event

    return [sse async for sse in adapter.process_workflow_events(_gen())]


@pytest.mark.asyncio
async def test_elided_content_still_blocks_truncated_overwrite(
    db_session: Session,
    test_user: User,
    screenplay_project: Project,
    redirect_stream_save_session,
):
    """bug03 保护不得被削弱：正文被省略后，截断补全仍不能覆盖整集正文。"""
    file_id = _seed_episode(db_session, screenplay_project)
    ToolContext.set_context(db_session, test_user.id, screenplay_project.id, None)

    sse_events = await _run_streaming_turn(
        screenplay_project, test_user.id, close_file=False
    )

    db_session.expire_all()
    stored = db_session.get(File, file_id)
    assert stored.content == EPISODE_BODY, (
        f"整集正文被残稿覆盖，现存 {len(stored.content or '')} 字"
    )

    # 拒绝写入必须让用户看得见，否则是静默丢内容
    surfaced = _collect_sse_text(sse_events)
    assert "本次改写未保存，原文保持不变。" in surfaced
    assert "</file>" not in surfaced and "edit_file" not in surfaced


@pytest.mark.asyncio
async def test_elided_content_does_not_block_legit_rewrite(
    db_session: Session,
    test_user: User,
    screenplay_project: Project,
    redirect_stream_save_session,
):
    """守卫不得矫枉过正：正常闭合 </file> 的重写照样要落库。"""
    file_id = _seed_episode(db_session, screenplay_project)
    ToolContext.set_context(db_session, test_user.id, screenplay_project.id, None)

    await _run_streaming_turn(screenplay_project, test_user.id, close_file=True)

    db_session.expire_all()
    stored = db_session.get(File, file_id)
    assert stored.content == f"{EPISODE_TITLE}\n{TRUNCATED_PROSE}"
