"""Unit tests for material LLM sticky session routing and subflow cache warm-up."""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.core import llm_failover, llm_providers
from core.error_codes import ErrorCode
from flows.utils.clients import llm as llm_mod
from flows.utils.helpers.exceptions import LLMNonRetryableError
from tests.test_flows.conftest import FakeMonitor, FakeTask

chapter_mod = importlib.import_module("flows.pipelines.subflows.chapter_extraction_flow")
char_mod = importlib.import_module("flows.pipelines.subflows.character_entity_build_flow")
story_mod = importlib.import_module("flows.pipelines.subflows.story_aggregate_flow")


def _ok_response(content: str = '{"summary": "synthetic"}', *, model: str = "wire-model"):
    usage = SimpleNamespace(model_dump=lambda: {"prompt_tokens": 100, "completion_tokens": 20})
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=usage,
        model=model,
    )


@contextmanager
def _fake_session():
    yield MagicMock()


def _make_client() -> llm_mod.MaterialLLMClient:
    client = llm_mod.MaterialLLMClient.__new__(llm_mod.MaterialLLMClient)
    client.max_tokens = 2048
    client.temperature = 0.2
    return client


def _patch_llm_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    owner_resolver,
    trial_limit: int | None = None,
) -> None:
    import flows.database_session as dbs
    import services.usage.llm_usage_service as usage_mod

    monkeypatch.setattr(dbs, "create_prefect_session", _fake_session)
    monkeypatch.setattr(usage_mod, "resolve_material_owner", owner_resolver)
    monkeypatch.setattr(llm_mod, "_trial_output_limit", lambda *_args, **_kwargs: trial_limit)
    monkeypatch.setattr(llm_mod, "_meter_material_call", MagicMock())


# ---------------------------------------------------------------- MaterialLLMClient session_id


@pytest.mark.unit
def test_material_llm_client_derives_novel_session_id_from_usage_novel_id(monkeypatch):
    seen_kwargs: dict = {}
    _patch_llm_dependencies(
        monkeypatch,
        owner_resolver=lambda _s, *, novel_id=None, chapter_id=None: ("user-1", novel_id),
    )

    def fake_create(**kwargs):
        seen_kwargs.update(kwargs)
        provider = SimpleNamespace(name="minimax", model="MiniMax-M3.1-Flash")
        return _ok_response(), provider

    monkeypatch.setattr(llm_failover, "create_chat_completion", fake_create)

    _make_client().chat_completion(
        [{"role": "user", "content": "synthetic"}],
        usage_novel_id=42,
    )

    assert seen_kwargs["session_id"] == "material:novel:42"


@pytest.mark.unit
def test_material_llm_client_derives_novel_session_id_from_usage_chapter_id(monkeypatch):
    seen_kwargs: dict = {}
    _patch_llm_dependencies(
        monkeypatch,
        owner_resolver=lambda _s, *, novel_id=None, chapter_id=None: ("user-1", 42),
    )

    def fake_create(**kwargs):
        seen_kwargs.update(kwargs)
        provider = SimpleNamespace(name="minimax", model="MiniMax-M3.1-Flash")
        return _ok_response(), provider

    monkeypatch.setattr(llm_failover, "create_chat_completion", fake_create)

    _make_client().chat_completion(
        [{"role": "user", "content": "synthetic"}],
        usage_chapter_id=101,
    )

    assert seen_kwargs["session_id"] == "material:novel:42"


@pytest.mark.unit
def test_material_llm_client_falls_back_to_chapter_session_id_when_novel_unresolved(monkeypatch):
    seen_kwargs: dict = {}
    _patch_llm_dependencies(
        monkeypatch,
        owner_resolver=lambda _s, *, novel_id=None, chapter_id=None: (None, None),
    )

    def fake_create(**kwargs):
        seen_kwargs.update(kwargs)
        provider = SimpleNamespace(name="minimax", model="MiniMax-M3.1-Flash")
        return _ok_response(), provider

    monkeypatch.setattr(llm_failover, "create_chat_completion", fake_create)

    _make_client().chat_completion(
        [{"role": "user", "content": "synthetic"}],
        usage_chapter_id=305,
    )

    assert seen_kwargs["session_id"] == "material:chapter:305"


