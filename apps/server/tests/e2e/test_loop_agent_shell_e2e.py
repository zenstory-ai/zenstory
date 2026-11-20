"""单循环 agent 走完整外壳：路由层 → AgentService → loop engine → runner → 真实工具 → StreamAdapter → 落库 / 计费。

模型换成按脚本输出的 SDK 事件流（tests/test_agent/test_file_body_resume.ScriptedModel），工具调用走 SDK Agent 上
真实的 FunctionTool（write_chapter / edit_file 等）。覆盖：整章替换与 edit_file 的 diff / 撤销元数据、撤销接口、
作者停止（有无产出的计费与 stop_outcome 回执）、请求时限、工具轮数耗尽的失控退还。正文是合成文字，不调用付费模型。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select
from starlette.requests import Request

import api.agent as agent_api
from models import ChatMessage, File, Project, User
from services.core.auth_service import hash_password
from services.features.file_version_service import FileVersionService
from tests.test_agent.test_file_body_resume import Reply, ScriptedModel

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("writing_prompt_configs")]

_SYNTHETIC_REQUEST = Request({"type": "http", "headers": []})
NEW_BODY = "\n".join(f"第{index}段合成新稿，只用于核对单循环外壳。" for index in range(1, 6))


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for chunk in text.split("\n\n"):
        name, payload = "", {}
        for line in chunk.splitlines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload = json.loads(line.split(":", 1)[1].strip())
        if name:
            events.append((name, payload))
    return events


@pytest.fixture
def shell_db(monkeypatch, db_session):
    """服务与工具自己开的 session 落在测试库上；不记模型用量。"""
    import database
    from services.features.activation_event_service import activation_event_service

    engine = db_session.get_bind()

    def get_session():
        session = Session(engine)
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(database, "get_session", get_session)
    monkeypatch.setattr(database, "create_session", lambda: Session(engine))
    monkeypatch.setattr("agent.service.create_session", lambda: Session(engine))
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_k: None)
    return engine


@pytest.fixture
def scripted():
    """把模型换成脚本：run 的每次模型调用按 Reply 输出，工具真实执行。"""

    def install(model: ScriptedModel):
        return (
            patch("agents.Runner.run_streamed", side_effect=model.run_streamed),
            patch("agent.openai_agents.runner.build_failover_chat_model", return_value="synthetic-model"),
            patch("agent.openai_agents.runner._preferred_model_name", AsyncMock(return_value="synthetic-model")),
            patch("agent.openai_agents.usage_hooks.schedule_llm_usage_record"),
        )

    return install


async def _login(client: AsyncClient, db_session: Session) -> tuple[User, str, Project]:
    suffix = uuid4().hex[:8]
    user = User(
        username=f"loop_shell_{suffix}",
        email=f"loop_shell_{suffix}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    project = Project(name="Loop Shell", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    login = await client.post("/api/auth/login", data={"username": user.username, "password": "password123"})
    assert login.status_code == 200
    return user, login.json()["access_token"], project


def _ai_chapter(db_session: Session, project: Project, title: str, content: str) -> File:
    file = File(project_id=project.id, title=title, content=content, file_type="draft")
    db_session.add(file)
    db_session.flush()
    FileVersionService().create_version(
        db_session, file.id, content, change_type="create", change_source="ai", commit=False, skip_quota=True
    )
    db_session.commit()
    db_session.refresh(file)
    return file


def _assistant_row(db_session: Session, project: Project) -> ChatMessage:
    db_session.expire_all()
    rows = db_session.exec(select(ChatMessage).where(ChatMessage.role == "assistant")).all()
    [row] = [r for r in rows if r.session_id]  # 每个用例一个会话、一条助手消息
    return row


def _content(db_session: Session, file_id: str) -> str:
    db_session.expire_all()
    return db_session.get(File, file_id).content or ""


async def _post(client: AsyncClient, token: str, project: Project, message: str):
    return await client.post(
        "/api/v1/agent/stream",
        json={"project_id": project.id, "message": message},
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_loop_author_material_source_is_copied_exactly_and_charged(
    client: AsyncClient, db_session: Session, shell_db, scripted
):
    """HTTP raw message -> SDK tool -> real create -> SSE/history keeps exact author bytes."""
    from services.quota_service import quota_service

    _user, token, project = await _login(client, db_session)
    source = (
        "《旧港残信》三章大纲\n"
        "第一章 退信：邮局分拣员苏砚收到一封旧信。\n"
        "第二章 暗格：苏砚找到钟表行学徒陈默。\n\n"
        "陈默：28岁，话少手稳，对十年前的火灾讳莫如深。"
    )
    message = f"请把下面资料保存到项目中，保持原样：\n\n{source}\n\n保存后先别续写。"
    model = ScriptedModel(
        runs=[[
            Reply(
                text="",
                tools=(("create_file", {"title": "旧港残信", "file_type": "outline", "source_text": source}),),
            ),
            Reply(text="资料已按作者原文保存。"),
        ]]
    )

    real_release = quota_service.release_ai_conversation
    patches = scripted(model)
    with patches[0], patches[1], patches[2], patches[3], patch.object(
        quota_service, "release_ai_conversation", side_effect=real_release
    ) as release:
        response = await _post(client, token, project, message)

    events = _parse_sse(response.text)
    names = [name for name, _ in events]
    assert response.status_code == 200
    assert "error" not in names and names.count("done") == 1
    assert "quota_refunded" not in names
    release.assert_not_called()
    [tool_result] = [payload for name, payload in events if name == "tool_result"]
    assert tool_result["status"] == "success"
    assert tool_result["data"]["source_copied"] is True
    assert tool_result["data"]["body_saved"] is True
    assert tool_result["data"]["content"] == source
    done = [payload for name, payload in events if name == "done"][0]
    assert done["file_mutated"] is True

    [saved] = db_session.exec(
        select(File).where(File.project_id == project.id, File.title == "旧港残信", File.is_deleted.is_(False))
    ).all()
    assert saved.file_type == "outline"
    assert saved.content == source

    row = _assistant_row(db_session, project)
    [card] = [call for call in json.loads(row.tool_calls) if call["name"] == "create_file"]
    assert card["arguments"]["source_text"] == source
    assert card["result"]["source_copied"] is True
    assert card["result"]["body_saved"] is True
    assert card["result"]["content"] == source


# ---------------------------------------------------------------------------
# diff / 撤销元数据
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_chapter_replacement_round_persists_an_undoable_card_and_is_charged(
    client: AsyncClient, db_session: Session, shell_db, scripted
):
    from services.quota_service import quota_service

    user, token, project = await _login(client, db_session)
    one = _ai_chapter(db_session, project, "剑来 - 第1章 雨夜", "合成第一章原文。")
    three = _ai_chapter(db_session, project, "剑来 - 第3章 灯塔", "合成第三章原文。")
    model = ScriptedModel(runs=[[
        Reply(text="", tools=(("write_chapter", {"chapter": 3, "title": "第3章 雾散", "file_type": "draft"}),)),
        Reply(text=f"<file>\n{NEW_BODY}\n</file>\n第三章换好了。"),
    ]])

    real_release = quota_service.release_ai_conversation
    patches = scripted(model)
    with patches[0], patches[1], patches[2], patches[3], patch.object(
        quota_service, "release_ai_conversation", side_effect=real_release
    ) as release:
        response = await _post(client, token, project, "把第三章换成新稿并改名雾散")

    events = _parse_sse(response.text)
    names = [name for name, _ in events]
    assert "error" not in names and names.count("done") == 1
    release.assert_not_called()
    [edit_end] = [payload for name, payload in events if name == "file_edit_end"]
    assert edit_end["original_content"] == "合成第三章原文。"
    assert edit_end["new_content"].rstrip("\n") == NEW_BODY
    done = [payload for name, payload in events if name == "done"][0]
    assert done["file_mutated"] is True
    assert _content(db_session, one.id) == "合成第一章原文。"
    assert _content(db_session, three.id).rstrip("\n") == NEW_BODY
    db_session.expire_all()
    assert db_session.get(File, three.id).title == "剑来 - 第3章 雾散"

    row = _assistant_row(db_session, project)
    [card] = [call for call in json.loads(row.tool_calls) if call["name"] == "write_chapter"]
    assert card["result"]["action"] == "replaced" and card["result"]["body_saved"] is True
    undo = card["result"]["undo"]
    assert undo["before_title"] == "剑来 - 第3章 灯塔"

    # 撤销接口按卡上的锚点同时恢复原稿正文与改名前标题，并把这张卡标成已撤销。
    rollback = await client.post(
        f"/api/v1/files/{three.id}/versions/{undo['before_version_number']}/rollback",
        json={
            "expected_updated_at": undo["expected_after_updated_at"],
            "chat_message_id": row.id,
            "tool_use_id": card["id"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["undo_recorded"] is True
    assert _content(db_session, three.id) == "合成第三章原文。"
    db_session.expire_all()
    assert db_session.get(File, three.id).title == "剑来 - 第3章 灯塔"


@pytest.mark.asyncio
async def test_loop_edit_file_round_persists_undo_metadata(
    client: AsyncClient, db_session: Session, shell_db, scripted
):
    _user, token, project = await _login(client, db_session)
    chapter = _ai_chapter(db_session, project, "第1章 雨夜", "雨下了一夜。\n灯一直亮着。")
    model = ScriptedModel(runs=[[
        Reply(text="", tools=(("query_files", {"id": chapter.id}),)),
        Reply(text="", tools=(("edit_file", {"id": chapter.id, "edits": [{"op": "replace", "old": "灯一直亮着。", "new": "灯熄了。"}]}),)),
        Reply(text="改好了第二句。"),
    ]])

    patches = scripted(model)
    with patches[0], patches[1], patches[2], patches[3]:
        response = await _post(client, token, project, "把第二句改成灯熄了")

    names = [name for name, _ in _parse_sse(response.text)]
    assert "file_edit_end" in names and "error" not in names
    assert _content(db_session, chapter.id) == "雨下了一夜。\n灯熄了。"
    row = _assistant_row(db_session, project)
    [card] = [call for call in json.loads(row.tool_calls) if call["name"] == "edit_file"]
    assert isinstance(card["result"]["undo"]["before_version_number"], int)
    assert card["result"]["undo"]["expected_after_updated_at"]


# ---------------------------------------------------------------------------
# 停止、时限、失控退还
# ---------------------------------------------------------------------------


async def _start(db_session: Session, user: User, project: Project, message: str):
    body = agent_api.AgentRequest(project_id=str(project.id), message=message)
    return await agent_api.stream_request(
        _SYNTHETIC_REQUEST,
        body=body,
        session=db_session,
        current_user=user,
        accept_language=None,
        client_stop_contract="1",
        _rate_limit=0,
    )


async def _read_until(iterator: Any, event_name: str, received: list[str]) -> None:
    while True:
        frame = await asyncio.wait_for(iterator.__anext__(), timeout=10)
        received.append(frame)
        if frame.startswith(f"event: {event_name}"):
            return


@pytest.mark.asyncio
@pytest.mark.parametrize("wrote_body", [False, True])
async def test_loop_author_stop_settles_billing_and_records_the_stop_outcome(
    client: AsyncClient, db_session: Session, shell_db, scripted, wrote_body
):
    from services.quota_service import quota_service

    user, _token, project = await _login(client, db_session)
    hang = asyncio.Event()
    if wrote_body:
        replies = [
            Reply(text="", tools=(("write_chapter", {"chapter": 1, "title": "雨夜", "file_type": "draft"}),)),
            Reply(text=f"<file>\n{NEW_BODY}\n</file>\n", hang=hang),
        ]
        wait_for = "file_content_end"
    else:
        replies = [Reply(text="我先看一下。", hang=hang)]
        wait_for = "content"
    model = ScriptedModel(runs=[replies])

    real_release = quota_service.release_ai_conversation
    patches = scripted(model)
    received: list[str] = []
    with patches[0], patches[1], patches[2], patches[3], patch.object(
        quota_service, "release_ai_conversation", side_effect=real_release
    ) as release:
        response = await _start(db_session, user, project, "写第一章")
        iterator = response.body_iterator
        await _read_until(iterator, wait_for, received)
        stopped = await agent_api.stop_stream(
            body=agent_api.StopRequest(agent_run_id=response.headers["X-Agent-Run-ID"]),
            current_user=user,
            _rate_limit=0,
        )
        assert stopped.stop_requested is True
        received.extend([frame async for frame in iterator])
        await asyncio.gather(*agent_api._round_outcome_tasks)

    events = _parse_sse("".join(received))
    done = [payload for name, payload in events if name == "done"][-1]
    assert done["stop_reason"] == "user_stopped"
    assert done["charged"] is True
    row = _assistant_row(db_session, project)
    metadata = json.loads(row.message_metadata)
    assert metadata["engine"] == "loop" and metadata["stop_reason"] == "user_stopped"
    outcome = metadata["stop_outcome"]
    assert outcome["reason"] == "user_stopped"
    release.assert_not_called()
    assert outcome["charged"] is True
    assert [payload for name, payload in events if name == "quota_refunded"] == []
    if wrote_body:
        assert outcome["saved_output"] is True
        [written] = outcome["written_files"]
        assert written["title"] == "第1章 雨夜" and written["words"] > 0
    else:
        assert outcome["saved_output"] is False


@pytest.mark.asyncio
async def test_loop_wall_clock_deadline_without_output_ends_with_timeout_and_charges_once_model_started(
    client: AsyncClient, db_session: Session, shell_db, scripted, monkeypatch
):
    from services.quota_service import quota_service

    user, _token, project = await _login(client, db_session)
    monkeypatch.setattr(agent_api, "AGENT_RUN_WALL_CLOCK_TIMEOUT_S", 0.5)
    monkeypatch.setattr(agent_api, "AGENT_SSE_HEARTBEAT_INTERVAL_S", 0.1)
    model = ScriptedModel(runs=[[Reply(text="", hang=asyncio.Event())]])

    real_release = quota_service.release_ai_conversation
    patches = scripted(model)
    with patches[0], patches[1], patches[2], patches[3], patch.object(
        quota_service, "release_ai_conversation", side_effect=real_release
    ) as release:
        response = await _start(db_session, user, project, "写第一章")
        text = "".join([frame async for frame in response.body_iterator])
        await asyncio.gather(*agent_api._round_outcome_tasks)

    assert "ERR_AGENT_RUN_TIMEOUT" in text
    release.assert_not_called()
    assert "event: quota_refunded" not in text


@pytest.mark.asyncio
async def test_loop_tool_turn_exhaustion_without_writes_is_charged_once_model_started(
    client: AsyncClient, db_session: Session, shell_db, scripted
):
    from services.quota_service import quota_service

    _user, token, project = await _login(client, db_session)
    chapter = _ai_chapter(db_session, project, "第1章 雨夜", "合成第一章原文。")
    model = ScriptedModel(runs=[[
        Reply(text="", tools=(("query_files", {"id": chapter.id}),), exceeds_max_turns=True),
    ]])

    real_release = quota_service.release_ai_conversation
    patches = scripted(model)
    with patches[0], patches[1], patches[2], patches[3], patch.object(
        quota_service, "release_ai_conversation", side_effect=real_release
    ) as release:
        response = await _post(client, token, project, "把所有章节都检查一遍")

    events = _parse_sse(response.text)
    [card] = [payload for name, payload in events if name == "iteration_exhausted"]
    assert card["layer"] == "tool_call" and card.get("last_agent") in (None, "")
    release.assert_not_called()
    assert [payload for name, payload in events if name == "quota_refunded"] == []


@pytest.mark.asyncio
async def test_loop_steering_during_a_run_is_taken_up_in_the_same_turn(
    client: AsyncClient, db_session: Session, shell_db, scripted
):
    user, token, project = await _login(client, db_session)
    hang = asyncio.Event()
    model = ScriptedModel(runs=[
        [Reply(text="我先想想第一章的开头。", hang=hang)],
        [Reply(text="好的，改成先写第二章的提纲。")],
    ])

    patches = scripted(model)
    received: list[str] = []
    with patches[0], patches[1], patches[2], patches[3]:
        response = await _start(db_session, user, project, "写第一章")
        iterator = response.body_iterator
        await _read_until(iterator, "content", received)
        [session_id] = [payload["session_id"] for name, payload in _parse_sse("".join(received)) if name == "session_started"]
        steer = await client.post(
            "/api/v1/agent/steer",
            json={"session_id": session_id, "message": "先别写第一章，改写第二章提纲"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert steer.status_code == 200, steer.text
        hang.set()
        received.extend([frame async for frame in iterator])

    events = _parse_sse("".join(received))
    names = [name for name, _ in events]
    assert "steering_received" in names and names.count("done") == 1
    # 插话让同一个 agent 在本轮再跑一次：第二次 run 的输入里有作者插的话。
    assert len(model.run_inputs) == 2
    assert "先别写第一章，改写第二章提纲" in json.dumps(model.run_inputs[1], ensure_ascii=False)
    db_session.expire_all()
    rows = db_session.exec(select(ChatMessage).where(ChatMessage.session_id == session_id)).all()
    assert any(r.role == "user" and "改写第二章提纲" in (r.content or "") for r in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["user_stopped", "client_disconnected", "deadline"])
async def test_loop_replacement_cut_off_mid_body_leaves_the_chapter_and_is_charged(
    client: AsyncClient, db_session: Session, shell_db, scripted, monkeypatch, ending
):
    """整章替换的正文流到一半就停止 / 断线 / 到时：原文不变，但本轮已进入模型执行，照常计费不退还。"""
    from services.quota_service import quota_service

    user, _token, project = await _login(client, db_session)
    three = _ai_chapter(db_session, project, "第3章 灯塔", "合成第三章原文。")
    if ending == "deadline":
        monkeypatch.setattr(agent_api, "AGENT_RUN_WALL_CLOCK_TIMEOUT_S", 1.0)
        monkeypatch.setattr(agent_api, "AGENT_SSE_HEARTBEAT_INTERVAL_S", 0.1)
    hang = asyncio.Event()
    model = ScriptedModel(runs=[[
        Reply(text="", tools=(("write_chapter", {"chapter": 3, "title": "第三章", "file_type": "draft"}),)),
        Reply(text=f"<file>\n{NEW_BODY}\n{NEW_BODY}", hang=hang),
    ]])

    real_release = quota_service.release_ai_conversation
    patches = scripted(model)
    received: list[str] = []
    with patches[0], patches[1], patches[2], patches[3], patch.object(
        quota_service, "release_ai_conversation", side_effect=real_release
    ) as release:
        response = await _start(db_session, user, project, "把第三章换成新稿")
        iterator = response.body_iterator
        await _read_until(iterator, "file_content", received)
        if ending == "user_stopped":
            stopped = await agent_api.stop_stream(
                body=agent_api.StopRequest(agent_run_id=response.headers["X-Agent-Run-ID"]),
                current_user=user,
                _rate_limit=0,
            )
            assert stopped.stop_requested is True
            received.extend([frame async for frame in iterator])
        elif ending == "client_disconnected":
            await iterator.aclose()
            await asyncio.gather(*agent_api._detached_refund_tasks)
        else:
            received.extend([frame async for frame in iterator])
        await asyncio.gather(*agent_api._round_outcome_tasks)

    assert _content(db_session, three.id) == "合成第三章原文。"
    release.assert_not_called()
    text = "".join(received)
    if ending == "deadline":
        assert "ERR_AGENT_RUN_TIMEOUT" in text
    assert "event: quota_refunded" not in text
    if ending == "user_stopped":
        metadata = json.loads(_assistant_row(db_session, project).message_metadata)
        assert metadata["stop_outcome"]["charged"] is True
        assert metadata["stop_outcome"]["saved_output"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["user_stopped", "client_disconnected"])
async def test_loop_replacement_of_an_empty_chapter_cut_off_mid_body_is_charged(
    client: AsyncClient, db_session: Session, shell_db, scripted, ending
):
    """替换一个已有的空白章节时正文流到一半就停止 / 断线：没有原文要保护，流过的正文照常落库，这一轮计费。"""
    from services.quota_service import quota_service

    user, _token, project = await _login(client, db_session)
    three = _ai_chapter(db_session, project, "第3章 灯塔", "")
    hang = asyncio.Event()
    model = ScriptedModel(runs=[[
        Reply(text="", tools=(("write_chapter", {"chapter": 3, "title": "第三章", "file_type": "draft"}),)),
        Reply(text=f"<file>\n{NEW_BODY}\n{NEW_BODY}", hang=hang),
    ]])

    real_release = quota_service.release_ai_conversation
    patches = scripted(model)
    received: list[str] = []
    with patches[0], patches[1], patches[2], patches[3], patch.object(
        quota_service, "release_ai_conversation", side_effect=real_release
    ) as release:
        response = await _start(db_session, user, project, "写第三章")
        iterator = response.body_iterator
        await _read_until(iterator, "file_content", received)
        if ending == "user_stopped":
            stopped = await agent_api.stop_stream(
                body=agent_api.StopRequest(agent_run_id=response.headers["X-Agent-Run-ID"]),
                current_user=user,
                _rate_limit=0,
            )
            assert stopped.stop_requested is True
            received.extend([frame async for frame in iterator])
        else:
            await iterator.aclose()
            await asyncio.gather(*agent_api._detached_refund_tasks)
        await asyncio.gather(*agent_api._round_outcome_tasks)

    for _ in range(50):
        if _content(db_session, three.id).strip():
            break
        await asyncio.sleep(0.05)
    assert "第1段合成新稿" in _content(db_session, three.id)
    release.assert_not_called()
    assert "event: quota_refunded" not in "".join(received)


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["user_stopped", "client_disconnected"])
async def test_loop_replacement_committed_while_the_round_is_cut_off_is_charged(
    client: AsyncClient, db_session: Session, shell_db, scripted, ending
):
    """正文已写到 </file>、整章替换正在落库时停止 / 断线：保存照常完成，这一轮按已写入计费。"""
    import threading

    from agent.stream_adapter import StreamAdapter
    from services.quota_service import quota_service

    user, _token, project = await _login(client, db_session)
    three = _ai_chapter(db_session, project, "第3章 灯塔", "合成第三章原文。")
    save_started = threading.Event()
    allow_save = threading.Event()
    real_replace = StreamAdapter._replace_file_body_sync

    def slow_replace(self, *args, **kwargs):
        save_started.set()
        allow_save.wait(5)
        return real_replace(self, *args, **kwargs)

    model = ScriptedModel(runs=[[
        Reply(text="", tools=(("write_chapter", {"chapter": 3, "title": "第三章", "file_type": "draft"}),)),
        Reply(text=f"<file>\n{NEW_BODY}\n</file>\n第三章换好了。"),
    ]])

    real_release = quota_service.release_ai_conversation
    patches = scripted(model)
    received: list[str] = []
    with patches[0], patches[1], patches[2], patches[3], patch.object(
        quota_service, "release_ai_conversation", side_effect=real_release
    ) as release, patch.object(StreamAdapter, "_replace_file_body_sync", slow_replace):
        response = await _start(db_session, user, project, "把第三章换成新稿")
        iterator = response.body_iterator
        await _read_until(iterator, "file_content", received)
        assert await asyncio.to_thread(save_started.wait, 5)
        if ending == "user_stopped":
            stop = asyncio.create_task(agent_api.stop_stream(
                body=agent_api.StopRequest(agent_run_id=response.headers["X-Agent-Run-ID"]),
                current_user=user,
                _rate_limit=0,
            ))
            await asyncio.sleep(0.05)
            allow_save.set()
            assert (await stop).stop_requested is True
            received.extend([frame async for frame in iterator])
        else:
            await iterator.aclose()
            allow_save.set()
            await asyncio.gather(*agent_api._detached_refund_tasks)
        await asyncio.gather(*agent_api._round_outcome_tasks)
        assert _content(db_session, three.id).rstrip("\n") == NEW_BODY
        release.assert_not_called()
        assert "event: quota_refunded" not in "".join(received)
        metadata = json.loads(_assistant_row(db_session, project).message_metadata)
        assert metadata["stop_outcome"]["charged"] is True
        assert metadata["stop_outcome"]["saved_output"] is True
