"""作者在 AI 写过之后亲手改了什么（2026-10-12 生产审计 drama，agent/tools/author_changes.py）。

以前作者手改过的文件，作者这一轮没「点名」就整份拒绝 edit_file。作者说「用第二个，直接写进
第2集结尾，我那句台词保持我改的版本」仍被拒，要照念口令。现在不判断授权：版本历史算出作者
具体改了什么给模型看，edit_file 只核对事实（模型读过之后正文又变了就不在旧正文上改；改到作者
写的段落时如实说）。
"""

import json

import pytest
from sqlmodel import Session

from agent.tools import mcp_tools
from agent.tools.author_changes import (
    AI,
    AUTHOR,
    FAILED_LINE,
    MAX_VERSION_WINDOW,
    ORIGINAL,
    AuthorChanges,
    AuthorHunk,
    author_changes,
    line_blame,
    pair_clip,
    render_author_edits_block,
    render_hunks,
    touched_author_text,
    version_actor,
)
from models import File, Project, User
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import BEFORE_AI_EDIT_SUMMARY, FileVersionService


@pytest.fixture
def owner_project(db_session, monkeypatch):
    user = User(username="diff-owner", email="diff-owner@example.test", hashed_password="unused", email_verified=True)
    project = Project(name="迟来的深情", owner_id=user.id)
    db_session.add_all([user, project])
    db_session.commit()
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_once", lambda *_a, **_kw: None)
    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (True, 0, 10))
    monkeypatch.setattr("services.infra.dashboard_cache.dashboard_cache.bump_project_version", lambda *_a, **_kw: None)
    return user, project


EP2_AI = "2-1 顾家老宅 餐厅 夜 内\n出场人物：苏晚、顾寒川\n\n顾寒川：签吧。\n苏晚：你有没有把我当成妻子？\n顾寒川：你配吗。\n\n2-2 餐厅 夜 内\n苏晚：好。"
AUTHOR_LINE = "顾寒川：（嗤笑一声）苏晚，你也配问这个？"
EP2_AUTHOR = EP2_AI.replace("顾寒川：你配吗。", AUTHOR_LINE)


class _V:
    def __init__(self, change_type, change_source, change_summary=None):
        self.change_type, self.change_source, self.change_summary = change_type, change_source, change_summary


@pytest.mark.parametrize(
    ("version", "actor"),
    [
        (_V("create", "ai"), AI),
        (_V("ai_edit", "ai"), AI),
        (_V("edit", "user"), AUTHOR),
        (_V("restore", "user"), AUTHOR),
        # 作者审阅 AI 修改后保存：拒掉的段落就是作者的选择。
        (_V("ai_edit", "user", "AI edit (reviewed)"), AUTHOR),
        # 去AI味审阅后保存：差异是作者接受的 AI 润色。
        (_V("ai_edit", "user", "AI polish (reviewed)"), AI),
        # 外部 Agent API 写入：不是作者逐段审阅的。
        (_V("ai_edit", "user", "Updated via Agent API"), AI),
        # AI 写入 / 恢复版本前替作者存下的、没进版本的手改。
        (_V("edit", "system", BEFORE_AI_EDIT_SUMMARY), AUTHOR),
        # 导入、新建、上传、快照基线、快照恢复：原稿，不是作者手改。
        (_V("create", "system", "Initial version"), ORIGINAL),
        (_V("create", "user", "Created via Agent API"), ORIGINAL),
        (_V("restore", "system"), ORIGINAL),
    ],
)
def test_version_actor(version, actor):
    assert version_actor(version) == actor


def _file(session, project, content, title="第2集 离婚协议", file_type="script"):
    file = File(project_id=project.id, title=title, content=content, file_type=file_type)
    session.add(file)
    session.commit()
    session.refresh(file)
    return file


def _version(session, file, content, change_type, change_source, summary=None, *, save=True):
    FileVersionService().create_version(
        session,
        file.id,
        content,
        change_type=change_type,
        change_source=change_source,
        change_summary=summary,
        skip_quota=True,
    )
    if save:
        file.content = content
        session.add(file)
        session.commit()
        session.refresh(file)


def test_author_change_since_the_ai_is_listed_with_the_text_it_replaced(db_session, owner_project):
    _, project = owner_project
    ep2 = _file(db_session, project, EP2_AI)
    _version(db_session, ep2, EP2_AI, "create", "ai")
    _version(db_session, ep2, EP2_AUTHOR, "edit", "user")

    changes = author_changes(db_session, ep2)
    assert changes is not None and changes.complete
    assert [(h.before, h.after) for h in changes.hunks] == [("顾寒川：你配吗。", AUTHOR_LINE)]
    assert render_hunks(changes) == [f"- 「顾寒川：你配吗。」→「{AUTHOR_LINE}」"]


