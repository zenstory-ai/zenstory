"""第三轮 review 收敛阶段回归测试：14 个并行组之间的跨组遗留项。

每条都对应一份"由 A 组修一半、B 组文件不在白名单里"的缺口，收敛阶段补齐另一半：

- bug-02 同族站点：_suggest_auto_current_phase_from_drafts 只查 draft，
  短剧项目（script）的 current_phase 永远推不出来。
- assembler 同族站点：找"前一章"只在 draft ∪ outline 里找，短剧续写丢上一集。
- stream #1：edit_file 返回给 LLM 的 status 恒为 success，
  部分/全部失败时模型无法自我纠正。
- stream #2：append/prepend 的 detail 缺 new_preview。
- stream #3：FileEditEndEventData 缺 failed_count/partial_success/all_failed/warnings。
- tools-core #4：stream_adapter 的 pending-empty-file 精确清除
  （多个待写空文件时不得一次清光）。
- service #2：message_manager 不得写空 assistant 行，
  message_count 必须按实际写入行数递增。
"""


import pytest
from sqlmodel import Session, select

from agent.constants import CONTENT_FILE_TYPES
from models import ChatSession, File, Project, User


@pytest.fixture
def converge_user(db_session: Session) -> User:
    user = User(
        email="round3_converge@example.com",
        username="round3_converge",
        hashed_password="hashed_password",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def converge_project(db_session: Session, converge_user: User) -> Project:
    project = Project(
        name="收敛回归项目",
        description="round3 converge",
        owner_id=converge_user.id,
        project_type="screenplay",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


# ---------------------------------------------------------------------------
# bug-02 同族站点：current_phase 推断必须覆盖 script
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_auto_current_phase_infers_from_script_files(
    db_session: Session, converge_project: Project, converge_user: User
):
    """短剧项目的正文是 script，只查 draft 会让 current_phase 永远推不出来。"""
    from agent.tools.mcp_tools import _suggest_auto_current_phase_from_drafts

    db_session.add(
        File(
            project_id=converge_project.id,
            title="第7集 反杀",
            file_type="script",
            content="正文",
        )
    )
    db_session.commit()

    suggested = _suggest_auto_current_phase_from_drafts(db_session, converge_project.id)

    assert suggested is not None
    assert "7" in suggested


@pytest.mark.unit
def test_auto_current_phase_still_infers_from_draft_files(
    db_session: Session, converge_user: User
):
    """小说项目（draft）的既有行为不能被回归掉。"""
    from agent.tools.mcp_tools import _suggest_auto_current_phase_from_drafts

    project = Project(name="小说项目", owner_id=converge_user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    db_session.add(
        File(project_id=project.id, title="第3章 起势", file_type="draft", content="正文")
    )
    db_session.commit()

    suggested = _suggest_auto_current_phase_from_drafts(db_session, project.id)

    assert suggested is not None
    assert "3" in suggested


@pytest.mark.unit
def test_auto_current_phase_counts_episodes_for_a_screenplay(db_session: Session, converge_user: User):
    """live run 2 场景 A：短剧项目被自动写成「已写至第3章」，剧本应按「集」。"""
    from agent.tools.mcp_tools import _suggest_auto_current_phase_from_drafts

    drama = Project(name="短剧项目", owner_id=converge_user.id, project_type="screenplay")
    short = Project(name="短篇项目", owner_id=converge_user.id, project_type="short")
    db_session.add_all([drama, short])
    db_session.commit()
    db_session.add_all([
        File(project_id=drama.id, title="第3集 车灯", file_type="script", content="正文"),
        File(project_id=short.id, title="第2章 交接", file_type="draft", content="正文"),
    ])
    db_session.commit()

    assert _suggest_auto_current_phase_from_drafts(db_session, drama.id) == "已写至第3集"
    # 写到第4集后继续往前推（「第3集」要能被认出来，不然进度会停在这里）。
    drama.current_phase = "已写至第3集"
    db_session.add(File(project_id=drama.id, title="第4集 认亲", file_type="script", content="正文"))
    db_session.commit()
    assert _suggest_auto_current_phase_from_drafts(db_session, drama.id) == "已写至第4集"
    assert _suggest_auto_current_phase_from_drafts(db_session, short.id) == "已写至第2章"


@pytest.mark.unit
def test_auto_current_phase_ignores_chapters_without_body(db_session: Session, converge_user: User):
    """F-10：第 1–3 章有正文、第 4–20 章只是空标题时，进度不能写成「已写至第20章」。"""
    from agent.tools.mcp_tools import _suggest_auto_current_phase_from_drafts

    novel = Project(name="空壳章节项目", owner_id=converge_user.id, project_type="novel")
    drama = Project(name="空壳剧集项目", owner_id=converge_user.id, project_type="screenplay")
    db_session.add_all([novel, drama])
    db_session.commit()
    db_session.add_all(
        [File(project_id=novel.id, title=f"第{n}章", file_type="draft", content="正文") for n in (1, 2, 3)]
        + [File(project_id=novel.id, title=f"第{n}章", file_type="draft", content="") for n in range(4, 20)]
        + [File(project_id=novel.id, title="第20章", file_type="draft", content="  \n ")]
        + [File(project_id=drama.id, title="第1集", file_type="script", content="正文")]
        + [File(project_id=drama.id, title="第2集", file_type="script", content="")]
    )
    db_session.commit()

    assert _suggest_auto_current_phase_from_drafts(db_session, novel.id) == "已写至第3章"
    assert _suggest_auto_current_phase_from_drafts(db_session, drama.id) == "已写至第1集"
    # 只有空壳时不推断进度。
    novel.current_phase = ""
    for f in db_session.exec(select(File).where(File.project_id == novel.id)).all():
        f.content = ""
    db_session.commit()
    assert _suggest_auto_current_phase_from_drafts(db_session, novel.id) is None


# ---------------------------------------------------------------------------
# assembler 同族站点：找"前一章"必须覆盖 script
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_previous_chapter_lookup_covers_script(
    db_session: Session, converge_project: Project
):
    """短剧续写时，"前一集"是 script 类型；只查 draft 会把上一集整个丢掉。"""
    from agent.context.assembler import ContextAssembler

    folder = File(
        project_id=converge_project.id, title="剧本", file_type="folder", order=0
    )
    db_session.add(folder)
    db_session.commit()
    db_session.refresh(folder)

    prev = File(
        project_id=converge_project.id,
        title="第1集",
        file_type="script",
        content="上一集正文",
        parent_id=folder.id,
        order=1,
    )
    focus = File(
        project_id=converge_project.id,
        title="第2集",
        file_type="script",
        content="",
        parent_id=folder.id,
        order=2,
    )
    db_session.add_all([prev, focus])
    db_session.commit()
    db_session.refresh(prev)
    db_session.refresh(focus)

    assembler = ContextAssembler()
    items = assembler._get_related_files(db_session, converge_project.id, focus)

    relations = {item.metadata.get("relation") for item in items}
    assert "previous" in relations, "短剧的前一集必须被收进上下文"
    previous_items = [i for i in items if i.metadata.get("relation") == "previous"]
    assert previous_items[0].title == "第1集"


@pytest.mark.unit
def test_content_file_types_is_the_single_source_of_truth():
    """守卫：正文类型集合只有一处定义，新增类型时两个站点自动跟随。"""
    assert "draft" in CONTENT_FILE_TYPES
    assert "script" in CONTENT_FILE_TYPES
    assert "folder" not in CONTENT_FILE_TYPES


# ---------------------------------------------------------------------------
# stream #1：edit_file 返回给 LLM 的 status 必须按结果降级
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"edits_applied": 3, "failed_edits": []}, "success"),
        ({"edits_applied": 1, "failed_edits": [{"error": "x"}], "partial_success": True}, "partial"),
        ({"edits_applied": 0, "failed_edits": [{"error": "x"}], "all_failed": True}, "error"),
        # 汇总标志缺失时按 failed_edits + edits_applied 兜底
        ({"edits_applied": 2, "failed_edits": [{"error": "x"}]}, "partial"),
        ({"edits_applied": 0, "failed_edits": [{"error": "x"}]}, "error"),
    ],
)
def test_derive_edit_status(result, expected):
    from agent.tools.mcp_tools import _derive_edit_status

    assert _derive_edit_status(result) == expected


