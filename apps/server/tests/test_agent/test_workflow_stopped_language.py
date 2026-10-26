"""F-30：作者可见的 workflow_stopped 提示与当前文件标签跟随作者界面语言，不出现内部角色 id。"""

import re
from unittest.mock import AsyncMock, patch

import pytest

from agent.core.workflow_events import StreamEvent, StreamEventType

_INTERNAL_IDS = ("writer", "planner", "quality_reviewer", "hook_designer")
_CJK = re.compile(r"[㐀-鿿]")


def _has_internal_id(text: str) -> bool:
    return any(re.search(rf"(?<![A-Za-z]){name}(?![A-Za-z])", text) for name in _INTERNAL_IDS)


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["self_handoff", "planned_self_handoff", "read_only_blocked"])
@pytest.mark.parametrize("agent_type", ["writer", "quality_reviewer", "planner"])
def test_workflow_stopped_messages_use_author_language_and_role_names(kind, agent_type):
    from agent.graph.writing_graph import _workflow_stopped_message

    english = _workflow_stopped_message(kind, agent_type, english=True)
    chinese = _workflow_stopped_message(kind, agent_type, english=False)

    # 和前端 chat:workflow.agents.* 的显示名一致。
    assert {"writer": "Writer", "quality_reviewer": "Reviewer", "planner": "Outline planner"}[agent_type] in english
    assert english and not _CJK.search(english)
    assert "_" not in english
    assert {"writer": "内容创作者", "quality_reviewer": "质量审稿人", "planner": "大纲规划师"}[agent_type] in chinese
    assert not _has_internal_id(chinese)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_english_author_self_handoff_notice_is_english():
    from agent.graph.writing_graph import run_writing_workflow_streaming

    async def fake_agent(_state, agent_type, **_kwargs):
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Editing"})
        yield StreamEvent(
            type=StreamEventType.HANDOFF,
            data={"target_agent": agent_type, "reason": "continue", "context": "self"},
        )

    router_result = {
        "current_agent": "writer",
        "workflow_plan": "quick",
        "workflow_agents": [],
        "routing_metadata": {"agent_type": "writer", "workflow_type": "quick", "reason": "test", "confidence": 1.0},
    }
    with (
        patch("agent.graph.writing_graph.router_node", AsyncMock(return_value=router_result)),
        patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
        patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
    ):
        events = [
            event async for event in run_writing_workflow_streaming(
                state={"user_message": "Edit it", "messages": [], "system_prompt": "", "language": "en"},
                thread_id="self-handoff-en",
                max_iterations=3,
            )
        ]

    stopped = [e for e in events if e.type == StreamEventType.WORKFLOW_STOPPED]
    assert stopped and stopped[0].data["reason"] == "invalid_handoff"
    message = stopped[0].data["message"]
    assert not _CJK.search(message)
    assert "Writer" in message and "writer" not in message


@pytest.mark.unit
@pytest.mark.parametrize(("language", "labels", "absent"), [
    ("en", ("- File ID: f-1", "- Type: draft", "- Title: Chapter 1"), ("文件 ID", "类型", "标题")),
    ("zh", ("- 文件 ID: f-1", "- 类型: draft", "- 标题: Chapter 1"), ("File ID", "Type:", "Title:")),
])
def test_current_file_labels_follow_language(language, labels, absent):
    from agent.core.message_manager import MessageManager

    manager = MessageManager(project_id="project-1")
    section = "\n".join(manager._build_metadata_section(
        {"current_file_id": "f-1", "current_file_type": "draft", "current_file_title": "Chapter 1"},
        manager._force_en(language),
    ))

    for label in labels:
        assert label in section
    for label in absent:
        assert label not in section
