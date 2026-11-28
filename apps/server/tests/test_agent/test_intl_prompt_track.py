"""Regression tests for ZenStory's foreign-language writing prompt track (PR 1/3).

Covers:
1. English static prompt bucket (`force_en=True`) KV-cache prefix stability, DB-only 503 gate,
   and zero hardcoded Chinese outline/guideline filenames across both `zh` and `en` buckets.
2. Dual `SUMMARY_PREFIX` / `SUMMARY_PREFIX_EN` compaction, English retry prompt, and cold-replay parity.
3. Symmetric display-only `drop_cjk_narration` filtering in `AgentTextShaper` and `loop/engine.py`.
4. English variants for loop/resume/steering/truncation/soft-landing/turn-context templates.
5. Official skills L1 catalog localization, English lookup in `find_active_skill`, language fence,
   localized `skill_matched` SSE events, and language-aware default `ChatSession` titles.
6. Multilingual `suggest_service.py` language matching across `en/id/pt/vi/es/ru/ko/fr/pl/ar`.
"""

from __future__ import annotations

import json
import re
from os.path import commonprefix
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlmodel import Session

from agent.context.assembler import PREVIOUS_CHAPTER_NOTE_EN, ContextAssembler
from agent.core.author_facing_text import (
    AgentTextShaper,
    is_cjk_process_narration,
    is_english_process_narration,
    text_contains_cjk,
)
from agent.core.message_manager import AGENT_DIRECTIVE_HEADER_EN, MessageManager
from agent.core.output_truncation import OUTPUT_TRUNCATED_HISTORY_EN, OUTPUT_TRUNCATED_RESUME_EN
from agent.core.run_meter import AgentRunMeter
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.loop.compaction import (
    _COMPACT_RETRY_NOTE_EN,
    _is_user_turn_item,
    archive_old_tool_results,
    compact_history,
)
from agent.loop.history import (
    LoopHistory,
    is_summary_message,
    load_loop_history,
    summary_message,
)
from agent.loop.prompts import (
    COMPACT_PROMPT,
    COMPACT_PROMPT_EN,
    LOOP_INPUT_LAYOUT_SECTION_EN,
    LOOP_WORKING_RULES,
    LOOP_WORKING_RULES_EN,
    SUMMARY_PREFIX,
    SUMMARY_PREFIX_EN,
)
from agent.loop.run_support import STEERING_FOLLOWUP_CONTEXT_EN
from agent.loop.turn_context import build_loop_turn_context
from agent.loop.ui_actions import build_ui_action_turn_section
from agent.openai_agents.runner import (
    LOOP_SOFT_LANDING_PROMPT_EN,
    LOOP_TIME_LANDING_PROMPT_EN,
    _format_tool_error,
    _ModelCallCountingFilter,
)
from agent.prompts import (
    GENRE_NORMS_EN,
    get_genre_norms,
    get_prompt_for_project_type,
)
from agent.prompts.base import (
    CONTENT_RED_LINES_EN,
    DIALOGUE_GUIDELINES_EN,
    IMPACT_ANALYSIS_EXAMPLES_EN,
    LOOP_AUTHOR_REPLY_PROTOCOL_EN,
    LOOP_TOOL_USAGE_PROTOCOL_EN,
    OUTPUT_PROTOCOL_EN,
    PRODUCT_IDENTITY_PROTOCOL_EN,
    UNIFIED_EXECUTION_PROTOCOL_EN,
)
from agent.schemas.context import ContextItem, ContextPriority
from agent.service import (
    DEFAULT_CHAT_SESSION_TITLE_EN,
    DEFAULT_CHAT_SESSION_TITLE_ZH,
    AgentService,
)
from agent.skills.active_skills import find_active_skill, load_active_skills, same_name_skills
from agent.skills.context_injector import SkillContextInjector
from agent.suggest_service import ENGLISH_OUTPUT_RULE, SuggestService
from core.error_handler import APIException
from models import ChatMessage, ChatSession, File, Project, PublicSkill, User, UserAddedSkill, UserSkill
from services.core.auth_service import hash_password


def _seed_user_project(
    db_session: Session,
    *,
    project_type: str = "novel",
) -> tuple[User, Project]:
    suffix = uuid4().hex[:8]
    user = User(
        email=f"intl-{suffix}@example.com",
        username=f"intl_{suffix}",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name=f"Intl Project {suffix}", owner_id=user.id, project_type=project_type)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return user, project


# =============================================================================
# 1. Static English Prompt Track & Zero Hardcoded Chinese Outline Names
# =============================================================================


