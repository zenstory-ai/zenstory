"""Task-board completion requires live, typed artifact evidence."""

from __future__ import annotations

import importlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.graph.writing_graph import (
    _auto_finalize_task_board_on_completion,
    _build_completion_task_payload,
    _existing_written_artifacts,
)
from agent.openai_agents.repeat_read_guard import ReadPlan, RepeatReadGuard
from agent.tools.file_ops.project import ProjectOperations
from agent.tools.mcp_tools import ToolContext
from agent.tools.task_progress import (
    ArtifactEvidence,
    artifact_units,
    format_units,
    task_evidence_spec,
    task_unit_spec,
    validate_task_artifact_fields,
)
from models import File

pytestmark = pytest.mark.unit


def _artifact(
    file_id: str,
    title: str,
    file_type: str,
    metadata: dict | None = None,
) -> ArtifactEvidence:
    return ArtifactEvidence(file_id, title, file_type, metadata or {})


def _range_task(status: str = "in_progress", **extra) -> dict:
    return {
        "task": "撰寫第2-5集劇本",
        "status": status,
        "artifact_file_type": "script",
        "unit_spec": {"unit": "集", "numbers": [2, 3, 4, 5]},
        **extra,
    }


def _draft_range_task(status: str = "in_progress", **extra) -> dict:
    return {
        "task": "写第1-3章正文",
        "status": status,
        "artifact_file_type": "draft",
        "unit_spec": {"unit": "章", "numbers": [1, 2, 3]},
        **extra,
    }


# ------------------------------------------------------------ typed payload


def test_partial_typed_range_records_only_units_written_in_this_request():
    payload = _build_completion_task_payload(
        [_range_task()],
        [_artifact("s2", "任意标题", "script", {"episode_number": 2})],
    )

    assert payload == [
        {
            **_range_task(),
            "done_units": [2],
            "progress": "已写：第2集；待写：第3–5集",
            "completed_artifact_refs": ["s2"],
        }
    ]


def test_range_completion_across_requests_requires_live_typed_prior_units():
    task = _range_task(
        done_units=[2, 3],
        progress="已写：第2–3集；待写：第4–5集",
        completed_artifact_refs=["s2", "s3"],
    )
    written = [
        _artifact("s4", "第四集", "script", {"episode_number": 4}),
        _artifact("s5", "第五集", "script", {"episode_number": 5}),
    ]
    live = [
        _artifact(f"s{number}", f"第{number}集", "script", {"episode_number": number})
        for number in range(2, 6)
    ]

    assert _build_completion_task_payload([task], written, live) == [
        {
            "task": task["task"],
            "status": "done",
            "artifact_file_type": "script",
            "unit_spec": task["unit_spec"],
            "completed_artifact_refs": ["s2", "s3", "s4", "s5"],
        }
    ]


def test_recorded_units_are_dropped_when_live_typed_files_disappear():
    task = _range_task(
        done_units=[2, 3],
        progress="已写：第2–3集；待写：第4–5集",
        completed_artifact_refs=["s2", "s3"],
    )
    written = [
        _artifact("s4", "第4集", "script"),
        _artifact("s5", "第5集", "script"),
    ]
    live = [
        _artifact("s2", "第2集", "script"),
        _artifact("s4", "第4集", "script"),
        _artifact("s5", "第5集", "script"),
    ]

    payload = _build_completion_task_payload([task], written, live)

    assert payload == [
        {
            **_range_task(),
            "done_units": [2, 4, 5],
            "progress": "已写：第2、4–5集；待写：第3集",
            "completed_artifact_refs": ["s2", "s4", "s5"],
        }
    ]


def test_recorded_units_without_completed_refs_cannot_count_even_if_live_unit_exists():
    task = _range_task(done_units=[2], progress="伪造进度")
    written = [
        _artifact("s3", "第3集", "script"),
    ]
    live = [
        _artifact("s2", "第2集", "script"),
        _artifact("s3", "第3集", "script"),
    ]

    payload = _build_completion_task_payload([task], written, live)

    assert payload == [
        {
            **_range_task(),
            "done_units": [3],
            "progress": "已写：第3集；待写：第2、4–5集",
            "completed_artifact_refs": ["s3"],
        }
    ]


