"""先写正文、再 create_file 建它时，正文落进它自己的文件。

生产 10-26（会话固定到备用模型后连写十五章）：每条回复都是
「<file>第N章正文</file> + create_file(第N章)」。没有在等正文的文件，第N章正文作为对话放出，
第N章空文件随后接住了下一条回复里第N+1章的正文——第64到第75章各存着下一章，
第76章与第75章重复，第64章原稿丢失。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from openai.types.responses import ResponseOutputMessage, ResponseOutputText

from agent.core.events import EventType
from agent.core.stream_processor import StreamProcessor, extract_reply_prebody
from agent.core.workflow_events import StreamEvent as WorkflowStreamEvent
from agent.core.workflow_events import StreamEventType
from agent.tools.mcp_tools import ToolContext, create_file


def _reply(text: str) -> SimpleNamespace:
    message = ResponseOutputMessage(
        id="msg-1",
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )
    return SimpleNamespace(output=[message], usage=SimpleNamespace(total_tokens=1))


def _hooks():
    from agent.openai_agents.usage_hooks import build_agent_run_hooks

    return build_agent_run_hooks("deepseek-flash")


async def _model_reply(hooks, text: str) -> None:
    """一次模型调用：开始、结束（工具在 on_llm_end 之后执行）。"""
    await hooks.on_llm_start(None, None, None, [])
    await hooks.on_llm_end(None, SimpleNamespace(model=None), _reply(text))


def _parse(payload):
    import json

    return json.loads(payload["content"][0]["text"])


def _make_project(db_session):
    from models import Project, User

    suffix = uuid4().hex[:8]
    user = User(email=f"prebody-{suffix}@example.com", username=f"prebody_{suffix}", hashed_password="hashed",
                email_verified=True, is_active=True)
    db_session.add(user)
    db_session.commit()
    project = Project(name=f"P {suffix}", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    return user, project


@pytest.mark.unit
def test_lone_file_block_without_pending_file_is_a_claimable_body():
    assert extract_reply_prebody("接着写第64章。\n<file>\n雨下了一夜。\n</file>\n") == "雨下了一夜。\n"
    # 行内提到、没闭合的都不算
    assert extract_reply_prebody("用 <file> 和 </file> 包住正文。") is None
    assert extract_reply_prebody("<file>\n写到一半") is None
    # 没开启认领的处理器照旧：标记丢掉，正文进对话
    legacy = StreamProcessor().process_content("<file>\n雨下了一夜。\n</file>\n")
    assert legacy.conversation_content == "雨下了一夜。\n"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_written_before_its_create_file_is_claimed_by_that_create(db_session):
    user, project = _make_project(db_session)
    ToolContext.set_context(session=db_session, user_id=user.id, project_id=project.id, session_id="s-prebody")
    hooks = _hooks()
    try:
        await _model_reply(hooks, "<file>\n第64章正文\n</file>\n")
        ch64 = _parse(await create_file({"title": "第64章", "file_type": "draft"}))
        # 下一条回复照样先写第65章正文、再建第65章：第64章不挡路
        await _model_reply(hooks, "<file>\n第65章正文\n</file>\n")
        ch65 = _parse(await create_file({"title": "第65章", "file_type": "draft"}))
        # 正文只认领一次：同一回复里再建一个空文件，它照常等自己的正文
        ch66 = _parse(await create_file({"title": "第66章", "file_type": "draft"}))
    finally:
        ToolContext.clear_pending_empty_file()
        ToolContext.clear_context()

    for payload in (ch64, ch65):
        assert payload["status"] == "success"
        assert payload["data"]["body_in_reply"] is True
        assert "awaiting_body" not in payload["data"]
    assert ch66["data"]["awaiting_body"] is True
    assert "body_in_reply" not in ch66["data"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_for_the_file_still_waiting_is_not_claimed_by_the_next_create(db_session):
    """两次回复协议的连写照旧：等正文的文件在前，这段正文是它的，下一个文件照常等正文。"""
    user, project = _make_project(db_session)
    ToolContext.set_context(session=db_session, user_id=user.id, project_id=project.id, session_id="s-chain")
    hooks = _hooks()
    try:
        await _model_reply(hooks, "")
        ep1 = _parse(await create_file({"title": "第1集", "file_type": "draft"}))
        await _model_reply(hooks, "<file>\n第1集正文\n</file>\n")
        ep2 = _parse(await create_file({"title": "第2集", "file_type": "draft"}))
    finally:
        ToolContext.clear_pending_empty_file()
        ToolContext.clear_context()

    assert ep1["data"]["awaiting_body"] is True
    assert ep2["status"] == "success"
    assert ep2["data"]["awaiting_body"] is True
    assert "body_in_reply" not in ep2["data"]


def _create_result(file_id: str, title: str, *, body_in_reply: bool) -> WorkflowStreamEvent:
    import json

    data = {"id": file_id, "title": title, "file_type": "draft", "content": "", "updated_at": "v1"}
    if body_in_reply:
        data["body_in_reply"] = True
    else:
        data["awaiting_body"] = True
    return WorkflowStreamEvent(
        type=StreamEventType.TOOL_RESULT,
        data={
            "name": "create_file",
            "tool_use_id": f"call-{file_id}",
            "result": {"content": [{"type": "text", "text": json.dumps({"status": "success", "data": data})}]},
        },
    )


def _text(text: str) -> WorkflowStreamEvent:
    return WorkflowStreamEvent(type=StreamEventType.TEXT, data={"text": text})


@pytest.mark.asyncio
@pytest.mark.unit
async def test_adapter_saves_each_body_into_the_file_created_after_it():
    from agent.stream_adapter import create_stream_adapter

    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    adapter._save_file_content = AsyncMock(return_value=True)

    async def events():
        yield _text("继续往下写。\n<fi")
        yield _text("le>\n第64章正文")
        yield _text("\n</file>\n")
        yield _create_result("ch64", "第64章", body_in_reply=True)
        yield _text("<file>\n第65章正文\n</file>\n")
        yield _create_result("ch65", "第65章", body_in_reply=True)
        yield _text("两章都写好了。")
        yield WorkflowStreamEvent(type=StreamEventType.MESSAGE_END, data={})

    out = [e async for e in adapter.process_workflow_events(events())]

    saved = [call.args[:2] for call in adapter._save_file_content.await_args_list]
    assert saved == [("ch64", "第64章正文\n"), ("ch65", "第65章正文\n")]
    chat = "".join(e.data.get("text", "") for e in out if e.type == EventType.CONTENT)
    assert chat == "继续往下写。\n两章都写好了。"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_adapter_returns_an_unclaimed_body_to_the_chat():
    """工具没确认正文属于新文件时不猜：正文照旧作为对话放出，新文件等下一次回复的 <file>。"""
    from agent.stream_adapter import create_stream_adapter

    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    adapter._save_file_content = AsyncMock(return_value=True)

    async def events():
        yield _text("<file>\n一段正文\n</file>\n")
        yield _create_result("ch1", "第1章", body_in_reply=False)
        yield _text("<file>\n第1章正文\n</file>\n")
        yield WorkflowStreamEvent(type=StreamEventType.MESSAGE_END, data={})

    out = [e async for e in adapter.process_workflow_events(events())]

    saved = [call.args[:2] for call in adapter._save_file_content.await_args_list]
    assert saved == [("ch1", "第1章正文\n")]
    chat = "".join(e.data.get("text", "") for e in out if e.type == EventType.CONTENT)
    assert chat.strip() == "一段正文"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_chaining_body_saved_before_on_llm_end_is_not_claimed_by_the_next_file(db_session):
    """StreamAdapter saves A as soon as </file> streams and clears A's entry, usually before on_llm_end;
    A's body must still not be offered to create_file(B)."""
    user, project = _make_project(db_session)
    ToolContext.set_context(session=db_session, user_id=user.id, project_id=project.id, session_id="s-race")
    hooks = _hooks()
    try:
        await _model_reply(hooks, "")
        ep1 = _parse(await create_file({"title": "第1集", "file_type": "draft"}))
        await hooks.on_llm_start(None, None, None, [])
        ToolContext.clear_pending_empty_file(ep1["data"]["id"])  # the adapter saved 第1集 mid-reply
        await hooks.on_llm_end(None, SimpleNamespace(model=None), _reply("<file>\n第1集正文\n</file>\n"))
        ep2 = _parse(await create_file({"title": "第2集", "file_type": "draft"}))
    finally:
        ToolContext.clear_pending_empty_file()
        ToolContext.clear_context()

    assert ep2["data"]["awaiting_body"] is True
    assert "body_in_reply" not in ep2["data"]