def test_several_author_saves_are_one_net_change(db_session, owner_project):
    _, project = owner_project
    ep2 = _file(db_session, project, EP2_AI)
    _version(db_session, ep2, EP2_AI, "create", "ai")
    _version(db_session, ep2, EP2_AI.replace("顾寒川：你配吗。", "顾寒川：你也配？"), "edit", "user")
    _version(db_session, ep2, EP2_AUTHOR, "edit", "user")

    hunks = author_changes(db_session, ep2).hunks
    # A→B→C 只显示 A→C。
    assert [(h.before, h.after) for h in hunks] == [("顾寒川：你配吗。", AUTHOR_LINE)]


def test_a_later_ai_version_does_not_hide_an_earlier_author_change(db_session, owner_project):
    """作者改了台词，AI 之后改了别处（新 AI 版本里带着作者的台词）：作者那处仍然列出。"""
    _, project = owner_project
    ep2 = _file(db_session, project, EP2_AI)
    _version(db_session, ep2, EP2_AI, "create", "ai")
    _version(db_session, ep2, EP2_AUTHOR, "edit", "user")
    ai_again = EP2_AUTHOR.replace("苏晚：好。", "苏晚：好。\n△ 楼上传来开门声。")
    _version(db_session, ep2, ai_again, "ai_edit", "ai")

    hunks = author_changes(db_session, ep2).hunks
    assert [(h.before, h.after) for h in hunks] == [("顾寒川：你配吗。", AUTHOR_LINE)]


def test_reviewed_save_counts_as_the_authors_choice(db_session, owner_project):
    """作者审阅 AI 修改时拒掉一处、换回自己的写法：那一处是作者的选择。"""
    _, project = owner_project
    ep2 = _file(db_session, project, EP2_AUTHOR)
    _version(db_session, ep2, EP2_AI, "create", "ai")
    _version(db_session, ep2, EP2_AUTHOR, "ai_edit", "user", "AI edit (reviewed)")
    assert [h.after for h in author_changes(db_session, ep2).hunks] == [AUTHOR_LINE]


def test_imported_or_author_written_files_have_no_ai_baseline(db_session, owner_project):
    _, project = owner_project
    imported = _file(db_session, project, EP2_AI, title="第1章")
    _version(db_session, imported, EP2_AI, "create", "system", "Initial version")
    _version(db_session, imported, EP2_AUTHOR, "edit", "user")
    # 全是作者的字（导入原稿 + 作者改），没有可以被 AI 改回去的写法。
    assert author_changes(db_session, imported) is None
    assert line_blame(db_session, imported.id, imported.content).has_ai is False


def test_imported_original_is_not_an_author_change_after_the_ai_writes(db_session, owner_project):
    _, project = owner_project
    chapter = _file(db_session, project, "第一段。\n第二段。", title="第1章", file_type="draft")
    _version(db_session, chapter, "第一段。\n第二段。", "create", "system", "Initial version")
    _version(db_session, chapter, "第一段。\n第二段。\nAI 续写的第三段。", "ai_edit", "ai")
    _version(db_session, chapter, "第一段。\n第二段。\n作者改过的第三段。", "edit", "user")
    hunks = author_changes(db_session, chapter).hunks
    assert [(h.before, h.after) for h in hunks] == [("AI 续写的第三段。", "作者改过的第三段。")]


def test_unversioned_author_text_counts(db_session, owner_project):
    """作者版本额度用满后保存不建版本：最新版本之后的正文仍算作者的。"""
    _, project = owner_project
    ep2 = _file(db_session, project, EP2_AI)
    _version(db_session, ep2, EP2_AI, "create", "ai")
    ep2.content = EP2_AUTHOR
    db_session.add(ep2)
    db_session.commit()
    assert [h.after for h in author_changes(db_session, ep2).hunks] == [AUTHOR_LINE]


def test_deleted_paragraph_since_the_last_ai_version(db_session, owner_project):
    _, project = owner_project
    ep2 = _file(db_session, project, EP2_AI)
    _version(db_session, ep2, EP2_AI, "create", "ai")
    _version(db_session, ep2, EP2_AI.replace("苏晚：你有没有把我当成妻子？\n", ""), "edit", "user")
    hunks = author_changes(db_session, ep2).hunks
    assert [(h.before, h.after) for h in hunks] == [("苏晚：你有没有把我当成妻子？", "")]
    assert render_hunks(AuthorChanges(hunks)) == ["- 删掉了：「苏晚：你有没有把我当成妻子？」（第 2 版删除）"]


