"""In-app agent writes normalize CJK double quotes; edit anchors stay quote-tolerant."""

import json

import pytest
from fastapi import BackgroundTasks
from sqlmodel import Session

from agent.stream_adapter import StreamAdapter, StreamAdapterConfig
from agent.tools import mcp_tools
from agent.tools.file_ops import FileCRUD, FileEditor
from agent.tools.file_ops.edit import EditFileError
from api import agent_api
from models import File, Project, User
from models.agent_api_key import AgentApiKey
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import FileVersionService


@pytest.fixture
def owner_project(db_session, monkeypatch):
    user = User(username="quote-owner", email="quote-owner@example.test", hashed_password="unused", email_verified=True)
    project = Project(name="引号测试", owner_id=user.id)
    db_session.add_all([user, project])
    db_session.commit()
    monkeypatch.setattr(FileCRUD, "_schedule_index_upsert", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_once", lambda *_a, **_kw: None)
    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (True, 0, 10))
    monkeypatch.setattr("services.llama_index.schedule_index_upsert", lambda **_kw: None)
    monkeypatch.setattr("services.infra.dashboard_cache.dashboard_cache.bump_project_version", lambda *_a, **_kw: None)
    return user, project


@pytest.fixture
def stream_save_to_test_db(monkeypatch):
    """StreamAdapter 落库用独立 session，重定向到测试库。"""
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


def _add_file(session, project, title, content, file_type="draft", order=0):
    file = File(project_id=project.id, title=title, content=content, file_type=file_type, order=order)
    session.add(file)
    session.commit()
    session.refresh(file)
    return file


async def _run_mcp(tool, session, user, project, args):
    engine = session.get_bind()
    mcp_tools.ToolContext.set_context(None, user.id, project.id, None, create_session_func=lambda: Session(engine))
    try:
        raw = await tool(args)
    finally:
        mcp_tools.ToolContext.clear_context()
    return json.loads(raw["content"][0]["text"])


def test_streamed_whole_file_write_persists_fullwidth_dialogue(db_session, owner_project, stream_save_to_test_db):
    user, project = owner_project
    chapter = _add_file(db_session, project, "第1章", "")
    adapter = StreamAdapter(StreamAdapterConfig(project_id=project.id, user_id=user.id, process_file_markers=True))

    saved, changed = adapter._save_file_content_sync(chapter.id, '他推开门："我回来了。"\n她没抬头："嗯。"')

    assert (saved, changed) == (True, True)
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == "他推开门：“我回来了。”\n她没抬头：“嗯。”"


def test_new_chapter_follows_previous_chapter_corner_style(db_session, owner_project, stream_save_to_test_db):
    user, project = owner_project
    _add_file(db_session, project, "第1章", "「走。」他说。", order=1)
    chapter = _add_file(db_session, project, "第2章", "", order=2)
    adapter = StreamAdapter(StreamAdapterConfig(project_id=project.id, user_id=user.id, process_file_markers=True))

    adapter._save_file_content_sync(chapter.id, '她问："去哪？"')

    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == "她问：「去哪？」"


async def test_mcp_create_file_with_content_is_normalized(db_session, owner_project):
    user, project = owner_project
    payload = await _run_mcp(
        mcp_tools.create_file, db_session, user, project,
        {"title": "第1章", "file_type": "draft", "content": '他说："好。"'},
    )
    assert payload["status"] == "success"
    db_session.expire_all()
    assert db_session.get(File, payload["data"]["id"]).content == "他说：“好。”"


async def test_mcp_edit_normalizes_only_the_new_text(db_session, owner_project):
    user, project = owner_project
    # 第二段是作者留下的半角引号：没被编辑就必须原样保留，不能在审阅里冒出无关 diff。
    original = '第一段：“你好。”\n第二段："作者原样。"\n第三段结束。'
    chapter = _add_file(db_session, project, "第1章", original)

    payload = await _run_mcp(
        mcp_tools.edit_file, db_session, user, project,
        {"id": chapter.id, "edits": [
            {"op": "replace", "old": "第三段结束。", "new": '第三段："改写了。"'},
            {"op": "append", "text": '\n尾声："完。"'},
        ]},
    )

    assert payload["status"] == "success"
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == (
        '第一段：“你好。”\n第二段："作者原样。"\n第三段：“改写了。”\n尾声：“完。”'
    )


async def test_halfwidth_old_text_locates_fullwidth_original_exactly(db_session, owner_project):
    user, project = owner_project
    chapter = _add_file(db_session, project, "第1章", "他说：“我不走。”她笑了。")

    payload = await _run_mcp(
        mcp_tools.edit_file, db_session, user, project,
        {"id": chapter.id, "edits": [{"op": "replace", "old": '他说："我不走。"', "new": '他说："我走。"'}]},
    )

    assert payload["status"] == "success"
    detail = payload["data"]["details"][0]
    assert detail["match_mode"] == "quote_equivalent"
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == "他说：“我走。”她笑了。"


