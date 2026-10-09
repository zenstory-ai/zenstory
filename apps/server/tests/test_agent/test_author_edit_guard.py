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
    request_deletes_file,
    request_deletes_folder,
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
        # 上一轮因为作者手改过没改它，作者这一轮说清了往哪改。
        assert request_targets_file(
            AuthorScope(messages=("统一成老周吧",), confirmed_file_ids=frozenset({"ch3"})),
            file_id="ch3",
            title="第3章",
            edits=[{"op": "replace", "old": "老秦", "new": "老周"}],
        ) is True

    @pytest.mark.parametrize(
        ("reply", "unlocked"),
        [
            # 说清了往哪改（不点名别的章）：放行把这一章往这个写法改。
            ("统一成老周吧", True),
            ("改回老周", True),
            ("可以，统一成老周，然后写下一章", True),
            # 上一轮 AI 问的是「要把第2章也改成老秦吗？」：「好 / 可以」答应的是改第2章，
            # 不放开作者改过的第3章（审计复查：答应改别的章时第3章被改回去）。
            ("可以", False),
            ("好", False),
            ("行", False),
            ("嗯", False),
            ("OK", False),
            ("没问题", False),
            ("改吧", False),
            ("好，改吧，然后写下一章", False),
            ("可以，第2章也改成老秦", False),
            ("第2章改成老秦", False),
            # 往作者的写法统一：用不着动作者改过的这一章。
            ("统一成老秦吧", False),
            # 作者不同意、或只是让 AI 往下写：上一轮问过也不放行。
            ("不用改，继续写下一章", False),
            ("不用改，第3章就叫老秦", False),
            ("先不改，按我的来", False),
            ("好，继续写下一章", False),
            ("继续写第5章", False),
            ("继续写下一章，人名要统一", False),
            ("统一吧", False),
            ("要统一", False),
            ("继续写第5章，写好一点", False),
            ("继续写下一章，开头改得有悬念一点", False),
            ("我已经把老周改成老秦了，继续写下一章", False),
            ("我自己改好了", False),
        ],
    )
    def test_reply_to_the_ais_question_unlocks_only_with_a_direction(self, reply, unlocked):
        scope = AuthorScope(messages=(reply,), confirmed_file_ids=frozenset({"ch3"}))
        revert = [{"op": "replace", "old": "老秦", "new": "老周", "replace_all": True}]
        assert request_targets_file(scope, file_id="ch3", title="第3章 三十年", edits=revert) is unlocked

    def test_direction_reply_still_allows_only_edits_toward_it(self):
        scope = AuthorScope(messages=("统一成老周吧",), confirmed_file_ids=frozenset({"ch3"}))
        unrelated = {"op": "replace", "old": "折叠桌一张张码上三轮车。", "new": "桌子收好了。"}
        assert request_targets_file(scope, file_id="ch3", title="第3章 三十年", edits=[unrelated]) is False
        # 没有逐处修改（删除 / 整份重写）不算答应。
        assert request_targets_file(scope, file_id="ch3", title="第3章 三十年") is False

    def test_edit_request_on_the_open_chapter(self):
        # 作者正开着第 1 章、直接提改动：就是改这一章。
        assert _targets("把开头改得更有悬念一点", title="第1章 夜班", file_id="ch1", focus="ch1") is True
        assert _targets("精简一下对话", title="第1章 夜班", file_id="ch1", focus="ch1") is True
        # 没开着它、点名了别的章、或者要的是下一章：都不算改它。
        assert _targets("把开头改得更有悬念一点", title="第1章 夜班", file_id="ch1") is False
        assert _targets("把第3章开头改一下", title="第1章 夜班", file_id="ch1", focus="ch1") is False
        assert _targets("继续写下一章，开头改得有悬念一点", title="第1章 夜班", file_id="ch1", focus="ch1") is False
        assert _targets("不用改，继续写", title="第1章 夜班", file_id="ch1", focus="ch1") is False

    def test_rename_by_description_allows_only_the_new_name(self):
        rename = {"op": "replace", "old": "张三", "new": "李明", "replace_all": True}
        in_context = {"op": "replace", "old": "张三推开门。", "new": "李明推开门。"}
        unrelated = {"op": "replace", "old": "折叠桌一张张码上三轮车。", "new": "桌子收好了。"}
        message = "帮我把主角的名字改成李明"
        assert _targets(message, edits=[rename]) is True
        assert _targets(message, edits=[rename, in_context]) is True
        assert _targets(message, edits=[rename, unrelated]) is False
        # 开着的那一章：提了改动，整章都算作者要改的。
        assert _targets(message, focus="ch3", edits=[unrelated]) is True

    def test_title_word_or_unify_alone_is_not_a_request_to_edit(self):
        # 提到「夜市」只是在说下一章的内容。
        assert _targets("继续写第四章，夜市那段的氛围延续下去", title="第3章 夜市") is False
        assert _targets("夜市那段改得紧凑一点", title="第3章 夜市") is True
        assert _targets("参考第3章的写法写第4章") is False
        assert _targets("第3章写得太拖了，帮我精简一下") is True
        assert _targets("第2章不用改，第3章改成老周", title="第2章 夜班", file_id="ch2") is False
        # 「统一」不再整轮放开所有已有章节（审计：作者手改的「老秦」会被改回去）。
        assert _targets("统一一下格式再写下一章", focus="ch3") is False
        assert _targets("继续写下一章，人名要统一") is False

    def test_unify_without_a_direction_does_not_unlock_the_open_chapter(self):
        # 作者刚把第 3 章的老周改成老秦、还开着它：「人名要统一」「统一一下格式」不能让 AI
        # 把老秦改回老周；点名「这一章」或说清「统一成老周」才算。
        revert = [{"op": "replace", "old": "老秦", "new": "老周"}]
        assert _targets("人名要统一", focus="ch3", edits=revert) is False
        assert _targets("把格式统一一下", focus="ch3", edits=revert) is False
        assert _targets("这一章的人名统一一下", focus="ch3", edits=revert) is True
        assert _targets("第3章人名统一一下", edits=revert) is True
        assert _targets("老秦统一成老周", focus="ch3", edits=revert) is True

    def test_words_that_only_look_like_an_edit_request(self):
        # 「改变」「继续更新」不是要改已有章节；作者讲自己改过的也不是。
        assert _targets("继续写第四章，夜市里主角命运改变", title="第3章 夜市") is False
        assert _targets("继续更新", focus="ch3", edits=[{"op": "replace", "old": "老秦", "new": "老周"}]) is False
        assert _targets("今天更新两章") is False
        assert _targets("我把第3章的老周改成老秦了，继续写下一章") is False
        assert _targets("第3章夜市那段我改过了，你继续写第4章", title="第3章 夜市") is False
        assert _targets("我在第3章把人名改了，继续写下一章") is False
        assert _targets("第3章已经改好了，继续写第4章") is False
        # 接着往下写时，开着的文件只能往后追加，不能改作者写过的字。
        assert _targets("接着写，节奏改快点", focus="ch3", edits=[{"op": "replace", "old": "老秦", "new": "老周"}]) is False
        assert _targets("接着写，节奏改快点", focus="ch3", edits=[{"op": "append", "text": "后来"}]) is True
        # 真正的要求照常放行。
        assert _targets("帮我把第3章改了") is True
        assert _targets("我想把第3章的开头改一下") is True
        assert _targets("我觉得第3章太拖了，精简一下") is True
        assert _targets("把之前写的第3章改一下") is True

    @pytest.mark.parametrize(
        "message",
        [
            # 拿第3章当标准 / 来源：作者是在让 AI 跟着第3章走，不是要改第3章。
            "写第4章，和第3章的人名保持统一",
            "第4章的人名要和第3章统一",
            "第4章要跟第3章保持一致",
            "第4章沿用第3章的改动",
            "以第3章为准，统一一下人名",
            "人名以第3章为准",
            "第3章为准，其他章统一一下",
            "按第3章统一人名",
            "按照《三十年》统一人名",
            "第2章按第3章改",
            "把第2章改得跟第3章一样",
            "同步第3章的人名到第4章",
            # 讲完第3章改过了，下一分句的要求说的是别的章。
            "第3章是我改过的，以它为准统一一下其他章",
            "第3章我改了名字，把前面的章节也统一一下",
            "第3章改了名字，后面的章节统一一下",
            # 讲第3章已经改过（没有「我 / 已经 / 过」）。
            "第3章老周改成老秦了，继续写下一章",
            "第3章里老周换成老秦了",
            "第3章改好了，继续写下一章",
            "第3章改完了，继续写下一章",
            "第3章刚改完，接着写第4章",
            "第3章有改动，继续写下一章",
            "第3章做了修改，继续写下一章",
            "第3章调整了一下人名，继续写下一章",
            "第3章改好了吗",
        ],
    )
    def test_naming_the_chapter_as_the_standard_or_reporting_an_edit_does_not_unlock_it(self, message):
        # 审计复查：这些话里作者都是在让 AI 跟着第3章走；模型要是反过来统一，就会把
        # 作者手改的「老秦」改回「老周」。
        revert = [{"op": "replace", "old": "老秦", "new": "老周", "replace_all": True}]
        assert _targets(message) is False
        assert _targets(message, edits=revert) is False

    @pytest.mark.parametrize(
        "message",
        [
            "第3章人名统一一下",
            "把第3章统一成老周",
            "把第3章和第4章统一一下",
            "把第2章和第3章统一成老周",
            "第3章跟第2章统一成老周",
            "第3章的人名改回老周",
            "第3章再改一下",
            "请把第3章改短一点",
            "第3章，帮我润色一下",
            "把第3章的结局改变一下",
            "第3章写得太烂了，删了吧",
        ],
    )
    def test_asking_to_change_the_named_chapter_still_unlocks_it(self, message):
        assert _targets(message) is True

    def test_deleting_inside_a_folder_is_not_deleting_the_folder(self):
        for message in ("正文里的第2章删掉", "正文里多余的空行删掉", "正文里的错别字去掉", "把正文里重复的段落删掉"):
            assert request_deletes_folder(_scope(message), file_id="f", title="正文") is False, message
        assert request_deletes_folder(_scope("正文整个删掉"), file_id="f", title="正文") is True
        # 删的说法要落在点名第3章的那一处：「把废稿删了」说的是废稿。
        assert request_deletes_file(_scope("第3章改一下，把废稿删了"), file_id="ch3", title="第3章 三十年") is False
        assert request_deletes_file(_scope("把第3章和废稿都删了"), file_id="ch3", title="第3章 三十年") is True

    def test_deleting_a_folder_needs_the_author_to_say_delete(self):
        assert request_deletes_folder(_scope("把废稿文件夹删了"), file_id="f", title="废稿") is True
        assert request_deletes_folder(_scope("废稿那个文件夹，不要了"), file_id="f", title="废稿") is True
        # 只提到文件夹名的改动要求、或者说的是别的，不算要删它。
        assert request_deletes_folder(_scope("正文改紧凑一点"), file_id="f", title="正文") is False
        assert request_deletes_folder(_scope("别删正文文件夹"), file_id="f", title="正文") is False
        assert request_deletes_folder(
            AuthorScope(messages=("改一下",), referenced_file_ids=frozenset({"f"})), file_id="f", title="正文"
        ) is False
        # 文件夹里作者改过的章节：点名并说删才跟着删，只说「改一下」不算。
        assert request_deletes_file(_scope("第3章删掉"), file_id="ch3", title="第3章 三十年") is True
        assert request_deletes_file(_scope("第3章改一下"), file_id="ch3", title="第3章 三十年") is False

    def test_whole_book_request_targets_every_file(self):
        assert _targets("全书统一一下称呼") is True
        assert _targets("全书写到第3章了，继续写下一章") is False


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