@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs")
@pytest.mark.parametrize("project_type", ["novel", "short", "screenplay"])
def test_english_static_prefix_is_clean_and_shared_across_projects(db_session: Session, project_type: str):
    """Two distinct English projects share 100% of the static English prefix before any UUIDs,
    with zero Chinese outline filenames, zero Chinese screenplay markers, and zero CJK characters."""
    user_a, proj_a = _seed_user_project(db_session, project_type=project_type)
    user_b, proj_b = _seed_user_project(db_session, project_type=project_type)

    catalog_en = "## Available Writing Skills\n\n- **Create an Outline**: Plan chapter outlines."
    prompt_a = MessageManager(proj_a.id, user_a.id).build_system_prompt(
        session=db_session, language="en", skill_catalog=catalog_en
    )
    prompt_b = MessageManager(proj_b.id, user_b.id).build_system_prompt(
        session=db_session, language="en", skill_catalog=catalog_en
    )

    shared = commonprefix([prompt_a, prompt_b])
    for block in (
        PRODUCT_IDENTITY_PROTOCOL_EN,
        CONTENT_RED_LINES_EN,
        OUTPUT_PROTOCOL_EN,
        LOOP_AUTHOR_REPLY_PROTOCOL_EN,
        UNIFIED_EXECUTION_PROTOCOL_EN,
        LOOP_TOOL_USAGE_PROTOCOL_EN.rstrip(),
        IMPACT_ANALYSIS_EXAMPLES_EN,
        get_genre_norms(project_type, force_en=True),
        "## Reply & Output Language Consistency",
        LOOP_INPUT_LAYOUT_SECTION_EN,
        LOOP_WORKING_RULES_EN,
        catalog_en,
    ):
        assert block in shared, f"Expected static block in shared prefix: {block[:60]!r}"

    # Dialogue guidelines are included when DB config enables include_dialogue_guidelines=True.
    from agent.prompts.base import get_base_prompt_static_prefix

    en_with_dialogue = get_base_prompt_static_prefix(
        {
            "role_definition": "Role",
            "capabilities": "Cap",
            "content_structure": "Struct",
            "file_types": "Types",
            "writing_guidelines": "Guide",
            "include_dialogue_guidelines": True,
        },
        force_en=True,
    )
    assert DIALOGUE_GUIDELINES_EN in en_with_dialogue

    # No project UUIDs before suffix; project ID sits at the very end.
    assert proj_a.id not in shared
    assert proj_b.id not in shared
    assert prompt_a.endswith(f"## Current Project ID: {proj_a.id}")
    assert prompt_b.endswith(f"## Current Project ID: {proj_b.id}")

    # Zero CJK characters anywhere in the English static prompt bucket (including screenplay).
    assert not text_contains_cjk(prompt_a)
    assert "2-1 公司大堂 日 内" not in prompt_a
    assert "分章大纲" not in prompt_a
    assert "分集大纲" not in prompt_a
    assert "写作要求" not in prompt_a
    assert not re.search(r"\{[A-Za-z_]+\}", LOOP_WORKING_RULES_EN)
    assert not re.search(r"\{[A-Za-z_]+\}", GENRE_NORMS_EN[project_type])


@pytest.mark.unit
def test_zh_working_rules_also_removed_hardcoded_chinese_outline_filenames():
    """Even in the zh bucket, hardcoded Chinese outline/guideline filenames are gone."""
    assert "分章大纲" not in LOOP_WORKING_RULES
    assert "分集大纲" not in LOOP_WORKING_RULES
    assert "写作要求" not in LOOP_WORKING_RULES
    assert "非中文作品" in LOOP_WORKING_RULES


@pytest.mark.unit
@pytest.mark.parametrize("project_type", ["novel", "short", "screenplay"])
def test_force_en_still_enforces_db_only_503_gate(monkeypatch, project_type: str):
    """Missing/inactive DB writing config still raises 503 even when force_en=True."""
    from agent import prompts

    monkeypatch.setattr(prompts, "_load_db_configs", lambda: {})
    with pytest.raises(APIException) as excinfo:
        get_prompt_for_project_type(project_type, "project-1", {}, force_en=True)
    assert excinfo.value.status_code == 503


# =============================================================================
# 2. Dual SUMMARY_PREFIX & English Compaction / History Replay
# =============================================================================


