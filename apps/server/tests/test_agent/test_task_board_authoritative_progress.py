"""Only backend-verified files may create or retain task-board progress."""

from __future__ import annotations

import importlib

import pytest

from agent.tools.file_ops.project import ProjectOperations
from models import File

pytestmark = pytest.mark.unit


class _InMemoryBoard:
    def __init__(self, tasks: list[dict] | None = None):
        self.tasks = [dict(task) for task in tasks or []]
        self.save_calls = 0

    def get_tasks(self, *_args, **_kwargs):
        return [dict(task) for task in self.tasks]

    def save_tasks(self, _session_id, tasks, **_kwargs):
        self.save_calls += 1
        self.tasks = [dict(task) for task in tasks]
        return True


def _use_board(monkeypatch, tasks: list[dict] | None = None) -> _InMemoryBoard:
    board = _InMemoryBoard(tasks)
    module = importlib.import_module("services.infra.task_board_service")
    monkeypatch.setattr(module, "task_board_service", board)
    return board


def _file_task(
    *,
    status: str = "in_progress",
    phase_id: str = "draft-opening",
    numbers: list[int] | None = None,
    **extra,
) -> dict:
    return {
        "phase_id": phase_id,
        "task": "写完开篇正文",
        "status": status,
        "task_kind": "file",
        "artifact_file_type": "draft",
        "unit_spec": {"unit": "章", "numbers": numbers or [1, 2]},
        **extra,
    }


def _add_file(
    db_session,
    *,
    file_id: str,
    title: str,
    file_type: str = "draft",
    content: str = "正文",
    deleted: bool = False,
    chapter_number: int | None = None,
) -> File:
    row = File(
        id=file_id,
        project_id="p",
        title=title,
        file_type=file_type,
        content=content,
        is_deleted=deleted,
    )
    if chapter_number is not None:
        row.set_metadata({"chapter_number": chapter_number})
    db_session.add(row)
    db_session.commit()
    return row


def _assert_no_authoritative_progress(task: dict) -> None:
    assert "done_units" not in task
    assert "progress" not in task
    assert "completed_artifact_refs" not in task


def test_model_cannot_forge_full_progress_without_any_file(monkeypatch, db_session):
    board = _use_board(monkeypatch)
    forged = _file_task(
        numbers=list(range(1, 11)),
        done_units=list(range(1, 11)),
        progress="已完成 100%",
        completed_artifact_refs=["invented-file"],
    )

    result = ProjectOperations(session=db_session, user_id="u").execute_update_plan(
        session_id="s",
        tasks=[forged],
        user_id="u",
        project_id="p",
    )

    assert result["status"] == "success"
    assert board.save_calls == 1
    _assert_no_authoritative_progress(board.tasks[0])
    assert board.tasks[0]["status"] == "in_progress"


@pytest.mark.parametrize("invalid_state", ["empty", "deleted", "wrong_type"])
def test_normal_resave_drops_progress_when_verified_ref_is_no_longer_live(
    monkeypatch,
    db_session,
    invalid_state: str,
):
    previous = _file_task(
        numbers=[1],
        done_units=[1],
        progress="已写：第1章；待写：",
        completed_artifact_refs=["d1"],
    )
    board = _use_board(monkeypatch, [previous])
    _add_file(
        db_session,
        file_id="d1",
        title="第1章",
        file_type="document" if invalid_state == "wrong_type" else "draft",
        content="   " if invalid_state == "empty" else "正文",
        deleted=invalid_state == "deleted",
        chapter_number=1,
    )

    result = ProjectOperations(session=db_session, user_id="u").execute_update_plan(
        session_id="s",
        tasks=[_file_task(numbers=[1])],
        user_id="u",
        project_id="p",
    )

    assert result["status"] == "success"
    assert board.save_calls == 1
    _assert_no_authoritative_progress(board.tasks[0])
    assert board.tasks[0]["status"] == "in_progress"


def test_new_untyped_write_done_is_rejected_but_explicit_conversation_done_is_allowed(
    monkeypatch,
    db_session,
):
    board = _use_board(monkeypatch)
    operations = ProjectOperations(session=db_session, user_id="u")

    for name in ("写完开篇正文", "和作者确认开篇方向"):
        rejected = operations.execute_update_plan(
            session_id="s",
            tasks=[{"task": name, "status": "done"}],
            user_id="u",
            project_id="p",
        )

        assert rejected["status"] == "ignored"
        assert rejected["reason"] == "unverified_done"
    assert board.save_calls == 0

    allowed = operations.execute_update_plan(
        session_id="s",
        tasks=[
            {
                "task": "和作者确认开篇方向",
                "status": "done",
                "task_kind": "conversation",
            }
        ],
        user_id="u",
        project_id="p",
    )

    assert allowed["status"] == "success"
    assert board.save_calls == 1
    assert board.tasks[0]["task_kind"] == "conversation"