def test_body_range_title_never_manufactures_multiple_delivered_units():
    task = _draft_range_task()

    assert _build_completion_task_payload(
        [task],
        [_artifact("d-range", "第1-3章正文", "draft")],
    ) is None

    payload = _build_completion_task_payload(
        [task],
        [_artifact("d1", "第1-3章正文", "draft", {"chapter_number": 1})],
    )
    assert payload is not None
    assert payload[0]["done_units"] == [1]
    assert payload[0]["status"] == "in_progress"
    assert payload[0]["completed_artifact_refs"] == ["d1"]


def test_outline_range_may_represent_outline_coverage_but_not_body_delivery():
    outline_task = {
        "task": "写第1-3章大纲",
        "status": "in_progress",
        "artifact_file_type": "outline",
        "unit_spec": {"unit": "章", "numbers": [1, 2, 3]},
    }
    outline = _artifact("o1", "第1-3章大纲", "outline")

    completed = _build_completion_task_payload([outline_task], [outline])[0]
    assert completed["status"] == "done"
    assert completed["completed_artifact_refs"] == ["o1"]
    assert _build_completion_task_payload([_draft_range_task()], [outline]) is None


def test_wrong_type_and_missing_type_fail_closed():
    script = _artifact("s2", "第2集", "script", {"episode_number": 2})
    legacy = {"task": "撰寫第2-5集劇本", "status": "in_progress"}

    assert _build_completion_task_payload([_draft_range_task()], [script]) is None
    assert _build_completion_task_payload([legacy], [script]) is None


def test_written_artifacts_outside_the_expected_unit_spec_do_not_become_evidence():
    assert _build_completion_task_payload(
        [_range_task()],
        [_artifact("s9", "第9集", "script", {"episode_number": 9})],
    ) is None


def test_invalid_structured_evidence_fields_fail_closed():
    malformed = {
        "task": "写第1章",
        "status": "in_progress",
        "artifact_file_type": "draft",
        "unit_spec": {"unit": "章", "numbers": [1, 1]},
    }

    assert _build_completion_task_payload(
        [malformed],
        [_artifact("d1", "第1章", "draft")],
    ) is None
    with pytest.raises(ValueError, match="unit_spec"):
        validate_task_artifact_fields(malformed)


def test_legacy_artifact_label_requires_exact_title_and_explicit_type():
    task = {
        "task": "写总纲初稿",
        "status": "in_progress",
        "artifact": "总纲",
        "artifact_file_type": "outline",
    }

    assert _build_completion_task_payload(
        [task],
        [_artifact("o1", "《总纲》", "outline")],
    ) is None
    assert _build_completion_task_payload(
        [task],
        [_artifact("o2", "总纲（修订）", "outline")],
    ) is None
    assert _build_completion_task_payload(
        [task],
        [_artifact("o3", "总纲", "document")],
    ) is None
    completed = _build_completion_task_payload(
        [task],
        [_artifact("o4", "总纲", "outline")],
    )[0]
    assert completed["status"] == "done"
    assert completed["completed_artifact_refs"] == ["o4"]


def test_exact_refs_require_every_referenced_live_file_of_the_expected_type():
    task = {
        "task": "交付人物卡",
        "status": "in_progress",
        "artifact_file_type": "character",
        "artifact_refs": ["c1", "c2"],
    }

    assert _build_completion_task_payload(
        [task],
        [_artifact("c1", "甲", "character")],
    ) == [{**task, "completed_artifact_refs": ["c1"]}]
    assert _build_completion_task_payload(
        [task],
        [
            _artifact("c1", "甲", "character"),
            _artifact("c2", "乙", "document"),
        ],
    ) == [{**task, "completed_artifact_refs": ["c1"]}]
    completed = _build_completion_task_payload(
        [task],
        [
            _artifact("c1", "甲", "character"),
            _artifact("c2", "乙", "character"),
        ],
    )[0]
    assert completed["status"] == "done"
    assert completed["completed_artifact_refs"] == ["c1", "c2"]


def test_exact_refs_accumulate_across_turns_only_from_expected_live_files():
    task = {
        "task": "交付人物卡",
        "status": "pending",
        "artifact_file_type": "character",
        "artifact_refs": ["c1", "c2"],
    }
    first = _build_completion_task_payload(
        [task],
        [
            _artifact("c1", "甲", "character"),
            _artifact("wrong", "乙", "character"),
            _artifact("c2", "乙", "document"),
        ],
    )
    assert first == [
        {
            **task,
            "completed_artifact_refs": ["c1"],
        }
    ]

    second = _build_completion_task_payload(
        first,
        [_artifact("c2", "乙", "character")],
        [
            _artifact("c1", "甲", "character"),
            _artifact("c2", "乙", "character"),
        ],
    )
    assert second == [
        {
            **task,
            "status": "done",
            "completed_artifact_refs": ["c1", "c2"],
        }
    ]