@pytest.mark.unit
def test_dual_summary_prefix_recognition_and_synthetic_user_filtering():
    zh_msg = summary_message("摘要内容", english=False)
    en_msg = summary_message("Handoff summary", english=True)

    assert zh_msg["content"].startswith(SUMMARY_PREFIX)
    assert en_msg["content"].startswith(SUMMARY_PREFIX_EN)
    assert is_summary_message(zh_msg) is True
    assert is_summary_message(en_msg) is True
    assert is_summary_message({"role": "user", "content": "Write chapter 2"}) is False

    # Synthetic user items (including EN summary & directive) must not count as real user turns.
    assert _is_user_turn_item(zh_msg) is False
    assert _is_user_turn_item(en_msg) is False
    assert _is_user_turn_item({"role": "user", "content": f"{AGENT_DIRECTIVE_HEADER_EN}\n\nRetry"}) is False
    assert _is_user_turn_item({"role": "user", "content": "Please continue chapter 3."}) is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_compact_history_english_uses_en_prompt_and_en_retry_note():
    """compact_history(english=True) uses COMPACT_PROMPT_EN, _COMPACT_RETRY_NOTE_EN on retry,
    and emits SUMMARY_PREFIX_EN in compacted items."""
    history = LoopHistory(
        items=[
            {"role": "user", "content": "Write Chapter 1 in Indonesian."},
            {"role": "assistant", "content": "Bab 1 selesai."},
        ],
        last_prompt_tokens=350_000,
        last_message_id="msg-2",
        author_messages=["Write Chapter 1 in Indonesian."],
    )
    captured_inputs: list[list[dict]] = []

    fake_provider = SimpleNamespace(model="minimax-m2", uses_responses_api=False)
    empty_resp = SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="", tool_calls=None))],
        usage=None,
    )
    good_resp = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="## Handoff\n- Bab 1 done.", tool_calls=None),
            )
        ],
        usage=None,
    )
    responses = [empty_resp, good_resp]

    async def fake_chat_completion(**kwargs):
        captured_inputs.append(kwargs["messages"])
        return responses.pop(0), fake_provider

    with (
        patch("agent.core.llm_failover.aordered_providers", AsyncMock(return_value=[fake_provider])),
        patch("agent.core.llm_failover.acreate_chat_completion", side_effect=fake_chat_completion),
        patch("agent.loop.compaction._chat_tools", return_value=[]),
    ):
        outcome = await compact_history(
            history,
            system_prompt="EN SYSTEM",
            session_id="sess-en",
            english=True,
        )

    assert len(captured_inputs) == 2
    assert captured_inputs[0][-1]["content"] == COMPACT_PROMPT_EN
    assert captured_inputs[1][-1]["content"] == f"{COMPACT_PROMPT_EN}{_COMPACT_RETRY_NOTE_EN}"
    assert outcome.record is not None
    assert outcome.items is not None
    assert outcome.items[-1]["content"].startswith(SUMMARY_PREFIX_EN)
    assert "never force Chinese" in COMPACT_PROMPT_EN
    assert "与本会话作者和稿件一致的语言" in COMPACT_PROMPT


@pytest.mark.unit
def test_load_loop_history_english_replays_en_summary_and_interrupted_notes(db_session: Session):
    user, project = _seed_user_project(db_session)
    chat = ChatSession(user_id=user.id, project_id=project.id, title="AI Assistant")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    m1 = ChatMessage(session_id=chat.id, role="user", content="Start chapter 1")
    db_session.add(m1)
    db_session.commit()
    db_session.refresh(m1)

    m2 = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content="Chapter 1 drafted.",
        message_metadata=json.dumps(
            {
                "engine": "loop",
                "compaction": {
                    "summary": "Chapter 1 summary in English.",
                    "covers_through_message_id": m1.id,
                    "kept_author_messages": ["Start chapter 1"],
                },
                "stop_reason": "output_truncated",
            }
        ),
    )
    db_session.add(m2)
    db_session.commit()

    loaded = load_loop_history(
        db_session,
        project_id=project.id,
        user_id=user.id,
        chat_session_id=chat.id,
        english=True,
    )
    assert loaded.compacted is True
    assert loaded.items[1] == {"role": "user", "content": f"{SUMMARY_PREFIX_EN}Chapter 1 summary in English."}
    assert any(item.get("content") == OUTPUT_TRUNCATED_HISTORY_EN for item in loaded.items)