@pytest.mark.unit
def test_material_llm_client_respects_explicit_session_id_and_leaves_none_unattributed(monkeypatch):
    captured: list[str | None] = []
    _patch_llm_dependencies(
        monkeypatch,
        owner_resolver=lambda _s, *, novel_id=None, chapter_id=None: ("user-1", novel_id),
    )

    def fake_create(**kwargs):
        captured.append(kwargs.get("session_id"))
        provider = SimpleNamespace(name="minimax", model="MiniMax-M3.1-Flash")
        return _ok_response(), provider

    monkeypatch.setattr(llm_failover, "create_chat_completion", fake_create)

    client = _make_client()
    client.chat_completion(
        [{"role": "user", "content": "explicit"}],
        usage_novel_id=42,
        session_id="custom:route:7",
    )
    client.chat_completion([{"role": "user", "content": "unattributed"}])

    assert captured == ["custom:route:7", None]


@pytest.mark.unit
def test_material_llm_client_pins_minimax_dual_key_routing_for_same_novel(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "MiniMax-M3.1-Flash")
    monkeypatch.delenv("LLM_BACKUP_PROVIDER", raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "synthetic-minimax-primary")
    monkeypatch.setenv("MINIMAX_SECONDARY_API_KEY", "synthetic-minimax-secondary")
    monkeypatch.setenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT", "50")

    llm_providers.reset_provider_client_cache()
    llm_providers.reset_key_routing_state()
    llm_failover.reset_failover_state()

    _patch_llm_dependencies(
        monkeypatch,
        owner_resolver=lambda _s, *, novel_id=None, chapter_id=None: (
            ("user-1", novel_id if novel_id is not None else 77)
        ),
    )

    seen_key_slots: list[str] = []

    def fake_get_sync_client(provider, **_kwargs):
        seen_key_slots.append(provider.key_slot)
        return SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_req: _ok_response(model=provider.model))
            )
        )

    monkeypatch.setattr(llm_providers, "get_sync_client", fake_get_sync_client)

    client = _make_client()
    for chapter_id in range(1, 11):
        client.chat_completion(
            [{"role": "user", "content": f"chapter {chapter_id}"}],
            usage_chapter_id=chapter_id,
        )
    for _ in range(5):
        client.chat_completion(
            [{"role": "user", "content": "novel-level"}],
            usage_novel_id=77,
        )

    # All 15 calls (chapter-scoped resolved to novel 77 + novel-scoped 77) stay on one key slot.
    assert len(seen_key_slots) == 15
    assert len(set(seen_key_slots)) == 1

    # Contrast: unattributed calls (session_id=None) rotate across both key slots.
    seen_key_slots.clear()
    for _ in range(10):
        client.chat_completion([{"role": "user", "content": "unattributed"}])
    assert set(seen_key_slots) == {llm_providers.KEY_SLOT_PRIMARY, llm_providers.KEY_SLOT_SECONDARY}


@pytest.mark.unit
def test_chapter_summary_json_call_does_not_pin_non_json_calls_to_deepseek(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "MiniMax-M3.1-Flash")
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_BACKUP_MODEL", "deepseek-flash")
    monkeypatch.setenv("MINIMAX_API_KEY", "synthetic-minimax")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-deepseek")
    monkeypatch.delenv("MINIMAX_SECONDARY_API_KEY", raising=False)

    llm_providers.reset_provider_client_cache()
    llm_providers.reset_key_routing_state()
    llm_failover.reset_failover_state()

    _patch_llm_dependencies(
        monkeypatch,
        owner_resolver=lambda _s, *, novel_id=None, chapter_id=None: ("user-1", 88),
    )

    served_providers: list[tuple[str, str]] = []

    def fake_get_sync_client(provider, **_kwargs):
        def create(**_req):
            served_providers.append((provider.slot, provider.name))
            return _ok_response(model=provider.model)

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setattr(llm_providers, "get_sync_client", fake_get_sync_client)

    client = _make_client()
    # 1. Chapter summary requests JSON object -> routed to eligible backup (deepseek)
    client.chat_completion(
        [{"role": "user", "content": "chapter 1 summary"}],
        usage_chapter_id=501,
        response_format={"type": "json_object"},
    )
    # 2. Subsequent non-JSON calls for the same novel MUST stay on primary (minimax)
    client.chat_completion(
        [{"role": "user", "content": "chapter 1 plots"}],
        usage_chapter_id=501,
    )
    client.chat_completion(
        [{"role": "user", "content": "character entity"}],
        usage_novel_id=88,
    )

    assert served_providers == [
        ("backup", "deepseek"),
        ("primary", "minimax"),
        ("primary", "minimax"),
    ]


