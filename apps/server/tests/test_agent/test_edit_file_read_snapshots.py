"""edit_file 的近似匹配与「读过之后正文又变了」（2026-10-12 作者手改差异）。

两件事不能混在一起：
- 模型复述原文不精确（标点、引号、空格、个别字）：正文自模型读过之后没变时，逐字 / 规范化 / 模糊 /
  近似匹配照旧可用，卡片显示实际被替换的原文。
- 数据库里的正文在模型读过之后变了（多半是作者在编辑器里改的）：旧快照上的修改一律不落库（不借近似
  匹配覆盖作者的新内容），明说冲突；重新读过之后能照常改（包括近似匹配），不会永远拒绝。

顺序都由测试自己控制：读（快照）→ 作者保存 → 工具调用，不靠 sleep。编辑器层的匹配细节
（引号边缘、occurrence、跨段、插入落点……）由 test_edit_file_semantics / test_round3_edit_match /
test_edit_file_paragraph_integrity / test_text_matching 覆盖，这里只测工具层与快照的组合。
"""

import json

import pytest
from sqlmodel import Session, select

from agent.tools import mcp_tools
from models import File, Project, User
from models.file_version import FileVersion
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import FileVersionService

CHAPTER = (
    "　　雨下了一整夜，“补纸斋”的门一直关着。\n\n"
    "　　温梨把伞收了，靠在摊子边上，看那些旧书一本一本被雨打湿。\n\n"
    "　　阿满趴在回声桥的栏杆上，数到第七条船，又数了一遍。\n\n"
    "　　桥下的水是浑的，什么也看不清。"
)


@pytest.fixture
def owner_project(db_session, monkeypatch):
    user = User(username="snap-owner", email="snap-owner@example.test", hashed_password="unused", email_verified=True)
    project = Project(name="澜溪镇", owner_id=user.id)
    db_session.add_all([user, project])
    db_session.commit()
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_once", lambda *_a, **_kw: None)
    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (True, 0, 10))
    monkeypatch.setattr("services.infra.dashboard_cache.dashboard_cache.bump_project_version", lambda *_a, **_kw: None)
    return user, project


def _chapter(session, project, content=CHAPTER, title="第一章 初七"):
    file = File(project_id=project.id, title=title, content=content, file_type="draft")
    session.add(file)
    session.flush()
    FileVersionService().create_version(
        session, file.id, content, change_type="create", change_source="ai", commit=False
    )
    session.commit()
    session.refresh(file)
    return file


def _author_saves(session, file, content):
    FileVersionService().create_version(
        session, file.id, content, change_type="edit", change_source="user", skip_quota=True
    )
    current = session.get(File, file.id)
    current.content = content
    session.add(current)
    session.commit()


def _versions(session, file_id):
    session.expire_all()
    return len(session.exec(select(FileVersion).where(FileVersion.file_id == file_id)).all())


class _Request:
    """一次请求的 ToolContext：按顺序调用工具，中间可以插入作者保存、换 agent。"""

    def __init__(self, session, user, project, *, seen=None, message="改一下第一章"):
        self.session, self.user, self.project = session, user, project
        engine = session.get_bind()
        mcp_tools.ToolContext.set_context(
            None,
            user.id,
            project.id,
            None,
            create_session_func=lambda: Session(engine),
            author_message=message,
            author_steering=[],
            read_snapshots=seen,
        )

    async def call(self, tool, args):
        raw = await tool(args)
        return json.loads(raw["content"][0]["text"])

    async def edit(self, file_id, *edits):
        return await self.call(mcp_tools.edit_file, {"id": file_id, "edits": list(edits)})

    async def read(self, file_id):
        return await self.call(mcp_tools.query_files, {"id": file_id})

    @staticmethod
    def next_model_call():
        """模型拿到上一条回复里工具结果之后的下一次调用（runner 的 on_llm_start 做同样的事）。"""
        mcp_tools.ToolContext.promote_pending_reads()

    def close(self):
        mcp_tools.ToolContext.clear_context()