@pytest.mark.unit
def test_stage1_tool_archive_english_receipt_uses_english_note():
    large_output = json.dumps(
        {"status": "success", "data": [{"id": "f1", "title": "Chapter 1", "content": "x" * 2500}]}
    )
    history = LoopHistory(
        items=[
            {"role": "user", "content": "Read chapter 1"},
            {"type": "function_call", "call_id": "c1", "name": "read_files", "arguments": '{"ids":["f1"]}'},
            {"type": "function_call_output", "call_id": "c1", "output": large_output},
            {"role": "assistant", "content": "Done."},
            {"role": "user", "content": "Turn 2"},
            {"role": "assistant", "content": "Ok."},
        ],
        last_message_id="m2",
    )
    res = archive_old_tool_results(
        history,
        keep_recent_turns=1,
        min_output_chars=100,
        min_savings_tokens=1,
        compact_at_tokens=500_000,
        english=True,
    )
    assert res.archived_count == 1
    archived_payload = json.loads(res.items[2]["output"])
    assert archived_payload["archived"] is True
    assert "read_files(ids=[...])" in archived_payload["note"]
    assert not text_contains_cjk(archived_payload["note"])


# =============================================================================
# 3. Symmetric drop_cjk_narration Filtering
# =============================================================================


@pytest.mark.unit
def test_drop_cjk_narration_symmetry_and_zero_false_drops():
    # Helper checks
    assert is_cjk_process_narration("我先看一下大纲。") is True
    assert is_cjk_process_narration("好的，我来读取第1章和人物设定。") is True
    assert is_cjk_process_narration("Let me check the outline first.") is False
    assert is_cjk_process_narration("Saya akan memeriksa kerangka bab dulu.") is False
    assert is_cjk_process_narration("먼저 개요를 확인하겠습니다.") is False
    # Japanese sentences containing Hiragana or Katakana are never treated as Chinese process chatter.
    assert is_cjk_process_narration("第1章を確認します。") is False
    assert is_cjk_process_narration("プロットを確認します。") is False
    assert is_cjk_process_narration("<file>\n我先看一下大纲。\n</file>") is False
    assert is_cjk_process_narration("正" * 121) is False
    assert is_english_process_narration("Now create the outline.") is True

    # Non-CJK author -> drop_cjk_narration=True:
    shaper_en = AgentTextShaper(previous_text="", drop_english_narration=False, drop_cjk_narration=True)
    # 1. Short Chinese pre-tool chatter is buffered and dropped right before tool call.
    assert shaper_en.feed("我先看一下大纲。") == ""
    assert shaper_en.end_segment(before_tool_call=True) == ""
    # 2. English / Indonesian / Korean / Russian pre-tool text passes through immediately.
    assert shaper_en.feed("Checking the outline...") == "Checking the outline..."
    assert shaper_en.end_segment(before_tool_call=True) == ""
    assert shaper_en.feed("Saya periksa dulu.") == "\n\nSaya periksa dulu."
    assert shaper_en.end_segment(before_tool_call=True) == ""
    # Japanese Kanji+Kana pre-tool line streams immediately and is never dropped.
    assert shaper_en.feed("第1章を確認します。") == "\n\n第1章を確認します。"
    assert shaper_en.end_segment(before_tool_call=True) == ""
    # 3. <file> block starting with CJK is NEVER dropped.
    assert shaper_en.feed("<file>\n我先看一下大纲。\n</file>") == "<file>\n我先看一下大纲。\n</file>"
    assert shaper_en.end_segment(before_tool_call=False) == ""
    # 4. Long (>120 chars) CJK prose is NEVER dropped even before a tool call.
    long_cjk = "这段是很长的引用正文内容，" * 12
    assert len(long_cjk) > 120
    assert shaper_en.feed(long_cjk) == long_cjk
    assert shaper_en.end_segment(before_tool_call=True) == ""
    # 5. Short CJK text NOT followed by a tool call (final reply) is NOT dropped.
    assert shaper_en.feed("好的，已完成。") == ""
    assert shaper_en.end_segment(before_tool_call=False) == "\n\n好的，已完成。"

    # Chinese author -> drop_cjk_narration=False, drop_english_narration=True:
    shaper_zh = AgentTextShaper(previous_text="", drop_english_narration=True, drop_cjk_narration=False)
    assert shaper_zh.feed("我先看一下大纲。") == "我先看一下大纲。"
    assert shaper_zh.end_segment(before_tool_call=True) == ""
    assert shaper_zh.feed("Now let me read the outline.") == ""
    assert shaper_zh.end_segment(before_tool_call=True) == ""


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    "author_message",
    [
        "Write Chapter 1",
        "Tulis bab pertama novel ini",
        "1장을 써주세요",
        "Напиши первую главу",
    ],
)
async def test_loop_engine_drops_cjk_pre_tool_chatter_for_non_cjk_authors(author_message: str):
    """Non-CJK author turns suppress short Chinese pre-tool chatter while keeping real English reply."""
    from agent.loop.engine import run_loop_agent_streaming

    async def fake_runner(**_kwargs):
        yield StreamEvent(type=StreamEventType.MESSAGE_START, data={"agent_type": "loop"})
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "我先看一下大纲。"})
        yield StreamEvent(
            type=StreamEventType.TOOL_USE,
            data={"id": "t1", "name": "read_files", "status": "complete", "input": {"ids": ["f1"]}},
        )
        yield StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={"tool_use_id": "t1", "name": "read_files", "result": '{"status":"success"}'},
        )
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Chapter 1 is ready."})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    state = {
        "user_message": author_message,
        "router_message": author_message,
        "language": "en",
        "system_prompt": "EN SYS",
        "messages": [],
    }
    texts: list[str] = []
    with patch("agent.loop.engine.run_openai_agents_streaming_agent", side_effect=fake_runner):
        async for ev in run_loop_agent_streaming(state):
            if ev.type == StreamEventType.TEXT:
                texts.append(ev.data["text"])

    combined = "".join(texts)
    assert "我先看一下大纲" not in combined
    assert "Chapter 1 is ready." in combined


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("author_message", ["ok", "1", "continue", "A", "https://example.com"])
async def test_loop_engine_keeps_chinese_pre_tool_narration_for_zh_session_short_replies(
    author_message: str,
):
    """P1 regression: a Chinese user (language='zh') replying 'ok' / '1' / 'continue' must NEVER
    have legitimate Chinese pre-tool lines dropped."""
    from agent.loop.engine import run_loop_agent_streaming

    async def fake_runner(**_kwargs):
        yield StreamEvent(type=StreamEventType.MESSAGE_START, data={"agent_type": "loop"})
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "好的，我先读取大纲和第3章。"})
        yield StreamEvent(
            type=StreamEventType.TOOL_USE,
            data={"id": "t1", "name": "read_files", "status": "complete", "input": {"ids": ["f1"]}},
        )
        yield StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={"tool_use_id": "t1", "name": "read_files", "result": '{"status":"success"}'},
        )
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第3章已经写好了。"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    state = {
        "user_message": author_message,
        "router_message": author_message,
        "language": "zh",
        "system_prompt": "ZH SYS",
        "messages": [],
    }
    texts: list[str] = []
    with patch("agent.loop.engine.run_openai_agents_streaming_agent", side_effect=fake_runner):
        async for ev in run_loop_agent_streaming(state):
            if ev.type == StreamEventType.TEXT:
                texts.append(ev.data["text"])

    combined = "".join(texts)
    assert "好的，我先读取大纲和第3章。" in combined
    assert "第3章已经写好了。" in combined


