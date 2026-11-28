"""Regression tests for Patrol #17 fixes:
1. update_project headroom targets, exact overshoot guidance, proactive soft-warn hint,
   StreamAdapter safe error/client payload sanitization, and ToolFailureBreaker recovery family.
2. content_range.py bracketed / parenthesized / quoted multilingual chapter/episode/volume headings.
"""

from __future__ import annotations

import json

import pytest
from sqlmodel import Session

from agent.core.events import EventType
from agent.core.workflow_events import StreamEvent as WorkflowStreamEvent
from agent.core.workflow_events import StreamEventType
from agent.openai_agents.tool_failure_breaker import (
    TRIP_REASON_RECOVERY,
    ToolFailureBreaker,
)
from agent.prompts.base import LOOP_TOOL_USAGE_PROTOCOL, LOOP_TOOL_USAGE_PROTOCOL_EN
from agent.stream_adapter import StreamAdapter, StreamAdapterConfig
from agent.tools import mcp_tools
from agent.tools.file_ops.content_range import ContentRangeError, apply_content_range
from agent.tools.file_ops.edit import (
    EDIT_ERROR_PROJECT_FIELD_TOO_LONG,
    edit_error_user_message,
)
from agent.tools.registry import LOOP_AGENT_TYPE
from agent.tools.tool_context import AgentRequestContext
from agent.tools.tool_schemas import UPDATE_PROJECT_TOOL
from config.project_status import (
    PROJECT_STATUS_MAX_LENGTHS,
    PROJECT_STATUS_RECOMMENDED_TARGETS,
    PROJECT_STATUS_SOFT_WARN_THRESHOLDS,
    ProjectStatusFieldTooLongError,
    normalize_project_status_payload,
)
from models import Project, User
from services.features.activation_event_service import activation_event_service


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def owner_project(db_session: Session, monkeypatch):
    import database

    user = User(
        username="headroom_owner",
        email="headroom_owner@example.test",
        hashed_password="unused",
        email_verified=True,
        is_active=True,
    )
    project = Project(name="冗余治理测试", owner_id=user.id, project_type="novel")
    db_session.add_all([user, project])
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(project)

    engine = db_session.get_bind()
    monkeypatch.setattr(database, "create_session", lambda: Session(engine))
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_once", lambda *_a, **_kw: None)
    return user, project


def _make_ctx(db_session: Session, user: User, project: Project) -> AgentRequestContext:
    engine = db_session.get_bind()
    return AgentRequestContext(
        session=None,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-headroom",
        current_agent=LOOP_AGENT_TYPE,
        author_message="更新一下项目简介",
        create_session_func=lambda: Session(engine),
        read_snapshots={},
    )


# ===========================================================================
# 1. project_status.py structured exception & REST ValueError compatibility
# ===========================================================================


@pytest.mark.unit
def test_project_status_headroom_constants_and_structured_error_subclass_value_error():
    assert PROJECT_STATUS_MAX_LENGTHS == {
        "summary": 4000,
        "current_phase": 1000,
        "writing_style": 1000,
        "notes": 4000,
    }
    assert PROJECT_STATUS_RECOMMENDED_TARGETS == {
        "summary": 3000,
        "current_phase": 700,
        "writing_style": 700,
        "notes": 3000,
    }
    assert PROJECT_STATUS_SOFT_WARN_THRESHOLDS == {
        "summary": 3200,
        "current_phase": 800,
        "writing_style": 800,
        "notes": 3200,
    }
    for field_name, max_len in PROJECT_STATUS_MAX_LENGTHS.items():
        assert PROJECT_STATUS_RECOMMENDED_TARGETS[field_name] < PROJECT_STATUS_SOFT_WARN_THRESHOLDS[field_name] < max_len

    overshoot = "文" * 4137
    with pytest.raises(ValueError) as exc_info:
        normalize_project_status_payload({"summary": f"  {overshoot}  "})

    exc = exc_info.value
    assert isinstance(exc, ProjectStatusFieldTooLongError)
    assert str(exc) == "summary exceeds max length (4000)"
    assert exc.field_name == "summary"
    assert exc.actual_length == 4137
    assert exc.max_length == 4000
    assert exc.excess == 137
    assert exc.recommended_target == 3000