def test_pending_typed_range_moves_to_in_progress_on_partial_delivery():
    payload = _build_completion_task_payload(
        [_range_task(status="pending")],
        [
            _artifact("s2", "第2集", "script"),
            _artifact("s3", "第3集", "script"),
        ],
    )

    assert payload == [
        {
            **_range_task(status="in_progress"),
            "done_units": [2, 3],
            "progress": "已写：第2–3集；待写：第4–5集",
            "completed_artifact_refs": ["s2", "s3"],
        }
    ]


def test_no_written_artifact_and_plain_tasks_do_not_auto_close():
    assert _build_completion_task_payload([_range_task()], []) is None
    assert _build_completion_task_payload(
        [{"task": "和作者讨论人物关系", "status": "in_progress"}],
        [_artifact("c1", "人物关系", "character")],
    ) is None


def test_completed_history_is_not_rewritten_by_new_request_evidence():
    historical = _range_task(status="done")

    assert _build_completion_task_payload(
        [historical],
        [_artifact("s2", "第2集", "script")],
    ) is None


# ------------------------------------------------------------ DB evidence


def _db_file(
    db_session,
    *,
    file_id: str,
    project_id: str,
    title: str,
    content: str,
    file_type: str,
    deleted: bool = False,
    metadata: dict | None = None,
) -> File:
    row = File(
        id=file_id,
        project_id=project_id,
        title=title,
        content=content,
        file_type=file_type,
        is_deleted=deleted,
    )
    if metadata:
        row.set_metadata(metadata)
    db_session.add(row)
    db_session.commit()
    return row


def test_existing_written_artifacts_excludes_deleted_empty_and_foreign_project_files(db_session):
    own = _db_file(
        db_session,
        file_id="own-live",
        project_id="project-task-board",
        title="任意标题",
        content="正文",
        file_type="script",
        metadata={"episode_number": 2},
    )
    empty = _db_file(
        db_session,
        file_id="own-empty",
        project_id="project-task-board",
        title="第3集",
        content="   ",
        file_type="script",
    )
    deleted = _db_file(
        db_session,
        file_id="own-deleted",
        project_id="project-task-board",
        title="第4集",
        content="正文",
        file_type="script",
        deleted=True,
    )
    foreign = _db_file(
        db_session,
        file_id="foreign-live",
        project_id="other-project",
        title="第5集",
        content="正文",
        file_type="script",
    )
    ToolContext.set_context(
        session=db_session,
        user_id="u",
        project_id="project-task-board",
        session_id="s",
    )
    try:
        evidence = _existing_written_artifacts([own.id, empty.id, deleted.id, foreign.id])
    finally:
        ToolContext.clear_context()

    assert evidence == [
        ArtifactEvidence("own-live", "任意标题", "script", {"episode_number": 2})
    ]


# ------------------------------------------------------------ auto finalize


def _guard_with_write(file_id: str = "s2", title: str = "第2集") -> RepeatReadGuard:
    guard = RepeatReadGuard()
    guard.observe(
        "create_file",
        ReadPlan(arguments=json.dumps({"title": title})),
        json.dumps({"status": "success", "data": {"id": file_id, "title": title}}),
    )
    return guard


@pytest.mark.asyncio
async def test_finalize_without_any_write_does_not_read_or_update_the_board():
    get_tasks = MagicMock()
    update = AsyncMock()
    ToolContext.set_context(session=None, user_id="u", project_id="p", session_id="s")
    try:
        with (
            patch(
                "services.infra.task_board_service.task_board_service.get_tasks",
                get_tasks,
            ),
            patch("agent.graph.writing_graph.update_project", update),
        ):
            assert await _auto_finalize_task_board_on_completion(RepeatReadGuard()) == []
            assert await _auto_finalize_task_board_on_completion(None) == []
    finally:
        ToolContext.clear_context()

    get_tasks.assert_not_called()
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_records_partial_progress_from_typed_written_artifacts():
    update = AsyncMock(return_value={"content": []})
    written = [_artifact("s2", "任意标题", "script", {"episode_number": 2})]
    ToolContext.set_context(session=None, user_id="u", project_id="p", session_id="s")
    try:
        with (
            patch(
                "services.infra.task_board_service.task_board_service.get_tasks",
                return_value=[_range_task()],
            ),
            patch(
                "agent.graph.writing_graph._existing_written_artifacts",
                return_value=written,
            ) as existing,
            patch("agent.graph.writing_graph.update_project", update),
        ):
            events = await _auto_finalize_task_board_on_completion(_guard_with_write())
    finally:
        ToolContext.clear_context()

    existing.assert_called_once_with(["s2"])
    sent = update.await_args.args[0]["tasks"]
    assert sent[0]["done_units"] == [2]
    assert sent[0]["progress"] == "已写：第2集；待写：第3–5集"
    assert sent[0]["completed_artifact_refs"] == ["s2"]
    update.assert_awaited_once_with(
        {"tasks": sent},
        written_artifact_ids=["s2"],
    )
    assert events[0].data["input"]["tasks"] == sent