# =============================================================================
# 4. English Loop / Runner / Turn-Context Templates
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_loop_engine_english_truncation_and_steering_directives():
    from agent.loop.engine import run_loop_agent_streaming

    directives: list[str] = []
    user_messages: list[str] = []
    runs = [
        [StreamEvent(type=StreamEventType.TEXT, data={"text": "Drafting..."})],
        [StreamEvent(type=StreamEventType.TEXT, data={"text": "Adjusted."})],
    ]

    async def fake_runner(*, state, agent_directive="", **_kwargs):
        directives.append(agent_directive)
        user_messages.append(state["user_message"])
        yield StreamEvent(type=StreamEventType.MESSAGE_START, data={"agent_type": "loop"})
        for ev in runs.pop(0):
            yield ev
        state["run_items"] = [{"role": "user", "content": "x"}]
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    steering = [[{"id": "s1", "content": "Make it darker"}], []]

    async def get_steering():
        return steering.pop(0) if steering else []

    state = {
        "user_message": "Continue chapter 2",
        "router_message": "Continue chapter 2",
        "language": "en",
        "system_prompt": "EN SYS",
        "previous_output_truncations": 1,
    }
    with patch("agent.loop.engine.run_openai_agents_streaming_agent", side_effect=fake_runner):
        async for _ev in run_loop_agent_streaming(state, get_steering_messages=get_steering):
            pass

    assert AGENT_DIRECTIVE_HEADER_EN in directives[0]
    assert OUTPUT_TRUNCATED_RESUME_EN in directives[0]
    assert not text_contains_cjk(directives[0])
    assert directives[1] == ""
    assert user_messages[1] == STEERING_FOLLOWUP_CONTEXT_EN


