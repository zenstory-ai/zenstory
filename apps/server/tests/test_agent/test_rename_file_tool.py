"""rename_file: a title-only change that keeps the file id and body (#273)."""

import json

import pytest
from sqlmodel import Session, select

from agent.core.stream_billing import StreamBillingTracker
from agent.tools import mcp_tools
from agent.tools.registry import AGENT_TOOL_NAME_MAP, FILE_WRITE_TOOL_NAMES
from models import File, FileVersion, Project, User
from services.features.activation_event_service import activation_event_service

LONG_BODY = "\n\n".join(f"第{n}章\n" + "雨还在下，她把信折好放回抽屉。" * 40 for n in range(1, 6))


@pytest.fixture
def owner_project(db_session, monkeypatch):
    user = User(username="rename-owner", email="rename-owner@example.test", hashed_password="x", email_verified=True)
    project = Project(name="改名测试", owner_id=user.id)
    db_session.add_all([user, project])
    db_session.commit()
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr("services.infra.dashboard_cache.dashboard_cache.bump_project_version", lambda *_a, **_kw: None)
    return user, project


def _file(session, project, title, content=LONG_BODY, **kwargs):
    file = File(project_id=project.id, title=title, content=content, file_type="draft", **kwargs)
    session.add(file)
    session.commit()
    session.refresh(file)
    return file


async def _rename(session, user, project, args):
    engine = session.get_bind()
    mcp_tools.ToolContext.set_context(None, user.id, project.id, None, create_session_func=lambda: Session(engine))
    try:
        raw = await mcp_tools.rename_file(args)
    finally:
        mcp_tools.ToolContext.clear_context()
    return json.loads(raw["content"][0]["text"])


async def test_rename_keeps_id_body_and_versions(db_session, owner_project):
    user, project = owner_project
    chapter = _file(db_session, project, "合集")
    versions_before = len(db_session.exec(select(FileVersion).where(FileVersion.file_id == chapter.id)).all())

    payload = await _rename(db_session, user, project, {"id": chapter.id, "title": "第一卷 雨夜"})

    assert payload["status"] == "success" and payload["mutation_applied"] is True
    assert payload["data"]["title"] == "第一卷 雨夜" and payload["data"]["previous_title"] == "合集"
    assert "content" not in payload["data"]
    db_session.expire_all()
    renamed = db_session.get(File, chapter.id)
    assert (renamed.title, renamed.content) == ("第一卷 雨夜", LONG_BODY)
    assert len(db_session.exec(select(File).where(File.project_id == project.id)).all()) == 1
    assert len(db_session.exec(select(FileVersion).where(FileVersion.file_id == chapter.id)).all()) == versions_before


@pytest.mark.parametrize("args,error", [
    ({"title": "新名"}, "需要文件 id"),
    ({"id": "x", "title": "  "}, "非空的新标题"),
    ({"id": "missing", "title": "新名"}, "不存在"),
])
async def test_rename_rejects_bad_input(db_session, owner_project, args, error):
    user, project = owner_project
    payload = await _rename(db_session, user, project, args)
    assert payload["status"] == "error" and error in payload["error"]


async def test_rename_refuses_a_sibling_with_the_same_title(db_session, owner_project):
    user, project = owner_project
    a = _file(db_session, project, "第1章", content="甲")
    _file(db_session, project, "第2章", content="乙")

    payload = await _rename(db_session, user, project, {"id": a.id, "title": "第2章"})

    assert payload["status"] == "error" and "已有名为" in payload["error"]
    db_session.expire_all()
    assert db_session.get(File, a.id).title == "第1章"


async def test_rename_cannot_reach_a_deleted_or_other_project_file(db_session, owner_project):
    user, project = owner_project
    other = Project(name="别的项目", owner_id=user.id)
    db_session.add(other)
    db_session.commit()
    foreign = _file(db_session, other, "别人的章", content="丙")
    gone = _file(db_session, project, "已删", content="丁", is_deleted=True)

    for file in (foreign, gone):
        payload = await _rename(db_session, user, project, {"id": file.id, "title": "新名"})
        assert payload["status"] == "error"
    db_session.expire_all()
    assert db_session.get(File, foreign.id).title == "别人的章"


async def test_same_title_is_a_no_op_that_writes_nothing(db_session, owner_project):
    user, project = owner_project
    chapter = _file(db_session, project, "第1章", content="甲")
    before = db_session.get(File, chapter.id).updated_at

    payload = await _rename(db_session, user, project, {"id": chapter.id, "title": "第1章"})

    assert payload["status"] == "success" and payload["mutation_applied"] is False
    db_session.expire_all()
    assert db_session.get(File, chapter.id).updated_at == before


def test_rename_is_a_write_tool_writers_have_and_reviewers_do_not():
    assert "rename_file" in FILE_WRITE_TOOL_NAMES  # rejected in read-only rounds
    assert "rename_file" in AGENT_TOOL_NAME_MAP["writer"]
    assert "rename_file" not in AGENT_TOOL_NAME_MAP["quality_reviewer"]


@pytest.mark.parametrize("applied", [True, False])
def test_only_an_applied_rename_counts_as_written(applied):
    tracker = StreamBillingTracker()
    data = {"tool_name": "rename_file", "status": "success", "data": {"mutation_applied": applied, "id": "f"}}
    tracker.observe(f"event: tool_result\ndata: {json.dumps(data)}\n\n")
    assert tracker.write_succeeded is applied
