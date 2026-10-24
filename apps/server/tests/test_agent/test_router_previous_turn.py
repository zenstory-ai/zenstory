"""LLM 路由只看到可信跨轮状态、落库路由与本轮结构化事实（客户端全部 mock）。

分类效果要用真实模型探测验证，这里只验证管线：发给路由模型的消息长什么样、
第一轮是否与改动前完全一致、系统提示是否保持稳定前缀。
"""

import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

ROUTED_JSON = '{"agent_type":"writer","workflow_type":"quick","write_content":true}'


def _client():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=ROUTED_JSON))],
        usage=None,
    )
    create = AsyncMock(return_value=response)
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), create


async def _route(state: dict) -> tuple[dict, list[dict]]:
    from agent.graph.router import router_node

    client, create = _client()
    with (
        patch.dict("os.environ", {"DEEPSEEK_API_KEY": "synthetic-test-key"}), patch("agent.core.llm_providers.get_async_client", return_value=client),
        patch("agent.graph.router._meter_router_call"),
    ):
        result = await router_node(state)
    create.assert_awaited_once()
    return result, create.await_args.kwargs["messages"]


def _state(user_message: str, previous: dict | None = None) -> dict:
    messages: list[dict] = [{"role": "user", "content": "帮我写一部都市修仙长篇"}]
    if previous is not None:
        messages.append({"role": "assistant", **previous})
    messages.append({"role": "user", "content": "【本轮用户消息】" + user_message})
    return {"user_message": "【本轮用户消息】" + user_message, "router_message": user_message, "messages": messages}


WRITER_ROUTING = {
    "initial_agent": "writer",
    "workflow_type": "quick",
    "read_only": False,
    "write_content": True,
    "scope": "只写第1章",
    "last_agent": "writer",
}