@pytest.mark.parametrize(("change_type", "protected"), [("edit", True), ("ai_edit", False)])
async def test_comparison_save_keeps_the_rename_protected_only_as_an_author_edit(
    db_session, owner_project, change_type, protected
):
    """作者改名还没自动保存时 AI 写了这一章，作者在对比里保留自己的改名后点完成：
    编辑器按作者编辑保存（change_type=edit），下一轮 AI 不能把改名改回去。
    作者接受的 AI 修改（去AI味审阅，change_type=ai_edit）照旧不算作者的文字。"""
    user, project = owner_project
    chapter = _ai_chapter(db_session, project)
    files_api.update_file(
        chapter.id,
        files_api.FileUpdate(content=AUTHOR_CH3, change_type=change_type, change_source="user"),
        BackgroundTasks(),
        current_user=user,
        session=db_session,
    )
    db_session.expire_all()
    assert latest_text_is_authors(db_session, db_session.get(File, chapter.id)) is protected

    payload = await _run(
        mcp_tools.edit_file, db_session, user, project, _revert_rename_edit(chapter.id),
        message="继续写下一章",
    )
    assert (payload.get("error_type") == AUTHOR_EDIT_PROTECTED_ERROR) is protected


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

    # AI 上一轮问「要把第2章也改成老秦吗？」，作者回「可以」：答应的是改第2章，第3章不动。
    agreed = await _run(
        mcp_tools.edit_file, db_session, user, project, _revert_rename_edit(chapter.id),
        message="可以", author_confirmed_file_ids=[chapter.id],
    )
    assert agreed["error_type"] == AUTHOR_EDIT_PROTECTED_ERROR
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == AUTHOR_CH3

    # 作者说清了往回统一：放行。
    payload = await _run(
        mcp_tools.edit_file, db_session, user, project, _revert_rename_edit(chapter.id),
        message="统一成老周吧", author_confirmed_file_ids=[chapter.id],
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


async def test_deleting_an_author_edited_chapter_needs_delete_where_it_is_named(db_session, owner_project):
    """「第3章改一下，把废稿删了」：删的是废稿，作者改过的第3章不能借这句被删掉。"""
    user, project = owner_project
    chapter = _ai_chapter(db_session, project)
    _author_saves(db_session, user, chapter, AUTHOR_CH3)

    refused = await _run(
        mcp_tools.delete_file, db_session, user, project, {"id": chapter.id},
        message="第3章改一下，把废稿删了",
    )
    assert refused["error_type"] == AUTHOR_EDIT_PROTECTED_ERROR
    db_session.expire_all()
    assert db_session.get(File, chapter.id).is_deleted is False

    allowed = await _run(
        mcp_tools.delete_file, db_session, user, project, {"id": chapter.id},
        message="第3章不要了，删掉吧",
    )
    assert allowed["status"] == "success"
    db_session.expire_all()
    assert db_session.get(File, chapter.id).is_deleted is True


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


async def test_declined_reply_keeps_the_file_protected(db_session, owner_project):
    """上一轮被拒、AI 问过作者；作者回「不用改，继续写下一章」时第 3 章仍然改不了。"""
    user, project = owner_project
    chapter = _ai_chapter(db_session, project)
    _author_saves(db_session, user, chapter, AUTHOR_CH3)
    first_round = AuthorEditRefusals()
    refused = await _run(
        mcp_tools.edit_file, db_session, user, project, _revert_rename_edit(chapter.id),
        message="继续写下一章", refusals=first_round,
    )
    assert refused["error_type"] == AUTHOR_EDIT_PROTECTED_ERROR
    history = [
        {"role": "user", "content": "继续写下一章"},
        {
            "role": "assistant",
            "content": "第4章写好了。第3章是你手动改过的，我没动它：那里摊主叫老秦，第2章写的是老周，要统一吗？",
            "routing": {CONFIRM_FILE_IDS_ROUTING_KEY: first_round.file_ids()},
        },
    ]

    payload = await _run(
        mcp_tools.edit_file, db_session, user, project, _revert_rename_edit(chapter.id),
        message="不用改，继续写下一章",
        author_confirmed_file_ids=confirmed_file_ids_from_history(history),
    )

    assert payload["error_type"] == AUTHOR_EDIT_PROTECTED_ERROR
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == AUTHOR_CH3


async def test_author_can_edit_the_chapter_they_have_open(db_session, owner_project):
    """作者自己写的第 1 章开着，说「把开头改得更有悬念一点」：照常改，不先问一句。"""
    user, project = owner_project
    chapter = _ai_chapter(db_session, project, title="第1章 夜班", content="夜里十点，他到了店里。")
    _author_saves(db_session, user, chapter, "夜里十点，他推门进了店。")

    payload = await _run(
        mcp_tools.edit_file, db_session, user, project,
        {"id": chapter.id, "edits": [
            {"op": "replace", "old": "夜里十点，他推门进了店。", "new": "门是从里面锁着的。夜里十点，他推门进了店。"},
        ]},
        message="把开头改得更有悬念一点", focus_file_id=chapter.id,
    )

    assert payload["status"] == "success"


async def test_rewriting_an_author_edited_episode_through_create_file_is_refused(db_session, owner_project):
    """剧本项目：create_file 复用同名剧集后 <file> 会整份覆盖，作者手改过的第 3 集不复用。"""
    user, project = owner_project
    project.project_type = "screenplay"
    folder = File(id=f"{project.id}-script-folder", project_id=project.id, title="剧本", file_type="folder")
    db_session.add_all([project, folder])
    db_session.commit()
    episode = File(
        project_id=project.id, parent_id=folder.id, title="第3集", file_type="script", content=AI_CH3, order=3
    )
    db_session.add(episode)
    db_session.flush()
    FileVersionService().create_version(
        db_session, episode.id, AI_CH3, change_type="create", change_source="ai", commit=False
    )
    db_session.commit()
    _author_saves(db_session, user, episode, AUTHOR_CH3)
    args = {"title": "第3集", "file_type": "script", "content": "", "parent_id": folder.id}

    refused = await _run(mcp_tools.create_file, db_session, user, project, args, message="继续写下一集")

    assert refused["status"] == "error"
    assert refused["error_type"] == AUTHOR_EDIT_PROTECTED_ERROR
    assert "<file>" in refused["error"]
    # 作者点名要重写第 3 集时照常复用。
    allowed = await _run(mcp_tools.create_file, db_session, user, project, args, message="第3集重写一下")
    assert allowed["status"] == "success"
    assert allowed["data"]["reused_existing"] is True


async def test_recursive_folder_delete_keeps_author_edited_chapters(db_session, owner_project):
    user, project = owner_project
    folder = File(project_id=project.id, title="正文", file_type="folder")
    db_session.add(folder)
    db_session.commit()
    chapter = _ai_chapter(db_session, project)
    chapter.parent_id = folder.id
    db_session.add(chapter)
    db_session.commit()
    _author_saves(db_session, user, chapter, AUTHOR_CH3)

    payload = await _run(
        mcp_tools.delete_file, db_session, user, project, {"id": folder.id, "recursive": True},
        message="继续写下一章",
    )

    assert payload["error_type"] == AUTHOR_EDIT_PROTECTED_ERROR
    db_session.expire_all()
    assert db_session.get(File, chapter.id).is_deleted is False
    assert db_session.get(File, folder.id).is_deleted is False

    # 只提到文件夹名的改动要求（「正文改紧凑一点」）不是要删它，里面的章节照样保住。
    edit_request = await _run(
        mcp_tools.delete_file, db_session, user, project, {"id": folder.id, "recursive": True},
        message="正文改紧凑一点",
    )
    assert edit_request["error_type"] == AUTHOR_EDIT_PROTECTED_ERROR
    db_session.expire_all()
    assert db_session.get(File, chapter.id).is_deleted is False

    # 作者明说删这个文件夹时照常删。
    allowed = await _run(
        mcp_tools.delete_file, db_session, user, project, {"id": folder.id, "recursive": True},
        message="把正文文件夹删了，我要重新开始",
    )
    assert allowed["status"] == "success"
    db_session.expire_all()
    assert db_session.get(File, chapter.id).is_deleted is True
