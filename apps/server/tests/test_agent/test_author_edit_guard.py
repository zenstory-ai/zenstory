"""作者手动改过的文件，作者本轮没点名时 AI 不改不删（2026-10-09 审计 P1-3）。

审计原样：作者在第 3 章把「老周」手改成「老秦」（19 处），发「继续写下一章」。AI 写完
第 4 章后把第 3 章全改了回去，总结里写「顺手修的两处」。
"""

import json

import pytest
from fastapi import BackgroundTasks
from sqlmodel import Session

from agent.service import routing_from_router_decided
from agent.tools import mcp_tools
from agent.tools.author_edit_guard import (
    AUTHOR_EDIT_PROTECTED_ERROR,
    CONFIRM_FILE_IDS_ROUTING_KEY,
    AuthorEditRefusals,
    AuthorScope,
    confirmed_file_ids_from_history,
    latest_text_is_authors,
    mentioned_sequences,
    referenced_file_ids_from_metadata,
    rename_source_terms,
    request_targets_file,
)
from agent.tools.file_ops import FileCRUD
from api import files as files_api
from models import File, Project, User
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import FileVersionService

AI_CH3 = "老周在收摊。折叠桌一张张码上三轮车。老周回头看了他一眼。"
AUTHOR_CH3 = "老秦在收摊。折叠桌一张张码上三轮车。老秦回头看了他一眼。"


def _scope(message: str, **kwargs) -> AuthorScope:
    return AuthorScope(messages=(message,), **kwargs)


def _targets(message: str, *, title: str = "第3章 三十年", file_id: str = "ch3", **kwargs) -> bool:
    focus = kwargs.pop("focus", None)
    return request_targets_file(
        _scope(message, focus_file_id=focus), file_id=file_id, title=title, **kwargs
    )


class TestRequestTargetsFile:
    def test_continue_next_chapter_does_not_target_the_open_chapter(self):
        # 审计原样：作者正开着第 3 章，说「继续写下一章」。
        assert _targets("继续写下一章", focus="ch3") is False
        assert _targets("继续写下一章", focus="ch3", edits=[{"op": "append", "text": "续写"}]) is False

    def test_named_chapter_is_targeted(self):
        assert _targets("把第三章结尾改一下") is True
        assert _targets("第3章的错别字帮我改掉") is True
        assert _targets("前三章的错别字改一下") is True
        assert _targets("第2、3章统一称呼") is True
        assert _targets("把《三十年》这章的结尾改得狠一点") is True

    def test_negated_mention_is_not_a_request(self):
        assert _targets("继续写第5章。另外以后别改我已经写好的章节，要改先问我。") is False
        assert _targets("别动第3章，写第4章") is False
        assert _targets("不要统一称呼，按我改的来") is False
        assert mentioned_sequences("不用管第2章，把第3章的结尾改一下") == {(3, "novel")}

    def test_rename_request_allows_only_replacing_the_old_name(self):
        assert rename_source_terms("帮我把老秦全部改成老周") == {"老秦"}
        rename = {"op": "replace", "old": "老秦在收摊。", "new": "老周在收摊。"}
        unrelated = {"op": "replace", "old": "折叠桌一张张码上三轮车。", "new": "桌子收好了。"}
        assert _targets("把老秦改回老周", edits=[rename]) is True
        assert _targets("把老秦改回老周", edits=[rename, unrelated]) is False

    def test_continue_writing_the_open_chapter_may_only_append(self):
        assert _targets("继续写", focus="ch3", edits=[{"op": "append", "text": "后来"}]) is True
        assert _targets(
            "继续写", focus="ch3", edits=[{"op": "replace", "old": "老秦", "new": "老周"}]
        ) is False
        # 不是正开着的文件，追加也不行。
        assert _targets("继续写", focus="ch2", edits=[{"op": "append", "text": "后来"}]) is False

    def test_this_chapter_refers_to_the_open_file(self):
        assert _targets("帮我润色一下这一章", focus="ch3") is True
        assert _targets("帮我润色一下这一章", focus="ch2") is False

    def test_file_category_needs_an_edit_verb(self):
        assert request_targets_file(
            _scope("按大纲写第5章"), file_id="o1", title="核心大纲", file_type="outline"
        ) is False
        assert request_targets_file(
            _scope("把大纲里第5章改一下"), file_id="o1", title="分章大纲", file_type="outline"
        ) is True

    def test_referenced_and_confirmed_files_are_targeted(self):
        assert request_targets_file(
            AuthorScope(messages=("改一下",), referenced_file_ids=frozenset({"ch3"})),
            file_id="ch3",
            title="第3章",
        ) is True
        # 上一轮问过作者要不要改它，作者这一轮只回了「可以」。
        assert request_targets_file(
            AuthorScope(messages=("可以",), confirmed_file_ids=frozenset({"ch3"})),
            file_id="ch3",
            title="第3章",
        ) is True

    def test_whole_book_request_targets_every_file(self):
        assert _targets("全书统一一下称呼") is True