def test_old_history_outside_the_window_is_marked_incomplete(db_session, owner_project):
    _, project = owner_project
    chapter = _file(db_session, project, "开头。", title="第1章", file_type="draft")
    _version(db_session, chapter, "开头。", "create", "ai")
    for index in range(MAX_VERSION_WINDOW + 2):
        _version(db_session, chapter, f"开头。\nAI 第{index}稿。", "ai_edit", "ai")
    _version(db_session, chapter, "开头。\n作者的结尾。", "edit", "user")
    changes = author_changes(db_session, chapter)
    assert changes is not None and changes.complete is False
    assert render_hunks(changes)[-1] == f"- 只看了最近 {MAX_VERSION_WINDOW} 个版本，更早的手改可能没列出"


def test_identical_lines_are_told_apart_by_position(db_session, owner_project):
    """两段一模一样的 AI 句子，作者只改了第二段：按位置对齐，不按字符串出现与否。"""
    _, project = owner_project
    ai = "雨下了。\n她走了。\n雨下了。\n门关了。"
    author = "雨下了。\n她走了。\n雨还在下，像没停过。\n门关了。"
    chapter = _file(db_session, project, ai, title="第1章", file_type="draft")
    _version(db_session, chapter, ai, "create", "ai")
    _version(db_session, chapter, author, "edit", "user")
    blame = line_blame(db_session, chapter.id, author)
    assert blame.labels == [AI, AI, AUTHOR, AI]
    # 改第一处（AI 写的）不算改到作者的字；改第三行算。
    assert touched_author_text(blame, author, author.replace("雨下了。\n她", "雨停了。\n她", 1)) == []
    touched = touched_author_text(blame, author, author.replace("雨还在下，像没停过。", "雨停了。"))
    assert touched == [{"before": "雨还在下，像没停过。", "after": "雨停了。"}]
    # 只在作者那段末尾续写不算改了作者的字。
    assert touched_author_text(blame, author, author.replace("像没停过。", "像没停过。她没回头。")) == []


def test_a_change_in_the_middle_of_a_long_paragraph_stays_visible():
    """长段中间只改了一个人名：省掉两边相同的头尾，改动和前后几个字留着，不冒充看不见的差异。"""
    before = "他" * 100 + "老周" + "她" * 100
    after = "他" * 100 + "老秦" + "她" * 100
    left, right, cut = pair_clip(before, after)
    assert "老周" in left and "老秦" in right and left != right and cut is False
    assert len(left) <= 160 and len(right) <= 160
    # 改动本身超长时如实标不完整。
    _, _, cut = pair_clip("雨" * 400, "风" * 400)
    assert cut is True
    lines = render_hunks(AuthorChanges([AuthorHunk("雨" * 400, "风" * 400)]))
    assert lines[-1].startswith("- 有的地方太长，只显示了改动附近（不完整")


def test_author_text_before_the_first_ai_write_is_their_original(db_session, owner_project):
    """新建空章节、作者先写了两段，再让 AI 续写：作者那两段是原稿，不是「AI 写过之后改的」。"""
    _, project = owner_project
    chapter = _file(db_session, project, "", title="第1章", file_type="draft")
    _version(db_session, chapter, "作者第一段。\n作者第二段。", "edit", "user")
    _version(db_session, chapter, "作者第一段。\n作者第二段。\nAI 续写的第三段。", "ai_edit", "ai")
    assert author_changes(db_session, chapter) is None
    blame = line_blame(db_session, chapter.id, chapter.content)
    polished = chapter.content.replace("作者第一段。", "润色后的第一段。")
    assert touched_author_text(blame, chapter.content, polished) == []


def test_changing_it_back_is_not_an_author_change(db_session, owner_project):
    _, project = owner_project
    ep2 = _file(db_session, project, EP2_AI)
    _version(db_session, ep2, EP2_AI, "create", "ai")
    _version(db_session, ep2, EP2_AUTHOR, "edit", "user")
    _version(db_session, ep2, EP2_AI, "edit", "user")
    assert author_changes(db_session, ep2) is None


def test_the_replaced_text_survives_a_blank_line_the_author_added(db_session, owner_project):
    _, project = owner_project
    chapter = _file(db_session, project, "a\nb\nc", title="第1章", file_type="draft")
    _version(db_session, chapter, "a\nb\nc", "create", "ai")
    _version(db_session, chapter, "a\n\nB\nc", "edit", "user")
    hunks = author_changes(db_session, chapter).hunks
    assert [(h.before, h.after) for h in hunks] == [("b", "B")]