@pytest.mark.unit
def test_schemas_and_zh_en_prompts_document_hard_caps_and_headroom_targets():
    props = UPDATE_PROJECT_TOOL["input_schema"]["properties"]
    assert "4000" in UPDATE_PROJECT_TOOL["description"]
    assert "3000" in UPDATE_PROJECT_TOOL["description"]
    assert "1000" in UPDATE_PROJECT_TOOL["description"]
    assert "700" in UPDATE_PROJECT_TOOL["description"]

    assert "4000" in props["summary"]["description"]
    assert "3000" in props["summary"]["description"]
    assert "1000" in props["current_phase"]["description"]
    assert "700" in props["current_phase"]["description"]
    assert "1000" in props["writing_style"]["description"]
    assert "700" in props["writing_style"]["description"]

    assert "4000" in LOOP_TOOL_USAGE_PROTOCOL and "3000" in LOOP_TOOL_USAGE_PROTOCOL
    assert "1000" in LOOP_TOOL_USAGE_PROTOCOL and "700" in LOOP_TOOL_USAGE_PROTOCOL
    assert "4,000" in LOOP_TOOL_USAGE_PROTOCOL_EN and "3,000" in LOOP_TOOL_USAGE_PROTOCOL_EN
    assert "1,000" in LOOP_TOOL_USAGE_PROTOCOL_EN and "700" in LOOP_TOOL_USAGE_PROTOCOL_EN


# ===========================================================================
# 2. update_project exact overshoot error + proactive headroom_hint
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_update_project_overshoot_returns_exact_counts_and_headroom_target(db_session, owner_project):
    user, project = owner_project
    project.summary = "已有简介"
    db_session.add(project)
    db_session.commit()

    ctx = _make_ctx(db_session, user, project)
    try:
        resp = _payload(
            await mcp_tools.update_project(
                {"summary": "甲" * 4137},
                request_context=ctx,
            )
        )
    finally:
        ctx.clear()

    assert resp["status"] == "error"
    assert resp["error_type"] == EDIT_ERROR_PROJECT_FIELD_TOO_LONG
    assert resp["mutation_applied"] is False
    assert resp["user_message"] == edit_error_user_message(EDIT_ERROR_PROJECT_FIELD_TOO_LONG)
    assert resp["field_name"] == "summary"
    assert resp["actual_length"] == 4137
    assert resp["max_length"] == 4000
    assert resp["excess"] == 137
    assert resp["recommended_target"] == 3000
    assert "4137" in resp["error"]
    assert "4000" in resp["error"]
    assert "137" in resp["error"]
    assert "3000" in resp["error"]

    db_session.expire_all()
    assert db_session.get(Project, project.id).summary == "已有简介"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_update_project_attaches_proactive_headroom_hint_only_at_or_above_soft_threshold(
    db_session, owner_project
):
    user, project = owner_project
    ctx = _make_ctx(db_session, user, project)
    try:
        # Below threshold (3199 < 3200): no headroom_hint
        under = _payload(
            await mcp_tools.update_project(
                {"summary": "乙" * (PROJECT_STATUS_SOFT_WARN_THRESHOLDS["summary"] - 1)},
                request_context=ctx,
            )
        )
        assert under["status"] == "success"
        assert "headroom_hint" not in under["data"]

        # At/above threshold (3500 >= 3200) and current_phase (850 >= 800): headroom_hint attached
        near = _payload(
            await mcp_tools.update_project(
                {"summary": "丙" * 3500, "current_phase": "丁" * 850},
                request_context=ctx,
            )
        )
        assert near["status"] == "success"
        hint = near["data"].get("headroom_hint", "")
        assert "summary" in hint and "3500/4000" in hint and "3000" in hint
        assert "current_phase" in hint and "850/1000" in hint and "700" in hint
    finally:
        ctx.clear()


