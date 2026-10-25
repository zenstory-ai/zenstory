"""第三轮 review 收敛阶段回归测试：14 个并行组之间的跨组遗留项。

每条都对应一份"由 A 组修一半、B 组文件不在白名单里"的缺口，收敛阶段补齐另一半：

- rank32 后半：parallel_start/parallel_end 必须带 requested_task_count /
  dropped_count，否则前端只知道"跑了 5 个"，模型请求的另外 2 个被静默丢弃。
- bug-02 同族站点：_suggest_auto_current_phase_from_drafts 只查 draft，
  短剧项目（script）的 current_phase 永远推不出来。
- assembler 同族站点：找"前一章"只在 draft ∪ outline 里找，短剧续写丢上一集。
- stream #1：edit_file 返回给 LLM 的 status 恒为 success，
  部分/全部失败时模型无法自我纠正。
- stream #2：append/prepend 的 detail 缺 new_preview。
- stream #3：FileEditEndEventData 缺 failed_count/partial_success/all_failed/warnings。
- tools-core #4：stream_adapter 的 pending-empty-file 精确清除
  （多个待写空文件时不得一次清光）。
- #33 第二半：router 那次非流式 LLM 调用的 usage 必须汇入整轮统计。
- service #2：message_manager 不得写空 assistant 行，
  message_count 必须按实际写入行数递增。
"""

from types import SimpleNamespace

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
# rank32 后半：并行截断信息必须随事件下发
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parallel_start_event_carries_requested_and_dropped_counts():
    """截断时 task_count 仍是实际执行数，另外两个字段说明"还有几个没跑"。"""
    from agent.core.events import parallel_start_event

    event = parallel_start_event(
        execution_id="exec-1",
        task_count=5,
        task_descriptions=["a", "b", "c", "d", "e"],
        requested_task_count=7,
        dropped_count=2,
    )

    assert event.data["task_count"] == 5
    assert event.data["requested_task_count"] == 7
    assert event.data["dropped_count"] == 2


@pytest.mark.unit
def test_parallel_end_event_carries_requested_and_dropped_counts():
    from agent.core.events import parallel_end_event

    event = parallel_end_event(
        execution_id="exec-1",
        total_tasks=5,
        completed=5,
        failed=0,
        duration_ms=12,
        requested_task_count=7,
        dropped_count=2,
    )

    assert event.data["requested_task_count"] == 7
    assert event.data["dropped_count"] == 2


@pytest.mark.unit
def test_parallel_events_default_to_no_truncation():
    """没有截断时两个新字段必须自洽：requested == 实际数、dropped == 0。"""
    from agent.core.events import parallel_end_event, parallel_start_event

    start = parallel_start_event("exec-2", 3, ["a", "b", "c"])
    end = parallel_end_event("exec-2", 3, 3, 0, 5)

    assert start.data["requested_task_count"] == 3
    assert start.data["dropped_count"] == 0
    assert end.data["requested_task_count"] == 3
    assert end.data["dropped_count"] == 0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_parallel_execute_emits_dropped_count_on_truncation(monkeypatch):
    """超过并发上限时，parallel_start/parallel_end 必须报出被丢弃的任务数。"""
    from agent.core import progress_channel
    from agent.tools import parallel_executor as pe

    emitted: list = []
    monkeypatch.setattr(
        progress_channel, "emit_progress", lambda event: emitted.append(event)
    )

    async def fake_handler(params):
        return {"content": [{"type": "text", "text": '{"status": "success"}'}]}

    monkeypatch.setattr(pe, "handle_query_files", fake_handler)

    tasks = [
        {"type": "query_files", "description": f"task-{i}", "params": {}}
        for i in range(pe.MAX_PARALLEL_TASKS + 2)
    ]
    await pe.execute_parallel(tasks)

    starts = [e for e in emitted if e.type.value == "parallel_start"]
    ends = [e for e in emitted if e.type.value == "parallel_end"]
    assert starts and ends

    assert starts[0].data["task_count"] == pe.MAX_PARALLEL_TASKS
    assert starts[0].data["requested_task_count"] == pe.MAX_PARALLEL_TASKS + 2
    assert starts[0].data["dropped_count"] == 2
    assert ends[0].data["dropped_count"] == 2