@pytest.mark.unit
def test_edit_file_tool_payload_status_degrades_on_failure(monkeypatch):
    """工具返回值里的 status 恒为 success 时，模型会以为改动已经落地。"""
    import json

    from agent.tools import mcp_tools

    fake_result = {
        "id": "f1",
        "title": "第1章",
        "file_type": "draft",
        "edits_applied": 1,
        "new_length": 10,
        "details": [],
        "failed_edits": [{"error": "锚点找不到"}],
        "partial_success": True,
        "all_failed": False,
        "warnings": [],
    }

    from unittest.mock import MagicMock

    class _Executor:
        def edit_file(self, **kwargs):
            return fake_result

    req_ctx = mcp_tools.AgentRequestContext(session=MagicMock(), user_id="u1", project_id="p1")
    monkeypatch.setattr(req_ctx, "executor", lambda _session: _Executor())

    payload = mcp_tools._edit_file_sync({"id": "f1", "edits": [{"op": "replace"}]}, req_ctx)
    parsed = json.loads(payload["content"][0]["text"])
    assert parsed["status"] == "partial"


# ---------------------------------------------------------------------------
# stream #2 / #3：detail 的 new_preview 与 file_edit_end 的一等字段
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_backfill_new_preview_covers_text_only_ops():
    """append/prepend/insert_* 的 detail 只有 text_preview，必须补出 new_preview。"""
    from agent.tools.file_ops.edit import FileEditor

    details = [
        {"op": "append", "text_preview": "追加的正文"},
        {"op": "insert_after", "text_preview": "插入的正文"},
        {"op": "replace", "new_preview": "替换后的正文", "text_preview": "不该覆盖"},
        {"op": "delete"},
    ]
    FileEditor._backfill_new_preview(details)

    assert details[0]["new_preview"] == "追加的正文"
    assert details[1]["new_preview"] == "插入的正文"
    assert details[2]["new_preview"] == "替换后的正文", "已有 new_preview 不得被覆盖"
    assert "new_preview" not in details[3], "没有文本的 op 不该凭空造出预览"


