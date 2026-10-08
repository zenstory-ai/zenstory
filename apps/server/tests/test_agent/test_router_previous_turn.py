"""LLM 路由能看到上一轮：助手结尾 + 落库路由拼进 user 消息（DeepSeek 客户端全部 mock）。

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
        patch("agent.graph.router.get_deepseek_client", return_value=client),
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
async def test_router_sees_previous_tail_and_routing_before_the_reply():
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
    routing_section, raw = rest.split("\n[用户本轮原话]\n")
    assert tail_section == "[上一轮助手结尾]\n" + reply
    assert json.loads(routing_section) == WRITER_ROUTING
    assert list(json.loads(routing_section)) == [
        "initial_agent",
        "last_agent",
        "workflow_type",
        "write_content",
        "read_only",
        "scope",
    ]
    assert raw == "可以"
    # 工具面包屑和 UI 装饰都不交给路由器
    assert "[此前的工具操作]" not in user_content
    assert "【本轮用户消息】" not in user_content


@pytest.mark.asyncio
@pytest.mark.unit
async def test_long_previous_reply_is_cut_to_its_tail():
    from agent.graph.router import ROUTER_PREV_TAIL_MAX_CHARS

    body = "开头的铺垫" + "雨一直下。" * 400
    question = "主角性格偏隐忍还是张扬？"
    previous = {"content": body + question, "routing": {"initial_agent": "planner"}}
    _, messages = await _route(_state("隐忍吧", previous))

    tail = messages[1]["content"].split("\n[上一轮路由]\n")[0].removeprefix("[上一轮助手结尾]\n")
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

    tail = messages[1]["content"].split("\n[上一轮路由]\n")[0]
    assert tail.startswith("[上一轮助手结尾]\n开书前确认两点。\n[workflow_stopped]")
    assert "question: 开篇选哪种？A 外卖途中觉醒 B 被开除后觉醒" in tail
    routing = json.loads(messages[1]["content"].split("\n[上一轮路由]\n")[1].split("\n[用户本轮原话]\n")[0])
    assert routing["last_agent"] == "planner"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_status_only_turn_without_loader_field_still_passes_card_text():
    content = "[workflow_stopped]\nreason: clarification_needed\nquestion: 主角叫什么？"
    _, messages = await _route(_state("主角叫陈默，是个外卖员", {"content": content}))

    assert messages[1]["content"] == (
        "[上一轮助手结尾]\n" + content + "\n[用户本轮原话]\n主角叫陈默，是个外卖员"
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_previous_turn_without_text_or_routing_keeps_old_message():
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
    assert headers == ["[上一轮助手结尾]", "[上一轮路由]", "[用户本轮原话]"]
    for header in headers:
        assert header in ROUTER_PROMPT


@pytest.mark.unit
def test_router_prompt_rules_for_replies_and_write_content():
    from agent.prompts.subagents import ROUTER_PROMPT

    # 补问作答（任务还没做完）沿用上一轮全部路由字段
    assert re.search(
        r"A 补问.*沿用上一轮路由的 agent.*workflow_type、write_content、read_only 和 scope", ROUTER_PROMPT
    )
    # 确认上一轮提议的下一步：按提议的那一步路由，不沿用只读 / 不要正文 / 已交付的范围
    # （与 inherit_routing_after_clarification 把这些问句收尾交回 LLM 路由的理由一致）
    offer_rule = re.search(r"^- B 提议下一步：.*$", ROUTER_PROMPT, flags=re.MULTILINE)
    assert offer_rule
    for required in ("writer + quick", "write_content=true", "read_only=false"):
        assert required in offer_rule.group(0)
    assert re.search(r"不要沿用上一轮的 read_only=true、write_content=false 或已交付的 scope", offer_rule.group(0))
    # 旧的「确认一律沿用 write_content / read_only」规则不能回来
    assert "write_content / read_only 也沿用" not in ROUTER_PROMPT
    # write_content=false 只认用户原话里的明确否定，补充设定信息不算
    assert re.search(r"只有用户原话里明确不要正文（“先别写/不要正文/只要大纲/先不用写”）", ROUTER_PROMPT)
    assert "上一轮助手说的话不能成为 false 的理由" in ROUTER_PROMPT
    # 检查并修改仍交给 writer；只审不改仍是 review_only
    assert re.search(r"检查并修改.*→ writer \+ quick", ROUTER_PROMPT)
    assert re.search(r"只审不改.*→ quality_reviewer \+ review_only", ROUTER_PROMPT)
