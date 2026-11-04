"""Real-model checks for the 2026-11-04 fixes: routing never fails on scope_constraints, and
precise edits on a long chapter land without the anchor breaker cutting the turn."""

import json
import random

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from agent.graph.router import RouterFailure, router_node
from agent.tools.file_ops.text_matching import normalize_for_fuzzy_match
from models import File
from tests.real_llm.sse_utils import collect_sse_events, event_names

# Synthetic requests shaped like the production failures (no author text reused).
_ROUTER_MESSAGES = [
    "第一项先推演别动文件，其余几项直接改：1. 推演主角和师父决裂的时间线；2. 把第3章里的城名统一成青岚城；"
    "3. 第5章删掉重复的打斗描写；4. 人物卡里补上配角年龄。",
    "先自检并修订第1到12章：按番茄爽文节奏标准审校，检查每章字数、节奏和剧情是否跑题；字数超标又压不下来的章节"
    "可以删掉重写；完成修订前不要开始后续正文；优先完成第12章压缩和第13章拆分；然后续写第13章。",
    "按三项决定更新《第一卷大纲》：陆家家主统一为陆含章；第11章改用《引气决·基础篇》；《万物生息诀》明确为"
    "《万物回春经》所含功法；只改大纲，不写正文。",
]


@pytest.mark.integration
@pytest.mark.real_llm
@pytest.mark.parametrize("message", _ROUTER_MESSAGES)
async def test_router_routes_production_shaped_requests_with_short_constraints(message, require_deepseek_key):
    try:
        result = await router_node({"user_message": message, "messages": []})
    except RouterFailure as failure:  # pragma: no cover - the regression this guards against
        pytest.fail(f"routing failed: {failure.category} {failure.failed_fields}")
    metadata = result["routing_metadata"]
    print(json.dumps({k: metadata.get(k) for k in ("agent_type", "read_only", "write_content", "scope",
                                                   "scope_constraints", "clarify_first")}, ensure_ascii=False))
    # Only read/write boundaries the turn fields cannot express, plus the short scope.
    assert len(metadata["scope_constraints"]) <= 4


def _long_chapter() -> tuple[str, list[str]]:
    rng = random.Random(11)
    who = ["林舟", "苏晚", "老掌柜", "少年", "守城的士兵", "白衣女子", "师父"]
    act = ["推开", "望向", "握紧", "想起", "绕过", "停在", "听见"]
    what = ["那扇吱呀作响的木门", "城头飘着的旧旗", "腰间那柄缺口的剑", "茶馆里的说书声", "巷口的灯笼", "桥下的流水", "远处的钟声"]
    end = ["，没有说话。", "，心里一沉。", "，轻轻叹了口气。", "，眼神变得复杂。", "，像是下了什么决心。"]
    paragraphs = []
    for i in range(90):
        body = "".join(rng.choice(who) + rng.choice(act) + rng.choice(what) + rng.choice(end) for _ in range(4))
        paragraphs.append(f"　　第{i + 1}个清晨，{body}")
    return "\n\n".join(paragraphs), paragraphs


@pytest.mark.integration
@pytest.mark.real_llm
async def test_agent_applies_several_edits_to_a_long_chapter(
    client: AsyncClient, db_session: Session, real_auth_context: dict, require_deepseek_key,
):
    context = real_auth_context
    content, paragraphs = _long_chapter()
    assert len(normalize_for_fuzzy_match(content)[0]) > 5000  # past the full-scan limit
    chapter = File(
        title="第一章 青岚城", content=content, file_type="draft",
        project_id=context["project"].id, user_id=context["user"].id,
    )
    db_session.add(chapter)
    db_session.commit()
    db_session.refresh(chapter)

    message = (
        "请直接修改《第一章 青岚城》，不要改别的地方：\n"
        "1. 第5段开头的“第5个清晨”改成“第五个清晨，雨还没停”；\n"
        "2. 第20段结尾再加一句：“城门在他身后缓缓合上。”；\n"
        "3. 第33段里所有“林舟”改成“林照”；\n"
        "4. 删掉第40段的最后一句。"
    )
    response = await client.post(
        "/api/v1/agent/stream",
        json={"project_id": str(context["project"].id), "message": message,
              "metadata": {"current_file_id": chapter.id, "current_file_type": "draft"}},
        headers=context["headers"],
    )
    assert response.status_code == 200
    events = await collect_sse_events(response, timeout_seconds=780.0)
    names = event_names(events)

    stop_reasons = [e.data.get("stop_reason") for e in events if e.event == "message_end"]
    errors = [e.data for e in events if e.event == "error"]
    tool_results = [e.data for e in events if e.event == "tool_result" and e.data.get("tool_name") == "edit_file"]
    print("events:", names[-12:], "stop_reasons:", stop_reasons, "errors:", errors)
    print("edit_file results:", [(r.get("status"), (r.get("result") or {}).get("error_type")
                                  if isinstance(r.get("result"), dict) else None) for r in tool_results])
    assert "tool_failure_circuit_open" not in stop_reasons
    assert not errors

    db_session.expire_all()
    saved = db_session.get(File, chapter.id).content
    assert "第五个清晨，雨还没停" in saved
    assert "城门在他身后缓缓合上。" in saved
    para33 = next(p for p in saved.split("\n\n") if "第33个清晨" in p)
    assert "林舟" not in para33


@pytest.mark.integration
@pytest.mark.real_llm
async def test_agent_revises_many_passages_in_a_long_chapter_without_tripping(
    client: AsyncClient, db_session: Session, real_auth_context: dict, require_deepseek_key,
):
    """The production breaker pattern: one long chapter, a broad revision that becomes many edits."""
    context = real_auth_context
    content, _paragraphs = _long_chapter()
    chapter = File(
        title="第一章 青岚城", content=content, file_type="draft",
        project_id=context["project"].id, user_id=context["user"].id,
    )
    db_session.add(chapter)
    db_session.commit()
    db_session.refresh(chapter)

    response = await client.post(
        "/api/v1/agent/stream",
        json={"project_id": str(context["project"].id),
              "message": "把《第一章 青岚城》第30到第60段里重复的“没有说话”“心里一沉”换成各不相同的具体动作或神态，"
                         "其余内容不动，直接改文件。",
              "metadata": {"current_file_id": chapter.id, "current_file_type": "draft"}},
        headers=context["headers"],
    )
    assert response.status_code == 200
    events = await collect_sse_events(response, timeout_seconds=780.0)
    stop_reasons = [e.data.get("stop_reason") for e in events if e.event == "message_end"]
    edits = [e.data for e in events if e.event == "tool_result" and e.data.get("tool_name") == "edit_file"]
    print("stop_reasons:", stop_reasons, "edit calls:", len(edits),
          "statuses:", [r.get("status") for r in edits])
    assert "tool_failure_circuit_open" not in stop_reasons
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content != content
