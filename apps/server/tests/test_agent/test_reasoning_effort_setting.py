"""AGENT_REASONING_EFFORT is an off-by-default evaluation switch (#278)."""

import importlib

import pytest

from agent.openai_agents import runner
from config import agent_runtime


@pytest.mark.parametrize(("raw", "expected"), [("", None), ("LOW", "low"), ("medium", "medium"), ("fast", None)])
def test_only_known_effort_values_are_used(monkeypatch, raw, expected):
    monkeypatch.setenv("AGENT_REASONING_EFFORT", raw)
    try:
        assert importlib.reload(agent_runtime).AGENT_REASONING_EFFORT == expected
    finally:
        monkeypatch.delenv("AGENT_REASONING_EFFORT")
        importlib.reload(agent_runtime)


@pytest.mark.parametrize("effort", [None, "low"])
def test_agent_sends_reasoning_effort_only_when_set(monkeypatch, effort):
    monkeypatch.setattr(agent_runtime, "AGENT_REASONING_EFFORT", effort)
    monkeypatch.setattr(runner, "get_deepseek_chat_model", lambda: "synthetic-model")

    agent = runner._build_agent("writer", "system prompt")  # noqa: SLF001

    reasoning = agent.model_settings.reasoning
    assert (reasoning.effort if reasoning else None) == effort


@pytest.mark.parametrize(
    ("agent_type", "rework", "expected"),
    [
        ("quality_reviewer", False, "low"),
        ("writer", True, "medium"),
        ("writer", False, None),
        ("planner", False, None),
    ],
)
def test_reviewer_and_rework_overrides_apply_only_to_their_role(monkeypatch, agent_type, rework, expected):
    monkeypatch.setattr(agent_runtime, "AGENT_REVIEWER_REASONING_EFFORT", "low")
    monkeypatch.setattr(agent_runtime, "AGENT_REWORK_REASONING_EFFORT", "medium")
    monkeypatch.setattr(runner, "get_deepseek_chat_model", lambda: "synthetic-model")

    agent = runner._build_agent(agent_type, "system prompt", rework=rework)  # noqa: SLF001

    reasoning = agent.model_settings.reasoning
    assert (reasoning.effort if reasoning else None) == expected


@pytest.mark.asyncio
@pytest.mark.unit
async def test_only_the_writer_run_handed_back_by_the_reviewer_is_a_rework():
    from tests.test_agent.body_write_events import edit_file_events
    from tests.test_agent.test_author_reply_shaping import _handoff, _run_graph, _text, _writer_writes_chapter

    runs: list[tuple[str, bool]] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        runs.append((agent_type, state.get("review_rework") is True))
        if agent_type == "quality_reviewer":
            yield _handoff("writer", "把结尾那句换个写法")
            return
        if len(runs) == 1:
            for event in _writer_writes_chapter():
                yield event
            return
        for event in edit_file_events("fix-1", "她把炉门带上。", file_id="draft-3", title="第三章"):
            yield event
        yield _text("第三章结尾改好了。")

    await _run_graph(fake_agent)

    assert runs == [("writer", False), ("quality_reviewer", False), ("writer", True)]
