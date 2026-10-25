"""送审前的程序检测证据（agent/graph/review_evidence.py + writing_graph 交接点）。

全部 mock 模型，不调用 DeepSeek；图测试用测试 SQLite 里的真实文件行。
"""

from unittest.mock import AsyncMock, patch

import pytest

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.graph.review_evidence import (
    MAX_CARD_FACTS,
    MAX_REPETITION_EVIDENCE,
    find_repetition_evidence,
    list_character_card_facts,
    quote_author_requirement_sources,
)
from tests.test_agent.body_write_events import edit_file_events

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


# ----------------------------------------------------------------- 角色卡事实


@pytest.mark.unit
def test_character_card_age_fields_are_listed_as_written():
    cards = [
        ("念念", "年龄：四岁\n性格：怕黑，爱笑"),
        ("阿远", "性格：沉默，三年前离家"),
        ("老周", "外号：老周头\n年龄: 52"),
        ("空卡", "   "),
    ]

    assert list_character_card_facts(cards) == ["《角色：念念》年龄：四岁", "《角色：老周》年龄：52"]


@pytest.mark.unit
def test_character_card_facts_are_capped_and_deduplicated():
    cards = [("念念", "年龄：四岁\n年龄：四岁")] + [(f"角色{i}", f"年龄：{i + 10}") for i in range(10)]

    facts = list_character_card_facts(cards)

    assert facts[0] == "《角色：念念》年龄：四岁"
    assert len(facts) == MAX_CARD_FACTS
    assert facts.count("《角色：念念》年龄：四岁") == 1


# ------------------------------------------------------- 写作图的送审交接点


def _text(text: str) -> StreamEvent:
    return StreamEvent(type=StreamEventType.TEXT, data={"text": text})