@pytest.mark.asyncio
async def test_finalize_revalidates_recorded_progress_against_live_typed_artifacts():
    update = AsyncMock(return_value={"content": []})
    task = _range_task(completed_artifact_refs=["s2", "s3"])
    written = [
        _artifact("s4", "第4集", "script"),
        _artifact("s5", "第5集", "script"),
    ]
    live = [
        _artifact("s2", "第2集", "script"),
        _artifact("s4", "第4集", "script"),
        _artifact("s5", "第5集", "script"),
    ]
    ToolContext.set_context(session=None, user_id="u", project_id="p", session_id="s")
    try:
        with (
            patch(
                "services.infra.task_board_service.task_board_service.get_tasks",
                return_value=[task],
            ),
            patch(
                "agent.graph.writing_graph._existing_written_artifacts",
                return_value=written,
            ),
            patch(
                "agent.graph.writing_graph._live_project_artifacts",
                return_value=live,
            ) as live_lookup,
            patch("agent.graph.writing_graph.update_project", update),
        ):
            await _auto_finalize_task_board_on_completion(_guard_with_write("s4", "第4集"))
    finally:
        ToolContext.clear_context()

    live_lookup.assert_called_once_with("p")
    sent = update.await_args.args[0]["tasks"]
    assert sent[0]["status"] == "in_progress"
    assert sent[0]["done_units"] == [2, 4, 5]
    assert sent[0]["completed_artifact_refs"] == ["s2", "s4", "s5"]
    update.assert_awaited_once_with(
        {"tasks": sent},
        written_artifact_ids=["s4"],
    )


# ------------------------------------------------------------ manual done claims


def _mock_task_board(monkeypatch, previous_tasks):
    module = importlib.import_module("services.infra.task_board_service")
    service = MagicMock()
    service.get_tasks.return_value = previous_tasks
    service.save_tasks.return_value = True
    monkeypatch.setattr(module, "task_board_service", service)
    return service


def _ops_with_rows(rows: list[File]) -> ProjectOperations:
    session = MagicMock()
    session.exec.return_value.all.return_value = rows
    return ProjectOperations(session=session, user_id="u")


def _row(
    file_id: str,
    title: str,
    file_type: str,
    *,
    content: str = "正文",
    metadata: dict | None = None,
) -> File:
    row = File(
        id=file_id,
        project_id="p",
        title=title,
        content=content,
        file_type=file_type,
    )
    if metadata:
        row.set_metadata(metadata)
    return row


def test_manual_range_done_without_artifact_type_is_ignored_before_save(monkeypatch):
    board = _mock_task_board(monkeypatch, [])
    ops = _ops_with_rows([_row("s2", "第2集", "script")])
    task = {"task": "撰寫第2-5集劇本", "status": "done"}

    result = ops.execute_update_plan(
        session_id="s",
        tasks=[task],
        user_id="u",
        project_id="p",
    )

    assert result["status"] == "ignored"
    assert result["reason"] == "unverified_done"
    assert result["unverified_done"][0]["task"] == task["task"]
    assert "明确的产物类型" in result["unverified_done"][0]["missing"]
    board.save_tasks.assert_not_called()


def test_manual_partial_typed_range_done_is_ignored_before_save(monkeypatch):
    board = _mock_task_board(monkeypatch, [_range_task()])
    ops = _ops_with_rows(
        [
            _row("s2", "任意标题", "script", metadata={"episode_number": 2}),
            _row("s3", "第3集", "script", content="   "),
        ]
    )

    result = ops.execute_update_plan(
        session_id="s",
        tasks=[_range_task(status="done")],
        user_id="u",
        project_id="p",
    )

    assert result["status"] == "ignored"
    assert result["reason"] == "unverified_done"
    assert result["unverified_done"] == [
        {"task": _range_task()["task"], "missing": "第3–5集"}
    ]
    board.save_tasks.assert_not_called()