@pytest.fixture
def request_ctx(db_session, owner_project):
    created: list[_Request] = []

    def make(**kwargs):
        user, project = owner_project
        req = _Request(db_session, user, project, **kwargs)
        created.append(req)
        return req

    yield make
    for req in created:
        req.close()


# ---------------------------------------------------------- 1. 读过之后没变：近似匹配照旧


@pytest.mark.parametrize(
    ("old", "mode"),
    [
        # 逐字
        ("温梨把伞收了，靠在摊子边上，看那些旧书一本一本被雨打湿。", "exact"),
        # 中英文标点 / 空格不同（模糊：忽略标点和空白）
        ("温梨把伞收了, 靠在摊子边上, 看那些旧书一本一本被雨打湿.", "fuzzy"),
        # 引号写法不同
        ('雨下了一整夜，"补纸斋"的门一直关着。', None),
        # 个别字不同（近似）
        ("温梨把伞收起来了，靠在摊子边上，看那些旧书一本本被雨打湿。", "approximate"),
    ],
)
async def test_unchanged_text_keeps_exact_fuzzy_and_approximate_matching(
    db_session, owner_project, request_ctx, old, mode
):
    _, project = owner_project
    chapter = _chapter(db_session, project)
    req = request_ctx(seen={chapter.id: CHAPTER})
    payload = await req.edit(chapter.id, {"op": "replace", "old": old, "new": "温梨收了伞。"})
    assert payload["status"] == "success", payload
    detail = payload["data"]["details"][0]
    if mode is not None:
        assert detail["match_mode"] == mode
    if mode in ("fuzzy", "approximate"):
        # 卡片要显示实际被替换的原文，不是模型复述的那句。
        assert "温梨把伞收了" in detail["matched_original"]
    db_session.expire_all()
    content = db_session.get(File, chapter.id).content
    assert "温梨收了伞。" in content and "阿满趴在回声桥的栏杆上" in content


async def test_unchanged_text_still_refuses_ambiguous_or_far_off_matches(db_session, owner_project, request_ctx):
    _, project = owner_project
    twice = CHAPTER + "\n\n　　桥下的水是浑的，什么也看不清。"
    chapter = _chapter(db_session, project, content=twice)
    req = request_ctx(seen={chapter.id: twice})
    ambiguous = await req.edit(
        chapter.id, {"op": "replace", "old": "桥下的水是浑的，什么也看不清。", "new": "水清了。"}
    )
    assert ambiguous["status"] == "error" and ambiguous["error_type"] == "anchor_ambiguous"
    far_off = await req.edit(chapter.id, {"op": "replace", "old": "城门外的山是青的，鸟也飞走了。", "new": "x"})
    assert far_off["status"] == "error"
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == twice


async def test_unchanged_text_insert_and_append_behave_as_before(db_session, owner_project, request_ctx):
    _, project = owner_project
    chapter = _chapter(db_session, project)
    req = request_ctx(seen={chapter.id: CHAPTER})
    payload = await req.edit(
        chapter.id,
        {
            "op": "insert_after",
            "anchor": "阿满趴在回声桥的栏杆上, 数到第七条船, 又数了一遍.",
            "text": "\n一条青帆船从桥洞下穿过去。",
        },
        {"op": "append", "text": "\n\n　　天快亮了。"},
    )
    assert payload["status"] == "success", payload
    db_session.expire_all()
    content = db_session.get(File, chapter.id).content
    assert "又数了一遍。\n\n　　一条青帆船从桥洞下穿过去。\n\n　　桥下的水" in content
    assert content.endswith("天快亮了。")


# ---------------------------------------------------------- 2. 读过之后变了：不在旧正文上改