def _write_done(call_id: str) -> list[StreamEvent]:
    # 一次写进正文（draft）的成功 edit_file：自动质检门只看正文类文件的写入字数。
    return edit_file_events(call_id, "林晚推开门，雨还在下。" * 30)


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
    assert "- 《角色：念念》年龄：四岁" in block
    assert "↔" not in block
    assert "第四章" not in block
    assert "auto_checks=repeat:1,facts:1" in _review_packet(events)["evidence"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_auto_review_handoff_does_not_guess_numeric_conflicts_from_prose(
    monkeypatch, db_session, novel_project
):
    """正文里「三年前」「五年前」这类时间数字不再被当作同一主语的矛盾送审（误报）。"""
    monkeypatch.setenv("AGENT_ENABLE_GRAPH_AUTO_REVIEW", "true")
    add = novel_project["add"]
    add("第二章", _chapter("林晚想起三年前的冬天。"), order=2)
    target = add("第三章", _chapter("林晚想起五年前的夏天。", "林晚想起三年前的那场雪。"), order=3)

    events, reviewer_messages, _ = await _run_writer_then_reviewer(
        db_session, novel_project["project"].id, target.id
    )

    assert len(reviewer_messages) == 1
    assert "[自动检测：需核对]" not in reviewer_messages[0]
    assert "auto_checks=repeat:0,facts:0" in _review_packet(events)["evidence"]


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


@pytest.mark.unit
def test_review_neighbors_use_metadata_sequence_before_misleading_title(db_session, novel_project):
    from sqlmodel import Session

    from agent.graph.writing_graph import _collect_review_auto_checks
    from agent.tools.mcp_tools import ToolContext

    add = novel_project["add"]
    previous = add("第1章 误导标题", "相邻参照正文", order=0)
    previous.set_metadata({"chapter_number": 12})
    target = add("第13章", "本章正文", order=13)
    future = add("第2章 也是误导标题", "未来正文不应当作前章", order=0)
    future.set_metadata({"chapter_number": 14})
    db_session.add_all([previous, future])
    db_session.commit()
    ToolContext.set_context(None, novel_project["project"].owner_id, novel_project["project"].id, None,
                            create_session_func=lambda: Session(db_session.get_bind()))
    try:
        with patch("agent.graph.writing_graph.find_repetition_evidence", return_value=[]) as finder:
            _collect_review_auto_checks([target.id])
        assert finder.call_args.args[2] == [(previous.title, previous.content)]
    finally:
        ToolContext.clear_context()


# ----------------------------------------------------------- 作者要求原文


@pytest.mark.unit
def test_author_requirement_sources_preserve_polarity_punctuation_and_languages_verbatim():
    sources = [
        ("作者本轮原话", "不用写满2000字；别再补字数。"),
        ("Author message", "Keep it under 1,500 words—do not pad it."),
        ("作者补充", "終わりは短く、余韻を残して。"),
    ]

    assert quote_author_requirement_sources(sources) == [
        "【作者本轮原话】\n不用写满2000字；别再补字数。",
        "【Author message】\nKeep it under 1,500 words—do not pad it.",
        "【作者补充】\n終わりは短く、余韻を残して。",
    ]


@pytest.mark.unit
def test_author_requirement_sources_prioritize_newest_but_return_selected_sources_chronologically():
    oldest = ("早期", "早期要求" * 30)
    middle = ("上轮", "保留这条要求")
    newest = ("本轮", "以本轮说法为准")
    middle_quote = "【上轮】\n保留这条要求"
    newest_quote = "【本轮】\n以本轮说法为准"

    assert quote_author_requirement_sources(
        [oldest, middle, newest], max_chars=len(middle_quote) + 1 + len(newest_quote)
    ) == [middle_quote, newest_quote]


@pytest.mark.unit
def test_author_requirement_source_truncation_is_explicit_and_counts_labels_and_marker_in_budget():
    prefix = "【作者原话】\n"
    marker = "\n…（中间内容已截断）…\n"
    budget = len(prefix) + 5 + len(marker)

    quoted = quote_author_requirement_sources(
        [("作者原话", "甲乙丙丁戊己庚辛壬癸" * 3)], max_chars=budget
    )

    assert quoted == [prefix + "甲乙丙" + marker + "壬癸"]
    assert sum(map(len, quoted)) == budget


@pytest.mark.unit
def test_author_requirement_sources_ignore_structurally_invalid_entries_without_rewriting_valid_text():
    sources = [
        ("", "  保留前后空格  "),
        ("空文本", ""),
        ("非文本", None),
        "not-a-source-pair",
    ]

    assert quote_author_requirement_sources(sources) == ["【来源】\n  保留前后空格  "]
    assert quote_author_requirement_sources([], max_chars=10) == []
    assert quote_author_requirement_sources([("x", "text")], max_chars=0) == []


def test_long_source_keeps_author_instructions_at_both_ends_instead_of_only_sample_opening():
    source = "按第三人称写，保留这个声线。\n" + "这是供参考的样文。" * 700 + "\n最后补充：不要写满2000字，也不要解释结尾。"
    [quoted] = quote_author_requirement_sources([("本轮原话", source)])
    assert quoted.startswith("【本轮原话】\n按第三人称写，保留这个声线。")
    assert quoted.endswith("最后补充：不要写满2000字，也不要解释结尾。")
    assert len(quoted) == 4000


@pytest.mark.unit
def test_long_latest_source_cannot_starve_project_and_earlier_sources():
    sources = [("项目写作风格", "保持限知视角，不替人物解释。"),
               ("项目备注", "保留人物已经作出的选择。"),
               ("早期作者要求", "不用写满2000字。") ]
    sources += [(f"历史{i}", f"历史开头{i}：" + "合成资料" * 200 + f"：历史结尾{i}") for i in range(40)]
    sources += [("本轮原话", "本轮开头：" + "合成样文" * 2000 + "：本轮结尾，保留留白。") ]
    quoted = quote_author_requirement_sources(sources)
    assert len(quoted) == len(sources)
    assert len("\n".join(quoted)) <= 4000
    for (label, text), excerpt in zip(sources, quoted, strict=True):
        assert excerpt.startswith(f"【{label}】\n" + text[:2])
        assert excerpt.endswith(text[-2:])
    assert sources[0][1] in quoted[0]
    assert sources[1][1] in quoted[1]
    assert sources[2][1] in quoted[2]
    assert quoted[-1].endswith("：本轮结尾，保留留白。")