def test_manual_typed_range_done_saves_only_with_all_live_units(monkeypatch):
    board = _mock_task_board(monkeypatch, [_range_task()])
    ops = _ops_with_rows(
        [
            _row(f"s{number}", "任意标题", "script", metadata={"episode_number": number})
            for number in range(2, 6)
        ]
    )

    result = ops.execute_update_plan(
        session_id="s",
        tasks=[_range_task(status="done")],
        user_id="u",
        project_id="p",
    )

    assert result["status"] == "success"
    board.save_tasks.assert_called_once()


def test_manual_ref_done_requires_the_exact_live_expected_type(monkeypatch):
    task = {
        "task": "交付人物卡",
        "status": "done",
        "artifact_file_type": "character",
        "artifact_refs": ["c1"],
    }
    board = _mock_task_board(monkeypatch, [])
    ops = _ops_with_rows([_row("c1", "人物卡", "document")])

    result = ops.execute_update_plan(
        session_id="s", tasks=[task], user_id="u", project_id="p"
    )

    assert result["status"] == "ignored"
    assert result["reason"] == "unverified_done"
    board.save_tasks.assert_not_called()


def test_manual_typed_done_without_refs_units_or_label_is_ignored_before_save(monkeypatch):
    task = {
        "task": "交付正文",
        "status": "done",
        "artifact_file_type": "draft",
    }
    board = _mock_task_board(monkeypatch, [])
    ops = _ops_with_rows([_row("d1", "正文", "draft")])

    result = ops.execute_update_plan(
        session_id="s", tasks=[task], user_id="u", project_id="p"
    )

    assert result["status"] == "ignored"
    assert result["reason"] == "unverified_done"
    assert result["unverified_done"] == [
        {"task": "交付正文", "missing": "明确的产物引用或编号"}
    ]
    board.save_tasks.assert_not_called()


def test_plain_conversational_manual_done_remains_supported(monkeypatch):
    board = _mock_task_board(monkeypatch, [])
    ops = _ops_with_rows([])
    task = {
        "task": "和作者确认主题",
        "status": "done",
        "task_kind": "conversation",
    }

    result = ops.execute_update_plan(
        session_id="s", tasks=[task], user_id="u", project_id="p"
    )

    assert result["status"] == "success"
    ops.session.exec.assert_not_called()
    board.save_tasks.assert_called_once()


def test_invalid_evidence_fields_are_rejected_before_save(monkeypatch):
    board = _mock_task_board(monkeypatch, [])
    ops = _ops_with_rows([])
    task = {
        "task": "写正文",
        "status": "done",
        "artifact_file_type": "chapter",
    }

    result = ops.execute_update_plan(
        session_id="s", tasks=[task], user_id="u", project_id="p"
    )

    assert result["status"] == "ignored"
    assert result["reason"] == "validation_rejected"
    ops.session.exec.assert_not_called()
    board.save_tasks.assert_not_called()


def test_previously_done_history_is_not_revalidated_or_rewritten(monkeypatch):
    historical = {"task": "旧版已完成记录", "status": "done"}
    board = _mock_task_board(monkeypatch, [historical])
    ops = _ops_with_rows([])

    result = ops.execute_update_plan(
        session_id="s",
        tasks=[dict(historical)],
        user_id="u",
        project_id="p",
    )

    assert result["status"] == "success"
    ops.session.exec.assert_not_called()
    board.save_tasks.assert_called_once()


# ------------------------------------------------------------ progress resaves


class _InMemoryBoard:
    def __init__(self, tasks: list[dict]):
        self.tasks = [dict(task) for task in tasks]
        self.save_calls = 0

    def get_tasks(self, *_args, **_kwargs):
        return [dict(task) for task in self.tasks]

    def save_tasks(self, _session_id, tasks, **_kwargs):
        self.save_calls += 1
        self.tasks = [dict(task) for task in tasks]
        return True


def _use_board(monkeypatch, board: _InMemoryBoard) -> None:
    module = importlib.import_module("services.infra.task_board_service")
    monkeypatch.setattr(module, "task_board_service", board)