def test_ai_continuing_the_authors_paragraph_keeps_it_the_authors(db_session, owner_project):
    _, project = owner_project
    ep2 = _file(db_session, project, EP2_AI)
    _version(db_session, ep2, EP2_AI, "create", "ai")
    _version(db_session, ep2, EP2_AUTHOR, "edit", "user")
    _version(db_session, ep2, EP2_AUTHOR.replace(AUTHOR_LINE, AUTHOR_LINE + "苏晚没抬头。"), "ai_edit", "ai")
    hunks = author_changes(db_session, ep2).hunks
    assert hunks[0].before == "顾寒川：你配吗。" and hunks[0].after.startswith(AUTHOR_LINE)


def test_moved_paragraphs_are_not_changes(db_session, owner_project):
    """作者把 AI 的段落挪了位置：不算改动；AI 把作者的段落整段挪走：不算改了作者的字。"""
    _, project = owner_project
    ai = "p1\np2\np3"
    chapter = _file(db_session, project, ai, title="第1章", file_type="draft")
    _version(db_session, chapter, ai, "create", "ai")
    _version(db_session, chapter, "p2\np3\np1", "edit", "user")
    assert author_changes(db_session, chapter) is None

    other = _file(db_session, project, "q1\nq2", title="第2章", file_type="draft")
    _version(db_session, other, "q1\nq2", "create", "ai")
    _version(db_session, other, "q1\n作者写的段落。\nq2", "edit", "user")
    blame = line_blame(db_session, other.id, other.content)
    assert touched_author_text(blame, other.content, "作者写的段落。\nq1\nq2") == []


def test_replay_failure_is_reported_not_hidden(db_session, owner_project, monkeypatch):
    _, project = owner_project
    ep2 = _author_edited_ep2(db_session, project)

    def broken(*_args, **_kwargs):
        raise ValueError("bad diff")

    monkeypatch.setattr(FileVersionService, "_apply_diff", broken)
    changes = author_changes(db_session, ep2)
    assert changes is not None and changes.failed and not changes.complete
    assert render_hunks(changes) == [FAILED_LINE]


def test_agent_api_writes_before_the_window_still_count_as_ai(db_session, owner_project):
    _, project = owner_project
    chapter = _file(db_session, project, "开头。", title="第1章", file_type="draft")
    _version(db_session, chapter, "开头。", "create", "system", "Initial version")
    _version(db_session, chapter, "开头。\nAPI 写的一段。", "ai_edit", "user", "Updated via Agent API")
    for index in range(MAX_VERSION_WINDOW + 1):
        _version(db_session, chapter, f"开头。\nAPI 写的一段。\n作者第{index}稿。", "edit", "user")
    changes = author_changes(db_session, chapter)
    assert changes is not None and changes.hunks and changes.complete is False


def test_render_budget_says_what_is_missing():
    many = AuthorChanges([AuthorHunk(f"原句{i}" + "雨" * 80, f"作者句{i}" + "风" * 80) for i in range(9)])
    lines = render_hunks(many)
    assert lines[-1] == "- 另有 3 处没列出（不完整，需要时用 query_files 读全文）"
    block = render_author_edits_block([(f"id{i}", f"第{i}章", many) for i in range(10)])
    assert block.startswith("<author_edits>") and block.endswith("</author_edits>")
    assert "个文件的手改没列出（不完整）" in block
    assert len(block) <= 3300


# ------------------------------------------------------------------ 工具层


async def _run(session, user, project, tool, args, *, message, read_snapshots=None):
    engine = session.get_bind()
    mcp_tools.ToolContext.set_context(
        None,
        user.id,
        project.id,
        None,
        create_session_func=lambda: Session(engine),
        author_message=message,
        author_steering=[],
        read_snapshots=read_snapshots,
    )
    try:
        raw = await tool(args)
    finally:
        mcp_tools.ToolContext.clear_context()
    return json.loads(raw["content"][0]["text"])


async def _run_many(session, user, project, calls, *, message, read_snapshots=None):
    """同一请求里先后调用几个工具（共享 ToolContext，快照会被更新）。

    每个调用算作模型的一条回复：调用之间模型看到了上一个结果（runner 的 on_llm_start 做的事）。
    """
    engine = session.get_bind()
    mcp_tools.ToolContext.set_context(
        None,
        user.id,
        project.id,
        None,
        create_session_func=lambda: Session(engine),
        author_message=message,
        author_steering=[],
        read_snapshots=read_snapshots,
    )
    out = []
    try:
        for tool, args in calls:
            mcp_tools.ToolContext.promote_pending_reads()
            raw = await tool(args)
            out.append(json.loads(raw["content"][0]["text"]))
    finally:
        mcp_tools.ToolContext.clear_context()
    return out


def _author_edited_ep2(db_session, project):
    ep2 = _file(db_session, project, EP2_AI)
    _version(db_session, ep2, EP2_AI, "create", "ai")
    _version(db_session, ep2, EP2_AUTHOR, "edit", "user")
    return ep2