@pytest.mark.unit
def test_material_llm_client_preserves_trial_invariants_with_session_id(monkeypatch):
    seen_kwargs: dict = {}
    _patch_llm_dependencies(
        monkeypatch,
        owner_resolver=lambda _s, *, novel_id=None, chapter_id=None: ("trial-user", 99),
        trial_limit=1200,
    )

    def fake_create(**kwargs):
        seen_kwargs.update(kwargs)
        provider = SimpleNamespace(name="deepseek", model="deepseek-flash")
        return _ok_response(), provider

    monkeypatch.setattr(llm_failover, "create_chat_completion", fake_create)

    _make_client().chat_completion(
        [{"role": "user", "content": "trial chapter"}],
        usage_chapter_id=701,
        trial_output_tokens=1500,
    )

    assert seen_kwargs["session_id"] == "material:novel:99"
    assert seen_kwargs["thinking_enabled"] is False
    assert seen_kwargs["max_retries"] == 0
    assert seen_kwargs["max_tokens"] == 1200


# ---------------------------------------------------------------- Subflow cache warm-up


class _RecordingFuture:
    def __init__(self, events: list[tuple[str, object]], key: object, value=None, error: Exception | None = None):
        self._events = events
        self._key = key
        self._value = value
        self._error = error

    def result(self):
        if self._error is not None:
            self._events.append(("result_error", self._key))
            raise self._error
        self._events.append(("result", self._key))
        return self._value


class _FakeCheckpoint:
    def __init__(self):
        self.updates: list[tuple] = []

    def get_pending_chapters(self, _stage, all_chapter_ids, capability="summaries"):
        return list(all_chapter_ids)

    def get_completed_chapters(self, _stage, capability="summaries"):
        return []

    def get_failed_chapters(self, _stage, capability="summaries"):
        return []

    def update_checkpoint(self, stage, status=None, data=None):
        self.updates.append((stage, status, data))