def test_range_progress_survives_resave_and_is_revalidated_live(monkeypatch):
    recorded = _range_task(
        done_units=[2],
        progress="已写：第2集；待写：第3–5集",
        completed_artifact_refs=["s2"],
    )
    board = _InMemoryBoard([recorded])
    _use_board(monkeypatch, board)
    ops = _ops_with_rows([_row("s2", "第2集", "script")])
    model_plan = [{"task": recorded["task"], "status": "in_progress"}]

    result = ops.execute_update_plan(
        session_id="s", tasks=model_plan, user_id="u", project_id="p"
    )

    assert result["status"] == "success"
    assert board.tasks == [recorded]

    written = [
        _artifact("s3", "第3集", "script"),
        _artifact("s4", "第4集", "script"),
        _artifact("s5", "第5集", "script"),
    ]
    live = [
        _artifact(f"s{number}", f"第{number}集", "script")
        for number in range(2, 6)
    ]
    assert _build_completion_task_payload(board.get_tasks(), written, live)[0]["status"] == "done"


def test_resave_with_changed_evidence_contract_does_not_inherit_progress(monkeypatch):
    recorded = _range_task(done_units=[2], progress="已写：第2集；待写：第3–5集")
    board = _InMemoryBoard([recorded])
    _use_board(monkeypatch, board)
    changed = {
        "task": recorded["task"],
        "status": "in_progress",
        "artifact_file_type": "draft",
        "unit_spec": {"unit": "章", "numbers": [2, 3, 4, 5]},
    }

    result = _ops_with_rows([]).execute_update_plan(
        session_id="s", tasks=[changed], user_id="u", project_id="p"
    )

    assert result["status"] == "success"
    assert board.tasks == [changed]


def test_resave_that_copies_rendered_progress_keeps_typed_contract(monkeypatch):
    recorded = _range_task(
        done_units=[2],
        progress="已写：第2集；待写：第3–5集",
        completed_artifact_refs=["s2"],
    )
    board = _InMemoryBoard([recorded])
    _use_board(monkeypatch, board)

    _ops_with_rows([_row("s2", "第2集", "script")]).execute_update_plan(
        session_id="s",
        tasks=[
            {
                "task": "撰寫第2-5集劇本（已写：第2集；待写：第3–5集）",
                "status": "in_progress",
            }
        ],
        user_id="u",
        project_id="p",
    )

    assert board.tasks == [recorded]


# ------------------------------------------------------------ helper and rendering


def test_artifact_unit_precedence_is_metadata_then_leading_title_only():
    artifacts = [
        _artifact("d12", "第1章 第一场雪", "draft", {"chapter_number": 12}),
        _artifact("d2", "第2章 第一节课", "draft"),
        _artifact("d-range", "第3-5章正文", "draft"),
        _artifact("o-range", "第3-5章大纲", "outline"),
    ]

    assert artifact_units(artifacts, "章", "draft") == {2, 12}
    assert artifact_units(artifacts, "章", "outline") == {3, 4, 5}


def test_task_evidence_spec_requires_type_for_legacy_text_but_accepts_structured_units():
    assert task_evidence_spec({"task": "写第1-3章", "status": "pending"}) is None
    typed = {
        "task": "写第1-3章",
        "status": "pending",
        "artifact_file_type": "draft",
    }
    assert task_evidence_spec(typed) == task_unit_spec(typed["task"])
    structured = {
        "task": "正文交付",
        "status": "pending",
        "artifact_file_type": "draft",
        "unit_spec": {"unit": "章", "numbers": [1, 3]},
    }
    spec = task_evidence_spec(structured)
    assert spec is not None and spec.numbers == frozenset({1, 3})


def test_format_units():
    assert format_units({2, 3, 4, 6, 8, 9}, "集") == "第2–4、6、8–9集"


def test_board_prompt_shows_recorded_progress(monkeypatch):
    from agent.core.message_manager import MessageManager

    _mock_task_board(
        monkeypatch,
        [
            _range_task(progress="已写：第2集；待写：第3–5集"),
            {"task": "收尾", "status": "pending"},
        ],
    )
    manager = MessageManager.__new__(MessageManager)
    manager.user_id = "u"
    manager.project_id = "p"

    text = "\n".join(manager._build_task_board_section("s", force_en=False))

    assert "🔄 撰寫第2-5集劇本（已写：第2集；待写：第3–5集）" in text
    assert "⏳ 收尾" in text
