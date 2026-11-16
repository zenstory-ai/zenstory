"""单循环 agent 的真实链路：HTTP → AgentService → loop engine → runner 事件映射 → StreamAdapter → 落库 / 计费。

只替换 SDK 的模型运行（Runner.run_streamed）与模型对象；不调用任何付费模型。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from models import ChatMessage, File, Project, User
from services.core.auth_service import hash_password

pytestmark = pytest.mark.e2e

_ALLOWED_DISPLAY_TYPES = {"content", "thinking_content", "tool_call", "iteration_exhausted"}


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


def _raw_text(delta: str) -> SimpleNamespace:
    return SimpleNamespace(type="raw_response_event", data=SimpleNamespace(type="response.output_text.delta", delta=delta))


def _tool_called(call_id: str, name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(
        type="run_item_stream_event",
        name="tool_called",
        item=SimpleNamespace(raw_item={"name": name, "call_id": call_id, "arguments": json.dumps(arguments, ensure_ascii=False)}),
    )


def _tool_output(call_id: str, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        type="run_item_stream_event",
        name="tool_output",
        item=SimpleNamespace(raw_item={"call_id": call_id}, output=json.dumps(payload, ensure_ascii=False)),
    )


class _FakeRun:
    """一次 SDK run 的替身：按给定顺序吐事件，最后一次调用的用量写进 raw_responses。"""

    def __init__(self, events: list, *, prompt_tokens: int = 1200):
        self._events = events
        self.raw_responses = [
            SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=prompt_tokens,
                    output_tokens=40,
                    total_tokens=prompt_tokens + 40,
                    input_tokens_details=SimpleNamespace(cached_tokens=200),
                ),
                output=[],
            )
        ]
        self.prompt_tokens = prompt_tokens
        self.hooks = None

    def cancel(self, mode="immediate"):
        return None

    def to_input_list(self):
        return []

    async def stream_events(self):
        if self.hooks is not None:
            # 与真实 SDK 一样：每次模型调用结束先经 on_llm_end（这里只关心实测输入 token）。
            await self.hooks.on_llm_end(
                None, SimpleNamespace(model=None), SimpleNamespace(usage=SimpleNamespace(input_tokens=self.prompt_tokens))
            )
        for event in self._events:
            yield event


async def _user_project(client: AsyncClient, db_session: Session, username: str) -> tuple[User, str, Project]:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    project = Project(name="Loop Synthetic", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    login = await client.post("/api/auth/login", data={"username": username, "password": "password123"})
    assert login.status_code == 200
    return user, login.json()["access_token"], project


@pytest.fixture
def loop_db(monkeypatch, db_session):
    """服务与工具自己开的 session 落在测试库上。"""
    import database

    engine = db_session.get_bind()
    monkeypatch.setattr(database, "create_session", lambda: Session(engine))
    monkeypatch.setattr("agent.service.create_session", lambda: Session(engine))
    monkeypatch.setattr("agent.openai_agents.usage_hooks.schedule_llm_usage_record", lambda *_a, **_k: None)
    return engine


def _patched_runs(runs: list[_FakeRun], captured: list[dict]):
    pending = list(runs)

    def run_streamed(agent, *, input, hooks=None, **kwargs):  # noqa: A002 - SDK keyword
        captured.append({"input": list(input), "agent": agent, **kwargs})
        run = pending.pop(0)
        run.hooks = hooks
        return run

    def build_agent(agent_type, system_prompt, *_a, **_k):
        captured.append({"built": agent_type, "system_prompt": system_prompt})
        return SimpleNamespace(name=agent_type)

    return (
        patch("agent.openai_agents.runner._build_agent", side_effect=build_agent),
        patch("agents.Runner.run_streamed", side_effect=run_streamed),
    )


def _input_text(captured_input: list) -> str:
    return "\n".join(str(item.get("content") or "") for item in captured_input if isinstance(item, dict))


@pytest.mark.asyncio
async def test_loop_round_persists_loop_shaped_row_injects_attachments_and_charges(
    client: AsyncClient, db_session: Session, writing_prompt_configs, loop_engine, loop_db
):
    from models.material_models import Character, Novel

    user, token, project = await _user_project(client, db_session, "loop_round_synthetic")
    focus = File(project_id=project.id, user_id=user.id, title="第1章 雨夜", content="合成正文：灯塔在雨里亮着。", file_type="draft")
    attached = File(project_id=project.id, user_id=user.id, title="人物：守灯人", content="合成设定：守灯人左手有旧伤。", file_type="character")
    novel = Novel(user_id=user.id, title="合成素材书")
    db_session.add_all([focus, attached, novel])
    db_session.commit()
    character = Character(novel_id=novel.id, name="合成素材人物", description="合成素材描述：总在黄昏出现。")
    db_session.add(character)
    db_session.commit()

    captured: list[dict] = []
    run = _FakeRun([
        _raw_text("我先看一下设定。"),
        _tool_called("call-q", "query_files", {"id": attached.id}),
        _tool_output("call-q", {"status": "success", "data": [{"id": attached.id, "title": "人物：守灯人"}]}),
        _raw_text("第1章已经对照设定检查过，没有需要改的地方。"),
    ], prompt_tokens=4321)
    build_agent, run_streamed = _patched_runs([run], captured)
    with build_agent, run_streamed, patch("agent.service.run_writing_workflow_streaming") as graph:
        response = await client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": project.id,
                "message": "检查第1章和设定是否一致",
                "selected_text": "合成选区：灯塔",
                "metadata": {
                    "current_file_id": focus.id,
                    "attached_file_ids": [attached.id],
                    "text_quotes": [{"text": "合成引用：雨一直下", "fileId": focus.id, "fileTitle": "第1章 雨夜"}],
                    "attached_library_materials": [
                        {"novel_id": novel.id, "entity_type": "characters", "entity_id": character.id}
                    ],
                    # 旧客户端仍可能带生成模式：照常接受，单循环 agent 不读它。
                    "generation_mode": "quality",
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    graph.assert_not_called()
    assert response.status_code == 200
    events = _parse_sse(response.text)
    names = [name for name, _ in events]
    assert not set(names) & {"router_thinking", "router_decided", "agent_selected", "handoff", "workflow_complete"}
    assert names.count("done") == 1 and "error" not in names
    assert [name for name, _ in events if name == "quota_refunded"] == []

    # 模型输入：附加文件、焦点文件、引用、素材库条目、选区都在本轮消息里；系统提示是 loop 的那一份。
    [built] = [entry for entry in captured if "built" in entry]
    assert built["built"] == "loop"
    assert "handoff_to_agent" not in built["system_prompt"] and "## 工作方式" in built["system_prompt"]
    [call] = [entry for entry in captured if "input" in entry]
    text = _input_text(call["input"])
    for snippet in ("合成正文：灯塔在雨里亮着。", "合成设定：守灯人左手有旧伤。", "合成引用：雨一直下", "合成素材描述：总在黄昏出现。", "合成选区：灯塔"):
        assert snippet in text
    assert call["input"][-1]["role"] == "user"
    assert call["input"][-1]["content"].endswith("合成选区：灯塔")

    [row] = db_session.exec(
        select(ChatMessage).where(ChatMessage.role == "assistant", ChatMessage.content.contains("检查过"))
    ).all()
    metadata = json.loads(row.message_metadata)
    assert metadata["engine"] == "loop"
    assert metadata["prompt_tokens"] == 4321
    assert "routing" not in metadata and "compaction" not in metadata
    assert {event["type"] for event in metadata["display_events"]} <= _ALLOWED_DISPLAY_TYPES
    assert [event["type"] for event in metadata["display_events"]] == ["content", "tool_call", "content"]
    tool_calls = json.loads(row.tool_calls)
    assert [tc["name"] for tc in tool_calls] == ["query_files"]
    assert metadata["usage"]["cache_read_tokens"] == 200
    done = [payload for name, payload in events if name == "done"][0]
    assert done["assistant_message_id"] == row.id
    assert "no_file_changes" not in done


@pytest.mark.asyncio
async def test_loop_round_without_output_is_refunded(
    client: AsyncClient, db_session: Session, writing_prompt_configs, loop_engine, loop_db
):
    """模型调用直接失败、什么都没产出：error 帧可退还，额度退回（计费规则与图相同）。"""
    from services.quota_service import quota_service

    _user, token, project = await _user_project(client, db_session, "loop_refund_synthetic")

    class _FailingRun(_FakeRun):
        async def stream_events(self):
            raise RuntimeError("synthetic provider failure")
            yield  # pragma: no cover

    captured: list[dict] = []
    build_agent, run_streamed = _patched_runs([_FailingRun([])], captured)
    real_release = quota_service.release_ai_conversation
    with build_agent, run_streamed, patch.object(
        quota_service, "release_ai_conversation", side_effect=real_release
    ) as release:
        response = await client.post(
            "/api/v1/agent/stream",
            json={"project_id": project.id, "message": "写第一章"},
            headers={"Authorization": f"Bearer {token}"},
        )
    events = _parse_sse(response.text)
    names = [name for name, _ in events]
    assert "error" in names and "done" not in names
    [error] = [payload for name, payload in events if name == "error"]
    assert error["refundable"] is True
    release.assert_called_once()
    assert names.count("quota_refunded") == 1


@pytest.mark.asyncio
async def test_loop_tool_turn_exhaustion_card_has_no_agent_and_bills_as_runaway(
    client: AsyncClient, db_session: Session, writing_prompt_configs, loop_engine, loop_db
):
    """工具轮数耗尽：沿用 tool_call 层的暂停卡（失控停止，没有写入就退还），last_agent 为空。"""
    from agents.exceptions import MaxTurnsExceeded

    _user, token, project = await _user_project(client, db_session, "loop_turns_synthetic")

    class _ExhaustedRun(_FakeRun):
        async def stream_events(self):
            yield _raw_text("还在查资料。")
            raise MaxTurnsExceeded("synthetic max turns")

    captured: list[dict] = []
    build_agent, run_streamed = _patched_runs([_ExhaustedRun([])], captured)
    with build_agent, run_streamed:
        response = await client.post(
            "/api/v1/agent/stream",
            json={"project_id": project.id, "message": "把所有章节都检查一遍"},
            headers={"Authorization": f"Bearer {token}"},
        )
    events = _parse_sse(response.text)
    [card] = [payload for name, payload in events if name == "iteration_exhausted"]
    assert card["layer"] == "tool_call"
    assert card.get("last_agent") in (None, "")
    assert "Agent" not in card["reason"]
    row = db_session.exec(
        select(ChatMessage).where(ChatMessage.role == "assistant", ChatMessage.content.contains("还在查资料"))
    ).one()
    metadata = json.loads(row.message_metadata)
    assert metadata["stop_reason"] == "max_turns_exceeded"
    assert {event["type"] for event in metadata["display_events"]} <= _ALLOWED_DISPLAY_TYPES


@pytest.mark.asyncio
async def test_graph_engine_switch_still_runs_the_graph(
    client: AsyncClient, db_session: Session, writing_prompt_configs, monkeypatch, loop_db
):
    from agent.core.workflow_events import StreamEvent, StreamEventType

    monkeypatch.setenv("AGENT_ENGINE", "graph")
    _user, token, project = await _user_project(client, db_session, "graph_switch_synthetic")

    async def graph_stream(*_args, **_kwargs):
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "图在运行"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    with (
        patch("agent.service.run_writing_workflow_streaming", side_effect=graph_stream) as graph,
        patch("agent.service.run_loop_agent_streaming") as loop,
    ):
        response = await client.post(
            "/api/v1/agent/stream",
            json={"project_id": project.id, "message": "继续"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    graph.assert_called_once()
    loop.assert_not_called()
    row = db_session.exec(select(ChatMessage).where(ChatMessage.content == "图在运行")).one()
    assert "engine" not in json.loads(row.message_metadata or "{}")


def _seed_loop_session(db_session: Session, user: User, project: Project, *, prompt_tokens: int):
    """一个已有两轮单循环对话的活跃会话；最后一条 assistant 行记下的实测输入 token 由参数给出。"""
    from datetime import datetime, timedelta

    from models import ChatSession

    chat = ChatSession(user_id=user.id, project_id=project.id, title="loop", is_active=True)
    db_session.add(chat)
    db_session.commit()
    base = datetime(2026, 11, 1, 8, 0, 0)
    rows = []
    for index, (role, content) in enumerate([
        ("user", "合成要求一：主角叫阿岚"),
        ("assistant", "记下了。"),
        ("user", "合成要求二：写第1章"),
        ("assistant", "第1章写好了。"),
    ]):
        metadata = None
        if role == "assistant":
            metadata = json.dumps({
                "engine": "loop",
                "display_events": [{"type": "content", "content": content}],
                "prompt_tokens": prompt_tokens,
            }, ensure_ascii=False)
        row = ChatMessage(session_id=chat.id, role=role, content=content,
                          created_at=base + timedelta(seconds=index), message_metadata=metadata)
        db_session.add(row)
        rows.append(row)
    db_session.commit()
    return chat, rows


def _summary_completion(content: str, finish_reason: str = "stop"):
    return (
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)],
            usage=SimpleNamespace(prompt_tokens=3000, completion_tokens=80, total_tokens=3080, prompt_tokens_details=None),
        ),
        SimpleNamespace(name="synthetic", model="synthetic-model"),
    )


@pytest.mark.asyncio
async def test_compaction_between_turns_is_persisted_and_rebuilds_the_next_turn(
    client: AsyncClient, db_session: Session, writing_prompt_configs, loop_engine, loop_db, monkeypatch
):
    from unittest.mock import AsyncMock

    from agent.loop.prompts import SUMMARY_PREFIX

    monkeypatch.setattr("agent.loop.compaction.AGENT_LOOP_COMPACT_AT_TOKENS", 5000)
    user, token, project = await _user_project(client, db_session, "loop_compaction_synthetic")
    chat, rows = _seed_loop_session(db_session, user, project, prompt_tokens=9000)

    captured: list[dict] = []
    build_agent, run_streamed = _patched_runs(
        [_FakeRun([_raw_text("第2章写好了。")], prompt_tokens=800), _FakeRun([_raw_text("第3章写好了。")], prompt_tokens=900)],
        captured,
    )
    summarize = AsyncMock(return_value=_summary_completion("合成交接摘要：主角阿岚，第1章已完成。"))
    with build_agent, run_streamed, patch("agent.core.llm_failover.acreate_chat_completion", summarize):
        first = await client.post(
            "/api/v1/agent/stream",
            json={"project_id": project.id, "message": "写第2章", "session_id": chat.id},
            headers={"Authorization": f"Bearer {token}"},
        )
        second = await client.post(
            "/api/v1/agent/stream",
            json={"project_id": project.id, "message": "写第3章", "session_id": chat.id},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert first.status_code == second.status_code == 200
    assert summarize.await_count == 1  # 第二轮实测输入 token 已降下来，不再压缩
    first_events = _parse_sse(first.text)
    assert any(name == "thinking" and "整理" in str(payload) for name, payload in first_events)
    assert [name for name, _ in first_events].count("done") == 1

    runs = [entry for entry in captured if "input" in entry]
    summary_text = f"{SUMMARY_PREFIX}合成交接摘要：主角阿岚，第1章已完成。"
    first_input = [item.get("content") for item in runs[0]["input"]]
    assert first_input[:3] == ["合成要求一：主角叫阿岚", "合成要求二：写第1章", summary_text]
    assert "第1章写好了。" not in first_input
    assert first_input[-1] == "写第2章" or str(first_input[-1]).endswith("写第2章")

    row = db_session.exec(
        select(ChatMessage).where(ChatMessage.role == "assistant", ChatMessage.content == "第2章写好了。")
    ).one()
    metadata = json.loads(row.message_metadata)
    assert metadata["compaction"]["covers_through_message_id"] == rows[-1].id
    assert metadata["compaction"]["summary"].startswith("合成交接摘要")
    assert metadata["prompt_tokens"] == 800
    # 压缩调用的用量计入这一轮的用量展示。
    assert metadata["usage"]["output_tokens"] >= 80

    # 下一轮从库里重建：保留的作者原话 + 摘要 + 压缩点之后的行。
    second_input = [item.get("content") for item in runs[1]["input"]]
    assert second_input[:5] == [
        "合成要求一：主角叫阿岚", "合成要求二：写第1章", summary_text, "写第2章", "第2章写好了。",
    ]


@pytest.mark.asyncio
async def test_compaction_with_empty_summary_keeps_the_full_history(
    client: AsyncClient, db_session: Session, writing_prompt_configs, loop_engine, loop_db, monkeypatch
):
    from unittest.mock import AsyncMock

    monkeypatch.setattr("agent.loop.compaction.AGENT_LOOP_COMPACT_AT_TOKENS", 5000)
    user, token, project = await _user_project(client, db_session, "loop_compaction_empty_synthetic")
    chat, _rows = _seed_loop_session(db_session, user, project, prompt_tokens=9000)

    captured: list[dict] = []
    build_agent, run_streamed = _patched_runs([_FakeRun([_raw_text("第2章写好了。")])], captured)
    summarize = AsyncMock(side_effect=[_summary_completion(""), _summary_completion("半截", "length")])
    with build_agent, run_streamed, patch("agent.core.llm_failover.acreate_chat_completion", summarize):
        response = await client.post(
            "/api/v1/agent/stream",
            json={"project_id": project.id, "message": "写第2章", "session_id": chat.id},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert summarize.await_count == 2
    [run] = [entry for entry in captured if "input" in entry]
    contents = [item.get("content") for item in run["input"]]
    assert contents[:4] == ["合成要求一：主角叫阿岚", "记下了。", "合成要求二：写第1章", "第1章写好了。"]
    row = db_session.exec(
        select(ChatMessage).where(ChatMessage.role == "assistant", ChatMessage.content == "第2章写好了。")
    ).one()
    assert "compaction" not in json.loads(row.message_metadata)


@pytest.mark.asyncio
async def test_skipped_compaction_is_recorded_and_not_retried_every_turn(
    client: AsyncClient, db_session: Session, writing_prompt_configs, loop_engine, loop_db, monkeypatch
):
    """摘要两次都不可用、这一轮的模型调用又失败（没有 assistant 行）：没压缩成的记录写在作者那条消息上，
    下一轮输入没长够就不再付摘要调用。"""
    from unittest.mock import AsyncMock

    monkeypatch.setattr("agent.loop.compaction.AGENT_LOOP_COMPACT_AT_TOKENS", 5000)
    user, token, project = await _user_project(client, db_session, "loop_compaction_backoff_synthetic")
    chat, _rows = _seed_loop_session(db_session, user, project, prompt_tokens=9000)

    class _FailingRun(_FakeRun):
        async def stream_events(self):
            raise RuntimeError("synthetic provider failure")
            yield  # pragma: no cover

    captured: list[dict] = []
    build_agent, run_streamed = _patched_runs([_FailingRun([]), _FakeRun([_raw_text("第2章写好了。")])], captured)
    summarize = AsyncMock(side_effect=[_summary_completion(""), _summary_completion("半截", "length")])
    with build_agent, run_streamed, patch("agent.core.llm_failover.acreate_chat_completion", summarize):
        first = await client.post(
            "/api/v1/agent/stream",
            json={"project_id": project.id, "message": "写第2章", "session_id": chat.id},
            headers={"Authorization": f"Bearer {token}"},
        )
        second = await client.post(
            "/api/v1/agent/stream",
            json={"project_id": project.id, "message": "再写第2章", "session_id": chat.id},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert first.status_code == second.status_code == 200
    assert summarize.await_count == 2  # 只在第一轮试过
    db_session.expire_all()
    user_row = db_session.exec(
        select(ChatMessage).where(ChatMessage.session_id == chat.id, ChatMessage.content == "写第2章")
    ).one()
    skipped = json.loads(user_row.message_metadata)["compaction_skipped"]
    assert skipped["reason"] == "empty_or_truncated_summary"
    assert skipped["at_prompt_tokens"] == 9000


@pytest.mark.asyncio
async def test_compaction_survives_a_turn_without_an_assistant_row(
    client: AsyncClient, db_session: Session, writing_prompt_configs, loop_engine, loop_db, monkeypatch
):
    """压缩成功、这一轮的模型调用却失败：压缩点记在作者那条消息上，下一轮直接用它，不再重付摘要。"""
    from unittest.mock import AsyncMock

    from agent.loop.prompts import SUMMARY_PREFIX

    monkeypatch.setattr("agent.loop.compaction.AGENT_LOOP_COMPACT_AT_TOKENS", 5000)
    user, token, project = await _user_project(client, db_session, "loop_compaction_user_row_synthetic")
    chat, _rows = _seed_loop_session(db_session, user, project, prompt_tokens=9000)

    class _FailingRun(_FakeRun):
        async def stream_events(self):
            raise RuntimeError("synthetic provider failure")
            yield  # pragma: no cover

    captured: list[dict] = []
    build_agent, run_streamed = _patched_runs([_FailingRun([]), _FakeRun([_raw_text("第2章写好了。")])], captured)
    summarize = AsyncMock(return_value=_summary_completion("合成交接摘要"))
    with build_agent, run_streamed, patch("agent.core.llm_failover.acreate_chat_completion", summarize):
        for text in ("写第2章", "再写第2章"):
            response = await client.post(
                "/api/v1/agent/stream",
                json={"project_id": project.id, "message": text, "session_id": chat.id},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200

    assert summarize.await_count == 1
    runs = [entry for entry in captured if "input" in entry]
    second_input = [item.get("content") for item in runs[1]["input"]]
    assert f"{SUMMARY_PREFIX}合成交接摘要" in second_input
    assert "第1章写好了。" not in second_input