# ===========================================================================
# 3. StreamAdapter client sanitization for update_project success & error
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_adapter_sanitizes_update_project_success_and_error_payloads():
    adapter = StreamAdapter(StreamAdapterConfig(project_id="proj-up", user_id="u-1"))

    success_with_hint = {
        "status": "success",
        "data": {
            "project_id": "proj-up",
            "updated_fields": ["summary", "notes"],
            "notes_added": ["主角左手有旧伤"],
            "project_name_updated": True,
            "project_name": "长夜孤灯",
            "project_status": {
                "project_id": "proj-up",
                "updated_fields": ["summary", "notes"],
                "current_status": {
                    "summary": "压缩后的简介",
                    "current_phase": "第5章",
                    "writing_style": "冷峻克制",
                    "notes": "主角左手有旧伤",
                },
            },
            "headroom_hint": "summary 已达 3500/4000 字，下次更新请压缩至 3000 字以内。",
            "hint": "internal model hint",
        },
    }
    too_long_error = {
        "status": "error",
        "error": "summary 当前传入 4137 字，超出 4000 字上限（多出 137 字）；请压缩至 3000 字以内",
        "error_type": EDIT_ERROR_PROJECT_FIELD_TOO_LONG,
        "user_message": edit_error_user_message(EDIT_ERROR_PROJECT_FIELD_TOO_LONG),
        "mutation_applied": False,
        "field_name": "summary",
        "actual_length": 4137,
        "max_length": 4000,
        "excess": 137,
        "recommended_target": 3000,
    }
    untyped_error = {
        "status": "error",
        "error": "summary exceeds max length (4000) raw internal traceback",
    }

    def _wf(call_id: str, payload: dict) -> WorkflowStreamEvent:
        return WorkflowStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "name": "update_project",
                "tool_use_id": call_id,
                "result": {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]},
            },
        )

    async def _gen():
        yield _wf("call-ok", success_with_hint)
        yield _wf("call-long", too_long_error)
        yield _wf("call-raw", untyped_error)
        yield WorkflowStreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    emitted = [ev async for ev in adapter.process_workflow_events(_gen())]
    results = [ev.data for ev in emitted if ev.type == EventType.TOOL_RESULT]
    assert len(results) == 3

    # 1) Success: UI fields preserved, model-only hints stripped on SSE AND persisted payloads
    ok_ev = results[0]
    assert ok_ev["status"] == "success"
    ok_data = ok_ev["data"]
    assert "headroom_hint" not in ok_data
    assert "hint" not in ok_data
    assert ok_data["project_id"] == "proj-up"
    assert ok_data["updated_fields"] == ["summary", "notes"]
    assert ok_data["notes_added"] == ["主角左手有旧伤"]
    assert ok_data["project_name_updated"] is True
    assert ok_data["project_name"] == "长夜孤灯"
    assert ok_data["project_status"]["current_status"]["summary"] == "压缩后的简介"
    persisted_ok = adapter.pop_persisted_tool_result("call-ok", ok_data)
    assert "headroom_hint" not in persisted_ok
    assert "hint" not in persisted_ok

    # 2) Typed project_field_too_long error: safe user_message only, no raw counts/guidance leaked
    long_ev = results[1]
    assert long_ev["status"] == "error"
    assert long_ev["error"] == "项目信息超出字数上限，未保存。"
    assert long_ev["data"] == {
        "error_type": EDIT_ERROR_PROJECT_FIELD_TOO_LONG,
        "user_message": "项目信息超出字数上限，未保存。",
    }
    assert "4137" not in json.dumps(long_ev, ensure_ascii=False)

    # 3) Untyped raw English error: never leaked to SSE or persisted payload
    raw_ev = results[2]
    assert raw_ev["status"] == "error"
    assert "exceeds max length" not in json.dumps(raw_ev, ensure_ascii=False)
    assert "traceback" not in json.dumps(raw_ev, ensure_ascii=False)
    assert raw_ev["error"] == "项目信息未能更新，请重试。"


# ===========================================================================
# 4. ToolFailureBreaker recovery family for update_project/project_field_too_long
# ===========================================================================