# ---------------------------------------------------------------------------
# bug-02 同族站点：current_phase 推断必须覆盖 script
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_auto_current_phase_infers_from_script_files(
    db_session: Session, converge_project: Project, converge_user: User
):
    """短剧项目的正文是 script，只查 draft 会让 current_phase 永远推不出来。"""
    from agent.tools.mcp_tools import ToolContext, _suggest_auto_current_phase_from_drafts

    db_session.add(
        File(
            project_id=converge_project.id,
            title="第7集 反杀",
            file_type="script",
            content="正文",
        )
    )
    db_session.commit()

    ToolContext.set_context(
        db_session, converge_user.id, converge_project.id, "sess-converge"
    )
    try:
        suggested = _suggest_auto_current_phase_from_drafts(converge_project.id)
    finally:
        ToolContext.clear_context()

    assert suggested is not None
    assert "7" in suggested


@pytest.mark.unit
def test_auto_current_phase_still_infers_from_draft_files(
    db_session: Session, converge_user: User
):
    """小说项目（draft）的既有行为不能被回归掉。"""
    from agent.tools.mcp_tools import ToolContext, _suggest_auto_current_phase_from_drafts

    project = Project(name="小说项目", owner_id=converge_user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    db_session.add(
        File(project_id=project.id, title="第3章 起势", file_type="draft", content="正文")
    )
    db_session.commit()

    ToolContext.set_context(db_session, converge_user.id, project.id, "sess-converge-2")
    try:
        suggested = _suggest_auto_current_phase_from_drafts(project.id)
    finally:
        ToolContext.clear_context()

    assert suggested is not None
    assert "3" in suggested


@pytest.mark.unit
def test_auto_current_phase_counts_episodes_for_a_screenplay(db_session: Session, converge_user: User):
    """live run 2 场景 A：短剧项目被自动写成「已写至第3章」，剧本应按「集」。"""
    from agent.tools.mcp_tools import ToolContext, _suggest_auto_current_phase_from_drafts

    drama = Project(name="短剧项目", owner_id=converge_user.id, project_type="screenplay")
    short = Project(name="短篇项目", owner_id=converge_user.id, project_type="short")
    db_session.add_all([drama, short])
    db_session.commit()
    db_session.add_all([
        File(project_id=drama.id, title="第3集 车灯", file_type="script", content="正文"),
        File(project_id=short.id, title="第2章 交接", file_type="draft", content="正文"),
    ])
    db_session.commit()

    ToolContext.set_context(db_session, converge_user.id, drama.id, "sess-drama")
    try:
        assert _suggest_auto_current_phase_from_drafts(drama.id) == "已写至第3集"
        # 写到第4集后继续往前推（「第3集」要能被认出来，不然进度会停在这里）。
        drama.current_phase = "已写至第3集"
        db_session.add(File(project_id=drama.id, title="第4集 认亲", file_type="script", content="正文"))
        db_session.commit()
        assert _suggest_auto_current_phase_from_drafts(drama.id) == "已写至第4集"
        assert _suggest_auto_current_phase_from_drafts(short.id) == "已写至第2章"
    finally:
        ToolContext.clear_context()


@pytest.mark.unit
def test_auto_current_phase_ignores_chapters_without_body(db_session: Session, converge_user: User):
    """F-10：第 1–3 章有正文、第 4–20 章只是空标题时，进度不能写成「已写至第20章」。"""
    from agent.tools.mcp_tools import ToolContext, _suggest_auto_current_phase_from_drafts

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

    ToolContext.set_context(db_session, converge_user.id, novel.id, "sess-empty-shells")
    try:
        assert _suggest_auto_current_phase_from_drafts(novel.id) == "已写至第3章"
        assert _suggest_auto_current_phase_from_drafts(drama.id) == "已写至第1集"
        # 只有空壳时不推断进度。
        novel.current_phase = ""
        for f in db_session.exec(select(File).where(File.project_id == novel.id)).all():
            f.content = ""
        db_session.commit()
        assert _suggest_auto_current_phase_from_drafts(novel.id) is None
    finally:
        ToolContext.clear_context()


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

    class _Executor:
        def edit_file(self, **kwargs):
            return fake_result

    monkeypatch.setattr(mcp_tools.ToolContext, "get_executor", classmethod(lambda cls: _Executor()))
    monkeypatch.setattr(
        mcp_tools.ToolContext,
        "_get_context",
        classmethod(lambda cls: {"project_id": "p1"}),
    )

    payload = mcp_tools._edit_file_sync({"id": "f1", "edits": [{"op": "replace"}]})
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
    from agent.stream_adapter import StreamAdapter
    from agent.tools.mcp_tools import ToolContext

    ToolContext.set_context(db_session, "u1", "p1", "s1")
    try:
        ToolContext.set_pending_empty_file("file-a", "第1集")
        ToolContext.set_pending_empty_file("file-b", "第2集")

        adapter = StreamAdapter()
        adapter._clear_pending_empty_file_guard("file-a")

        remaining = {p["file_id"] for p in ToolContext.get_pending_empty_files()}
        assert remaining == {"file-b"}, "只该摘掉 file-a"

        # 无参调用仍是"流结束兜底"的无条件清空
        adapter._clear_pending_empty_file_guard()
        assert ToolContext.get_pending_empty_files() == []
    finally:
        ToolContext.clear_context()


# ---------------------------------------------------------------------------
# #33 第二半：router 的 usage 必须汇入整轮统计
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_router_extracts_usage_from_response():
    from agent.graph.router import _extract_usage

    # 键名必须是规范键（与 runner._usage_dict_from_result 一致），
    # 否则 _merge_usage 按字面键相加时两族键并存，下游只认其中一族。
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=22, total_tokens=33)
    )
    assert _extract_usage(response) == {
        "input_tokens": 11,
        "output_tokens": 22,
        "total_tokens": 33,
    }
    assert _extract_usage(SimpleNamespace()) is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_router_decided_usage_is_merged_into_message_usage():
    """router 那次调用不发 MESSAGE_END，用量只能随 ROUTER_DECIDED 捎回来。
    不汇入累加器就等于整轮统计系统性偏低。"""
    from agent.core.workflow_events import StreamEvent, StreamEventType
    from agent.stream_adapter import StreamAdapter

    adapter = StreamAdapter()

    async def _drain(event):
        async for _ in adapter._process_workflow_event(event):
            pass

    await _drain(
        StreamEvent(
            type=StreamEventType.ROUTER_DECIDED,
            data={
                "initial_agent": "writer",
                "routing_usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                },
            },
        )
    )
    await _drain(
        StreamEvent(
            type=StreamEventType.MESSAGE_END,
            data={
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 900,
                    "output_tokens": 80,
                    "total_tokens": 980,
                },
            },
        )
    )

    usage = adapter.get_last_message_metadata()["usage"]
    assert usage["input_tokens"] == 1000
    assert usage["output_tokens"] == 100
    assert usage["total_tokens"] == 1100
    # 两族键名不得并存：否则 writing_stats_service 只认 input/output 那族，
    # 另一族被静默丢弃，而 total_tokens 却把它算进去了。
    assert "prompt_tokens" not in usage
    assert "completion_tokens" not in usage


@pytest.mark.asyncio
@pytest.mark.unit
async def test_router_decided_without_usage_does_not_break_accumulator():
    """router 走 fallback（没有 usage）时不能把已累计的值抹掉。"""
    from agent.core.workflow_events import StreamEvent, StreamEventType
    from agent.stream_adapter import StreamAdapter

    adapter = StreamAdapter()
    async for _ in adapter._process_workflow_event(
        StreamEvent(
            type=StreamEventType.MESSAGE_END,
            data={"usage": {"total_tokens": 50}},
        )
    ):
        pass
    async for _ in adapter._process_workflow_event(
        StreamEvent(
            type=StreamEventType.ROUTER_DECIDED,
            data={"initial_agent": "writer", "routing_usage": None},
        )
    ):
        pass

    assert adapter.get_last_message_metadata()["usage"] == {"total_tokens": 50}


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