def test_same_task_name_in_a_different_phase_does_not_inherit_progress(
    monkeypatch,
    db_session,
):
    previous = _file_task(
        phase_id="phase-a",
        numbers=[1],
        done_units=[1],
        progress="已写：第1章；待写：",
        completed_artifact_refs=["d1"],
    )
    board = _use_board(monkeypatch, [previous])
    _add_file(
        db_session,
        file_id="d1",
        title="第1章",
        chapter_number=1,
    )

    result = ProjectOperations(session=db_session, user_id="u").execute_update_plan(
        session_id="s",
        tasks=[_file_task(phase_id="phase-b", numbers=[1])],
        user_id="u",
        project_id="p",
    )

    assert result["status"] == "success"
    _assert_no_authoritative_progress(board.tasks[0])
    assert board.tasks[0]["phase_id"] == "phase-b"


def test_same_task_name_done_in_another_phase_does_not_bypass_new_evidence(
    monkeypatch,
    db_session,
):
    board = _use_board(
        monkeypatch,
        [_file_task(status="done", phase_id="phase-a", numbers=[1])],
    )

    result = ProjectOperations(session=db_session, user_id="u").execute_update_plan(
        session_id="s",
        tasks=[_file_task(status="done", phase_id="phase-b", numbers=[1])],
        user_id="u",
        project_id="p",
    )

    assert result["status"] == "ignored"
    assert result["reason"] == "unverified_done"
    assert board.save_calls == 0


def test_hidden_model_progress_is_ignored_but_trusted_writes_accumulate_across_turns(
    monkeypatch,
    db_session,
):
    board = _use_board(monkeypatch)
    operations = ProjectOperations(session=db_session, user_id="u")
    _add_file(
        db_session,
        file_id="d1",
        title="第1章",
        chapter_number=1,
    )
    _add_file(
        db_session,
        file_id="d2",
        title="第2章",
        chapter_number=2,
    )

    first = operations.execute_update_plan(
        session_id="s",
        tasks=[
            _file_task(
                done_units=[2],
                progress="伪造进度",
                completed_artifact_refs=["d2"],
            )
        ],
        user_id="u",
        project_id="p",
        written_artifact_ids=["d1"],
    )

    assert first["status"] == "success"
    assert board.tasks[0]["done_units"] == [1]
    assert board.tasks[0]["completed_artifact_refs"] == ["d1"]
    assert "第1章" in board.tasks[0]["progress"]

    resaved = operations.execute_update_plan(
        session_id="s",
        tasks=[_file_task()],
        user_id="u",
        project_id="p",
    )
    assert resaved["status"] == "success"
    assert board.tasks[0]["done_units"] == [1]
    assert board.tasks[0]["completed_artifact_refs"] == ["d1"]

    second = operations.execute_update_plan(
        session_id="s",
        tasks=[_file_task()],
        user_id="u",
        project_id="p",
        written_artifact_ids=["d2"],
    )

    assert second["status"] == "success"
    assert board.tasks[0]["status"] == "done"
    assert board.tasks[0]["completed_artifact_refs"] == ["d1", "d2"]
    assert "done_units" not in board.tasks[0]
    assert "progress" not in board.tasks[0]


def test_existing_file_task_cannot_be_reclassified_as_conversation_to_bypass_evidence(monkeypatch, db_session):
    board = _use_board(monkeypatch, [_file_task()])
    result = ProjectOperations(session=db_session, user_id="u").execute_update_plan(
        session_id="s",
        tasks=[{"phase_id": "draft-opening", "task": "写完开篇正文", "status": "done", "task_kind": "conversation"}],
        user_id="u", project_id="p",
    )
    assert result["status"] == "ignored"
    assert board.save_calls == 0


def test_legacy_file_task_cannot_be_reclassified_by_adding_a_phase(monkeypatch, db_session):
    previous = _file_task()
    previous.pop("phase_id")
    board = _use_board(monkeypatch, [previous])
    result = ProjectOperations(session=db_session, user_id="u").execute_update_plan(
        session_id="s",
        tasks=[{"phase_id": "new-phase", "task": previous["task"], "status": "done", "task_kind": "conversation"}],
        user_id="u", project_id="p",
    )
    assert result["status"] == "ignored"
    assert board.save_calls == 0


def test_task_identity_does_not_depend_on_composite_string_delimiters():
    first = {"phase_id": "a|task:b", "task": "c"}
    second = {"phase_id": "a", "task": "b|task:c"}
    assert ProjectOperations._task_identity(first) != ProjectOperations._task_identity(second)