@pytest.mark.parametrize(
    ("author_version", "listed"),
    [
        # 作者改了模型要改的那一段（只差几个字，以前近似匹配会把它覆盖掉）
        (CHAPTER.replace("温梨把伞收了，靠在摊子边上", "温梨把伞折好了，靠在摊子边上"), "温梨把伞折好了"),
        # 作者改了别处
        (CHAPTER.replace("数到第七条船", "数到第八条船"), "第八条船"),
        # 作者删掉了一段
        (CHAPTER.replace("　　阿满趴在回声桥的栏杆上，数到第七条船，又数了一遍。\n\n", ""), "删掉了"),
        # 作者只改了标点
        (CHAPTER.replace("温梨把伞收了，靠在", "温梨把伞收了——靠在"), "——"),
    ],
)
async def test_text_changed_after_the_read_is_not_edited(
    db_session, owner_project, request_ctx, author_version, listed
):
    _, project = owner_project
    chapter = _chapter(db_session, project)
    req = request_ctx(seen={chapter.id: CHAPTER})
    _author_saves(db_session, chapter, author_version)
    versions_before = _versions(db_session, chapter.id)

    payload = await req.edit(
        chapter.id,
        {"op": "replace", "old": "温梨把伞收了，靠在摊子边上，看那些旧书一本一本被雨打湿。", "new": "温梨收了伞。"},
    )

    assert payload["status"] == "error" and payload["error_type"] == "stale_content"
    assert payload["mutation_applied"] is False and payload["edits_applied"] == 0
    assert listed in payload["error"]
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == author_version
    # 不生成空版本 / 假的 AI 版本。
    assert _versions(db_session, chapter.id) == versions_before


async def test_moved_paragraph_and_several_autosaves_then_reread_and_edit(db_session, owner_project, request_ctx):
    """作者挪了段落、又连续自动保存了几次：旧编辑冲突；重新读过之后，带标点差异的近似修改照常生效。"""
    _, project = owner_project
    chapter = _chapter(db_session, project)
    req = request_ctx(seen={chapter.id: CHAPTER})
    paragraphs = CHAPTER.split("\n\n")
    moved = "\n\n".join([paragraphs[0], paragraphs[2], paragraphs[1], paragraphs[3]])
    _author_saves(db_session, chapter, moved)
    autosaved = moved.replace("什么也看不清。", "什么也看不清，只有雨。")
    _author_saves(db_session, chapter, autosaved)

    stale = await req.edit(chapter.id, {"op": "replace", "old": "雨下了一整夜", "new": "雨下了两夜"})
    assert stale["error_type"] == "stale_content"

    read = await req.read(chapter.id)
    assert read["data"][0]["content"] == autosaved
    req.next_model_call()
    again = await req.edit(
        chapter.id,
        {"op": "replace", "old": "温梨把伞收了, 靠在摊子边上, 看那些旧书一本一本被雨打湿.", "new": "温梨收了伞。"},
    )
    assert again["status"] == "success" and again["data"]["details"][0]["match_mode"] == "fuzzy"
    db_session.expire_all()
    content = db_session.get(File, chapter.id).content
    assert "温梨收了伞。" in content and "只有雨。" in content


async def test_a_fully_listed_change_lets_the_next_edit_go_on_the_new_text(db_session, owner_project, request_ctx):
    _, project = owner_project
    chapter = _chapter(db_session, project)
    req = request_ctx(seen={chapter.id: CHAPTER})
    author_version = CHAPTER.replace("数到第七条船", "数到第八条船")
    _author_saves(db_session, chapter, author_version)
    assert (await req.edit(chapter.id, {"op": "append", "text": "\n\n　　天亮了。"}))["error_type"] == "stale_content"
    # 改动已经完整告诉模型：它看到之后（下一次模型调用）基于现在的正文改就行，不会永远拒绝。
    req.next_model_call()
    ok = await req.edit(chapter.id, {"op": "append", "text": "\n\n　　天亮了。"})
    assert ok["status"] == "success"
    db_session.expire_all()
    assert "第八条船" in db_session.get(File, chapter.id).content