def _patch_chapter_flow_basics(monkeypatch: pytest.MonkeyPatch, *, batch: int = 3) -> None:
    monkeypatch.setattr(chapter_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(chapter_mod, "create_performance_monitor", lambda _name: FakeMonitor())
    monkeypatch.setattr(chapter_mod, "create_checkpoint_manager", lambda _nid, job_id=None: _FakeCheckpoint())
    monkeypatch.setattr(chapter_mod, "_sync_stage1_job_progress", lambda *_a, **_k: None)
    monkeypatch.setattr(chapter_mod.settings, "MAX_CONCURRENT_CHAPTERS", batch)
    monkeypatch.setattr(chapter_mod.settings, "ENABLE_CHAPTER_SUMMARIES", True)
    monkeypatch.setattr(chapter_mod.settings, "ENABLE_PLOT_EXTRACTION", True)
    monkeypatch.setattr(chapter_mod.settings, "ENABLE_CHARACTER_EXTRACTION", True)
    monkeypatch.setattr(chapter_mod, "update_chapter_summary_task", FakeTask({}))
    monkeypatch.setattr(chapter_mod, "save_plots_task", FakeTask({"saved_count": 1}))


@pytest.mark.unit
def test_chapter_extraction_flow_warms_up_first_chapter_before_concurrent_fanout(monkeypatch):
    _patch_chapter_flow_basics(monkeypatch, batch=3)

    summary_events: list[tuple[str, object]] = []
    plot_events: list[tuple[str, object]] = []
    mention_events: list[tuple[str, object]] = []

    class _SummaryTask:
        def submit(self, chapter_id: int):
            summary_events.append(("submit", chapter_id))
            return _RecordingFuture(summary_events, chapter_id, {"chapter_id": chapter_id, "summary": "ok"})

    class _PlotTask:
        def submit(self, chapter_id: int):
            plot_events.append(("submit", chapter_id))
            return _RecordingFuture(plot_events, chapter_id, {"chapter_id": chapter_id, "plots": [{"p": 1}]})

    class _MentionTask:
        def submit(self, chapter_id: int):
            mention_events.append(("submit", chapter_id))
            return _RecordingFuture(mention_events, chapter_id, {"chapter_id": chapter_id, "mentions": ["A"]})

    monkeypatch.setattr(chapter_mod, "generate_chapter_summary_task", _SummaryTask())
    monkeypatch.setattr(chapter_mod, "extract_chapter_plots_task", _PlotTask())
    monkeypatch.setattr(chapter_mod, "extract_character_mentions_task", _MentionTask())

    result = chapter_mod.chapter_extraction_flow.fn(novel_id=1, chapter_ids=[10, 20, 30, 40], job_id=1)

    assert result["status"] == "completed"
    assert result["summaries_count"] == 4
    assert result["plots_count"] == 4
    assert result["mentions_extracted"] is True

    expected_order = [
        ("submit", 10),
        ("result", 10),
        ("submit", 20),
        ("submit", 30),
        ("submit", 40),
        ("result", 20),
        ("result", 30),
        ("result", 40),
    ]
    assert summary_events == expected_order
    assert plot_events == expected_order
    assert mention_events == expected_order


@pytest.mark.unit
def test_chapter_extraction_flow_warmup_non_fatal_error_continues_and_fatal_stops(monkeypatch):
    _patch_chapter_flow_basics(monkeypatch, batch=3)

    # 1. Non-fatal error on chapter #1 still proceeds to chapters #2..#3 and records #1 as failed.
    submitted: dict[str, list[int]] = {"summaries": [], "plots": [], "mentions": []}

    class _FirstFailsSummary:
        def submit(self, chapter_id: int):
            submitted["summaries"].append(chapter_id)
            if chapter_id == 10:
                return _RecordingFuture([], chapter_id, error=RuntimeError("transient summary fail"))
            return _RecordingFuture([], chapter_id, {"chapter_id": chapter_id, "summary": "ok"})

    class _FirstFailsPlot:
        def submit(self, chapter_id: int):
            submitted["plots"].append(chapter_id)
            if chapter_id == 10:
                return _RecordingFuture([], chapter_id, error=RuntimeError("transient plot fail"))
            return _RecordingFuture([], chapter_id, {"chapter_id": chapter_id, "plots": []})

    class _FirstFailsMention:
        def submit(self, chapter_id: int):
            submitted["mentions"].append(chapter_id)
            if chapter_id == 10:
                return _RecordingFuture([], chapter_id, error=RuntimeError("transient mention fail"))
            return _RecordingFuture([], chapter_id, {"chapter_id": chapter_id, "mentions": ["A"]})

    monkeypatch.setattr(chapter_mod, "generate_chapter_summary_task", _FirstFailsSummary())
    monkeypatch.setattr(chapter_mod, "extract_chapter_plots_task", _FirstFailsPlot())
    monkeypatch.setattr(chapter_mod, "extract_character_mentions_task", _FirstFailsMention())

    result = chapter_mod.chapter_extraction_flow.fn(novel_id=1, chapter_ids=[10, 20, 30], job_id=1)

    assert submitted == {"summaries": [10, 20, 30], "plots": [10, 20, 30], "mentions": [10, 20, 30]}
    assert result["summaries_count"] == 2
    assert result["failed_chapters"] == [10]
    assert result["failed_mention_chapters"] == [10]
    assert result["status"] == "completed_with_errors"

    # 2. Fatal error on chapter #1 stops immediately before submitting chapters #2..#3.
    fatal_submitted: list[int] = []
    fatal_err = LLMNonRetryableError("account failure", ErrorCode.MATERIAL_LLM_UNAVAILABLE)

    class _FatalFirstSummary:
        def submit(self, chapter_id: int):
            fatal_submitted.append(chapter_id)
            return _RecordingFuture([], chapter_id, error=fatal_err)

    monkeypatch.setattr(chapter_mod, "generate_chapter_summary_task", _FatalFirstSummary())

    with pytest.raises(LLMNonRetryableError) as raised:
        chapter_mod.chapter_extraction_flow.fn(novel_id=1, chapter_ids=[10, 20, 30], job_id=1)

    assert raised.value is fatal_err
    assert fatal_submitted == [10]


@pytest.mark.unit
def test_character_entity_build_flow_warms_up_first_character_and_handles_errors(monkeypatch):
    import flows.database_session as dbs
    import services.material.character_mentions_service as mentions_mod
    import services.material.ingestion_jobs_service as jobs_mod

    monkeypatch.setattr(char_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(char_mod, "create_performance_monitor", lambda _name: FakeMonitor())
    monkeypatch.setattr(char_mod.settings, "MAX_CONCURRENT_CHAPTERS", 3)
    monkeypatch.setattr(dbs, "get_db_session", _fake_session)
    monkeypatch.setattr(jobs_mod, "executing_trial_job", lambda _db, _nid: None)

    mentions = [
        SimpleNamespace(character_name="Alice", importance="major"),
        SimpleNamespace(character_name="Alice", importance="major"),
        SimpleNamespace(character_name="Alice", importance="major"),
        SimpleNamespace(character_name="Alice", importance="major"),
        SimpleNamespace(character_name="Bob", importance="major"),
        SimpleNamespace(character_name="Bob", importance="major"),
        SimpleNamespace(character_name="Bob", importance="major"),
        SimpleNamespace(character_name="Carol", importance="supporting"),
        SimpleNamespace(character_name="Carol", importance="supporting"),
        SimpleNamespace(character_name="Dave", importance="minor"),
    ]

    class _FakeMentionsSvc:
        def get_by_novel(self, _db, _nid):
            return mentions

    monkeypatch.setattr(mentions_mod, "CharacterMentionsService", _FakeMentionsSvc)

    # 1. Warm-up ordering: Alice completes before Bob/Carol/Dave are submitted concurrently.
    events: list[tuple[str, object]] = []

    class _BuildTask:
        def submit(self, *, novel_id: int, character_name: str):
            events.append(("submit", character_name))
            return _RecordingFuture(
                events, character_name, {"status": "created", "character_name": character_name}
            )

    monkeypatch.setattr(char_mod, "build_character_entity_task", _BuildTask())

    result = char_mod.character_entity_build_flow.fn(novel_id=7)

    assert result["created_count"] == 4
    assert result["status"] == "completed"
    assert events == [
        ("submit", "Alice"),
        ("result", "Alice"),
        ("submit", "Bob"),
        ("submit", "Carol"),
        ("submit", "Dave"),
        ("result", "Bob"),
        ("result", "Carol"),
        ("result", "Dave"),
    ]

    # 2. Non-fatal error on Alice still builds Bob/Carol/Dave.
    non_fatal_submitted: list[str] = []

    class _NonFatalFirstBuild:
        def submit(self, *, novel_id: int, character_name: str):
            non_fatal_submitted.append(character_name)
            if character_name == "Alice":
                return _RecordingFuture([], character_name, error=RuntimeError("bad json"))
            return _RecordingFuture([], character_name, {"status": "created", "character_name": character_name})

    monkeypatch.setattr(char_mod, "build_character_entity_task", _NonFatalFirstBuild())

    partial = char_mod.character_entity_build_flow.fn(novel_id=7)
    assert non_fatal_submitted == ["Alice", "Bob", "Carol", "Dave"]
    assert partial["created_count"] == 3
    assert partial["failed_characters"] == ["Alice"]
    assert partial["status"] == "completed_with_errors"

    # 3. Fatal error on Alice aborts before submitting Bob/Carol/Dave.
    fatal_submitted: list[str] = []
    fatal_err = LLMNonRetryableError("trial cap", ErrorCode.MATERIAL_TRIAL_COST_LIMIT)

    class _FatalFirstBuild:
        def submit(self, *, novel_id: int, character_name: str):
            fatal_submitted.append(character_name)
            return _RecordingFuture([], character_name, error=fatal_err)

    monkeypatch.setattr(char_mod, "build_character_entity_task", _FatalFirstBuild())

    with pytest.raises(LLMNonRetryableError) as raised:
        char_mod.character_entity_build_flow.fn(novel_id=7)

    assert raised.value is fatal_err
    assert fatal_submitted == ["Alice"]


@pytest.mark.unit
def test_story_aggregate_flow_warms_up_first_framework_and_handles_errors(monkeypatch):
    monkeypatch.setattr(story_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(story_mod, "create_performance_monitor", lambda _name: FakeMonitor())
    monkeypatch.setattr(story_mod, "create_checkpoint_manager", lambda *_a, **_k: MagicMock())
    monkeypatch.setattr(story_mod.settings, "ENABLE_NOVEL_SYNOPSIS", False)
    monkeypatch.setattr(story_mod.settings, "ENABLE_CHAPTER_SUMMARIES", True)
    monkeypatch.setattr(story_mod.settings, "ENABLE_PLOT_EXTRACTION", True)
    monkeypatch.setattr(story_mod.settings, "ENABLE_STORY_AGGREGATION", True)
    monkeypatch.setattr(story_mod.settings, "ENABLE_STORYLINE_GENERATION", False)
    monkeypatch.setattr(story_mod.settings, "ENABLE_META_EXTRACTION", False)
    monkeypatch.setattr(story_mod.settings, "MAX_CONCURRENT_WORKFLOWS", 3)

    frameworks = [{"title": "F1"}, {"title": "F2"}, {"title": "F3"}, {"title": "F4"}]
    monkeypatch.setattr(
        story_mod,
        "identify_story_frameworks_with_chunking",
        lambda **_k: {"story_frameworks": frameworks, "chunking_info": {"chunk_count": 1}},
    )
    monkeypatch.setattr(
        story_mod,
        "save_stories_with_plots",
        lambda stories, novel_id: {
            "saved_count": len(stories),
            "story_id_map": {s["title"]: idx for idx, s in enumerate(stories, 1)},
        },
    )
    monkeypatch.setattr(
        story_mod,
        "handle_orphan_plots_task",
        lambda **_k: {"assigned_count": 0, "skipped": True, "orphan_ratio": 0.0},
    )

    # 1. Warm-up ordering: F1 finishes before F2/F3/F4 are submitted concurrently.
    events: list[tuple[str, object]] = []

    class _AggTask:
        def submit(self, *, story_framework, novel_id: int):
            title = story_framework["title"]
            events.append(("submit", title))
            return _RecordingFuture(events, title, {"story": {"title": title}})

    monkeypatch.setattr(story_mod, "aggregate_plots_for_story_framework", _AggTask())

    result = story_mod.story_aggregate_flow.fn(novel_id=3, chapter_ids=[1, 2, 3, 4])

    assert result["stories_count"] == 4
    assert result["status"] == "completed"
    assert events == [
        ("submit", "F1"),
        ("result", "F1"),
        ("submit", "F2"),
        ("submit", "F3"),
        ("submit", "F4"),
        ("result", "F2"),
        ("result", "F3"),
        ("result", "F4"),
    ]

    # 2. Non-fatal error on F1 proceeds to F2/F3/F4 and records 1:F1 as failed.
    non_fatal_submitted: list[str] = []

    class _NonFatalFirstAgg:
        def submit(self, *, story_framework, novel_id: int):
            title = story_framework["title"]
            non_fatal_submitted.append(title)
            if title == "F1":
                return _RecordingFuture([], title, error=RuntimeError("bad story json"))
            return _RecordingFuture([], title, {"story": {"title": title}})

    monkeypatch.setattr(story_mod, "aggregate_plots_for_story_framework", _NonFatalFirstAgg())

    partial = story_mod.story_aggregate_flow.fn(novel_id=3, chapter_ids=[1, 2, 3, 4])
    assert non_fatal_submitted == ["F1", "F2", "F3", "F4"]
    assert partial["stories_count"] == 3
    assert partial["failed_stories"] == ["1:F1"]
    assert partial["status"] == "completed_with_errors"

    # 3. Fatal error on F1 stops immediately before submitting F2/F3/F4.
    fatal_submitted: list[str] = []
    fatal_err = LLMNonRetryableError("budget exceeded", ErrorCode.QUOTA_AI_DAILY_COST_EXCEEDED)

    class _FatalFirstAgg:
        def submit(self, *, story_framework, novel_id: int):
            title = story_framework["title"]
            fatal_submitted.append(title)
            return _RecordingFuture([], title, error=fatal_err)

    monkeypatch.setattr(story_mod, "aggregate_plots_for_story_framework", _FatalFirstAgg())

    with pytest.raises(LLMNonRetryableError) as raised:
        story_mod.story_aggregate_flow.fn(novel_id=3, chapter_ids=[1, 2, 3, 4])

    assert raised.value is fatal_err
    assert fatal_submitted == ["F1"]
