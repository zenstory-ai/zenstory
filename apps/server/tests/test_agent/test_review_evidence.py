"""送审前的程序检测证据（agent/graph/review_evidence.py + writing_graph 交接点）。

全部 mock 模型，不调用 DeepSeek；图测试用测试 SQLite 里的真实文件行。
"""

from unittest.mock import AsyncMock, patch

import pytest

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.graph.review_evidence import (
    MAX_REPETITION_EVIDENCE,
    find_numeric_fact_pairs,
    find_repetition_evidence,
)

# 15 个汉字（去掉标点后）。
REPEAT_15 = "窗外的雨下了一整夜，敲着铁皮屋顶"
# 14 个汉字。
REPEAT_14 = "窗外的雨下了一整夜，敲着铁皮屋"


def _chapter(*paragraphs: str) -> str:
    return "\n".join(f"　　{p}" for p in paragraphs)


# ----------------------------------------------------------------- 复读


@pytest.mark.unit
def test_cross_chapter_repeat_of_15_chars_is_reported_with_paragraph_numbers():
    previous = _chapter("林晚回到小镇。", f"那一晚，{REPEAT_15}。她没睡。")
    target = _chapter("第二天清晨。", "她推开窗。", f"{REPEAT_15}，像是谁在数日子。")

    evidence = find_repetition_evidence("第三章", target, [("第二章", previous)])

    assert evidence == [
        "《第三章》第 3 段与《第二章》第 2 段逐字重复：「窗外的雨下了一整夜，敲着铁皮屋顶」（共 15 字）"
    ]


@pytest.mark.unit
def test_cross_chapter_repeat_of_14_chars_is_not_reported():
    previous = _chapter("林晚回到小镇。", f"那一晚，{REPEAT_14}。她没睡。")
    target = _chapter("第二天清晨。", f"{REPEAT_14}，像是谁在数日子。")

    assert find_repetition_evidence("第三章", target, [("第二章", previous)]) == []


@pytest.mark.unit
def test_punctuation_and_whitespace_differences_do_not_hide_a_repeat():
    previous = _chapter("“窗外的雨，下了一整夜！敲着铁皮屋顶。”她说。")
    target = _chapter("窗外的雨下了 一整夜……敲着、铁皮屋顶")

    evidence = find_repetition_evidence("第三章", target, [("第二章", previous)])

    assert len(evidence) == 1
    assert evidence[0].startswith("《第三章》第 1 段与《第二章》第 1 段逐字重复：")
    assert evidence[0].endswith("（共 15 字）")


@pytest.mark.unit
def test_long_repeat_is_merged_and_excerpt_truncated_to_30_chars():
    long_text = "她站在码头上看着那艘船一点一点驶远直到海面上只剩下一道白色的浪痕"
    previous = _chapter(long_text)
    target = _chapter("开头。", long_text)

    evidence = find_repetition_evidence("第三章", target, [("第二章", previous)])

    assert evidence == [
        f"《第三章》第 2 段与《第二章》第 1 段逐字重复：「{long_text[:30]}…」（共 {len(long_text)} 字）"
    ]


@pytest.mark.unit
def test_adjacent_paragraph_repeat_and_identical_paragraphs_are_reported():
    target = _chapter(
        f"她低头看着手机，{REPEAT_15}。",
        f"{REPEAT_15}，她又想起那通电话。",
        "中间是完全不同的一段描写。",
        "她把伞收起来靠在门边。",
        "又是一段不同的描写内容。",
        "她把伞收起来，靠在门边。",
    )

    evidence = find_repetition_evidence("第三章", target, [])

    assert "《第三章》第 2 段与《第三章》第 1 段逐字重复：「窗外的雨下了一整夜，敲着铁皮屋顶」（共 15 字）" in evidence
    assert "《第三章》第 6 段与《第三章》第 4 段逐字重复：「她把伞收起来，靠在门边。」（共 10 字）" in evidence
    assert len(evidence) == 2


@pytest.mark.unit
def test_identical_adjacent_paragraphs_are_reported_once():
    target = _chapter("她把伞收起来靠在门边。", "她把伞收起来靠在门边。")

    evidence = find_repetition_evidence("第三章", target, [])

    assert evidence == ["《第三章》第 2 段与《第三章》第 1 段逐字重复：「她把伞收起来靠在门边。」（共 10 字）"]


