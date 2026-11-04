"""Semantic follow-up routing must use structured model/state decisions, not phrase lists."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.graph.router import RouterFailure, router_node


def _response(payload: dict) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "usage": {"input_tokens": 17, "output_tokens": 9, "total_tokens": 26},
        "finish_reason": "stop",
    }


def _state(message: str, previous: dict) -> dict:
    return {
        "user_message": message,
        "router_message": message,
        "messages": [
            {"role": "user", "content": "上一轮请求"},
            {"role": "assistant", **previous},
            {"role": "user", "content": message},
        ],
    }


async def test_two_mid_plan_resumes_roundtrip_persisted_history_without_repeating_design_stage():
    from agent.core.session_loader import SessionLoader
    from agent.service import routing_from_router_decided

    previous = {"content": "设计阶段尚未结束。", "stop_reason": "no_progress", "routing": {
        "initial_agent": "planner", "last_agent": "hook_designer", "workflow_type": "full",
        "write_content": True, "scope": "大纲后写第一章", "requested_file_types": ["outline", "draft"],
    }}
    model = _response({"agent_type": "writer", "workflow_type": "quick",
                       "turn_disposition": "resume_interrupted_task"})
    for _ in range(2):
        with patch("agent.graph.router._route_with_chat_model", AsyncMock(return_value=model)):
            result = await router_node(_state("接着做未完成的部分", previous))
        assert result["current_agent"] == "hook_designer"
        assert result["workflow_agents"] == ["writer"]
        routing = routing_from_router_decided({
            "initial_agent": result["current_agent"], "workflow_plan": result["workflow_plan"],
            "routing_metadata": result["routing_metadata"],
        })
        routing["last_agent"] = "hook_designer"
        previous = SessionLoader("p", "u")._format_chat_message_for_history(SimpleNamespace(
            id="synthetic-round", role="assistant", content="本轮中段停止。", tool_calls=None,
            reasoning_content=None, message_metadata=json.dumps({"routing": routing, "stop_reason": "no_progress"}),
        ))


@pytest.mark.parametrize("disposition", ["resume_interrupted_task", "answer_pending_clarification"])
async def test_first_turn_mismatched_followup_keeps_an_oversize_scope_as_a_constraint(disposition):
    """过长的 follow-up scope 不再整轮报错：落库的短 scope 截到上限，全文作为限制保留，不放宽范围。"""
    model = _response({"agent_type": "writer", "workflow_type": "quick",
                       "turn_disposition": disposition, "scope": "长" * 81})
    with patch("agent.graph.router._route_with_chat_model", AsyncMock(return_value=model)):
        result = await router_node({"user_message": "处理这条新要求", "messages": []})
    assert len(result["routing_metadata"]["scope"]) == 80
    assert "长" * 81 in result["routing_metadata"]["scope_constraints"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_arbitrary_resume_wording_uses_structured_disposition_and_previous_route():
    """The wording is intentionally absent from the former continue allow-list."""
    previous = {
        "content": "写到规划的一半。",
        "stop_reason": "run_deadline_exceeded",
        "routing": {
            "initial_agent": "planner",
            "last_agent": "planner",
            "owner_agent": "planner",
            "workflow_type": "standard",
            "read_only": False,
            "write_content": True,
            "scope": "先规划，再写第1—3章",
            "requested_file_types": ["outline", "draft"],
        },
    }
    model_decision = {
        "agent_type": "writer",
        "workflow_type": "quick",
        "turn_disposition": "resume_interrupted_task",
        "read_only": False,
        "write_content": True,
    }

    with patch(
        "agent.graph.router._route_with_chat_model",
        AsyncMock(return_value=_response(model_decision)),
    ):
        result = await router_node(_state("把刚才没收住的那段接起来", previous))

    assert result["current_agent"] == "planner"
    assert result["workflow_plan"] == "standard"
    assert result["workflow_agents"] == ["writer"]
    assert result["routing_metadata"]["scope"] == "先规划，再写第1—3章"
    assert result["routing_metadata"]["requested_file_types"] == ["outline", "draft"]
    assert result["routing_metadata"]["turn_disposition"] == "resume_interrupted_task"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_long_clarification_answer_is_not_rejected_by_character_count():
    previous = {
        "content": "请选择人物关系。",
        "clarification_pending": True,
        "pending_clarification": {
            "question": "李审与主角是什么关系？",
            "context": "人物关系会影响第一章冲突。",
            "details": ["师徒", "旧敌", "亲属"],
        },
        "routing": {
            "initial_agent": "planner",
            "last_agent": "planner",
            "owner_agent": "planner",
            "workflow_type": "standard",
            "read_only": True,
            "write_content": False,
            "scope": "只补人物关系，不写正文",
            "requested_file_types": ["character"],
        },
    }
    answer = "李审是主角失散多年的师兄，两人过去因一次误判决裂；本轮只把这层关系补进人设，不需要改正文。"
    model_decision = {
        "agent_type": "writer",
        "workflow_type": "quick",
        "turn_disposition": "answer_pending_clarification",
        "read_only": False,
        "write_content": True,
    }

    with patch(
        "agent.graph.router._route_with_chat_model",
        AsyncMock(return_value=_response(model_decision)),
    ):
        result = await router_node(_state(answer, previous))

    assert len(answer) > 40
    assert result["current_agent"] == "planner"
    assert result["workflow_agents"] == []
    assert result["routing_metadata"]["read_only"] is True
    assert result["routing_metadata"]["write_content"] is False
    assert result["routing_metadata"]["requested_file_types"] == ["character"]
    assert result["routing_metadata"]["turn_disposition"] == "answer_pending_clarification"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_new_request_does_not_inherit_previous_write_authority():
    previous = {
        "content": "第一章已完成。",
        "routing": {
            "initial_agent": "writer",
            "last_agent": "writer",
            "owner_agent": "writer",
            "workflow_type": "quick",
            "read_only": False,
            "write_content": True,
            "scope": "第一章正文",
            "requested_file_types": ["draft"],
        },
    }
    model_decision = {
        "agent_type": "quality_reviewer",
        "workflow_type": "review_only",
        "turn_disposition": "new_or_changed_request",
        "read_only": True,
        "write_content": False,
        "scope": "只评估第一章，不改文件",
        "requested_file_types": ["draft"],
    }

    with patch(
        "agent.graph.router._route_with_chat_model",
        AsyncMock(return_value=_response(model_decision)),
    ):
        result = await router_node(_state("换个事情：只评估第一章，不动稿子", previous))

    assert result["current_agent"] == "quality_reviewer"
    assert result["workflow_plan"] == "review_only"
    assert result["routing_metadata"]["read_only"] is True
    assert result["routing_metadata"]["write_content"] is False
    assert result["routing_metadata"]["scope"] == "只评估第一章，不改文件"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_legacy_decision_without_disposition_never_inherits_previous_route():
    previous = {
        "content": "写到一半。",
        "stop_reason": "run_deadline_exceeded",
        "routing": {
            "initial_agent": "planner",
            "last_agent": "planner",
            "owner_agent": "planner",
            "workflow_type": "standard",
            "read_only": True,
            "write_content": False,
            "scope": "旧任务",
        },
    }
    legacy = {
        "agent_type": "writer",
        "workflow_type": "quick",
        "read_only": False,
        "write_content": True,
        "scope": "新任务",
    }

    with patch(
        "agent.graph.router._route_with_chat_model", AsyncMock(return_value=_response(legacy))
    ):
        result = await router_node(_state("任意表达", previous))

    assert result["current_agent"] == "writer"
    assert result["workflow_plan"] == "quick"
    assert result["routing_metadata"]["scope"] == "新任务"
    assert result["routing_metadata"].get("turn_disposition") is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_unknown_turn_disposition_is_a_router_failure_not_a_guessed_route():
    payload = {
        "agent_type": "writer",
        "workflow_type": "quick",
        "turn_disposition": "keep_going_probably",
        "read_only": False,
        "write_content": True,
    }
    with patch(
        "agent.graph.router._route_with_chat_model",
        AsyncMock(return_value=_response(payload)),
    ):
        with pytest.raises(RouterFailure) as caught:
            await router_node({"user_message": "随便", "router_message": "随便"})

    assert caught.value.category == "schema"
    assert caught.value.failed_fields == ("turn_disposition",)
    assert caught.value.usage == {"input_tokens": 17, "output_tokens": 9, "total_tokens": 26}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_scope_restrictions_persist_across_two_resumes_without_expanding_types():
    from agent.service import routing_from_router_decided

    first_previous = {
        "content": "审到一半。",
        "stop_reason": "run_deadline_exceeded",
        "routing": {
            "initial_agent": "quality_reviewer",
            "last_agent": "quality_reviewer",
            "owner_agent": "quality_reviewer",
            "workflow_type": "review_only",
            "read_only": True,
            "write_content": False,
            "scope": "只审前60章",
            "scope_constraints": ["不改文件"],
            "requested_file_types": ["draft"],
        },
    }
    first_decision = {
        "agent_type": "writer",
        "workflow_type": "quick",
        "turn_disposition": "resume_interrupted_task",
        "read_only": False,
        "write_content": True,
        "scope": "本次先看前20章",
        "requested_file_types": ["outline"],
    }
    with patch(
        "agent.graph.router._route_with_chat_model",
        AsyncMock(return_value=_response(first_decision)),
    ):
        first = await router_node(_state("把未完成的部分接上", first_previous))

    persisted = routing_from_router_decided(
        {
            "initial_agent": first["current_agent"],
            "workflow_plan": first["workflow_plan"],
            "routing_metadata": first["routing_metadata"],
        }
    )
    persisted["last_agent"] = "quality_reviewer"
    persisted["owner_agent"] = "quality_reviewer"
    second_previous = {
        "content": "仍未审完。",
        "stop_reason": "model_call_budget_exhausted",
        "routing": persisted,
    }
    second_decision = {
        "agent_type": "planner",
        "workflow_type": "standard",
        "turn_disposition": "resume_interrupted_task",
        "read_only": False,
        "write_content": True,
        "scope": "只列问题，不给改法",
        "requested_file_types": ["character", "lore"],
    }
    with patch(
        "agent.graph.router._route_with_chat_model",
        AsyncMock(return_value=_response(second_decision)),
    ):
        second = await router_node(_state("接着把这件事做完", second_previous))

    metadata = second["routing_metadata"]
    assert metadata["scope_constraints"] == [
        "只审前60章",
        "不改文件",
        "本次先看前20章",
        "只列问题，不给改法",
    ]
    assert metadata["requested_file_types"] == ["draft"]
    assert metadata["read_only"] is True
    assert metadata["write_content"] is False


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    ("routing_patch", "scope", "failed_field"),
    [
        ({"scope_constraints": ["合法", 3]}, "", "previous_routing.scope_constraints"),
        ({"last_agent": "mystery_agent"}, "", "previous_routing.last_agent"),
    ],
)
async def test_corrupted_previous_routing_fails_before_authority_is_granted(
    routing_patch, scope, failed_field
):
    routing = {
        "initial_agent": "writer",
        "last_agent": "writer",
        "owner_agent": "writer",
        "workflow_type": "quick",
        "read_only": False,
        "write_content": True,
        "scope": "原范围",
        "requested_file_types": ["draft"],
        **routing_patch,
    }
    previous = {
        "content": "未完成。",
        "stop_reason": "run_deadline_exceeded",
        "routing": routing,
    }
    decision = {
        "agent_type": "writer",
        "workflow_type": "quick",
        "turn_disposition": "resume_interrupted_task",
        "read_only": False,
        "write_content": True,
        "scope": scope,
    }
    with patch(
        "agent.graph.router._route_with_chat_model",
        AsyncMock(return_value=_response(decision)),
    ):
        with pytest.raises(RouterFailure) as caught:
            await router_node(_state("继续原任务", previous))

    assert caught.value.failed_fields == (failed_field,)
    assert caught.value.usage == {"input_tokens": 17, "output_tokens": 9, "total_tokens": 26}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_unknown_previous_write_permission_is_not_upgraded_by_followup_true():
    previous = {
        "content": "未完成。",
        "stop_reason": "output_truncated",
        "routing": {
            "initial_agent": "writer",
            "last_agent": "writer",
            "owner_agent": "writer",
            "workflow_type": "quick",
            "read_only": False,
            "write_content": None,
            "scope": "",
            "requested_file_types": [],
        },
    }
    decision = {
        "agent_type": "writer",
        "workflow_type": "quick",
        "turn_disposition": "resume_interrupted_task",
        "read_only": False,
        "write_content": True,
        "clarify_first": True,
    }
    with patch(
        "agent.graph.router._route_with_chat_model",
        AsyncMock(return_value=_response(decision)),
    ):
        result = await router_node(_state("把被截断的输出接完整", previous))

    assert result["routing_metadata"]["write_content"] is None
    assert result["routing_metadata"]["previous_stop_reason"] == "output_truncated"
    assert result["routing_metadata"]["clarify_first"] is True
    assert result["workflow_agents"] == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_legacy_followup_with_valid_last_agent_and_restrictions_can_resume():
    previous = {
        "content": "旧轮次没有完整的新路由字段。",
        "stop_reason": "output_truncated",
        "routing": {
            "last_agent": "writer",
            "read_only": False,
            "write_content": False,
            "scope": "只写第三章",
        },
    }
    decision = {
        "agent_type": "writer",
        "workflow_type": "quick",
        "turn_disposition": "resume_interrupted_task",
        "read_only": False,
        "write_content": True,
    }
    with patch(
        "agent.graph.router._route_with_chat_model",
        AsyncMock(return_value=_response(decision)),
    ):
        result = await router_node(_state("把被截断的部分补完", previous))

    assert result["current_agent"] == "writer"
    assert result["routing_metadata"]["write_content"] is False
    assert result["routing_metadata"]["scope_constraints"] == ["只写第三章"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_exact_duplicate_constraints_are_removed_without_semantic_comparison():
    previous = {
        "content": "未完成。",
        "stop_reason": "run_deadline_exceeded",
        "routing": {
            "last_agent": "writer",
            "workflow_type": "quick",
            "read_only": False,
            "write_content": True,
            "scope": "只写第三章",
            "scope_constraints": ["只写第三章", "不要续写", "不要续写"],
        },
    }
    decision = {
        "agent_type": "writer",
        "workflow_type": "quick",
        "turn_disposition": "resume_interrupted_task",
        "read_only": False,
        "write_content": True,
        "scope": "只写第三章，不要续写",
    }
    with patch(
        "agent.graph.router._route_with_chat_model",
        AsyncMock(return_value=_response(decision)),
    ):
        result = await router_node(_state("接上", previous))

    assert result["routing_metadata"]["scope_constraints"] == [
        "只写第三章",
        "不要续写",
        "只写第三章，不要续写",
    ]


@pytest.mark.parametrize("allowed", [True, False])
@pytest.mark.parametrize("disposition", ["new_or_changed_request", "resume_interrupted_task", "answer_pending_clarification"])
async def test_notes_permission_is_current_turn_only(allowed, disposition):
    previous = {"content": "上轮停下", "stop_reason": "no_progress", "clarification_pending": True,
                "routing": {"initial_agent": "writer", "last_agent": "writer", "workflow_type": "quick",
                            "notes_append_allowed": not allowed}}
    response = _response({"agent_type": "writer", "workflow_type": "quick",
                          "turn_disposition": disposition, "notes_append_allowed": allowed})
    with patch("agent.graph.router._route_with_chat_model", AsyncMock(return_value=response)):
        result = await router_node(_state("本轮请求", previous))
    assert result["routing_metadata"]["notes_append_allowed"] is allowed


@pytest.mark.parametrize("invalid", ["true", "false", 1, None, [], {}])
async def test_notes_permission_rejects_non_boolean(invalid):
    response = _response({"agent_type": "writer", "workflow_type": "quick", "notes_append_allowed": invalid})
    with patch("agent.graph.router._route_with_chat_model", AsyncMock(return_value=response)):
        with pytest.raises(RouterFailure) as caught:
            await router_node({"user_message": "本轮请求"})
    assert caught.value.usage == response["usage"]


def _read_only_proposal_card(**routing_overrides) -> dict:
    """合成样例：只读回合核对后用补问卡提出具体修改方案，问作者要不要改。"""
    routing = {
        "initial_agent": "writer",
        "last_agent": "writer",
        "owner_agent": "writer",
        "workflow_type": "quick",
        "read_only": True,
        "write_content": False,
        "scope": "只核对第5集的时间线",
        "requested_file_types": [],
    }
    routing.update(routing_overrides)
    return {
        "content": "第5集有两处日期和第3集对不上，改法如下。",
        "clarification_pending": True,
        "pending_clarification": {
            "question": "第5集这两处日期现在改吗？",
            "details": ["闪回标头：春天改成冬天", "台词里的日期跟着改"],
        },
        "routing": routing,
    }


@pytest.mark.asyncio
@pytest.mark.unit
async def test_accepting_a_read_only_rounds_proposal_lifts_read_only():
    """只读回合提出改法，作者接受并要求执行时，本轮按方案那一步可写。"""
    model_decision = {
        "agent_type": "writer",
        "workflow_type": "quick",
        "turn_disposition": "answer_pending_clarification",
        "accepts_previous_proposal": True,
        "read_only": False,
        "write_content": True,
        "scope": "按上轮方案改第5集两处日期",
        "requested_file_types": ["script"],
    }

    with patch(
        "agent.graph.router._route_with_chat_model",
        AsyncMock(return_value=_response(model_decision)),
    ):
        result = await router_node(_state("就照你说的那样改吧", _read_only_proposal_card()))

    metadata = result["routing_metadata"]
    assert result["current_agent"] == "writer"
    assert metadata["read_only"] is False
    assert metadata["write_content"] is True
    # 只读回合的范围已经交付，不再限制本轮；本轮范围来自被接受的方案。
    assert metadata["scope"] == "按上轮方案改第5集两处日期"
    assert metadata["scope_constraints"] == ["按上轮方案改第5集两处日期"]
    assert metadata["requested_file_types"] == ["script"]
    assert metadata["turn_disposition"] == "answer_pending_clarification"
    assert metadata["accepts_previous_proposal"] is True
    assert result["followup_resolved"] is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_accepted_proposal_routes_away_from_a_read_only_reviewer():
    """只读审稿后问要不要改：接受方案时由路由选的可写角色接手，不回到没有写工具的审稿人。"""
    previous = _read_only_proposal_card(
        initial_agent="quality_reviewer",
        last_agent="quality_reviewer",
        owner_agent="quality_reviewer",
        workflow_type="review_only",
    )
    model_decision = {
        "agent_type": "writer",
        "workflow_type": "quick",
        "turn_disposition": "answer_pending_clarification",
        "accepts_previous_proposal": True,
        "read_only": False,
        "write_content": True,
    }

    with patch(
        "agent.graph.router._route_with_chat_model",
        AsyncMock(return_value=_response(model_decision)),
    ):
        result = await router_node(_state("都按建议处理", previous))

    assert result["current_agent"] == "writer"
    assert result["workflow_plan"] == "quick"
    assert result["routing_metadata"]["read_only"] is False


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("write_content", [False, True])
async def test_new_do_not_change_request_keeps_read_only_even_when_accepting(write_content):
    """作者接受思路但本轮又说先别动文件：仍然只读。

    本轮 read_only=true 时不算接受方案，上一轮继承来的只读照常保留；即使「不改文件又要写正文」
    的单轮纠正把本轮回答的 read_only 改回 false，继承的只读也不会因此解除。"""
    model_decision = {
        "agent_type": "writer",
        "workflow_type": "quick",
        "turn_disposition": "answer_pending_clarification",
        "accepts_previous_proposal": True,
        "read_only": True,
        "write_content": write_content,
    }

    with patch(
        "agent.graph.router._route_with_chat_model",
        AsyncMock(return_value=_response(model_decision)),
    ):
        result = await router_node(_state("思路可以，但先别动文件", _read_only_proposal_card()))

    metadata = result["routing_metadata"]
    assert result["workflow_agents"] == []
    assert metadata["read_only"] is True
    assert metadata["write_content"] is False
    assert metadata["scope"] == "只核对第5集的时间线"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_answer_without_accepting_a_proposal_still_inherits_read_only():
    """纠错或补充说明（没有接受方案去执行）沿用只读，不因本轮 read_only=false 放宽。"""
    model_decision = {
        "agent_type": "writer",
        "workflow_type": "quick",
        "turn_disposition": "answer_pending_clarification",
        "accepts_previous_proposal": False,
        "read_only": False,
        "write_content": True,
    }

    with patch(
        "agent.graph.router._route_with_chat_model",
        AsyncMock(return_value=_response(model_decision)),
    ):
        result = await router_node(_state("你理解错了，我说的是第3集不是第5集", _read_only_proposal_card()))

    metadata = result["routing_metadata"]
    assert result["workflow_agents"] == []
    assert metadata["read_only"] is True
    assert metadata["write_content"] is False
    assert metadata["scope"] == "只核对第5集的时间线"
    assert result["followup_resolved"] is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_accepting_does_not_widen_a_writable_rounds_inherited_scope():
    """上一轮本来就可写：接受字段不改变正常的补问继承（不扩大旧范围、不提升旧限制）。"""
    previous = _read_only_proposal_card(read_only=False, write_content=False, scope="只改大纲")
    model_decision = {
        "agent_type": "writer",
        "workflow_type": "quick",
        "turn_disposition": "answer_pending_clarification",
        "accepts_previous_proposal": True,
        "read_only": False,
        "write_content": True,
        "scope": "顺便写第6集",
    }

    with patch(
        "agent.graph.router._route_with_chat_model",
        AsyncMock(return_value=_response(model_decision)),
    ):
        result = await router_node(_state("可以，就这么改", previous))

    metadata = result["routing_metadata"]
    assert metadata["read_only"] is False
    assert metadata["write_content"] is False
    assert metadata["scope"] == "只改大纲"
    assert metadata["scope_constraints"] == ["只改大纲", "顺便写第6集"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_accepting_cannot_lift_read_only_when_resuming_an_interrupted_task():
    """截停续接不是接受新方案：只读审查被截停后“继续”仍然只读。"""
    previous = {
        "content": "审到第20章时停下了。",
        "stop_reason": "max_turns_exceeded",
        "routing": {
            "initial_agent": "quality_reviewer",
            "last_agent": "quality_reviewer",
            "owner_agent": "quality_reviewer",
            "workflow_type": "review_only",
            "read_only": True,
            "write_content": False,
            "scope": "只检查前30章",
        },
    }
    model_decision = {
        "agent_type": "writer",
        "workflow_type": "quick",
        "turn_disposition": "resume_interrupted_task",
        "accepts_previous_proposal": True,
        "read_only": False,
        "write_content": True,
    }

    with patch(
        "agent.graph.router._route_with_chat_model",
        AsyncMock(return_value=_response(model_decision)),
    ):
        result = await router_node(_state("接着来", previous))

    assert result["current_agent"] == "quality_reviewer"
    assert result["routing_metadata"]["read_only"] is True


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("invalid", ["true", 1, [], {}])
async def test_accepts_previous_proposal_rejects_non_boolean(invalid):
    response = _response({"agent_type": "writer", "workflow_type": "quick",
                          "accepts_previous_proposal": invalid})
    with patch("agent.graph.router._route_with_chat_model", AsyncMock(return_value=response)):
        with pytest.raises(RouterFailure) as caught:
            await router_node({"user_message": "本轮请求"})
    assert caught.value.usage == response["usage"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_passthrough_guard_planner_no_body_decision_schedules_no_writer():
    """透传护栏（只贴材料型）：路由模型给出 planner + 禁写正文 + 只读时，服务端不额外安排 writer。

    决策是 mock 的，这条只证明服务端照单执行；“只贴材料先问”这条提示词规则本身没有自动化覆盖，
    要靠合规样本的真实路由评测。
    """
    model_decision = {
        "agent_type": "planner",
        "workflow_type": "standard",
        "turn_disposition": "new_or_changed_request",
        "read_only": True,
        "write_content": False,
        "scope": "先问作者要用这些材料做什么",
        "requested_file_types": [],
    }

    with patch(
        "agent.graph.router._route_with_chat_model",
        AsyncMock(return_value=_response(model_decision)),
    ):
        result = await router_node({"user_message": "故事简介\n边陲少年在遗迹里融合了一块魔晶……"})

    assert result["current_agent"] == "planner"
    assert result["workflow_agents"] == []
    assert result["routing_metadata"]["write_content"] is False


@pytest.mark.unit
def test_router_input_shows_the_previous_ask_first_material_scope():
    """只贴材料先问的一轮用补问卡问了怎么用：路由器输入里能看到上一轮的“先问”范围，
    模型才分得出作者这次说出要做什么是新的交付物，而不是接着上一轮的只读。"""
    from agent.graph.router import build_router_user_message

    previous = {
        "content": "读完了这份简介。",
        "clarification_pending": True,
        "pending_clarification": {
            "question": "想用这份简介做什么？",
            "details": ["按它写第一章", "先整理成大纲"],
        },
        "routing": {
            "initial_agent": "planner",
            "last_agent": "planner",
            "owner_agent": "planner",
            "workflow_type": "standard",
            "read_only": True,
            "write_content": False,
            "scope": "先问作者要用这些材料做什么",
            "requested_file_types": [],
        },
    }
    state = _state("按它写第一章", previous)
    assert "先问作者要用这些材料做什么" in build_router_user_message(state)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_passthrough_guard_read_only_hook_focus_schedules_no_writer():
    """透传护栏（只读后纠错型）：路由模型给出只读的 hook_focus 时，服务端不计划交给 writer。

    决策是 mock 的；“纠错沿用只读”和 hook_designer 只在明确要正文时交接这两条提示词规则
    没有自动化覆盖，要靠合规样本的真实路由评测。
    """
    previous = {
        "content": "按你给的三条线索，凶手更可能是管家。",
        "routing": {
            "initial_agent": "hook_designer",
            "last_agent": "hook_designer",
            "workflow_type": "hook_focus",
            "read_only": True,
            "write_content": False,
        },
    }
    model_decision = {
        "agent_type": "hook_designer",
        "workflow_type": "hook_focus",
        "turn_disposition": "new_or_changed_request",
        "read_only": True,
        "write_content": False,
    }

    with patch(
        "agent.graph.router._route_with_chat_model",
        AsyncMock(return_value=_response(model_decision)),
    ):
        result = await router_node(_state("你理解错了，我说的是另一个配角", previous))

    assert result["workflow_agents"] == []
    assert result["routing_metadata"]["read_only"] is True


def test_router_prompt_example_requests_every_semantic_decision_field():
    """提示词里的 JSON 示例必须是合法决策，并向模型要到解析器会读的语义字段。"""
    from agent.graph.router import _parse_router_response
    from agent.prompts.subagents import ROUTER_PROMPT

    example = ROUTER_PROMPT.split("下面是一个合法示例；实际范围字段按用户本轮请求填写：\n", 1)[1]
    example = example.split("\n}\n", 1)[0] + "\n}"
    payload = json.loads(example)
    decision = _parse_router_response(_response(payload))

    assert "accepts_previous_proposal" in payload
    assert decision.accepts_previous_proposal is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_followup_with_more_limits_and_a_long_scope_keeps_every_restriction():
    """生产 11-01～03：续接时旧限制加本轮限制超过 8 条、或本轮 scope 过长，整轮报错。现在全部保留。"""
    previous = {
        "content": "未完成。",
        "stop_reason": "run_deadline_exceeded",
        "routing": {
            "initial_agent": "writer", "last_agent": "writer", "owner_agent": "writer",
            "workflow_type": "quick", "read_only": False, "write_content": True,
            "scope": "原范围", "requested_file_types": ["draft"],
            "scope_constraints": [f"限制{i}" for i in range(8)],
        },
    }
    decision = {
        "agent_type": "writer", "workflow_type": "quick", "turn_disposition": "resume_interrupted_task",
        "read_only": False, "write_content": True, "scope": "超" * 81,
    }
    with patch("agent.graph.router._route_with_chat_model", AsyncMock(return_value=_response(decision))):
        result = await router_node(_state("继续原任务", previous))
    restrictions = result["routing_metadata"]["scope_constraints"]
    assert all(f"限制{i}" in restrictions for i in range(8))
    assert "超" * 81 in restrictions
    assert result["routing_metadata"]["clarify_first"] is not True