@pytest.mark.unit
@pytest.mark.parametrize("trigger", ["turns", "time"])
def test_runner_landing_reminders_and_tool_not_found_use_english(trigger: str):
    from agents.run_config import ModelInputData

    if trigger == "turns":
        meter, max_turns = AgentRunMeter(max_model_calls=10, model_calls=8), 60
    else:
        meter, max_turns = AgentRunMeter(wrap_up_after_s=0), 60

    turn_filter = _ModelCallCountingFilter(
        lambda data: data.model_data, meter, max_turns=max_turns, english=True
    )
    sent = turn_filter(SimpleNamespace(model_data=ModelInputData(input=[], instructions="sys")))
    text = sent.input[-1]["content"]
    expected = LOOP_SOFT_LANDING_PROMPT_EN.format(remaining=1) if trigger == "turns" else LOOP_TIME_LANDING_PROMPT_EN
    assert text == expected
    assert not text_contains_cjk(text)

    err = _format_tool_error(
        SimpleNamespace(kind="tool_not_found", tool_name="legacy_tool"), english=True
    )
    assert err is not None
    assert "legacy_tool" in err
    assert not text_contains_cjk(err)


@pytest.mark.unit
def test_context_assembler_and_turn_context_english_rendering(db_session: Session):
    user, project = _seed_user_project(db_session)
    project.summary = "A coastal mystery in Lisbon."
    project.writing_style = "First-person, crisp and atmospheric."
    project.current_phase = "Chapter 2 drafting"
    db_session.add(project)
    chapter = File(
        project_id=project.id,
        title="Chapter 1: Fog",
        file_type="draft",
        content="The ferry horn echoed across the harbor.",
    )
    db_session.add(chapter)
    db_session.commit()
    db_session.refresh(chapter)

    turn_ctx = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={"current_file_id": chapter.id},
        language="en",
        author_message="Continue chapter 2",
    )

    assert "Items marked [Full]" in turn_ctx.text
    assert "Project Status [Core]" in turn_ctx.text
    assert "[Project Summary]" in turn_ctx.text
    assert "[Writing Style]" in turn_ctx.text
    assert "[Current Writing Conventions]" in turn_ctx.text
    assert "[Manuscript Drafts]" in turn_ctx.text
    assert "[Full]" in turn_ctx.text
    assert "← Current focus" in turn_ctx.text
    assert "[全文]" not in turn_ctx.text
    assert "项目状态" not in turn_ctx.text
    assert "【写作风格】" not in turn_ctx.text
    assert "【当前写作约定】" not in turn_ctx.text

    # Direct assembler check for previous-chapter note and truncated tag in English
    prev_item = ContextItem(
        id="c1",
        type="outline",
        title="Chapter 1",
        content="Opening scene...",
        priority=ContextPriority.CRITICAL,
        metadata={
            "file_type": "draft",
            "relation": "previous",
            "truncated": True,
            "shown_chars": 100,
            "original_chars": 500,
        },
    )
    rendered = ContextAssembler().format_context([prev_item], None, {}, force_en=True)
    assert "Project Status [Pending]" in rendered
    assert PREVIOUS_CHAPTER_NOTE_EN in rendered
    assert "[Truncated: showing 100/500 chars;" in rendered
    assert not text_contains_cjk(rendered)


# =============================================================================
# 5. Official Skills L1 Localization, Language Fence & Default Session Title
# =============================================================================