@pytest.mark.unit
def test_repetition_evidence_is_capped_and_sorted_longest_first():
    base = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥金木水火土日月星"
    pieces = [f"{base[: 15 + i]}" for i in range(10)]
    # 每段之间用不同的长句隔开，避免片段之间相连被合并。
    previous = "\n".join(f"{piece}。完全无关的隔断第{i}句话写在这里。" for i, piece in enumerate(pieces))
    target = "\n".join(f"{piece}，后面接着另外的句子内容{'一二三四五六七八九十'[i]}。" for i, piece in enumerate(pieces))

    evidence = find_repetition_evidence("第三章", target, [("第二章", previous)])

    assert len(evidence) == MAX_REPETITION_EVIDENCE
    lengths = [int(item.rsplit("共 ", 1)[1].split(" 字")[0]) for item in evidence]
    assert lengths == sorted(lengths, reverse=True)
    assert lengths[0] == 24


# ----------------------------------------------------------------- 数字事实


@pytest.mark.unit
@pytest.mark.parametrize("target_age", ["五岁", "5岁"])
def test_age_mismatch_against_character_card_and_previous_chapter(target_age):
    target = ("第三章", _chapter(f"念念今年{target_age}了，正是爱跑的年纪。"))
    references = [
        ("第二章", _chapter("那天，念念才四岁。")),
        ("角色：念念", "年龄：四岁\n性格：怕黑，爱笑"),
    ]

    pairs = find_numeric_fact_pairs(target, references)

    assert pairs == [
        f"《第三章》「念念今年{target_age}」↔《第二章》「念念才四岁」",
        f"《第三章》「念念今年{target_age}」↔《角色：念念》「四岁」",
    ]


@pytest.mark.unit
def test_age_mismatch_within_the_same_file():
    target = ("第三章", _chapter("孩子五岁，刚上幼儿园。", "她想起孩子四岁那年的冬天。"))

    assert find_numeric_fact_pairs(target, []) == ["《第三章》「孩子五岁」↔《第三章》第 2 段「孩子四岁」"]


@pytest.mark.unit
def test_different_subjects_and_matching_values_are_not_paired():
    target = ("第三章", _chapter("念念五岁。", "林晚二十八岁。"))
    references = [
        ("第二章", _chapter("阿远四岁。", "林晚二十八岁。")),
        ("角色：阿远", "年龄：四岁"),
    ]

    assert find_numeric_fact_pairs(target, references) == []


@pytest.mark.unit
def test_approximate_ages_are_ignored():
    target = ("第三章", _chapter("念念三四岁的样子。"))

    assert find_numeric_fact_pairs(target, [("第二章", "念念五岁。")]) == []


# ------------------------------------------------------- 写作图的送审交接点


def _text(text: str) -> StreamEvent:
    return StreamEvent(type=StreamEventType.TEXT, data={"text": text})


def _write_done(call_id: str) -> list[StreamEvent]:
    return [
        StreamEvent(
            type=StreamEventType.TOOL_USE,
            data={"id": call_id, "name": "edit_file", "status": "complete"},
        ),
        StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "tool_use_id": call_id,
                "name": "edit_file",
                "result": {"content": [{"type": "text", "text": '{"status": "success"}'}]},
            },
        ),
    ]


