"""Material-flow DeepSeek calls are metered and billed to the novel owner."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from flows.utils.clients import llm as llm_mod
from models import Chapter, LLMUsageEvent, Novel, User
from services.usage import llm_usage_service

ATOMIC_TASKS = Path(__file__).resolve().parents[3] / "flows" / "atomic_tasks"


class _FakeCompletions:
    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(
            prompt_tokens=2000,
            completion_tokens=6000,
            total_tokens=8000,
            prompt_cache_hit_tokens=1500,
            prompt_cache_miss_tokens=500,
            model_dump=lambda: {"prompt_tokens": 2000, "completion_tokens": 6000},
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'), finish_reason="stop")],
            usage=usage,
            model="deepseek-flash",
        )


def _client() -> tuple[llm_mod.DeepSeekClient, _FakeCompletions]:
    client = llm_mod.DeepSeekClient.__new__(llm_mod.DeepSeekClient)
    completions = _FakeCompletions()
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    client.model = "deepseek-flash"
    client.max_tokens = 100
    client.temperature = 0.5
    return client, completions


@pytest.fixture
def owned_chapter(db_session: Session):
    llm_usage_service._novel_owner_cache.clear()
    llm_usage_service._chapter_novel_cache.clear()
    owner = User(username="flow_owner", email="flow_owner@example.com", hashed_password="x")
    db_session.add(owner)
    db_session.commit()
    novel = Novel(user_id=owner.id, title="Flow book")
    db_session.add(novel)
    db_session.commit()
    chapter = Chapter(novel_id=novel.id, chapter_number=1, title="c1")
    db_session.add(chapter)
    db_session.commit()
    yield owner, novel, chapter
    llm_usage_service._novel_owner_cache.clear()
    llm_usage_service._chapter_novel_cache.clear()


def _events(db_session: Session) -> list[LLMUsageEvent]:
    db_session.expire_all()
    return list(db_session.exec(select(LLMUsageEvent)).all())


@pytest.mark.integration
def test_chat_completion_meters_by_chapter_owner(db_session: Session, owned_chapter):
    owner, novel, chapter = owned_chapter
    client, completions = _client()

    response = client.chat_completion(
        [{"role": "user", "content": "x"}], system_prompt="s", usage_chapter_id=chapter.id
    )

    assert response.content == '{"ok": true}'
    # Attribution kwargs never reach the OpenAI request.
    assert "usage_chapter_id" not in completions.calls[0]
    assert "usage_novel_id" not in completions.calls[0]
    [event] = _events(db_session)
    assert (event.user_id, event.source, event.correlation_id) == (owner.id, "material", f"novel:{novel.id}")
    assert (event.cache_hit_tokens, event.cache_miss_tokens, event.output_tokens) == (1500, 500, 6000)


@pytest.mark.integration
def test_chat_completion_meters_by_novel_and_skips_without_target(db_session: Session, owned_chapter):
    owner, novel, _chapter = owned_chapter
    client, _completions = _client()

    client.chat_completion([{"role": "user", "content": "x"}])
    assert _events(db_session) == []

    client.chat_completion([{"role": "user", "content": "x"}], usage_novel_id=novel.id)
    [event] = _events(db_session)
    assert event.user_id == owner.id


@pytest.mark.unit
def test_call_deepseek_api_forwards_attribution(monkeypatch):
    seen: dict = {}

    class _Client:
        def chat_completion(self, messages, system_prompt=None, **kwargs):
            seen.update(kwargs)
            return "resp"

    monkeypatch.setattr(llm_mod, "get_deepseek_client", lambda: _Client())
    assert llm_mod.call_deepseek_api([{"role": "user", "content": "x"}], "s", usage_novel_id=7) == "resp"
    assert seen == {"usage_novel_id": 7}


def _deepseek_calls() -> list[tuple[str, ast.Call]]:
    calls = []
    for path in sorted(ATOMIC_TASKS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "call_deepseek_api":
                calls.append((f"{path.relative_to(ATOMIC_TASKS)}:{node.lineno}", node))
    return calls


@pytest.mark.unit
def test_every_material_llm_call_names_who_pays():
    calls = _deepseek_calls()
    assert calls, "expected material LLM call sites"
    missing = [
        where
        for where, node in calls
        if not {kw.arg for kw in node.keywords} & {"usage_novel_id", "usage_chapter_id"}
    ]
    assert missing == []