async def test_drama_hook_with_the_authors_line_kept_is_written(db_session, owner_project):
    """审计原样：作者手改过第2集的台词，要在结尾加钩子、保留那句台词。不再要口令。"""
    user, project = owner_project
    ep2 = _author_edited_ep2(db_session, project)
    payload = await _run(
        db_session,
        user,
        project,
        mcp_tools.edit_file,
        {
            "id": ep2.id,
            "edits": [
                {"op": "replace", "old": "出场人物：苏晚、顾寒川", "new": "出场人物：苏晚、顾寒川（画外音）"},
                {"op": "append", "text": "\n顾寒川：（隔着楼板）曼曼，明天就办完了。"},
            ],
        },
        message="用第二个，直接写进第2集结尾，我那句台词保持我改的版本",
        read_snapshots={ep2.id: EP2_AUTHOR},
    )
    assert payload["status"] == "success", payload
    assert "author_edits_touched" not in payload["data"]
    db_session.expire_all()
    content = db_session.get(File, ep2.id).content
    assert AUTHOR_LINE in content and content.endswith("曼曼，明天就办完了。")


async def test_rewriting_the_authors_line_is_done_and_reported(db_session, owner_project):
    user, project = owner_project
    ep2 = _author_edited_ep2(db_session, project)
    payload = await _run(
        db_session,
        user,
        project,
        mcp_tools.edit_file,
        {"id": ep2.id, "edits": [{"op": "replace", "old": AUTHOR_LINE, "new": "顾寒川：（冷笑）你也配？"}]},
        message="把我改的那句台词再改狠一点",
        read_snapshots={ep2.id: EP2_AUTHOR},
    )
    assert payload["status"] == "success"
    assert payload["data"]["author_edits_touched"] == [{"before": AUTHOR_LINE, "after": "顾寒川：（冷笑）你也配？"}]
    assert payload["data"]["undo"]["before_version_number"] >= 2


async def test_edit_based_on_text_the_author_changed_meanwhile_is_refused(db_session, owner_project):
    """模型读过之后作者又在编辑器里改了：不在旧正文上改，列出新改动；之后基于新正文改得了。"""
    user, project = owner_project
    ep2 = _file(db_session, project, EP2_AI)
    _version(db_session, ep2, EP2_AI, "create", "ai")
    seen = EP2_AI
    # 作者在 AI 生成期间改了这句（模型的 old 与之近似：以前近似匹配会把它覆盖掉）。
    _version(db_session, ep2, EP2_AUTHOR, "edit", "user")
    edit = {"id": ep2.id, "edits": [{"op": "replace", "old": "顾寒川：你配吗。", "new": "顾寒川：滚。"}]}
    first, second = await _run_many(
        db_session,
        user,
        project,
        [
            (mcp_tools.edit_file, edit),
            (mcp_tools.edit_file, {"id": ep2.id, "edits": [{"op": "append", "text": "\n苏晚关了灯。"}]}),
        ],
        message="第2集结尾再加一句",
        read_snapshots={ep2.id: seen},
    )
    assert first["status"] == "error" and first["error_type"] == "stale_content"
    assert first["mutation_applied"] is False
    assert f"「顾寒川：你配吗。」→「{AUTHOR_LINE}」" in first["error"]
    assert "AI 没有在旧内容上改" in first["user_message"]
    # 新改动已完整告诉模型：接着基于现在的正文改，作者那句保留。
    assert second["status"] == "success"
    db_session.expire_all()
    content = db_session.get(File, ep2.id).content
    assert AUTHOR_LINE in content and content.endswith("苏晚关了灯。")


async def test_without_a_read_the_old_text_must_match_exactly(db_session, owner_project):
    """本请求没读过的文件：old 只按逐字匹配，不拿记忆里的旧写法去近似命中作者刚改的字。"""
    user, project = owner_project
    chapter = _file(db_session, project, "门外站着一个穿白衣的老人，手里提着灯。", title="第1章", file_type="draft")
    _version(db_session, chapter, "门外站着一个穿白衣的老人，手里提着灯。", "create", "ai")
    _version(db_session, chapter, "门外站着一个穿黑衣的陌生人，手里提着灯。", "edit", "user")
    payload = await _run(
        db_session,
        user,
        project,
        mcp_tools.edit_file,
        {
            "id": chapter.id,
            "edits": [
                {"op": "replace", "old": "门外站着一个穿白衣的老人，手里提着灯。", "new": "门外空无一人。"},
            ],
        },
        message="第一章开头改得冷一点",
    )
    assert payload["status"] == "error"
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == "门外站着一个穿黑衣的陌生人，手里提着灯。"