def test_history_and_metadata_helpers():
    history = [
        {"role": "user", "content": "继续写下一章"},
        {"role": "assistant", "content": "…", "routing": {CONFIRM_FILE_IDS_ROUTING_KEY: ["ch3"]}},
        {"role": "user", "content": "可以"},
    ]
    assert confirmed_file_ids_from_history(history) == ["ch3"]
    # 只看上一条 assistant 消息：更早问过的不算。
    later = [*history, {"role": "assistant", "content": "好的", "routing": {}}]
    assert confirmed_file_ids_from_history(later) == []
    assert referenced_file_ids_from_metadata(
        {"attached_file_ids": ["a"], "text_quotes": [{"fileId": "b", "text": "x"}], "current_file_id": "c"}
    ) == ["a", "b"]
    # 笼统要求问过一次的标记随路由落库（下一轮不再问第二次）。
    persisted = routing_from_router_decided(
        {"initial_agent": "writer", "routing_metadata": {"agent_type": "writer", "clarify_first": True}}
    )
    assert persisted["clarify_first"] is True
    assert "clarify_first" not in routing_from_router_decided({"initial_agent": "writer"})


# ----------------------------------------------------------------- 工具层


@pytest.fixture
def owner_project(db_session, monkeypatch):
    user = User(username="guard-owner", email="guard-owner@example.test", hashed_password="unused", email_verified=True)
    project = Project(name="还剩三分钟", owner_id=user.id)
    db_session.add_all([user, project])
    db_session.commit()
    monkeypatch.setattr(FileCRUD, "_schedule_index_upsert", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_once", lambda *_a, **_kw: None)
    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (True, 0, 10))
    monkeypatch.setattr("services.llama_index.schedule_index_upsert", lambda **_kw: None)
    monkeypatch.setattr("services.infra.dashboard_cache.dashboard_cache.bump_project_version", lambda *_a, **_kw: None)
    return user, project


def _ai_chapter(session, project, title="第3章 三十年", content=AI_CH3):
    """AI 写的章节：正文 + 一个 AI 版本（和 create_file 带正文时一样）。"""
    file = File(project_id=project.id, title=title, content=content, file_type="draft")
    session.add(file)
    session.flush()
    FileVersionService().create_version(
        session, file.id, content, change_type="create", change_source="ai", commit=False
    )
    session.commit()
    session.refresh(file)
    return file


def _author_saves(session, user, file, content):
    """作者在编辑器里手动改正文（走真实的保存接口，留下作者版本）。"""
    files_api.update_file(
        file.id,
        files_api.FileUpdate(content=content),
        BackgroundTasks(),
        current_user=user,
        session=session,
    )


async def _run(tool, session, user, project, args, *, message, refusals=None, **context):
    engine = session.get_bind()
    mcp_tools.ToolContext.set_context(
        None,
        user.id,
        project.id,
        None,
        create_session_func=lambda: Session(engine),
        author_message=message,
        author_steering=[],
        author_edit_refusals=refusals,
        **context,
    )
    try:
        raw = await tool(args)
    finally:
        mcp_tools.ToolContext.clear_context()
    return json.loads(raw["content"][0]["text"])


def _revert_rename_edit(file_id):
    return {"id": file_id, "edits": [
        {"op": "replace", "old": "老秦", "new": "老周", "replace_all": True},
    ]}