@pytest.fixture
def novel_project(db_session):
    from models import File, Project, User

    user = User(
        email="review_evidence@example.com",
        username="reviewevidence",
        hashed_password="x",
        name="Review Evidence",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    project = Project(name="Review Evidence", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()

    folder = File(project_id=project.id, title="正文", file_type="folder")
    db_session.add(folder)
    db_session.commit()

    def add(title: str, content: str, *, file_type: str = "draft", order: int = 0, parent=folder.id) -> File:
        row = File(
            project_id=project.id,
            title=title,
            content=content,
            file_type=file_type,
            order=order,
            parent_id=parent if file_type != "character" else None,
        )
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)
        return row

    return {"project": project, "add": add}


async def _run_writer_then_reviewer(
    db_session, project_id, written_id, *, workflow_plan="quick", initial="writer", writer_text=None
):
    from agent.graph.writing_graph import run_writing_workflow_streaming
    from agent.tools.mcp_tools import ToolContext

    reviewer_messages: list[str] = []
    calls: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        guard = state["repeat_read_guard"]
        if agent_type == "writer":
            guard._record_write(written_id, "第三章")
            for event in _write_done("w1"):
                yield event
            yield _text(writer_text or ("第三章写好了。" + "她走在雨里。" * 40))
            return
        reviewer_messages.append(state["user_message"])
        yield _text("审查通过。[TASK_COMPLETE]")

    router = AsyncMock(
        return_value={"current_agent": initial, "workflow_plan": workflow_plan, "workflow_agents": []}
    )
    ToolContext.set_context(session=db_session, user_id="u", project_id=project_id, session_id="s")
    with (
        patch("agent.graph.writing_graph.router_node", router),
        patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
        patch("agent.tools.mcp_tools.ToolContext.refresh_file_inventory", return_value={}),
        patch(
            "agent.graph.writing_graph._auto_finalize_task_board_on_completion",
            AsyncMock(return_value=[]),
        ),
    ):
        events = [
            event
            async for event in run_writing_workflow_streaming(
                state={"user_message": "写第三章", "messages": [], "system_prompt": ""},
                thread_id="t",
                auto_review_threshold=100,
            )
        ]
    return events, reviewer_messages, calls


def _review_packet(events):
    handoff = next(
        e for e in events if e.type == StreamEventType.HANDOFF and e.data["target_agent"] == "quality_reviewer"
    )
    return handoff.data["handoff_packet"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_auto_review_handoff_appends_detected_evidence(monkeypatch, db_session, novel_project):
    monkeypatch.setenv("AGENT_ENABLE_GRAPH_AUTO_REVIEW", "true")
    add = novel_project["add"]
    add("第一章", _chapter("林晚回到小镇。"), order=1)
    add("第二章", _chapter("那天，念念才四岁。", f"那一晚，{REPEAT_15}。"), order=2)
    target = add("第三章", _chapter("念念五岁了。", f"{REPEAT_15}，像是谁在数日子。"), order=3)
    add("第四章", _chapter(f"{REPEAT_15}。"), order=4)  # 排在后面，不作参照
    add("念念", "年龄：四岁", file_type="character")

    events, reviewer_messages, _ = await _run_writer_then_reviewer(
        db_session, novel_project["project"].id, target.id
    )

    assert len(reviewer_messages) == 1
    message = reviewer_messages[0]
    assert "[自动检测：需核对]" in message
    block = message.split("[自动检测：需核对]\n", 1)[1]
    assert "- 《第三章》第 2 段与《第二章》第 2 段逐字重复：「窗外的雨下了一整夜，敲着铁皮屋顶」（共 15 字）" in block
    assert "- 《第三章》「念念五岁」↔《第二章》「念念才四岁」" in block
    assert "- 《第三章》「念念五岁」↔《角色：念念》「四岁」" in block
    assert "第四章" not in block
    assert "auto_checks=repeat:1,facts:2" in _review_packet(events)["evidence"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_auto_review_handoff_without_findings_has_no_evidence_block(monkeypatch, db_session, novel_project):
    monkeypatch.setenv("AGENT_ENABLE_GRAPH_AUTO_REVIEW", "true")
    add = novel_project["add"]
    add("第二章", _chapter("林晚回到小镇。"), order=2)
    target = add("第三章", _chapter("第二天清晨，她推开窗。"), order=3)

    events, reviewer_messages, _ = await _run_writer_then_reviewer(
        db_session, novel_project["project"].id, target.id
    )

    assert len(reviewer_messages) == 1
    assert "[自动检测：需核对]" not in reviewer_messages[0]
    assert "auto_checks=repeat:0,facts:0" in _review_packet(events)["evidence"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_auto_review_still_hands_off_when_checks_fail(monkeypatch, db_session, novel_project, caplog):
    monkeypatch.setenv("AGENT_ENABLE_GRAPH_AUTO_REVIEW", "true")
    target = novel_project["add"]("第三章", _chapter(f"{REPEAT_15}。", f"{REPEAT_15}。"), order=3)

    with patch(
        "agent.graph.writing_graph._collect_review_auto_checks",
        side_effect=RuntimeError("db gone"),
    ):
        events, reviewer_messages, calls = await _run_writer_then_reviewer(
            db_session, novel_project["project"].id, target.id
        )

    assert calls == ["writer", "quality_reviewer"]
    assert "[自动检测：需核对]" not in reviewer_messages[0]
    assert all(not item.startswith("auto_checks=") for item in _review_packet(events)["evidence"])
    assert any("Review auto-checks failed" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_auto_review_still_hands_off_when_checks_time_out(monkeypatch, db_session, novel_project):
    import time

    monkeypatch.setenv("AGENT_ENABLE_GRAPH_AUTO_REVIEW", "true")
    monkeypatch.setattr("agent.graph.writing_graph.REVIEW_AUTO_CHECK_TIMEOUT_SECONDS", 0.05)
    target = novel_project["add"]("第三章", _chapter("第二天清晨。"), order=3)

    def slow(_ids):
        time.sleep(0.3)
        return (["不应出现"], [])

    with patch("agent.graph.writing_graph._collect_review_auto_checks", side_effect=slow):
        _, reviewer_messages, calls = await _run_writer_then_reviewer(
            db_session, novel_project["project"].id, target.id
        )

    assert calls == ["writer", "quality_reviewer"]
    assert "不应出现" not in reviewer_messages[0]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_review_only_request_does_not_run_auto_checks(monkeypatch, db_session, novel_project):
    """用户要求审查（review_only）：审稿人让 writer 改完再交回来，也不附程序检测。"""
    from agent.graph.writing_graph import run_writing_workflow_streaming
    from agent.tools.mcp_tools import ToolContext

    monkeypatch.setenv("AGENT_ENABLE_GRAPH_AUTO_REVIEW", "true")
    target = novel_project["add"]("第三章", _chapter(f"{REPEAT_15}。", f"{REPEAT_15}。"), order=3)
    calls: list[str] = []
    reviewer_messages: list[str] = []

    def _handoff(to: str, context: str) -> StreamEvent:
        packet = {"target_agent": to, "reason": "交接", "context": context, "completed": [], "todo": [], "evidence": []}
        return StreamEvent(
            type=StreamEventType.HANDOFF,
            data={"target_agent": to, "reason": "交接", "context": context, "handoff_packet": packet},
        )

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if agent_type == "writer":
            state["repeat_read_guard"]._record_write(target.id, "第三章")
            for event in _write_done("w1"):
                yield event
            yield _text("改好了。" + "她走在雨里。" * 40)
            yield _handoff("quality_reviewer", "已按意见修改，请复审")
            return
        reviewer_messages.append(state["user_message"])
        if len(reviewer_messages) == 1:
            yield _text("第二段重复。")
            yield _handoff("writer", "删掉重复的第二段")
            return
        yield _text("复审通过。[TASK_COMPLETE]")

    collector = AsyncMock(return_value=(["不应出现"], []))
    ToolContext.set_context(session=db_session, user_id="u", project_id=novel_project["project"].id, session_id="s")
    with (
        patch(
            "agent.graph.writing_graph.router_node",
            AsyncMock(
                return_value={"current_agent": "quality_reviewer", "workflow_plan": "review_only", "workflow_agents": []}
            ),
        ),
        patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
        patch("agent.tools.mcp_tools.ToolContext.refresh_file_inventory", return_value={}),
        patch(
            "agent.graph.writing_graph._auto_finalize_task_board_on_completion",
            AsyncMock(return_value=[]),
        ),
        patch("agent.graph.writing_graph._review_auto_checks", collector),
    ):
        _ = [
            event
            async for event in run_writing_workflow_streaming(
                state={"user_message": "帮我审一下第三章", "messages": [], "system_prompt": ""},
                thread_id="t",
                auto_review_threshold=100,
            )
        ]

    assert calls == ["quality_reviewer", "writer", "quality_reviewer"]
    collector.assert_not_called()
    assert all("[自动检测：需核对]" not in message for message in reviewer_messages)


# 流式落库（P1b）已把半角引号规范成 “”；送审内容必须是库里的这份，而不是模型的原始输出，
# 否则审稿人会把已修好的半角引号当客观缺陷打回，白白多一轮付费返工。
_STREAMED_DIALOGUE = '他推开门："我回来了。"她没抬头："嗯。"\n' * 6
_PERSISTED_DIALOGUE = _STREAMED_DIALOGUE.replace('"我回来了。"', "“我回来了。”").replace('"嗯。"', "“嗯。”")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_auto_review_payload_uses_persisted_normalized_text(monkeypatch, db_session, novel_project):
    monkeypatch.setenv("AGENT_ENABLE_GRAPH_AUTO_REVIEW", "true")
    target = novel_project["add"]("第三章", _PERSISTED_DIALOGUE.strip(), order=3)

    _, reviewer_messages, calls = await _run_writer_then_reviewer(
        db_session,
        novel_project["project"].id,
        target.id,
        writer_text=f"<file>{_STREAMED_DIALOGUE}</file>",
    )

    assert calls == ["writer", "quality_reviewer"]
    payload = reviewer_messages[0].split("[待审查内容]\n", 1)[1]
    assert "他推开门：“我回来了。”她没抬头：“嗯。”" in payload
    assert '"' not in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_auto_review_payload_falls_back_to_writer_output_when_read_fails(
    monkeypatch, db_session, novel_project
):
    monkeypatch.setenv("AGENT_ENABLE_GRAPH_AUTO_REVIEW", "true")
    target = novel_project["add"]("第三章", _PERSISTED_DIALOGUE.strip(), order=3)

    with patch(
        "agent.graph.writing_graph._collect_review_payload_text",
        side_effect=RuntimeError("db gone"),
    ):
        _, reviewer_messages, calls = await _run_writer_then_reviewer(
            db_session,
            novel_project["project"].id,
            target.id,
            writer_text=f"<file>{_STREAMED_DIALOGUE}</file>",
        )

    assert calls == ["writer", "quality_reviewer"]
    assert '他推开门："我回来了。"' in reviewer_messages[0].split("[待审查内容]\n", 1)[1]