async def test_query_files_records_the_read_and_returns_author_edits(db_session, owner_project):
    user, project = owner_project
    ep2 = _author_edited_ep2(db_session, project)
    read, edit = await _run_many(
        db_session,
        user,
        project,
        [
            (mcp_tools.query_files, {"id": ep2.id}),
            (
                mcp_tools.edit_file,
                {"id": ep2.id, "edits": [{"op": "replace", "old": "苏晚：好。", "new": "苏晚：好，我签。"}]},
            ),
        ],
        message="第2集结尾那句改一下",
        read_snapshots={},
    )
    row = read["data"][0]
    assert row["author_edits"]["changes"] == [{"before": "顾寒川：你配吗。", "after": AUTHOR_LINE}]
    assert row["author_edits"]["complete"] is True
    # 读过之后有了快照：普通的修改照常生效。
    assert edit["status"] == "success"


async def test_two_edits_of_the_same_file_in_one_request_both_apply(db_session, owner_project):
    """同一请求里先后两次改同一个文件（parallel_execute 的两个子任务也是这样）：第二次不是「别人改过」。"""
    user, project = owner_project
    ep2 = _author_edited_ep2(db_session, project)
    first, second = await _run_many(
        db_session,
        user,
        project,
        [
            (
                mcp_tools.edit_file,
                {"id": ep2.id, "edits": [{"op": "replace", "old": "顾寒川：签吧。", "new": "顾寒川：签。"}]},
            ),
            (mcp_tools.edit_file, {"id": ep2.id, "edits": [{"op": "append", "text": "\n苏晚关了灯。"}]}),
        ],
        message="第2集改两处",
        read_snapshots={ep2.id: EP2_AUTHOR},
    )
    assert first["status"] == "success" and second["status"] == "success"


@pytest.mark.parametrize(
    "edit",
    [
        {
            "op": "replace",
            "old": "门外站着一个穿白衣的老人，手里提着灯。",
            "new": "门外空无一人。",
            "match_mode": "auto",
        },
        {"op": "Replace", "old": "门外站着一个穿白衣的老人，手里提着灯。", "new": "门外空无一人。"},
        {"old": "门外站着一个穿白衣的老人，手里提着灯。", "new": "门外空无一人。"},
    ],
)
async def test_unread_files_match_verbatim_whatever_the_edit_says(db_session, owner_project, edit):
    user, project = owner_project
    chapter = _file(db_session, project, "门外站着一个穿白衣的老人，手里提着灯。", title="第1章", file_type="draft")
    _version(db_session, chapter, "门外站着一个穿白衣的老人，手里提着灯。", "create", "ai")
    _version(db_session, chapter, "门外站着一个穿黑衣的陌生人，手里提着灯。", "edit", "user")
    payload = await _run(
        db_session, user, project, mcp_tools.edit_file, {"id": chapter.id, "edits": [edit]}, message="改一下开头"
    )
    assert payload["status"] == "error"
    assert "query_files" in payload["error"]
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == "门外站着一个穿黑衣的陌生人，手里提着灯。"


def test_what_the_previous_agent_read_is_not_what_the_next_one_saw():
    from agent.tools.mcp_tools import ToolContext

    ToolContext.set_context(None, "u", "p", None, read_snapshots={"f": "turn start"})
    try:
        ToolContext.record_read_snapshot("f", "read by writer")
        ToolContext.record_written_snapshot("g", "written by writer")
        # 读到的正文要等模型下一次调用才算看过；自己写下的立即生效。
        assert ToolContext.read_snapshot("f") == "turn start"
        assert ToolContext.read_snapshot("g") == "written by writer"
        ToolContext.promote_pending_reads()
        assert ToolContext.read_snapshot("f") == "read by writer"
        ToolContext.begin_agent_run()
        # 下一个 agent 没看到 writer 读到的新正文；本请求自己写下的仍算看到了。
        assert ToolContext.read_snapshot("f") == "turn start"
        assert ToolContext.read_snapshot("g") == "written by writer"
    finally:
        ToolContext.clear_context()


def test_undoing_an_ai_rewrite_is_the_authors_choice(db_session, owner_project):
    """作者手改后 AI 又改了那句，作者撤销（回滚到 AI 改之前）：作者的写法回来、仍列为作者的手改；
    AI 原来的写法不会被说成作者写的。"""
    user, project = owner_project
    ep2 = _author_edited_ep2(db_session, project)
    before_ai = FileVersionService().get_latest_version(db_session, ep2.id).version_number
    _version(db_session, ep2, EP2_AUTHOR.replace(AUTHOR_LINE, "顾寒川：滚。"), "ai_edit", "ai")
    FileVersionService().rollback_to_version(db_session, ep2.id, before_ai, user.id)
    db_session.expire_all()
    ep2 = db_session.get(File, ep2.id)
    assert ep2.content == EP2_AUTHOR
    hunks = author_changes(db_session, ep2).hunks
    assert [(h.before, h.after) for h in hunks] == [("顾寒川：滚。", AUTHOR_LINE)]