@pytest.mark.parametrize(
    ("original", "edit", "expected"),
    [
        pytest.param(
            "他说：“我明天就走，你别拦我。”她没回头。",
            {"op": "replace", "old": "明天就走，你别拦我", "new": '明天就走"她急了："你别拦我'},
            "他说：“我明天就走”她急了：“你别拦我。”她没回头。",
            id="replace-inside-open-dialogue",
        ),
        pytest.param(
            "他说：“甲来了，乙来了。”",
            {"op": "replace", "old": "来了", "new": '来了"她说："好', "replace_all": True},
            "他说：“甲来了”她说：“好，乙来了”她说：“好。”",
            id="replace-all-inside-open-dialogue",
        ),
        pytest.param(
            "他说：“我明天就走\n她没回头。",
            {"op": "insert_after", "anchor": "我明天就走", "text": '，你别拦我。"'},
            "他说：“我明天就走，你别拦我。”\n她没回头。",
            id="insert-closing-fragment",
        ),
        pytest.param(
            "开头。\n他说：“旧台词。”\n结尾。",
            {"op": "replace", "old": "他说：“旧台词。”", "new": '他说："新台词。"她笑："好。"'},
            "开头。\n他说：“新台词。”她笑：“好。”\n结尾。",
            id="whole-line-replace",
        ),
        pytest.param(
            "他说：“我明天就走，你别拦我。”",
            {"op": "replace", "old": "明天就走，你别拦我", "new": '明天就走。”她急了："你别拦我'},
            "他说：“我明天就走。”她急了：“你别拦我。”",
            id="copied-curly-quote-not-flipped",
        ),
        pytest.param(
            "第一章\n",
            {"op": "append", "text": 'He said "go".'},
            '第一章\nHe said "go".',
            id="english-untouched",
        ),
        pytest.param(
            "他说：走吧。",
            {"op": "insert_before", "anchor": "走吧", "text": '"'},
            '他说："走吧。',
            id="odd-parity-line-skipped",
        ),
    ],
)
def test_edit_normalizes_new_text_by_its_line_context(db_session, owner_project, original, edit, expected):
    user, project = owner_project
    chapter = _add_file(db_session, project, "第1章", original)

    FileEditor(db_session, user.id).edit_file(chapter.id, [edit], normalize_quotes=True)

    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == expected


def test_halfwidth_old_text_mid_dialogue_matches_curly_original_and_keeps_direction(db_session, owner_project):
    user, project = owner_project
    chapter = _add_file(db_session, project, "第1章", "他说：“我不走。”她笑了。")

    result = FileEditor(db_session, user.id).edit_file(
        chapter.id,
        [{"op": "replace", "old": '我不走。"她笑了。', "new": '我走。"她笑："好。"'}],
        normalize_quotes=True,
    )

    detail = result["details"][0]
    assert detail["match_mode"] == "quote_equivalent"
    assert detail["new_preview"] == "我走。”她笑：“好。”"
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == "他说：“我走。”她笑：“好。”"


def test_quote_equivalent_match_keeps_the_uniqueness_guard(db_session, owner_project):
    user, project = owner_project
    content = "“走。”他说。\n“走。”她也说。"
    chapter = _add_file(db_session, project, "第1章", content)
    editor = FileEditor(db_session, user.id)

    with pytest.raises(EditFileError) as excinfo:
        editor.edit_file(chapter.id, [{"op": "delete", "old": '"走。"'}])
    assert "occurrence" in str(excinfo.value)
    # 候选片段取自原文，模型看到的是稿件里的全角引号。
    assert "“走。”" in str(excinfo.value)
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == content

    result = editor.edit_file(chapter.id, [{"op": "insert_after", "anchor": '"走。"', "text": "（停顿）", "occurrence": 2}])
    assert result["details"][0]["match_mode"] == "quote_equivalent"
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == "“走。”他说。\n“走。”（停顿）她也说。"


def test_agent_api_writes_are_not_normalized(db_session, owner_project):
    user, project = owner_project
    key = AgentApiKey(user_id=user.id, key_prefix="quote-key", key_hash=f"quote-{user.id}", name="Quote writer", scopes=["read", "write"])
    db_session.add(key)
    db_session.commit()
    chapter = _add_file(db_session, project, "第1章", "旧正文")
    context = (db_session, user.id, key)

    agent_api.update_file(chapter.id, agent_api.FileUpdate(content='他说："外部写入。"'), BackgroundTasks(), _rate_limit=0, context=context)
    created = agent_api.create_file(
        project.id, agent_api.FileCreate(title="第2章", content='她说："也是外部。"'), BackgroundTasks(), _rate_limit=0, context=context,
    )

    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == '他说："外部写入。"'
    assert db_session.get(File, created.id).content == '她说："也是外部。"'


def test_edit_result_reports_editor_word_count_next_to_char_length(db_session, owner_project):
    user, project = owner_project
    chapter = _add_file(db_session, project, "第1章", "他说：“走。”")

    result = FileEditor(db_session, user.id).edit_file(chapter.id, [{"op": "append", "text": "\nOK 好的。"}])

    # 编辑器口径：汉字各算 1，连续拉丁字母算 1，标点、空白、换行不算。
    assert result["new_word_count"] == 6
    assert result["new_length"] == len("他说：“走。”\nOK 好的。")


@pytest.mark.parametrize("file_type", ["outline", "character"])
async def test_outline_and_character_files_are_not_normalized(db_session, owner_project, stream_save_to_test_db, file_type):
    user, project = owner_project
    target = _add_file(db_session, project, "设定稿", "", file_type=file_type)
    adapter = StreamAdapter(StreamAdapterConfig(project_id=project.id, user_id=user.id, process_file_markers=True))
    adapter._save_file_content_sync(target.id, '口头禅："就这？"')

    payload = await _run_mcp(
        mcp_tools.edit_file, db_session, user, project,
        {"id": target.id, "edits": [{"op": "append", "text": '\n外号："老猫"'}]},
    )

    assert payload["status"] == "success"
    db_session.expire_all()
    assert db_session.get(File, target.id).content == '口头禅："就这？"\n外号："老猫"'