async def test_too_many_changes_need_a_reread_before_editing(db_session, owner_project, request_ctx):
    """读过之后改动太多、没列全：在重新读全文之前一直不在旧正文上改；读过之后就能改。"""
    _, project = owner_project
    original = "\n\n".join(f"　　第{i}段，AI 写的。" for i in range(16))
    chapter = _chapter(db_session, project, content=original)
    req = request_ctx(seen={chapter.id: original})
    # 隔一段改一段：8 处分开的改动，超过一次能列出的处数。
    rewritten = "\n\n".join(f"　　第{i}段，作者重写。" if i % 2 else f"　　第{i}段，AI 写的。" for i in range(16))
    _author_saves(db_session, chapter, rewritten)
    first = await req.edit(chapter.id, {"op": "append", "text": "\n\n　　尾声。"})
    assert first["error_type"] == "stale_content" and "不完整" in first["error"]
    req.next_model_call()
    second = await req.edit(chapter.id, {"op": "append", "text": "\n\n　　尾声。"})
    assert second["error_type"] == "stale_content"
    await req.read(chapter.id)
    req.next_model_call()
    third = await req.edit(
        chapter.id, {"op": "replace", "old": "第3段, 作者重写.", "new": "第3段，作者重写，又改了一次。"}
    )
    assert third["status"] == "success"


# ---------------------------------------------------------- 3. 同一请求里的多次写入、多个文件、换 agent


async def test_own_writes_refresh_the_snapshot_across_edits_and_files(db_session, owner_project, request_ctx):
    _, project = owner_project
    one = _chapter(db_session, project)
    two = _chapter(db_session, project, content=CHAPTER.replace("温梨", "阿满"), title="第二章")
    req = request_ctx(seen={one.id: CHAPTER, two.id: two.content})
    assert (await req.edit(one.id, {"op": "replace", "old": "雨下了一整夜", "new": "雨下了两夜"}))[
        "status"
    ] == "success"
    # 刚写下的内容不当成别人改的；近似匹配照旧。
    follow = await req.edit(one.id, {"op": "replace", "old": "雨下了两夜, “补纸斋”的门一直关着.", "new": "雨停了。"})
    assert follow["status"] == "success" and follow["data"]["details"][0]["match_mode"] == "fuzzy"
    assert (await req.edit(two.id, {"op": "append", "text": "\n\n　　第二章完。"}))["status"] == "success"


async def test_the_next_agent_does_not_inherit_what_the_previous_one_read(db_session, owner_project, request_ctx):
    """writer 读到了作者的新改动并交接；下一个 agent 只看过本轮开始时的正文：它的修改仍算陈旧。"""
    _, project = owner_project
    chapter = _chapter(db_session, project)
    req = request_ctx(seen={chapter.id: CHAPTER})
    author_version = CHAPTER.replace("数到第七条船", "数到第八条船")
    _author_saves(db_session, chapter, author_version)
    await req.read(chapter.id)
    req.next_model_call()
    mcp_tools.ToolContext.begin_agent_run()
    payload = await req.edit(chapter.id, {"op": "replace", "old": "数到第七条船", "new": "数到第九条船"})
    assert payload["error_type"] == "stale_content"
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == author_version


async def test_unread_file_first_edit_is_verbatim_then_works_after_a_read(db_session, owner_project, request_ctx):
    """本请求没看过全文的文件：带标点差异的修改先被要求读一次；读过之后近似匹配照常可用。"""
    _, project = owner_project
    chapter = _chapter(db_session, project)
    req = request_ctx(seen={})
    blind = await req.edit(
        chapter.id,
        {"op": "replace", "old": "温梨把伞收了, 靠在摊子边上, 看那些旧书一本一本被雨打湿.", "new": "温梨收了伞。"},
    )
    assert blind["status"] == "error" and "query_files" in blind["error"]
    # 逐字相同的修改不受影响（正常创作不被打断）。
    verbatim = await req.edit(chapter.id, {"op": "replace", "old": "数到第七条船", "new": "数到第八条船"})
    assert verbatim["status"] == "success"
    # 自己改过一处不等于看过全文：模糊写法仍要先读。
    still_blind = await req.edit(
        chapter.id,
        {"op": "replace", "old": "温梨把伞收了, 靠在摊子边上, 看那些旧书一本一本被雨打湿.", "new": "温梨收了伞。"},
    )
    assert still_blind["status"] == "error" and "query_files" in still_blind["error"]
    await req.read(chapter.id)
    req.next_model_call()
    fuzzy = await req.edit(
        chapter.id,
        {"op": "replace", "old": "温梨把伞收了, 靠在摊子边上, 看那些旧书一本一本被雨打湿.", "new": "温梨收了伞。"},
    )
    assert fuzzy["status"] == "success" and fuzzy["data"]["details"][0]["match_mode"] == "fuzzy"