# ---------------------------------------------------------- 第二轮审查补充


def test_window_with_no_listed_change_still_says_it_is_incomplete(db_session, owner_project):
    """作者的手改在最近 40 个版本之外、窗口里只有 AI 版本：不能当作「没改过」。"""
    _, project = owner_project
    chapter = _file(db_session, project, "开头。", title="第1章", file_type="draft")
    _version(db_session, chapter, "开头。", "create", "ai")
    _version(db_session, chapter, "作者改的开头。", "edit", "user")
    for index in range(MAX_VERSION_WINDOW + 2):
        _version(db_session, chapter, f"作者改的开头。\nAI 第{index}稿。", "ai_edit", "ai")
    changes = author_changes(db_session, chapter)
    assert changes is not None and changes.hunks == [] and changes.complete is False
    assert render_hunks(changes) == [f"- 只看了最近 {MAX_VERSION_WINDOW} 个版本，更早的手改可能没列出"]


def test_a_deletion_inside_a_replaced_block_is_listed(db_session, owner_project):
    """作者删了一段、又在附近改了一段（difflib 会合成一个 replace 块）：删掉的那段仍列出。"""
    _, project = owner_project
    ai = "甲段。\n乙段要删。\n丙段。\n丁段。"
    author = "甲段。\n丙段改过了。\n丁段。"
    chapter = _file(db_session, project, ai, title="第1章", file_type="draft")
    _version(db_session, chapter, ai, "create", "ai")
    _version(db_session, chapter, author, "edit", "user")
    pairs = {(h.before, h.after) for h in author_changes(db_session, chapter).hunks}
    assert ("乙段要删。", "") in pairs or any("乙段要删。" in before for before, _ in pairs)
    rendered = "\n".join(render_hunks(author_changes(db_session, chapter)))
    assert "乙段要删。" in rendered and "丙段改过了。" in rendered


@pytest.mark.parametrize(
    ("ai_line", "is_extension"),
    [
        ("作者的句子。AI 接着写。", True),
        ("　　作者的句子。  ", False),  # 只是空白 / 全角缩进不同：不是续写，也不算改动
        ("AI 先说一句。作者的句子。", True),
        ("不作者的句子。", False),  # 在作者句子前面加字改了意思
    ],
)
def test_extension_needs_a_sentence_boundary(ai_line, is_extension):
    from agent.tools.author_changes import _extends

    assert _extends(ai_line, "作者的句子。") is is_extension


def test_short_author_word_prefixed_is_not_an_extension():
    from agent.tools.author_changes import _extends

    assert _extends("好不容易才来", "好") is False
    assert _extends("不行。", "行。") is False


def test_ai_multi_line_replace_that_extends_author_lines_keeps_them(db_session, owner_project):
    _, project = owner_project
    ai = "a1。\na2。\na3。"
    author = "作者一。\n作者二。\na3。"
    chapter = _file(db_session, project, ai, title="第1章", file_type="draft")
    _version(db_session, chapter, ai, "create", "ai")
    _version(db_session, chapter, author, "edit", "user")
    _version(db_session, chapter, "作者一。AI 补一句。\nAI 重写第二行。\na3。", "ai_edit", "ai")
    blame = line_blame(db_session, chapter.id, chapter.content)
    assert blame.labels == [AUTHOR, AI, AI]


def test_legacy_reviewed_polish_save_is_not_an_author_change(db_session, owner_project):
    """旧版前端把去AI味审阅也记成「AI edit (reviewed)」：前面不是 AI 写入时，差异是作者接受的润色。"""
    _, project = owner_project
    ai = "雨下了。\n她走了。"
    chapter = _file(db_session, project, ai, title="第1章", file_type="draft")
    _version(db_session, chapter, ai, "create", "ai")
    _version(db_session, chapter, "雨下了。\n她走了，没回头。", "edit", "user")
    _version(db_session, chapter, "雨落了一夜。\n她走了，没回头。", "ai_edit", "user", "AI edit (reviewed)")
    hunks = author_changes(db_session, chapter).hunks
    assert [(h.before, h.after) for h in hunks] == [("她走了。", "她走了，没回头。")]


def test_whitespace_only_changes_are_not_listed(db_session, owner_project):
    _, project = owner_project
    ai = "　　雨下了。\n　　她走了。"
    chapter = _file(db_session, project, ai, title="第1章", file_type="draft")
    _version(db_session, chapter, ai, "create", "ai")
    _version(db_session, chapter, "雨下了。 \n\n　　她走了。", "edit", "user")
    assert author_changes(db_session, chapter) is None