@pytest.mark.unit
def test_file_edit_end_event_has_failure_fields_in_schema():
    """failed_count/partial_success/all_failed/warnings 必须是 schema 一等字段，
    而不是工厂之外手工塞进 data 的自由键。"""
    from agent.core.events import FileEditEndEventData, file_edit_end_event

    assert "failed_count" in FileEditEndEventData.model_fields
    assert "partial_success" in FileEditEndEventData.model_fields
    assert "all_failed" in FileEditEndEventData.model_fields
    assert "warnings" in FileEditEndEventData.model_fields

    event = file_edit_end_event(
        file_id="f1",
        edits_applied=1,
        new_length=10,
        failed_count=2,
        partial_success=True,
        warnings=["锚点匹配到多处", ""],
    )
    assert event.data["failed_count"] == 2
    assert event.data["partial_success"] is True
    assert event.data["all_failed"] is False
    assert event.data["warnings"] == ["锚点匹配到多处"], "空告警要被过滤掉"


# ---------------------------------------------------------------------------
# tools-core #4：pending-empty-file 精确清除
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_stream_adapter_clears_only_the_targeted_pending_file(db_session: Session):
    """同时存在多个待写空文件时，一份写完不得把其余的标记一起清光。"""
    from agent.core.request_context import AgentRequestContext
    from agent.stream_adapter import StreamAdapter, StreamAdapterConfig

    req_ctx = AgentRequestContext(session=db_session, user_id="u1", project_id="p1", session_id="s1")
    try:
        req_ctx.set_pending_empty_file("file-a", "第1集")
        req_ctx.set_pending_empty_file("file-b", "第2集")

        adapter = StreamAdapter(StreamAdapterConfig(request_context=req_ctx))
        adapter._clear_pending_empty_file_guard("file-a")

        remaining = {p["file_id"] for p in req_ctx.get_pending_empty_files()}
        assert remaining == {"file-b"}, "只该摘掉 file-a"

        # 无参调用仍是"流结束兜底"的无条件清空
        adapter._clear_pending_empty_file_guard()
        assert req_ctx.get_pending_empty_files() == []
    finally:
        req_ctx.clear()