async def test_appending_to_an_unread_file_is_not_blocked(db_session, owner_project, request_ctx):
    _, project = owner_project
    chapter = _chapter(db_session, project)
    req = request_ctx(seen={})
    assert (await req.edit(chapter.id, {"op": "append", "text": "\n\n　　天亮了。"}))["status"] == "success"


async def test_unread_file_own_append_then_author_edit_is_still_caught(db_session, owner_project, request_ctx):
    """没读过的文件追加一段后，自己写下的正文就是比对基准：之后作者再改，下一次修改照样报冲突。"""
    _, project = owner_project
    chapter = _chapter(db_session, project)
    req = request_ctx(seen={})
    assert (await req.edit(chapter.id, {"op": "append", "text": "\n\n　　天亮了。"}))["status"] == "success"
    db_session.expire_all()
    after_append = db_session.get(File, chapter.id).content
    _author_saves(db_session, chapter, after_append.replace("天亮了。", "天还没亮。"))
    payload = await req.edit(chapter.id, {"op": "append", "text": "\n\n　　鸡叫了。"})
    assert payload["error_type"] == "stale_content"


# ---------------------------------------------------------- 3b. 同一条回复里的多个调用：结果要等模型看到才算数


async def test_a_second_edit_in_the_same_reply_after_a_conflict_is_still_refused(
    db_session, owner_project, request_ctx
):
    """同一条回复里连发两个 edit_file：第一个报冲突，第二个是模型没看到冲突结果时写的，同样不能落在新正文上。"""
    _, project = owner_project
    chapter = _chapter(db_session, project)
    req = request_ctx(seen={chapter.id: CHAPTER})
    author_version = CHAPTER.replace("温梨把伞收了，靠在摊子边上", "温梨把伞折好了，靠在摊子边上")
    _author_saves(db_session, chapter, author_version)
    first = await req.edit(chapter.id, {"op": "replace", "old": "温梨把伞收了，靠在摊子边上", "new": "温梨扔了伞"})
    assert first["error_type"] == "stale_content"
    second = await req.edit(chapter.id, {"op": "replace", "old": "温梨把伞收了, 靠在摊子边上", "new": "温梨扔了伞"})
    assert second["error_type"] == "stale_content"
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == author_version
    # 下一次模型调用看到了冲突里列出的改动：基于新正文的修改照常（含近似匹配）。
    req.next_model_call()
    third = await req.edit(chapter.id, {"op": "replace", "old": "温梨把伞折好了, 靠在摊子边上", "new": "温梨扔了伞"})
    assert third["status"] == "success" and third["data"]["details"][0]["match_mode"] == "fuzzy"


async def test_read_and_edit_in_the_same_reply_uses_what_the_model_had_seen(db_session, owner_project, request_ctx):
    """同一条回复里 query_files + edit_file：edit 是模型看到读取结果之前写的，仍按旧快照判冲突。"""
    _, project = owner_project
    chapter = _chapter(db_session, project)
    req = request_ctx(seen={chapter.id: CHAPTER})
    author_version = CHAPTER.replace("数到第七条船", "数到第八条船")
    _author_saves(db_session, chapter, author_version)
    await req.read(chapter.id)
    same_reply = await req.edit(chapter.id, {"op": "replace", "old": "数到第七条船", "new": "数到第九条船"})
    assert same_reply["error_type"] == "stale_content"
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == author_version
    req.next_model_call()
    ok = await req.edit(chapter.id, {"op": "replace", "old": "数到第八条船", "new": "数到第九条船"})
    assert ok["status"] == "success"