@pytest.mark.unit
def test_official_skill_l1_catalog_localization_and_english_lookup(db_session: Session):
    user, _project = _seed_user_project(db_session)
    official = PublicSkill(
        name="创建大纲",
        description="规划全书总纲与分章大纲",
        instructions="按三幕式和因果链梳理大纲。",
        tags=json.dumps(["大纲", "细纲", "剧情"]),
        source="official",
        status="approved",
    )
    community = PublicSkill(
        name="Noir Atmosphere",
        description="Craft hard-boiled noir imagery.",
        instructions="Focus on rain, neon, and moral ambiguity.",
        tags=json.dumps(["noir", "detective"]),
        source="community",
        status="approved",
    )
    db_session.add_all([official, community])
    db_session.commit()
    db_session.refresh(official)
    db_session.refresh(community)

    db_session.add_all([
        UserAddedSkill(user_id=user.id, public_skill_id=official.id, is_active=True),
        UserAddedSkill(user_id=user.id, public_skill_id=community.id, is_active=True),
    ])
    db_session.commit()

    active = load_active_skills(db_session, user.id)
    assert len(active) == 2
    official_active = next(s for s in active if s.id == official.id)
    community_active = next(s for s in active if s.id == community.id)
    assert official_active.is_official is True
    assert community_active.is_official is False

    # find_active_skill resolves both Chinese stored name AND English official display name!
    assert find_active_skill(active, "Create an Outline") == official_active
    assert find_active_skill(active, "create an outline") == official_active
    assert find_active_skill(active, "创建大纲") == official_active

    catalog_en = SkillContextInjector().build_skill_catalog(db_session, user.id, language="en")
    assert catalog_en is not None
    assert "## Available Writing Skills" in catalog_en
    assert "**Create an Outline**" in catalog_en
    assert "Plan the overall outline" in catalog_en
    assert "**Noir Atmosphere**" in catalog_en
    # "noir" already appears in the description so only non-redundant trigger "detective" is appended.
    assert "(Triggers: detective)" in catalog_en
    # Language fence is present in L1 catalog header, and raw Chinese official name/tags are absent!
    assert "apply only the methodology" in catalog_en.lower()
    assert "创建大纲" not in catalog_en
    assert "大纲、细纲" not in catalog_en
    assert not text_contains_cjk(catalog_en)

    # User-authored skill with the same English name takes precedence over official English alias.
    custom = UserSkill(
        user_id=user.id,
        name="Create an Outline",
        description="My custom outline flow",
        instructions="Use my custom beat structure.",
        is_active=True,
    )
    db_session.add(custom)
    db_session.commit()
    db_session.refresh(custom)
    active_with_custom = load_active_skills(db_session, user.id)
    matched = find_active_skill(active_with_custom, "Create an Outline")
    assert matched is not None
    assert matched.id == custom.id
    assert matched.source == "user"
    # same_name_skills reports official "创建大纲" as a colliding skill both with language="en" and default language=None.
    assert [s.id for s in same_name_skills(active_with_custom, matched, language="en")] == [official.id]
    assert [s.id for s in same_name_skills(active_with_custom, matched)] == [official.id]
    assert same_name_skills(active_with_custom, matched, language="zh") == []

    # Chinese catalog remains unchanged.
    catalog_zh = SkillContextInjector().build_skill_catalog(db_session, user.id, language="zh")
    assert catalog_zh is not None
    assert "## 可用写作技能" in catalog_zh
    assert "**创建大纲**" in catalog_zh


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
async def test_service_emits_localized_skill_matched_language_fence_and_session_title(db_session: Session):
    user, project = _seed_user_project(db_session)
    official = PublicSkill(
        name="创建大纲",
        description="规划大纲",
        instructions="按三幕式梳理每章核心冲突。",
        source="official",
        status="approved",
    )
    db_session.add(official)
    db_session.commit()
    db_session.refresh(official)
    db_session.add(UserAddedSkill(user_id=user.id, public_skill_id=official.id, is_active=True))
    db_session.commit()

    captured_state: dict = {}

    def fake_loop(state, **_kwargs):
        captured_state.update(state)

        async def _gen():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Outline saved."})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return _gen()

    service = AgentService()
    with patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop):
        sse_events = [
            ev
            async for ev in service.process_stream(
                project_id=project.id,
                user_id=user.id,
                message="Help me outline chapters 1-3.",
                session=db_session,
                language="en",
                selected_skill_ids=[official.id],
            )
        ]

    # 1. Default session title for non-zh is "AI Assistant".
    chat_session = db_session.get(ChatSession, captured_state["session_id"])
    assert chat_session is not None
    assert chat_session.title == DEFAULT_CHAT_SESSION_TITLE_EN

    # 2. skill_matched SSE event emits localized official name.
    matched_payloads = []
    for raw in sse_events:
        if "event: skill_matched" in raw:
            data_line = next(line for line in raw.splitlines() if line.startswith("data:"))
            matched_payloads.append(json.loads(data_line[len("data:"):].strip()))
    assert matched_payloads == [
        {"skill_id": official.id, "skill_name": "Create an Outline", "matched_trigger": "selected"}
    ]

    # 3. <turn_context> renders localized heading + language fence for Chinese official skill body.
    turn_user_msg = captured_state["user_message"]
    assert "### Create an Outline" in turn_user_msg
    assert "Language fence:" in turn_user_msg
    assert "Apply only the methodology" in turn_user_msg

    # 4. ui_action=natural_polish with Chinese official DB skill also injects language fence.
    deai = PublicSkill(
        name="去AI味",
        description="去AI味",
        instructions="三层检查去AI味方法正文。",
        source="official",
        status="approved",
    )
    db_session.add(deai)
    db_session.commit()
    ui_lines = "\n".join(
        build_ui_action_turn_section(
            db_session,
            ui_action="natural_polish",
            force_en=True,
            focus_file_id="f-1",
            focus_file_title="Chapter 1",
            focus_file_type="draft",
            has_selected_text=False,
        )
    )
    assert "Language fence" in ui_lines