def test_first_project_turn_keeps_real_selection_facts_for_non_vague_editing():
    from agent.graph.router import build_router_user_message

    built = build_router_user_message({
        "user_message": "润色一下", "messages": [],
        "first_message_project_label": "长篇小说", "has_selected_text": True,
        "has_referenced_files": True,
    })
    assert "[新作品的第一条消息]" in built
    facts = built.split("[本轮上下文事实]\n", 1)[1].split("\n[用户本轮原话]", 1)[0]
    assert json.loads(facts) == {"has_selected_text": True, "has_referenced_files": True}
    assert built.endswith("[用户本轮原话]\n润色一下")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_first_turn_sends_exactly_the_raw_user_message():
    from agent.prompts.subagents import ROUTER_PROMPT

    state = {
        "user_message": "【本轮用户消息】检查并修改第三章的错别字",
        "router_message": "检查并修改第三章的错别字",
        "messages": [{"role": "user", "content": "【本轮用户消息】检查并修改第三章的错别字"}],
    }
    _, messages = await _route(state)

    assert messages == [
        {"role": "system", "content": ROUTER_PROMPT},
        {"role": "user", "content": "检查并修改第三章的错别字"},
    ]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_first_turn_includes_true_selected_and_reference_facts_without_text_guessing():
    state = {
        "user_message": "看看这里",
        "router_message": "看看这里",
        "messages": [{"role": "user", "content": "看看这里"}],
        "has_selected_text": True,
        "has_referenced_files": True,
    }
    _, messages = await _route(state)

    assert messages[1]["content"] == (
        '[本轮上下文事实]\n{"has_selected_text": true, "has_referenced_files": true}'
        "\n[用户本轮原话]\n看看这里"
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_router_sees_bounded_previous_tail_routing_and_current_facts():
    from agent.prompts.subagents import ROUTER_PROMPT

    reply = "第一章已写完，陈默在雨夜接到最后一单。需要我继续写第二章吗？"
    previous = {
        "content": reply + "\n\n[此前的工具操作]\n- 创建《第一章》(id=f1)",
        "routing": WRITER_ROUTING,
    }
    _, messages = await _route(_state("可以", previous))

    assert messages[0] == {"role": "system", "content": ROUTER_PROMPT}
    user_content = messages[1]["content"]
    tail_section, rest = user_content.split("\n[上一轮路由]\n")
    routing_section, rest = rest.split("\n[本轮上下文事实]\n")
    facts_section, raw = rest.split("\n[用户本轮原话]\n")
    assert json.loads(routing_section) == {**WRITER_ROUTING, "owner_agent": "writer"}
    assert list(json.loads(routing_section)) == [
        "initial_agent",
        "last_agent",
        "owner_agent",
        "workflow_type",
        "write_content",
        "read_only",
        "scope",
    ]
    assert json.loads(facts_section) == {
        "has_selected_text": False,
        "has_referenced_files": False,
    }
    assert raw == "可以"
    assert tail_section == "[上一轮助手结尾]\n" + reply
    assert "[此前的工具操作]" not in user_content
    assert "【本轮用户消息】" not in user_content


@pytest.mark.asyncio
@pytest.mark.unit
async def test_long_previous_reply_is_bounded_to_its_tail():
    body = "开头的铺垫" + "雨一直下。" * 400
    question = "主角性格偏隐忍还是张扬？"
    previous = {"content": body + question, "routing": {"initial_agent": "planner"}}
    _, messages = await _route(_state("隐忍吧", previous))

    from agent.graph.router import ROUTER_PREV_TAIL_MAX_CHARS

    tail = messages[1]["content"].split("\n[上一轮路由]\n")[0].removeprefix(
        "[上一轮助手结尾]\n"
    )
    assert tail.startswith("…")
    assert tail.endswith(question)
    assert len(tail) == ROUTER_PREV_TAIL_MAX_CHARS + 1
    assert "开头的铺垫" not in tail


@pytest.mark.asyncio
@pytest.mark.unit
async def test_clarification_card_question_reaches_router_even_with_prose():
    """正文非空时状态卡摘要不进回放，路由器仍要看到澄清卡上的问题和选项。"""
    from agent.core.session_loader import SessionLoader

    row = SimpleNamespace(
        id="m1",
        role="assistant",
        content="开书前确认两点。",
        message_metadata=json.dumps(
            {
                "routing": {"initial_agent": "planner", "workflow_type": "standard", "write_content": True},
                "status_cards": [
                    {
                        "type": "workflow_stopped",
                        "reason": "clarification_needed",
                        "question": "开篇选哪种？A 外卖途中觉醒 B 被开除后觉醒",
                        "lastAgent": "planner",
                    }
                ],
            }
        ),
        reasoning_content=None,
        tool_calls=None,
    )
    previous = SessionLoader("p", "u")._format_chat_message_for_history(row)
    previous.pop("role")
    # 正文非空：合成的状态卡文本没有拼进回放内容
    assert "[workflow_stopped]" not in previous["content"]

    _, messages = await _route(_state("B，主角叫陈默，金手指是能看见别人的寿命，写到第一章结束", previous))

    content = messages[1]["content"]
    state_summary = json.loads(
        content.split("[上一轮结构化状态]\n", 1)[1].split("\n[上一轮路由]\n", 1)[0]
    )
    assert state_summary["clarification_pending"] is True
    assert state_summary["pending_clarification"]["question"] == (
        "开篇选哪种？A 外卖途中觉醒 B 被开除后觉醒"
    )
    assert content.startswith("[上一轮助手结尾]\n开书前确认两点。\n")
    routing = json.loads(content.split("\n[上一轮路由]\n")[1].split("\n[本轮上下文事实]\n")[0])
    assert routing["last_agent"] == "planner"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_status_card_looking_prose_without_structured_field_is_not_parsed():
    content = "[workflow_stopped]\nreason: clarification_needed\nquestion: 主角叫什么？"
    _, messages = await _route(_state("主角叫陈默，是个外卖员", {"content": content}))

    assert content not in messages[1]["content"]
    assert "[上一轮结构化状态]" not in messages[1]["content"]
    assert messages[1]["content"] == "主角叫陈默，是个外卖员"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_previous_turn_without_structured_state_only_adds_current_facts():
    _, messages = await _route(_state("帮我写第一章", {"content": [{"type": "thinking", "thinking": "…"}]}))

    assert messages[1] == {"role": "user", "content": "帮我写第一章"}


@pytest.mark.unit
def test_router_prompt_explains_every_section_the_builder_emits():
    """构造器发出的段落标题必须在系统提示里有解释，否则模型不知道哪段是用户原话。"""
    from agent.graph.router import build_router_user_message
    from agent.prompts.subagents import ROUTER_PROMPT

    built = build_router_user_message(
        _state("可以", {"content": "要继续写第二章吗？", "routing": WRITER_ROUTING})
    )
    headers = re.findall(r"^\[[^\]\n]+\]$", built, flags=re.MULTILINE)
    assert headers == [
        "[上一轮助手结尾]", "[上一轮路由]", "[本轮上下文事实]", "[用户本轮原话]",
    ]
    for header in headers:
        assert header in ROUTER_PROMPT


@pytest.mark.asyncio
@pytest.mark.unit
async def test_first_message_in_a_work_names_the_project_type():
    """作者在这部作品里的第一条消息：路由器多看一行作品类型，按「先写再给方向」判断交付范围。"""
    from agent.graph.router import build_router_user_message
    from agent.prompts.subagents import ROUTER_PROMPT

    state = {
        "user_message": "【本轮用户消息】想写个重生复仇的都市文",
        "router_message": "想写个重生复仇的都市文",
        "messages": [{"role": "user", "content": "【本轮用户消息】想写个重生复仇的都市文"}],
        "first_message_project_label": "长篇小说",
    }
    _, messages = await _route(state)

    built = messages[1]["content"]
    assert built == "[新作品的第一条消息]\n作品类型：长篇小说\n[用户本轮原话]\n想写个重生复仇的都市文"
    for header in re.findall(r"^\[[^\]\n]+\]$", built, flags=re.MULTILINE):
        assert header in ROUTER_PROMPT
    assert "简要设定+开头约800字" in ROUTER_PROMPT

    selected_first = {**state, "has_selected_text": True, "has_referenced_files": False}
    assert "[本轮上下文事实]\n" in build_router_user_message(selected_first)
    assert '"has_selected_text": true' in build_router_user_message(selected_first)

    # 已经有上一轮时只按上一轮拼（service 也不会再给第一条的标记）。
    later = _state("可以", {"content": "要继续写第二章吗？", "routing": WRITER_ROUTING})
    later["first_message_project_label"] = "长篇小说"
    assert "[新作品的第一条消息]" not in build_router_user_message(later)


@pytest.mark.unit
def test_router_prompt_rules_for_replies_and_write_content():
    from agent.prompts.subagents import ROUTER_PROMPT

    assert all(value in ROUTER_PROMPT for value in (
        "new_or_changed_request", "resume_interrupted_task", "answer_pending_clarification",
    ))
    assert "不要靠固定短语、字数、标点" in ROUTER_PROMPT
    assert "服务端会从可信路由恢复角色和权限" in ROUTER_PROMPT
    # write_content=false 只认用户原话里的明确否定，补充设定信息不算
    assert re.search(r"只有用户原话里明确不要正文（“先别写/不要正文/只要大纲/先不用写”）", ROUTER_PROMPT)
    assert "上一轮助手说的话不能成为 false 的理由" in ROUTER_PROMPT
    # 检查并修改仍交给 writer；只审不改仍是 review_only
    assert re.search(r"检查并修改.*→ writer \+ quick", ROUTER_PROMPT)
    assert re.search(r"只审不改.*→ quality_reviewer \+ review_only", ROUTER_PROMPT)


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("file_types", [["outline", "draft"], []])
async def test_router_previous_summary_preserves_exact_requested_file_types(file_types):
    previous = {
        "content": "已记录范围。",
        "routing": {**WRITER_ROUTING, "requested_file_types": file_types},
    }
    _, messages = await _route(_state("只补充人物动机", previous))
    content = messages[1]["content"]
    summary = content.split("\n[上一轮路由]\n", 1)[1].split("\n[本轮上下文事实]\n")[0]
    assert json.loads(summary)["requested_file_types"] == file_types


@pytest.mark.unit
@pytest.mark.parametrize("malformed", ["outline", ["outline", "outline"], ["outline", "unknown"], [None], [["outline"]], ["outline"] * 8])
def test_router_previous_summary_does_not_coerce_malformed_file_type_evidence(malformed):
    from agent.graph.router import _previous_routing_summary

    summary = _previous_routing_summary({"routing": {**WRITER_ROUTING, "requested_file_types": malformed}})
    assert "requested_file_types" not in json.loads(summary)