async def test_a_truncated_read_is_not_counted_as_seen(db_session, owner_project, request_ctx, monkeypatch):
    """按 id 读到的结果超出工具结果上限被截断：模型没拿到全文，不当作看过。"""
    _, project = owner_project
    chapter = _chapter(db_session, project)
    req = request_ctx(seen={})
    monkeypatch.setattr(mcp_tools, "TOOL_RESULT_MAX_CHARS", 200)
    await req.read(chapter.id)
    req.next_model_call()
    assert mcp_tools.ToolContext.read_snapshot(chapter.id) is None
    assert mcp_tools.ToolContext.has_seen_file(chapter.id) is False


async def test_a_truncated_parallel_read_is_not_counted_as_seen(db_session, owner_project, request_ctx):
    from agent.tools import parallel_executor

    _, project = owner_project
    long_text = "\n\n".join(f"　　第{i}段，" + "很长的一段字。" * 60 for i in range(30))
    chapter = _chapter(db_session, project, content=long_text)
    small = _chapter(db_session, project, content=CHAPTER, title="第二章")
    req = request_ctx(seen={})
    tasks = [
        {"type": "query_files", "description": f"读 {i}", "params": {"id": file_id, "response_mode": "full"}}
        for i, file_id in enumerate([chapter.id, small.id])
    ]
    original = parallel_executor._task_result_budget
    parallel_executor._task_result_budget = lambda count: 4000
    try:
        await parallel_executor.execute_parallel(tasks)
    finally:
        parallel_executor._task_result_budget = original
    req.next_model_call()
    assert mcp_tools.ToolContext.read_snapshot(chapter.id) is None
    assert mcp_tools.ToolContext.read_snapshot(small.id) == CHAPTER


# ---------------------------------------------------------- 5. 改到作者的字：撤销回到作者的写法


async def test_undo_of_an_edit_that_touched_the_authors_text_restores_it(db_session, owner_project, request_ctx):
    user, project = owner_project
    chapter = _chapter(db_session, project)
    author_version = CHAPTER.replace("温梨把伞收了", "温梨把伞折好了")
    _author_saves(db_session, chapter, author_version)
    req = request_ctx(seen={chapter.id: author_version})
    payload = await req.edit(chapter.id, {"op": "replace", "old": "温梨把伞折好了", "new": "温梨扔了伞"})
    assert payload["data"]["author_edits_touched"]
    undo = payload["data"]["undo"]
    from datetime import datetime

    FileVersionService().rollback_to_version(
        db_session,
        chapter.id,
        undo["before_version_number"],
        user.id,
        expected_updated_at=datetime.fromisoformat(undo["expected_after_updated_at"]),
    )
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == author_version


# ---------------------------------------------------------- runner 接线与提交失败


async def test_run_hooks_promote_reads_at_the_next_model_call():
    """真实 runner 用的 run hooks：on_llm_start 时把上一条回复里读到的正文算作模型看过。"""
    from agent.openai_agents.usage_hooks import build_agent_run_hooks

    mcp_tools.ToolContext.set_context(None, "u", "p", None, read_snapshots={"f": "turn start"})
    try:
        mcp_tools.ToolContext.record_read_snapshot("f", "read in reply 1")
        assert mcp_tools.ToolContext.read_snapshot("f") == "turn start"
        assert mcp_tools.ToolContext.has_seen_file("g") is False
        mcp_tools.ToolContext.record_read_snapshot("g", "other file")
        await build_agent_run_hooks("deepseek-flash").on_llm_start(None, None, "system", [])
        assert mcp_tools.ToolContext.read_snapshot("f") == "read in reply 1"
        assert mcp_tools.ToolContext.has_seen_file("g") is True
    finally:
        mcp_tools.ToolContext.clear_context()