def _tool_result(name: str = "query_files") -> WorkflowStreamEvent:
    import json

    return WorkflowStreamEvent(
        type=StreamEventType.TOOL_RESULT,
        data={"name": name, "tool_use_id": "call-q",
              "result": {"content": [{"type": "text", "text": json.dumps({"status": "success", "data": {}})}]}},
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_a_reply_starts_on_a_new_line_even_when_the_previous_reply_did_not_end_with_one():
    """on_llm_end judges each reply on its own; the adapter must too, or the claimed body is lost."""
    from agent.stream_adapter import create_stream_adapter

    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    adapter._save_file_content = AsyncMock(return_value=True)

    async def events():
        yield _text("我先看看目录。")
        yield _tool_result()
        yield _text("<file>\n第二章正文\n</file>\n")
        yield _create_result("ch2", "第二章", body_in_reply=True)
        yield WorkflowStreamEvent(type=StreamEventType.MESSAGE_END, data={})

    out = [e async for e in adapter.process_workflow_events(events())]

    assert [call.args[:2] for call in adapter._save_file_content.await_args_list] == [("ch2", "第二章正文\n")]
    chat = "".join(e.data.get("text", "") for e in out if e.type == EventType.CONTENT)
    assert chat == "我先看看目录。"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_text_after_an_unclaimed_body_stays_after_it():
    from agent.stream_adapter import create_stream_adapter

    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)

    async def events():
        yield _text("示例：\n<file>\n正文X\n</file>\n以上是格式。")
        yield WorkflowStreamEvent(type=StreamEventType.MESSAGE_END, data={})

    out = [e async for e in adapter.process_workflow_events(events())]

    chat = "".join(e.data.get("text", "") for e in out if e.type == EventType.CONTENT)
    assert chat == "示例：\n正文X\n以上是格式。"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_a_claim_without_a_held_body_does_not_capture_the_next_reply_into_that_file():
    """If the two sides ever disagree, the next chapter's body must not land in the claimed file."""
    from agent.stream_adapter import create_stream_adapter

    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    adapter._save_file_content = AsyncMock(return_value=True)

    async def events():
        yield _create_result("ch2", "第二章", body_in_reply=True)
        yield _text("<file>\n第三章正文\n</file>\n")
        yield _create_result("ch3", "第三章", body_in_reply=True)
        yield WorkflowStreamEvent(type=StreamEventType.MESSAGE_END, data={})

    _ = [e async for e in adapter.process_workflow_events(events())]

    assert [call.args[:2] for call in adapter._save_file_content.await_args_list] == [("ch3", "第三章正文\n")]


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("first_tool", ["update_project", "folder"])
async def test_a_body_waits_for_the_create_file_behind_other_tools_of_the_same_reply(first_tool):
    """The tool side lets the first empty-file create_file of the reply claim the body even after other
    tools (update_project, creating a folder); the adapter must still be holding it then."""
    import json

    from agent.stream_adapter import create_stream_adapter

    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    adapter._save_file_content = AsyncMock(return_value=True)
    if first_tool == "folder":
        folder = {"id": "vol2", "title": "第二卷", "file_type": "folder", "content": ""}
        before = WorkflowStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={"name": "create_file", "tool_use_id": "call-vol",
                  "result": {"content": [{"type": "text", "text": json.dumps({"status": "success", "data": folder})}]}},
        )
    else:
        before = _tool_result("update_project")

    async def events():
        yield _text("<file>\n第二章正文\n</file>\n好了。")
        yield before
        yield _create_result("ch2", "第二章", body_in_reply=True)
        yield _text("第二章写好了。")
        yield WorkflowStreamEvent(type=StreamEventType.MESSAGE_END, data={})

    out = [e async for e in adapter.process_workflow_events(events())]

    assert [call.args[:2] for call in adapter._save_file_content.await_args_list] == [("ch2", "第二章正文\n")]
    chat = "".join(e.data.get("text", "") for e in out if e.type == EventType.CONTENT)
    assert chat == "好了。第二章写好了。"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_an_unclaimed_body_is_released_when_the_next_reply_starts():
    from agent.stream_adapter import create_stream_adapter

    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)

    async def events():
        yield _text("<file>\n一段正文\n</file>\n")
        yield _tool_result("query_files")
        yield _text("接着说。")
        yield WorkflowStreamEvent(type=StreamEventType.MESSAGE_END, data={})

    out = [e async for e in adapter.process_workflow_events(events())]

    chat = "".join(e.data.get("text", "") for e in out if e.type == EventType.CONTENT)
    assert chat == "一段正文\n接着说。"