def test_long_documents_replay_without_aligning_on_blank_lines_or_say_they_did_not(db_session, owner_project):
    """段落间的空行不参与对齐（它们到处都是，用来对齐会让比对开销接近平方）；太长的正文明说没算出来。

    不用牆钟时间断言（CI 带覆盖率时会慢很多）：直接检查空行被当作 junk。
    """
    from agent.tools.author_changes import MAX_BLAME_LINES, _matcher

    _, project = owner_project
    ai = "\n\n".join(f"　　第{i}段，AI 写的一句话。" for i in range(1000))
    lines = ai.split("\n")
    assert "" in _matcher(lines, lines).bjunk
    chapter = _file(db_session, project, ai, title="第1章", file_type="draft")
    _version(db_session, chapter, ai, "create", "ai")
    author = ai.replace("第500段，AI 写的一句话。", "第500段，作者改过。")
    _version(db_session, chapter, author, "edit", "user")
    changes = author_changes(db_session, chapter)
    assert [h.after for h in changes.hunks] == ["　　第500段，作者改过。"]

    huge = "\n".join(f"第{i}行。" for i in range(MAX_BLAME_LINES + 1))
    big = _file(db_session, project, huge, title="第2章", file_type="draft")
    _version(db_session, big, huge, "create", "ai")
    too_long = author_changes(db_session, big)
    assert too_long is not None and too_long.failed


def test_any_error_while_computing_is_reported_as_failed(db_session, owner_project, monkeypatch):
    import agent.tools.author_changes as module

    _, project = owner_project
    ep2 = _author_edited_ep2(db_session, project)

    def boom(*_args, **_kwargs):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(module, "author_hunks", boom)
    changes = author_changes(db_session, ep2)
    assert changes is not None and changes.failed


# #268：作者删掉 AI 稿里的一段后，AI 只改别处，删除不能被新的 AI 版本吸收。
_A = "段落A：她把信烧了，没告诉任何人。"
_B = "段落B：雨下了一整夜。"
_B_AI = "段落B：雨一直下到天亮。"


def _deleted_then_ai_elsewhere(db_session, project):
    chapter = _file(db_session, project, f"{_A}\n\n{_B}", title="第1章", file_type="draft")
    _version(db_session, chapter, f"{_A}\n\n{_B}", "create", "ai")
    _version(db_session, chapter, _B, "edit", "user")
    _version(db_session, chapter, _B_AI, "ai_edit", "ai")
    return chapter


def test_author_deletion_survives_a_later_ai_edit_elsewhere(db_session, owner_project):
    _, project = owner_project
    chapter = _deleted_then_ai_elsewhere(db_session, project)

    changes = author_changes(db_session, chapter)

    assert changes is not None and changes.complete is True
    assert [(h.before, h.after, h.deleted_in_version) for h in changes.hunks] == [(_A, "", 2)]
    assert render_hunks(changes) == [f"- 删掉了：「{_A}」（第 2 版删除）"]


def test_restoring_the_deleted_paragraph_verbatim_is_reported_with_its_source(db_session, owner_project):
    _, project = owner_project
    chapter = _deleted_then_ai_elsewhere(db_session, project)
    blame = line_blame(db_session, chapter.id, _B_AI)

    touched = touched_author_text(blame, _B_AI, f"{_B_AI}\n\n{_A}")

    assert touched == [{"before": "", "after": _A, "restored_deleted": True, "deleted_in_version": 2}]


@pytest.mark.parametrize("new_content", [
    f"{_B_AI}\n\n段落C：天亮后她去了车站。",  # an ordinary new paragraph
    f"{_B_AI}\n\n段落A：她把信烧了。",  # a rewrite that is not the deleted text
])
def test_ordinary_additions_are_not_called_restorations(db_session, owner_project, new_content):
    _, project = owner_project
    chapter = _deleted_then_ai_elsewhere(db_session, project)
    blame = line_blame(db_session, chapter.id, _B_AI)

    assert touched_author_text(blame, _B_AI, new_content) == []


def test_a_paragraph_the_author_put_back_is_no_longer_a_deletion(db_session, owner_project):
    _, project = owner_project
    chapter = _deleted_then_ai_elsewhere(db_session, project)
    _version(db_session, chapter, f"{_A}\n\n{_B_AI}", "edit", "user")

    changes = author_changes(db_session, chapter)

    assert changes is None or all(hunk.before != _A for hunk in changes.hunks)
    blame = line_blame(db_session, chapter.id, chapter.content)
    assert blame.deletions == []