# ---------------------------------------------------------------------------
# service #2：message_manager 不写空 assistant 行 + message_count 按实际行数
# ---------------------------------------------------------------------------


def _make_chat_session(db_session: Session, user: User, project: Project) -> ChatSession:
    chat_session = ChatSession(
        user_id=user.id,
        project_id=project.id,
        title="AI 助手对话",
        is_active=True,
        message_count=0,
    )
    db_session.add(chat_session)
    db_session.commit()
    db_session.refresh(chat_session)
    return chat_session


@pytest.mark.unit
def test_message_manager_skips_empty_assistant_row(
    db_session: Session, converge_user: User, converge_project: Project
):
    """本轮一个 token 都没产出时不能写空 assistant 行：
    前端永远渲染不出来，却占掉"最近消息"窗口的一格。"""
    from agent.core.message_manager import MessageManager
    from models import ChatMessage

    chat_session = _make_chat_session(db_session, converge_user, converge_project)
    manager = MessageManager(project_id=converge_project.id, user_id=converge_user.id)

    returned = manager._save_messages_with_session(
        db_session, chat_session.id, "写第一章", ""
    )

    db_session.expire_all()
    rows = db_session.exec(
        __import__("sqlmodel").select(ChatMessage).where(
            ChatMessage.session_id == chat_session.id
        )
    ).all()
    assert [r.role for r in rows] == ["user"]
    assert returned is None, "没有写 assistant 行就不该返回 assistant 消息 id"

    refreshed = db_session.get(ChatSession, chat_session.id)
    assert refreshed.message_count == 1, "message_count 必须按实际写入行数递增"


@pytest.mark.unit
def test_message_manager_keeps_assistant_row_with_only_tool_calls(
    db_session: Session, converge_user: User, converge_project: Project
):
    """正文为空但有工具调用时仍是有效产出，不能被当成空 assistant 丢掉。"""
    from agent.core.message_manager import MessageManager
    from models import ChatMessage

    chat_session = _make_chat_session(db_session, converge_user, converge_project)
    manager = MessageManager(project_id=converge_project.id, user_id=converge_user.id)

    manager._save_messages_with_session(
        db_session,
        chat_session.id,
        "建一个大纲",
        "",
        [{"tool_name": "create_file", "arguments": {}}],
    )

    db_session.expire_all()
    rows = db_session.exec(
        __import__("sqlmodel").select(ChatMessage).where(
            ChatMessage.session_id == chat_session.id
        )
    ).all()
    assert sorted(r.role for r in rows) == ["assistant", "user"]

    refreshed = db_session.get(ChatSession, chat_session.id)
    assert refreshed.message_count == 2


@pytest.mark.unit
def test_message_count_matches_rows_with_steering(
    db_session: Session, converge_user: User, converge_project: Project
):
    """message_count 写死 +2 会与实际行数脱钩：空白 steering 被跳过时更明显。"""
    from agent.core.message_manager import MessageManager
    from models import ChatMessage

    chat_session = _make_chat_session(db_session, converge_user, converge_project)
    manager = MessageManager(project_id=converge_project.id, user_id=converge_user.id)

    manager._save_messages_with_session(
        db_session,
        chat_session.id,
        "写第二章",
        "写好了",
        steering_messages=["快一点", "   ", ""],
    )

    db_session.expire_all()
    rows = db_session.exec(
        __import__("sqlmodel").select(ChatMessage).where(
            ChatMessage.session_id == chat_session.id
        )
    ).all()
    refreshed = db_session.get(ChatSession, chat_session.id)
    assert len(rows) == 3, "user + 1 条有效 steering + assistant"
    assert refreshed.message_count == len(rows)
