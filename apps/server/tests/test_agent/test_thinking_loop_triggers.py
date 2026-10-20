"""思考循环的触发条件（I07，10-10 之后仍出现的案例）。

不做重复检测或思考上限：这里锁住的是「模型收到的指令/上下文里互相矛盾或做不到」的几处输入。
全部 mock 模型；数据库用测试 SQLite 里的真实文件行，文本都是本文件构造的。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.graph.author_scope import SCOPE_DIRECTIVE_WITH_CONTENT_AFTER_PLANNING
from utils.text_metrics import count_words

# ------------------------------------------------------------ 规划类角色专用的范围约束


def _route(payload: dict[str, object]) -> AsyncMock:
    return AsyncMock(
        return_value={"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
    )


def _handoff(to: str, context: str) -> StreamEvent:
    packet = {"target_agent": to, "reason": "交接", "context": context, "completed": [], "todo": [], "evidence": []}
    return StreamEvent(
        type=StreamEventType.HANDOFF,
        data={"target_agent": to, "reason": "交接", "context": context, "handoff_packet": packet},
    )


async def _run_planner_writer_reviewer(route_payload: dict[str, object], message: str) -> dict[str, str]:
    from agent.graph.writing_graph import run_writing_workflow_streaming

    seen: dict[str, str] = {}

    async def fake_agent(state, agent_type, **_kwargs):
        seen[agent_type] = str(state.get("scope_directive") or "")
        if agent_type == "planner":
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "设定已经补进总纲。"})
            return
        if agent_type == "writer":
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第一章写好了，我再通读核对一遍。"})
            yield _handoff("quality_reviewer", "本轮新写第一章")
            return
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第一章写好了。[TASK_COMPLETE]"})

    environ = {"AGENT_ROUTER_STRATEGY": "llm", "AGENT_ENABLE_GRAPH_AUTO_REVIEW": "false"}
    with (
        patch.dict("os.environ", environ),
        patch("agent.graph.router._route_with_chat_model", _route(route_payload)),
        patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
        patch("agent.tools.mcp_tools.ToolContext.refresh_file_inventory", return_value={}),
    ):
        _ = [
            event
            async for event in run_writing_workflow_streaming(
                state={"user_message": message, "router_message": message, "messages": [], "system_prompt": ""},
                thread_id="thinking-loop",
                max_iterations=6,
                auto_review_threshold=50,
            )
        ]
    return seen


@pytest.mark.asyncio
@pytest.mark.unit
async def test_planning_only_directive_does_not_reach_writer_or_reviewer():
    """10-10 U070：计划交接后的 writer 读到「作者本轮已经要求写正文：规划完成后直接交接 writer」，
    又读到「作者要的已经全部交付时直接结束」，作者那句话只是补人设——两条之间来回权衡到撞上限。
    这句是写给规划类角色的，writer / 审稿人不应该收到。"""
    seen = await _run_planner_writer_reviewer(
        {"agent_type": "planner", "workflow_type": "standard", "write_content": True, "scope": ""},
        "主角叫陈默，二十二岁，刚毕业",
    )

    assert set(seen) == {"planner", "writer", "quality_reviewer"}
    assert SCOPE_DIRECTIVE_WITH_CONTENT_AFTER_PLANNING in seen["planner"]
    assert SCOPE_DIRECTIVE_WITH_CONTENT_AFTER_PLANNING not in seen["writer"]
    assert SCOPE_DIRECTIVE_WITH_CONTENT_AFTER_PLANNING not in seen["quality_reviewer"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_shared_scope_still_reaches_every_agent():
    seen = await _run_planner_writer_reviewer(
        {"agent_type": "planner", "workflow_type": "standard", "write_content": True, "scope": "大纲+第1章"},
        "先列大纲，再写第一章",
    )

    for agent_type in ("planner", "writer", "quality_reviewer"):
        assert "用户要求的交付范围：大纲+第1章" in seen[agent_type]
    assert SCOPE_DIRECTIVE_WITH_CONTENT_AFTER_PLANNING in seen["planner"]
    assert SCOPE_DIRECTIVE_WITH_CONTENT_AFTER_PLANNING not in seen["writer"]


# ------------------------------------------------------------ 送审内容：改动段 + 编辑器字数


def _chapter(number: int, sentence: str, repeat: int) -> str:
    return f"第{number}章\n" + "\n".join(f"　　{sentence}第{number}章的第{i}段。" for i in range(repeat))


def _assert_in_order(payload: str, appended: str) -> None:
    """追加的每一行都按顺序出现在送审内容里。

    edit_file 会按本文的段首缩进给追加的行补缩进（见 paragraph_layout.layout_insertion），
    所以逐行去掉缩进后对照，不要求整段逐字相同。
    """
    position = 0
    for line in appended.strip().splitlines():
        text = line.strip("\u3000 ")
        found = payload.find(text, position)
        assert found != -1, text
        position = found + len(text)


@pytest.fixture
def long_novel(db_session, monkeypatch):
    from agent.tools.mcp_tools import ToolContext
    from models import File, Project, User
    from services.features.activation_event_service import activation_event_service

    # 写入路径的副作用（向量索引、激活事件、看板缓存）与送审内容无关，就地关掉。
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_once", lambda *_a, **_kw: None)
    monkeypatch.setattr("services.infra.dashboard_cache.dashboard_cache.bump_project_version", lambda *_a, **_kw: None)

    user = User(
        email="thinking_loop@example.com",
        username="thinkingloop",
        hashed_password="x",
        name="Thinking Loop",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    project = Project(name="Thinking Loop", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    folder = File(project_id=project.id, title="正文", file_type="folder")
    db_session.add(folder)
    db_session.commit()

    # 一个文件装着前 30 章（远超送审预算），作者让 AI 在末尾续写。
    old_text = "\n".join(_chapter(n, "旧城的钟楼在雨里响了很久，", 15) for n in range(1, 31))
    novel = File(
        project_id=project.id, title="正文全本", content=old_text, file_type="draft", parent_id=folder.id
    )
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)

    ToolContext.set_context(session=db_session, user_id=user.id, project_id=project.id, session_id="s")
    try:
        yield {"project": project, "folder": folder, "novel": novel, "old_text": old_text}
    finally:
        ToolContext.clear_context()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_review_payload_for_append_to_long_file_shows_the_new_chapters(long_novel):
    """10-10 U074：14 万字的单文件末尾追加第 101—105 章，送审内容取了整份文件的开头（第 1—4 章）
    和结尾的一小段，新写的章节大半不在里面。送审内容应该是本请求改动的那一段。"""
    from agent.graph.writing_graph import _collect_review_payload_text, _format_review_payload
    from agent.tools import mcp_tools

    new_chapters = "\n" + "\n".join(_chapter(n, "她把伞收起来，靠在门边，", 70) for n in (31, 32))
    assert len(new_chapters) > 3000  # 比旧的尾部预算（2700 字符）长
    assert len(long_novel["old_text"]) > 9000  # 整份放不进送审预算

    result = await mcp_tools.edit_file(
        {"id": long_novel["novel"].id, "edits": [{"op": "append", "text": new_chapters}]}
    )
    assert json.loads(result["content"][0]["text"])["status"] == "success"

    payload = _format_review_payload(_collect_review_payload_text([long_novel["novel"].id]) or "")

    _assert_in_order(payload, new_chapters)
    assert "第1章的第0段" not in payload
    current = long_novel["old_text"] + new_chapters
    assert f"《正文全本》编辑器字数 {count_words(current)}" in payload
    # 写明其余部分没附上，审稿人知道要核对前文时去读。
    assert "没有附上" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_review_payload_for_new_files_lists_editor_word_counts(long_novel, db_session):
    """本请求新建的文件整份都是新写的；字数由系统按编辑器口径标出，审稿人不必为了一个数字再读文件
    （10-10 U072 / 10-11 U094：summary 读取不带 word_count，模型反复权衡要不要整份重读）。"""
    from agent.graph.writing_graph import _collect_review_payload_text
    from agent.tools import mcp_tools

    ids: list[str] = []
    bodies = {"第三十一章": _chapter(31, "风从北边来，", 5), "第三十二章": _chapter(32, "夜里没有灯，", 7)}
    for title, body in bodies.items():
        created = await mcp_tools.create_file(
            {"title": title, "file_type": "draft", "parent_id": long_novel["folder"].id}
        )
        file_id = json.loads(created["content"][0]["text"])["data"]["id"]
        # 流式落库走 update_file（与 StreamAdapter 相同的路径）。
        from agent.tools.mcp_tools import ToolContext

        ToolContext.get_executor().update_file(id=file_id, content=body)
        ids.append(file_id)

    payload = _collect_review_payload_text(ids) or ""

    counts = "、".join(f"《{title}》{count_words(body)}" for title, body in bodies.items())
    assert payload.startswith(f"[本请求写过的正文（编辑器字数）：{counts}]")
    for title, body in bodies.items():
        assert f"[《{title}》编辑器字数 {count_words(body)}]\n{body}" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_auto_review_task_carries_the_appended_chapters(long_novel, monkeypatch):
    """走真实的图：writer 用 edit_file 在长文件末尾续写，自动送审时审稿人收到的是新写的章节。"""
    from agent.graph.writing_graph import run_writing_workflow_streaming
    from agent.tools import mcp_tools

    monkeypatch.setenv("AGENT_ENABLE_GRAPH_AUTO_REVIEW", "true")
    novel_id = long_novel["novel"].id
    new_chapters = "\n" + "\n".join(_chapter(n, "她把伞收起来，靠在门边，", 70) for n in (31, 32))
    reviewer_messages: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        if agent_type == "writer":
            result = await mcp_tools.edit_file({"id": novel_id, "edits": [{"op": "append", "text": new_chapters}]})
            state["repeat_read_guard"]._record_write(novel_id, "正文全本")
            yield StreamEvent(
                type=StreamEventType.TOOL_USE,
                data={
                    "id": "w1",
                    "name": "edit_file",
                    "status": "complete",
                    "input": {"id": novel_id, "edits": [{"op": "append", "text": new_chapters}]},
                },
            )
            yield StreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={"tool_use_id": "w1", "name": "edit_file", "result": result},
            )
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第31、32章续写好了。" + "她走在雨里。" * 40})
            return
        reviewer_messages.append(str(state.get("user_message") or ""))
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "写好了。[TASK_COMPLETE]"})

    router = AsyncMock(return_value={"current_agent": "writer", "workflow_plan": "quick", "workflow_agents": []})
    with (
        patch("agent.graph.writing_graph.router_node", router),
        patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
        patch("agent.tools.mcp_tools.ToolContext.refresh_file_inventory", return_value={}),
        patch("agent.graph.writing_graph._auto_finalize_task_board_on_completion", AsyncMock(return_value=[])),
        patch("agent.graph.writing_graph.REVIEW_AUTO_CHECK_TIMEOUT_SECONDS", 5),
    ):
        _ = [
            event
            async for event in run_writing_workflow_streaming(
                state={"user_message": "接着写第31、32章", "messages": [], "system_prompt": ""},
                thread_id="thinking-loop-review",
                auto_review_threshold=100,
            )
        ]

    assert len(reviewer_messages) == 1
    payload = reviewer_messages[0].split("[待审查内容]\n", 1)[1]
    _assert_in_order(payload, new_chapters)
    assert "第1章的第0段" not in payload


# ------------------------------------------------------------ 作者手改保护拒绝的文件要告诉下一个 agent


@pytest.mark.asyncio
@pytest.mark.unit
async def test_reviewer_is_told_which_files_author_edit_protection_refused():
    """10-11 U094 09:09：writer 改大纲被「作者手改保护」拒绝（工具结果不跨 agent 回放），审稿人不知道，
    把「大纲那三处没改」列为必改项交回 writer——一个工具层不会放行的要求。"""
    from agent.graph.writing_graph import run_writing_workflow_streaming
    from agent.tools.author_edit_guard import AuthorEditRefusals
    from agent.tools.mcp_tools import ToolContext

    reviewer_messages: list[str] = []

    async def fake_agent(state, agent_type, **_kwargs):
        if agent_type == "writer":
            state["repeat_read_guard"]._record_write("draft-1", "第一章")
            ToolContext.record_author_edit_refusal("outline-1")
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第一章改好了，我再通读核对一遍。"})
            yield _handoff("quality_reviewer", "本轮改了第一章；大纲是作者手改过的，没改成")
            return
        reviewer_messages.append(str(state.get("user_message") or ""))
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第一章改好了。[TASK_COMPLETE]"})

    ToolContext.set_context(
        session=None, user_id="u", project_id="p", session_id="s", author_edit_refusals=AuthorEditRefusals()
    )
    router = AsyncMock(return_value={"current_agent": "writer", "workflow_plan": "quick", "workflow_agents": []})
    try:
        with (
            patch.dict("os.environ", {"AGENT_ENABLE_GRAPH_AUTO_REVIEW": "false"}),
            patch("agent.graph.writing_graph.router_node", router),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
            patch("agent.tools.mcp_tools.ToolContext.refresh_file_inventory", return_value={}),
            patch("agent.graph.writing_graph._auto_finalize_task_board_on_completion", AsyncMock(return_value=[])),
            patch("agent.graph.writing_graph._review_payload_text", AsyncMock(return_value=None)),
            patch("agent.graph.writing_graph._review_auto_checks", AsyncMock(return_value=None)),
        ):
            _ = [
                event
                async for event in run_writing_workflow_streaming(
                    state={"user_message": "按新来历修改", "messages": [], "system_prompt": ""},
                    thread_id="thinking-loop-refusal",
                    auto_review_threshold=100,
                )
            ]
    finally:
        ToolContext.clear_context()

    assert len(reviewer_messages) == 1
    assert "outline-1" in reviewer_messages[0]
    # 2026-10-12 起只有删除 / 整份重写会被拒；只说发生了什么，修改照常用 edit_file。
    assert "删除或整份重写" in reviewer_messages[0] and "不要因此要求返工" in reviewer_messages[0]
    assert "工具不会修改" not in reviewer_messages[0]
    # 229 后聊天同意不会解除删除/同名覆写保护；普通文本编辑不是一律禁止。
    assert "删除请引导作者使用现有文件菜单" in reviewer_messages[0]
    assert "不要以再次聊天确认重试被保护的删除或同名覆写" in reviewer_messages[0]
    assert "常规文本编辑仍按现有读写保护执行" in reviewer_messages[0]
    assert "确实要删或整份重写时在给作者的回复里问作者" not in reviewer_messages[0]


@pytest.mark.asyncio
async def test_refusal_notice_skips_files_written_later_in_the_request():
    """同一文件先被拒（replace），后来改成追加写成功了：它已经改过，不能再说「没改成、别要求改」。"""
    from agent.graph.writing_graph import run_writing_workflow_streaming
    from agent.tools.author_edit_guard import AuthorEditRefusals
    from agent.tools.mcp_tools import ToolContext

    reviewer_messages: list[str] = []

    async def fake_agent(state, agent_type, **_kwargs):
        if agent_type == "writer":
            ToolContext.record_author_edit_refusal("chapter-x")
            state["repeat_read_guard"]._record_write("chapter-x", "第十章")
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第十章续写好了。"})
            yield _handoff("quality_reviewer", "本轮在第十章末尾续写了一段")
            return
        reviewer_messages.append(str(state.get("user_message") or ""))
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第十章续写好了。[TASK_COMPLETE]"})

    ToolContext.set_context(
        session=None, user_id="u", project_id="p", session_id="s", author_edit_refusals=AuthorEditRefusals()
    )
    router = AsyncMock(return_value={"current_agent": "writer", "workflow_plan": "quick", "workflow_agents": []})
    try:
        with (
            patch.dict("os.environ", {"AGENT_ENABLE_GRAPH_AUTO_REVIEW": "false"}),
            patch("agent.graph.writing_graph.router_node", router),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
            patch("agent.tools.mcp_tools.ToolContext.refresh_file_inventory", return_value={}),
            patch("agent.graph.writing_graph._auto_finalize_task_board_on_completion", AsyncMock(return_value=[])),
            patch("agent.graph.writing_graph._review_payload_text", AsyncMock(return_value=None)),
            patch("agent.graph.writing_graph._review_auto_checks", AsyncMock(return_value=None)),
        ):
            _ = [
                event
                async for event in run_writing_workflow_streaming(
                    state={"user_message": "继续写第十章", "messages": [], "system_prompt": ""},
                    thread_id="thinking-loop-refusal-then-write",
                    auto_review_threshold=100,
                )
            ]
    finally:
        ToolContext.clear_context()

    assert len(reviewer_messages) == 1
    assert "chapter-x" in reviewer_messages[0]  # 读写记录里是「已修改」
    assert "没有改成的文件" not in reviewer_messages[0]


def test_every_role_reply_protocol_accepts_the_system_word_count():
    """审稿提示让它用 [待审查内容] 里系统标出的编辑器字数、不为字数读文件；每个角色都遵守的回复规则
    不能还说「只报工具返回的 word_count，工具没给就不报」——两条规则冲突，审稿人又会为字数来回权衡。"""
    from agent.prompts.base import AUTHOR_REPLY_PROTOCOL
    from agent.prompts.subagents import QUALITY_REVIEWER_PROMPT

    assert "[本轮正文字数]" in QUALITY_REVIEWER_PROMPT
    assert "系统标出的数" in AUTHOR_REPLY_PROTOCOL
    assert "工具没给字数就不报字数" not in AUTHOR_REPLY_PROTOCOL
    # live run 2 场景 F：规则里引的标签「编辑器字数」被规划师照抄成「（编辑器字数 244）」，
    # 而那个文件刚在同一次回复里流式写完、还没有工具结果。回复规则不引这个标签。
    assert "编辑器字数" not in AUTHOR_REPLY_PROTOCOL
    assert "刚用 <file> 写的文件" in AUTHOR_REPLY_PROTOCOL