@pytest.mark.unit
def test_tool_failure_breaker_trips_after_3_project_field_too_long_with_varying_payloads():
    breaker = ToolFailureBreaker()

    def _too_long(length: int) -> tuple[str, str]:
        args = json.dumps({"summary": "字" * length}, ensure_ascii=False)
        out = json.dumps(
            {
                "status": "error",
                "error": f"summary 当前传入 {length} 字，超出 4000 字上限（多出 {length - 4000} 字）",
                "error_type": EDIT_ERROR_PROJECT_FIELD_TOO_LONG,
                "user_message": "项目信息超出字数上限，未保存。",
                "mutation_applied": False,
            },
            ensure_ascii=False,
        )
        return args, out

    # Attempt 1: passes through without retry_hint
    a1, o1 = _too_long(4137)
    r1 = breaker.observe("update_project", a1, o1)
    assert not breaker.is_open
    assert "retry_hint" not in json.loads(r1)

    # Attempt 2 (slightly trimmed, still over cap): attaches actionable retry_hint
    a2, o2 = _too_long(4028)
    r2 = breaker.observe("update_project", a2, o2)
    assert not breaker.is_open
    parsed_r2 = json.loads(r2)
    assert parsed_r2["repeated_failures"] == 2
    assert "3000" in parsed_r2["retry_hint"]
    assert "再发生 1 次同类失败" in parsed_r2["retry_hint"]

    # Attempt 3 (still oscillating above cap): trips cleanly with TRIP_REASON_RECOVERY
    a3, o3 = _too_long(4018)
    breaker.observe("update_project", a3, o3)
    assert breaker.is_open
    assert breaker.trip is not None
    assert breaker.trip.reason == TRIP_REASON_RECOVERY
    assert breaker.trip.tool_name == "update_project"
    assert breaker.trip.failures == 3
    assert breaker.trip.threshold == 3


@pytest.mark.unit
def test_tool_failure_breaker_resets_project_field_too_long_family_on_success():
    breaker = ToolFailureBreaker()
    err_out = json.dumps(
        {
            "status": "error",
            "error": "summary 当前传入 4050 字，超出 4000 字上限",
            "error_type": EDIT_ERROR_PROJECT_FIELD_TOO_LONG,
        },
        ensure_ascii=False,
    )
    ok_out = json.dumps({"status": "success", "data": {"updated_fields": ["summary"]}})

    breaker.observe("update_project", json.dumps({"summary": "a" * 4050}), err_out)
    breaker.observe("update_project", json.dumps({"summary": "a" * 4020}), err_out)
    assert not breaker.is_open

    breaker.observe("update_project", json.dumps({"summary": "a" * 2800}), ok_out)

    # Two more failures after a success must not trip (family count was cleared).
    breaker.observe("update_project", json.dumps({"summary": "b" * 4050}), err_out)
    breaker.observe("update_project", json.dumps({"summary": "b" * 4020}), err_out)
    assert not breaker.is_open