def test_written_snapshot_is_restored_when_the_commit_fails():
    from agent.tools.file_ops.edit import written_snapshot_for_commit

    mcp_tools.ToolContext.set_context(None, "u", "p", None, read_snapshots={"f": "seen"})
    try:
        with pytest.raises(RuntimeError), written_snapshot_for_commit("f", "never committed"):
            # 提交前快照已经更新（同一文件的下一个写入在锁内看得到）。
            assert mcp_tools.ToolContext.read_snapshot("f") == "never committed"
            raise RuntimeError("commit failed")
        assert mcp_tools.ToolContext.read_snapshot("f") == "seen"
        with pytest.raises(RuntimeError), written_snapshot_for_commit("new", "body", full=True):
            raise RuntimeError("commit failed")
        assert mcp_tools.ToolContext.read_snapshot("new") is None
        assert mcp_tools.ToolContext.has_seen_file("new") is False
        with written_snapshot_for_commit("new", "body", full=True):
            pass
        assert mcp_tools.ToolContext.read_snapshot("new") == "body"
        assert mcp_tools.ToolContext.has_seen_file("new") is True
    finally:
        mcp_tools.ToolContext.clear_context()


def test_a_read_taken_before_an_own_write_does_not_override_it():
    """并行批次里 query_files 先读库、edit_file 后写入，但读取结果晚一步记下：按读库的先后算，写下的正文仍是最新的。"""
    mcp_tools.ToolContext.set_context(None, "u", "p", None, read_snapshots={"f": "turn start"})
    try:
        ticket = mcp_tools.ToolContext.read_ticket()
        mcp_tools.ToolContext.record_written_snapshot("f", "written after the read")
        mcp_tools.ToolContext.record_read_snapshot("f", "read before the write", ticket=ticket)
        mcp_tools.ToolContext.promote_pending_reads()
        assert mcp_tools.ToolContext.read_snapshot("f") == "written after the read"
    finally:
        mcp_tools.ToolContext.clear_context()


def _save_version(session, file, content, *, change_type, change_source, summary=None):
    FileVersionService().create_version(
        session, file.id, content, change_type=change_type, change_source=change_source,
        change_summary=summary, skip_quota=True,
    )
    current = session.get(File, file.id)
    current.content = content
    session.add(current)
    session.commit()
    return current


def test_reviewed_save_right_after_an_ai_write_is_protected_from_delete(db_session, owner_project):
    """作者在审阅里拒掉 AI 修改后存下的版本（紧跟 AI 写入的 user/ai_edit/AI edit (reviewed)）是作者的正文。"""
    from agent.tools.author_edit_guard import latest_text_is_authors

    _, project = owner_project
    chapter = _chapter(db_session, project)  # v1: AI 写的
    current = _save_version(
        db_session, chapter, CHAPTER + "x", change_type="ai_edit", change_source="user",
        summary="AI edit (reviewed)",
    )
    assert latest_text_is_authors(db_session, current) is True


def test_reviewed_save_not_following_an_ai_write_stays_unprotected(db_session, owner_project):
    """不紧跟 AI 写入的「AI edit (reviewed)」是旧版前端把去AI味审阅也记成了它，仍算 AI 的润色。"""
    from agent.tools.author_edit_guard import latest_text_is_authors

    _, project = owner_project
    chapter = _chapter(db_session, project)
    _save_version(db_session, chapter, CHAPTER + "a", change_type="edit", change_source="user")
    current = _save_version(
        db_session, chapter, CHAPTER + "b", change_type="ai_edit", change_source="user",
        summary="AI edit (reviewed)",
    )
    assert latest_text_is_authors(db_session, current) is False


@pytest.mark.parametrize(
    ("change_source", "summary"),
    [("user", "AI polish (reviewed)"), ("ai", "AI edit (reviewed)"), ("user", "AI edit")],
)
def test_other_ai_edit_versions_after_an_ai_write_stay_unprotected(
    db_session, owner_project, change_source, summary
):
    """去AI味审阅、source=ai、外部 Agent API 的 ai_edit 仍按 AI 写的算，删除与复用不受保护。"""
    from agent.tools.author_edit_guard import latest_text_is_authors

    _, project = owner_project
    chapter = _chapter(db_session, project)
    current = _save_version(
        db_session, chapter, CHAPTER + "x", change_type="ai_edit", change_source=change_source,
        summary=summary,
    )
    assert latest_text_is_authors(db_session, current) is False
