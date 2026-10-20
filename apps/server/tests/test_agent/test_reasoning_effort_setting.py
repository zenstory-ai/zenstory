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
    monkeypatch.setattr(runner, "build_failover_chat_model", lambda *_args, **_kwargs: "synthetic-model")

    agent = runner._build_agent("writer", "system prompt")  # noqa: SLF001

    reasoning = agent.model_settings.reasoning
    assert (reasoning.effort if reasoning else None) == effort