# ===========================================================================
# 5. content_range.py bracketed / parenthesized / quoted multilingual headings
# ===========================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    ("body", "query", "expected_section", "must_include", "must_exclude"),
    [
        # Chinese lens brackets 【...】 (exact Patrol #17 outline pattern)
        (
            "【第五十二章 布局】\n旧局内容。\n\n"
            "【第五十三章 破局】\n"
            "【核心冲突】\n主角当众揭穿假印。\n"
            "（众人哗然）\n"
            "「你竟然早有准备！」\n\n"
            "【第五十四章 余波】\n后续内容。",
            "第五十三章",
            "【第五十三章 破局】",
            ["【核心冲突】", "主角当众揭穿假印。", "（众人哗然）", "「你竟然早有准备！」"],
            ["旧局内容。", "【第五十四章 余波】", "后续内容。"],
        ),
        # Arabic digits query against Chinese numeral inside 【...】
        (
            "【第五十三章 破局】\n破局正文。\n\n【第五十四章 余波】\n余波正文。",
            "第53章",
            "【第五十三章 破局】",
            ["破局正文。"],
            ["【第五十四章 余波】"],
        ),
        # Square brackets English Chapter
        (
            "[Chapter 11: Storm]\nRain fell.\n\n"
            "[Chapter 12: Arrival at the Harbor]\n"
            "(He stepped ashore.)\n"
            "Ships lined the pier.\n\n"
            "[Chapter 13: Departure]\nMorning mist.",
            "Chapter 12",
            "[Chapter 12: Arrival at the Harbor]",
            ["(He stepped ashore.)", "Ships lined the pier."],
            ["Rain fell.", "[Chapter 13: Departure]"],
        ),
        # Full-width parentheses Chinese episode
        (
            "（第2集）\n铺垫。\n\n"
            "（第3集 试探）\n茶馆对质。\n\n"
            "（第4集）\n反转。",
            "第3集",
            "（第3集 试探）",
            ["茶馆对质。"],
            ["铺垫。", "（第4集）"],
        ),
        # Half-width parentheses English episode
        (
            "(Episode 6)\nIntro.\n\n(Episode 7: Fallout)\nKey scene.\n\n(Episode 8)\nOutro.",
            "Episode 7",
            "(Episode 7: Fallout)",
            ["Key scene."],
            ["Intro.", "(Episode 8)"],
        ),
        # Indonesian Bab in square brackets
        (
            "[Bab 3]\nBab tiga.\n\n[Bab 4: Konflik]\nIsi bab empat.\n\n[Bab 5]\nBab lima.",
            "Bab 4",
            "[Bab 4: Konflik]",
            ["Isi bab empat."],
            ["Bab tiga.", "[Bab 5]"],
        ),
        # Spanish Capítulo in lens brackets (accent-insensitive query)
        (
            "【Capítulo 1: Inicio】\nUno.\n\n"
            "【Capítulo 2: El regreso】\nDos.\n\n"
            "【Capítulo 3: Final】\nTres.",
            "Capitulo 2",
            "【Capítulo 2: El regreso】",
            ["Dos."],
            ["Uno.", "【Capítulo 3: Final】"],
        ),
        # Corner quotes 「...」 and 『...』
        (
            "「第4章 暗线」\n暗线内容。\n\n「第5章 雨夜」\n雨夜正文。\n\n「第6章 黎明」\n黎明内容。",
            "第5章",
            "「第5章 雨夜」",
            ["雨夜正文。"],
            ["暗线内容。", "「第6章 黎明」"],
        ),
        # Volume in 『...』 contains nested bracketed chapters and stops at next volume
        (
            "『卷一 起势』\n【第1章 开端】\n开端。\n\n"
            "『卷二 暗涌』\n【第2章 交锋】\n交锋。\n【第3章 伏笔】\n伏笔。\n\n"
            "『卷三 归途』\n【第4章 终局】\n终局。",
            "第2卷",
            "『卷二 暗涌』",
            ["【第2章 交锋】", "【第3章 伏笔】"],
            ["『卷一 起势』", "『卷三 归途』"],
        ),
    ],
)
def test_content_range_matches_decorated_multilingual_headings_and_stops_at_sibling(
    body: str,
    query: str,
    expected_section: str,
    must_include: list[str],
    must_exclude: list[str],
):
    row = apply_content_range({"id": "outline-1", "content": body}, {"section": query})

    assert row["section"] == expected_section
    assert body[row["content_range"]["start"] : row["content_range"]["end"]] == row["content"]
    assert row["content"].startswith(expected_section)
    for snippet in must_include:
        assert snippet in row["content"]
    for snippet in must_exclude:
        assert snippet not in row["content"]


@pytest.mark.unit
def test_content_range_long_outline_30_bracketed_chapters_matches_patrol17_scenario():
    # Reproduces Patrol #17 session bce1ab4c: >12,000-char outline with ~30 【第N章…】 headings
    chapters = [
        f"【第{n}章 节点{n}】\n" + (f"第{n}章大纲细化情节与人物动机说明。" * 35)
        for n in range(30, 60)
    ]
    long_outline = "\n\n".join(chapters)
    assert len(long_outline) > 12_000

    row = apply_content_range({"id": "long-outline", "content": long_outline}, {"section": "第五十三章"})
    assert row["section"] == "【第53章 节点53】"
    assert "第53章大纲细化" in row["content"]
    assert "【第54章 节点54】" not in row["content"]
    assert row["content_partial"] is True
    assert long_outline[row["content_range"]["start"] : row["content_range"]["end"]] == row["content"]


@pytest.mark.unit
def test_content_range_duplicate_bracketed_chapter_disambiguates_only_on_exact_title():
    body = "【第五十三章 上】\n上半内容。\n\n【第五十三章 下】\n下半内容。\n\n【第五十四章】\n下一章。"
    long_body = body + ("\n补充" * 6000)

    with pytest.raises(ContentRangeError) as exc_info:
        apply_content_range({"id": "dup", "content": long_body}, {"section": "第五十三章"})
    assert exc_info.value.reason == "ambiguous"

    exact = apply_content_range({"id": "dup", "content": long_body}, {"section": "【第五十三章 上】"})
    assert exact["section"] == "【第五十三章 上】"
    assert "上半内容。" in exact["content"]
    assert "下半内容。" not in exact["content"]