@pytest.mark.unit
@pytest.mark.parametrize(
    ("language", "expected_title"),
    [
        ("en", DEFAULT_CHAT_SESSION_TITLE_EN),
        ("en-US", DEFAULT_CHAT_SESSION_TITLE_EN),
        ("id", DEFAULT_CHAT_SESSION_TITLE_EN),
        ("es", DEFAULT_CHAT_SESSION_TITLE_EN),
        ("zh", DEFAULT_CHAT_SESSION_TITLE_ZH),
        ("zh-CN", DEFAULT_CHAT_SESSION_TITLE_ZH),
        (None, DEFAULT_CHAT_SESSION_TITLE_ZH),
    ],
)
def test_service_default_session_title_is_language_aware(
    db_session: Session, language: str | None, expected_title: str
):
    user, project = _seed_user_project(db_session)
    service = AgentService(context_assembler=MagicMock())
    sid = service._resolve_or_create_chat_session_id(
        db_session,
        project_id=project.id,
        user_id=user.id,
        requested_session_id=None,
        language=language,
    )
    created = db_session.get(ChatSession, sid)
    assert created is not None
    assert created.title == expected_title


@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs")
@pytest.mark.parametrize("raw_lang", ["EN", "en-US", "id", "es", "fr"])
def test_non_zh_and_uppercase_languages_align_force_en_and_turn_context(
    db_session: Session, raw_lang: str
):
    """P2 regression: uppercase 'EN' and non-zh locales like 'id'/'es'/'fr' consistently select the EN bucket
    across MessageManager, turn_context, SkillContextInjector, and service session titles."""
    user, project = _seed_user_project(db_session)
    official = PublicSkill(
        name="创建大纲",
        description="规划大纲",
        instructions="大纲方法",
        source="official",
        status="approved",
    )
    db_session.add(official)
    db_session.commit()
    db_session.refresh(official)
    db_session.add(UserAddedSkill(user_id=user.id, public_skill_id=official.id, is_active=True))
    db_session.commit()

    catalog = SkillContextInjector().build_skill_catalog(db_session, user.id, language=raw_lang)
    assert catalog is not None and "**Create an Outline**" in catalog

    prompt = MessageManager(project.id, user.id).build_system_prompt(
        session=db_session, language=raw_lang, skill_catalog=catalog
    )
    assert "## Reply & Output Language Consistency" in prompt
    assert not text_contains_cjk(prompt)

    turn_ctx = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={},
        language=raw_lang,
        author_message="Hello",
    )
    assert "Items marked [Full]" in turn_ctx.text
    assert "[全文]" not in turn_ctx.text


# =============================================================================
# 6. Multilingual suggest_service.py
# =============================================================================


@pytest.mark.unit
@pytest.mark.parametrize("lang", ["en", "en-US", "id", "pt", "vi", "es", "ru", "ko", "fr", "pl", "ar"])
def test_suggest_service_multilingual_rule_matches_author_language(lang: str):
    svc = SuggestService()
    msgs = svc._build_messages(
        system_prompt="Base",
        context="Context",
        recent_messages=[{"role": "user", "content": "Tulis bab 2"}],
        language=lang,
    )
    assert msgs[0]["content"] == f"Base\n\n{ENGLISH_OUTPUT_RULE}"
    assert "exact language of the author's most recent messages" in ENGLISH_OUTPUT_RULE
    assert "Bahasa Indonesia" in ENGLISH_OUTPUT_RULE
    assert "Português" in ENGLISH_OUTPUT_RULE
    assert "Tiếng Việt" in ENGLISH_OUTPUT_RULE
    assert "fall back to English only when" in ENGLISH_OUTPUT_RULE
    assert "Project info:" in msgs[1]["content"]
    assert "Recent conversation:" in msgs[1]["content"]


@pytest.mark.unit
def test_suggest_service_zh_stays_unchanged():
    svc = SuggestService()
    msgs = svc._build_messages(
        system_prompt="Base",
        context="项目信息",
        recent_messages=[{"role": "user", "content": "写第二章"}],
        language="zh",
    )
    assert msgs[0]["content"] == "Base"
    assert ENGLISH_OUTPUT_RULE not in msgs[0]["content"]
    assert "最近对话:" in msgs[1]["content"]
