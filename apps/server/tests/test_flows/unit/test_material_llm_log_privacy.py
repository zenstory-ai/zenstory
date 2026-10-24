from __future__ import annotations

import asyncio
import json
import pickle
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from prefect.states import exception_to_failed_state

from flows.atomic_tasks.entities import character_tasks_v2 as character_mod
from flows.atomic_tasks.parsing import plot_tasks as plot_mod
from flows.atomic_tasks.summaries import chapter_summary_tasks as chapter_mod
from flows.atomic_tasks.summaries import novel_synopsis_tasks as synopsis_mod
from flows.utils.clients import llm as llm_mod
from flows.utils.decorators import prefect as prefect_decorators
from flows.utils.helpers.exceptions import LLMOutputError, ValidationError

SENTINEL = "PRIVATE_MATERIAL_SENTINEL_7f3a"
UNKNOWN_FINISH_REASON = "PRIVATE_FINISH_REASON_SENTINEL_91c2"


def _new_client() -> llm_mod.MaterialLLMClient:
    return llm_mod.MaterialLLMClient.__new__(llm_mod.MaterialLLMClient)


def _response(content: str) -> llm_mod.LLMResponse:
    return llm_mod.LLMResponse(
        content=content,
        usage={},
        model="test-model",
        finish_reason=UNKNOWN_FINISH_REASON,
    )


@contextmanager
def _fake_session():
    yield MagicMock()


def _patch_common(monkeypatch: pytest.MonkeyPatch, module, logger: MagicMock, content: str) -> None:
    monkeypatch.setattr(prefect_decorators, "get_run_logger", lambda: logger)
    monkeypatch.setattr(module, "get_run_logger", lambda: logger)
    monkeypatch.setattr(module, "get_db_session", _fake_session)
    monkeypatch.setattr(module, "call_material_llm", lambda **_kwargs: _response(content))
    monkeypatch.setattr(module, "get_material_llm_client", _new_client)


def _invoke_material_task(
    monkeypatch: pytest.MonkeyPatch,
    task_name: str,
    logger: MagicMock,
    content: str,
) -> None:
    if task_name == "chapter":
        _patch_common(monkeypatch, chapter_mod, logger, content)
        monkeypatch.setattr(chapter_mod, "create_chapter_summary_prompt", lambda: "prompt")
        monkeypatch.setattr(
            chapter_mod.ChaptersService,
            "get_chapter_core_fields",
            lambda *_args: {"content": "chapter", "title": "title", "number": 1},
        )
        chapter_mod.generate_chapter_summary_task.fn(11)
        return

    if task_name == "synopsis":
        from services.material.novels_service import NovelsService

        _patch_common(monkeypatch, synopsis_mod, logger, content)
        monkeypatch.setattr(synopsis_mod, "create_novel_synopsis_prompt", lambda: "prompt")
        monkeypatch.setattr(
            NovelsService,
            "get_by_id",
            lambda *_args: SimpleNamespace(title="novel", author="author"),
        )
        synopsis_mod.generate_novel_synopsis_task.fn(
            12,
            [{"chapter_number": 1, "chapter_title": "title", "summary": "summary"}],
        )
        return

    if task_name == "plot":
        _patch_common(monkeypatch, plot_mod, logger, content)
        monkeypatch.setattr(plot_mod, "create_plot_extraction_prompt", lambda **_kwargs: "prompt")
        monkeypatch.setattr(
            plot_mod.ChaptersService,
            "get_by_id",
            lambda *_args: SimpleNamespace(
                novel_id=1,
                original_content="chapter",
                title="title",
            ),
        )
        plot_mod.extract_chapter_plots_task.fn(13)
        return

    if task_name == "character":
        from services.material.chapters_service import ChaptersService

        _patch_common(monkeypatch, character_mod, logger, content)
        monkeypatch.setattr(character_mod, "create_character_mention_prompt", lambda: "prompt")
        monkeypatch.setattr(
            ChaptersService,
            "get_by_id",
            lambda *_args: SimpleNamespace(
                novel_id=1,
                content="chapter",
                original_content="chapter",
                chapter_number=1,
            ),
        )
        character_mod.extract_character_mentions_task.fn(14)
        return

    raise AssertionError(f"unknown task: {task_name}")


@pytest.mark.unit
def test_unparsable_output_exception_and_prefect_state_are_metadata_only():
    client = _new_client()
    response = _response(f"model prose {SENTINEL}")

    with pytest.raises(LLMOutputError) as raised:
        client.extract_json_from_response(response)

    error_text = str(raised.value)
    assert SENTINEL not in error_text
    assert UNKNOWN_FINISH_REASON not in error_text
    assert "finish_reason=unknown" in error_text
    assert f"content_length={len(response.content.strip())}" in error_text

    state = asyncio.run(exception_to_failed_state(raised.value))
    assert SENTINEL not in state.message
    assert UNKNOWN_FINISH_REASON not in state.message
    assert "finish_reason=unknown" in state.message

    restored = pickle.loads(pickle.dumps(raised.value))
    assert isinstance(restored, LLMOutputError)
    assert str(restored) == error_text


@pytest.mark.unit
@pytest.mark.parametrize("task_name", ["chapter", "synopsis", "plot", "character"])
def test_unparsable_material_task_logs_do_not_include_response_content(
    monkeypatch: pytest.MonkeyPatch,
    task_name: str,
):
    logger = MagicMock()

    with pytest.raises(LLMOutputError):
        _invoke_material_task(
            monkeypatch,
            task_name,
            logger,
            f"model prose {SENTINEL}",
        )

    captured_logs = repr(logger.method_calls)
    assert SENTINEL not in captured_logs
    assert UNKNOWN_FINISH_REASON not in captured_logs
    assert "error_type=LLMOutputError" in captured_logs
    assert "finish_reason=unknown" in captured_logs


@pytest.mark.unit
@pytest.mark.parametrize(
    ("task_name", "data"),
    [
        ("chapter", {"summary": SENTINEL}),
        ("synopsis", {"synopsis": SENTINEL}),
        (
            "plot",
            {
                "plots": [
                    {
                        "sequence": 1,
                        "plot_type": "ACTION",
                        "description": "description",
                        "characters": [SENTINEL, 2],
                    }
                ]
            },
        ),
    ],
)
def test_material_validation_failure_logs_do_not_include_parsed_data(
    monkeypatch: pytest.MonkeyPatch,
    task_name: str,
    data: dict,
):
    logger = MagicMock()

    with pytest.raises(ValidationError):
        _invoke_material_task(monkeypatch, task_name, logger, json.dumps(data))

    captured_logs = repr(logger.method_calls)
    assert SENTINEL not in captured_logs
    assert "验证失败" in captured_logs
    assert "error_type=ValidationError" in captured_logs