async def test_ai_cannot_revert_the_authors_rename_in_an_unrequested_chapter(db_session, owner_project):
    user, project = owner_project
    chapter = _ai_chapter(db_session, project)
    _author_saves(db_session, user, chapter, AUTHOR_CH3)
    refusals = AuthorEditRefusals()

    payload = await _run(
        mcp_tools.edit_file, db_session, user, project, _revert_rename_edit(chapter.id),
        message="继续写下一章", refusals=refusals, focus_file_id=chapter.id,
    )

    assert payload["status"] == "error"
    assert payload["error_type"] == AUTHOR_EDIT_PROTECTED_ERROR
    assert payload["mutation_applied"] is False
    # 给模型的说明：以作者为准、不要重试、去问作者；给作者的话不带内部名字。
    assert "以作者为准" in payload["error"] and "问作者" in payload["error"]
    assert "手动改过" in payload["user_message"] and "edit_file" not in payload["user_message"]
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == AUTHOR_CH3
    # 记下来随路由落库，作者下一轮回答时放行。
    assert refusals.file_ids() == [chapter.id]


async def test_author_can_still_ask_for_the_change(db_session, owner_project):
    user, project = owner_project
    chapter = _ai_chapter(db_session, project)
    _author_saves(db_session, user, chapter, AUTHOR_CH3)

    payload = await _run(
        mcp_tools.edit_file, db_session, user, project, _revert_rename_edit(chapter.id),
        message="第3章的老秦还是改回老周吧",
    )

    assert payload["status"] == "success"
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == AI_CH3


async def test_reply_to_the_ais_question_unlocks_the_file(db_session, owner_project):
    user, project = owner_project
    chapter = _ai_chapter(db_session, project)
    _author_saves(db_session, user, chapter, AUTHOR_CH3)

    payload = await _run(
        mcp_tools.edit_file, db_session, user, project, _revert_rename_edit(chapter.id),
        message="可以", author_confirmed_file_ids=[chapter.id],
    )

    assert payload["status"] == "success"


async def test_ai_written_chapter_is_still_editable_this_round(db_session, owner_project):
    """审稿返工改本轮刚写的章节（最新版本是 AI 的）不受影响。"""
    user, project = owner_project
    chapter = _ai_chapter(db_session, project, title="第4章 数在我这儿", content="老周在收摊。")

    payload = await _run(
        mcp_tools.edit_file, db_session, user, project,
        {"id": chapter.id, "edits": [{"op": "replace", "old": "老周在收摊。", "new": "老周收摊了。"}]},
        message="继续写下一章",
    )

    assert payload["status"] == "success"


async def test_unversioned_manual_edit_is_protected_too(db_session, owner_project):
    """作者的版本额度用满时正文照存、不留版本：正文和最新 AI 版本不一致也算作者改过。"""
    user, project = owner_project
    chapter = _ai_chapter(db_session, project)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (False, 10, 10))
        _author_saves(db_session, user, chapter, AUTHOR_CH3)
    db_session.expire_all()
    assert latest_text_is_authors(db_session, db_session.get(File, chapter.id)) is True

    payload = await _run(
        mcp_tools.edit_file, db_session, user, project, _revert_rename_edit(chapter.id),
        message="继续写下一章",
    )

    assert payload["error_type"] == AUTHOR_EDIT_PROTECTED_ERROR


async def test_delete_of_an_author_edited_chapter_is_refused(db_session, owner_project):
    user, project = owner_project
    chapter = _ai_chapter(db_session, project)
    _author_saves(db_session, user, chapter, AUTHOR_CH3)

    payload = await _run(
        mcp_tools.delete_file, db_session, user, project, {"id": chapter.id},
        message="继续写下一章",
    )

    assert payload["error_type"] == AUTHOR_EDIT_PROTECTED_ERROR
    db_session.expire_all()
    assert db_session.get(File, chapter.id).is_deleted is False


async def test_guard_is_off_without_an_author_message(db_session, owner_project):
    """非应用内对话的调用路径（没有作者原话）不受影响。"""
    user, project = owner_project
    chapter = _ai_chapter(db_session, project)
    _author_saves(db_session, user, chapter, AUTHOR_CH3)
    engine = db_session.get_bind()
    mcp_tools.ToolContext.set_context(None, user.id, project.id, None, create_session_func=lambda: Session(engine))
    try:
        raw = await mcp_tools.edit_file(_revert_rename_edit(chapter.id))
    finally:
        mcp_tools.ToolContext.clear_context()

    assert json.loads(raw["content"][0]["text"])["status"] == "success"
