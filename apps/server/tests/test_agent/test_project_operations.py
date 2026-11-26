"""Tests for project operation task-plan state-machine rules."""

import importlib
from unittest.mock import MagicMock

import pytest

from agent.tools.file_ops.project import ProjectOperations
from agent.tools.tool_schemas import UPDATE_PROJECT_TOOL


def _mock_task_board(monkeypatch, previous_tasks=None, save_ok=True):
    """Patch task_board_service used by ProjectOperations.execute_update_plan."""
    task_board_module = importlib.import_module("services.infra.task_board_service")

    service = MagicMock()
    service.get_tasks.return_value = previous_tasks
    service.save_tasks.return_value = save_ok
    monkeypatch.setattr(task_board_module, "task_board_service", service)
    return service


def test_update_project_tool_schema_supports_phase_fields():
    """update_project.tasks schema should expose lightweight phase metadata fields."""
    task_props = (
        UPDATE_PROJECT_TOOL["input_schema"]["properties"]["tasks"]["items"]["properties"]
    )

    assert "phase_id" in task_props
    assert "artifact" in task_props
    assert "done_when" in task_props


@pytest.mark.unit
def test_execute_update_plan_supports_legacy_task_format(monkeypatch):
    """Legacy task/status-only payloads should remain valid."""
    service = _mock_task_board(monkeypatch, previous_tasks=[])
    ops = ProjectOperations(session=MagicMock(), user_id="user-1")
    tasks = [
        {"task": "收集资料", "status": "in_progress"},
        {"task": "输出初稿", "status": "pending"},
    ]

    result = ops.execute_update_plan(session_id="sess-1", tasks=tasks)

    assert result["status"] == "success"
    assert result["tasks"] == tasks
    assert result["task_summary"] == {
        "total": 2,
        "pending": 1,
        "in_progress": 1,
        "done": 0,
    }
    service.save_tasks.assert_called_once_with(
        "sess-1",
        tasks,
        user_id=None,
        project_id=None,
    )


@pytest.mark.unit
def test_execute_update_plan_normalizes_task_alias_fields(monkeypatch):
    """Task payloads using title/name aliases should be coerced into the canonical `task` field."""
    service = _mock_task_board(monkeypatch, previous_tasks=[])
    ops = ProjectOperations(session=MagicMock(), user_id="user-1")
    tasks = [
        {"title": "收集资料", "status": "in_progress"},
        {"name": "输出初稿", "status": "pending"},
    ]

    result = ops.execute_update_plan(session_id="sess-alias", tasks=tasks)

    assert result["status"] == "success"
    assert result["task_summary"] == {
        "total": 2,
        "pending": 1,
        "in_progress": 1,
        "done": 0,
    }

    service.save_tasks.assert_called_once()
    saved_tasks = service.save_tasks.call_args.args[1]
    assert saved_tasks[0]["task"] == "收集资料"
    assert saved_tasks[1]["task"] == "输出初稿"


@pytest.mark.unit
def test_execute_update_plan_normalizes_multiple_in_progress(monkeypatch):
    """Extra in_progress tasks are auto-normalized to pending with a concise warning."""
    service = _mock_task_board(monkeypatch, previous_tasks=[])
    ops = ProjectOperations(session=MagicMock(), user_id="user-1")
    tasks = [
        {"task": "任务1", "status": "in_progress"},
        {"task": "任务2", "status": "in_progress"},
    ]

    result = ops.execute_update_plan(session_id="sess-2", tasks=tasks)

    assert result["status"] == "success"
    assert "2 个任务为 in_progress" in result["warning"]
    assert "pending" in result["warning"]
    assert result["task_summary"] == {
        "total": 2,
        "pending": 1,
        "in_progress": 1,
        "done": 0,
    }
    assert result["tasks"] == [
        {"task": "任务1", "status": "in_progress"},
        {"task": "任务2", "status": "pending"},
    ]
    service.save_tasks.assert_called_once_with(
        "sess-2",
        [
            {"task": "任务1", "status": "in_progress"},
            {"task": "任务2", "status": "pending"},
        ],
        user_id=None,
        project_id=None,
    )


@pytest.mark.unit
def test_execute_update_plan_rejects_done_to_in_progress_rollback(monkeypatch):
    """A previously done task cannot rollback to in_progress."""
    previous_tasks = [
        {"task": "章节大纲", "status": "done", "phase_id": "phase-outline"},
    ]
    service = _mock_task_board(monkeypatch, previous_tasks=previous_tasks)
    ops = ProjectOperations(session=MagicMock(), user_id="user-1")
    tasks = [
        {
            "task": "章节大纲",
            "status": "in_progress",
            "phase_id": "phase-outline",
            "artifact": "outline.md",
            "done_when": "通过审阅",
        }
    ]

    result = ops.execute_update_plan(session_id="sess-3", tasks=tasks)

    assert result["status"] == "ignored"
    assert result["reason"] == "validation_rejected"
    assert "done -> in_progress rollback is not allowed" in result["error"]
    service.save_tasks.assert_not_called()


@pytest.mark.unit
def test_execute_update_plan_rejects_done_to_in_progress_rollback_even_when_second_in_progress(monkeypatch):
    """Submitting a done -> in_progress task as a 2nd in_progress entry must NOT bypass rollback rejection."""
    previous_tasks = [
        {"task": "任务1", "status": "pending", "phase_id": "phase-1"},
        {"task": "章节大纲", "status": "done", "phase_id": "phase-outline"},
    ]
    service = _mock_task_board(monkeypatch, previous_tasks=previous_tasks)
    ops = ProjectOperations(session=MagicMock(), user_id="user-1")
    tasks = [
        {"task": "任务1", "status": "in_progress", "phase_id": "phase-1"},
        {"task": "章节大纲", "status": "in_progress", "phase_id": "phase-outline"},
    ]

    result = ops.execute_update_plan(session_id="sess-3b", tasks=tasks)

    assert result["status"] == "ignored"
    assert result["reason"] == "validation_rejected"
    assert "done -> in_progress rollback is not allowed" in result["error"]
    assert "phase-outline" in result["error"]
    service.save_tasks.assert_not_called()


@pytest.mark.unit
def test_execute_update_plan_allows_same_task_name_across_different_phases(monkeypatch):
    """Same task name in another phase should not be treated as rollback."""
    previous_tasks = [
        {"task": "章节大纲", "status": "done", "phase_id": "phase-outline"},
    ]
    service = _mock_task_board(monkeypatch, previous_tasks=previous_tasks)
    ops = ProjectOperations(session=MagicMock(), user_id="user-1")
    tasks = [
        {
            "task": "章节大纲",
            "status": "in_progress",
            "phase_id": "phase-draft",
        }
    ]

    result = ops.execute_update_plan(session_id="sess-4", tasks=tasks)

    assert result["status"] == "success"
    service.save_tasks.assert_called_once_with(
        "sess-4",
        tasks,
        user_id=None,
        project_id=None,
    )


@pytest.mark.unit
def test_execute_update_plan_allows_phase_id_reuse_across_different_tasks(monkeypatch):
    """Reusing phase_id for a different task should not be treated as rollback."""
    previous_tasks = [
        {"task": "生成大纲", "status": "done", "phase_id": "second"},
    ]
    service = _mock_task_board(monkeypatch, previous_tasks=previous_tasks)
    ops = ProjectOperations(session=MagicMock(), user_id="user-1")
    tasks = [
        {
            "task": "润色文本",
            "status": "in_progress",
            "phase_id": "second",
        }
    ]

    result = ops.execute_update_plan(session_id="sess-6", tasks=tasks)

    assert result["status"] == "success"
    service.save_tasks.assert_called_once_with(
        "sess-6",
        tasks,
        user_id=None,
        project_id=None,
    )


@pytest.mark.unit
def test_execute_update_plan_still_blocks_legacy_task_name_rollback(monkeypatch):
    """Legacy tasks without phase_id should still rollback-check by task name."""
    previous_tasks = [
        {"task": "章节大纲", "status": "done"},
    ]
    service = _mock_task_board(monkeypatch, previous_tasks=previous_tasks)
    ops = ProjectOperations(session=MagicMock(), user_id="user-1")
    tasks = [
        {
            "task": "章节大纲",
            "status": "in_progress",
        }
    ]

    result = ops.execute_update_plan(session_id="sess-5", tasks=tasks)

    assert result["status"] == "ignored"
    assert result["reason"] == "validation_rejected"
    assert "done -> in_progress rollback is not allowed" in result["error"]
    service.save_tasks.assert_not_called()


@pytest.mark.unit
def test_execute_update_plan_rejects_legacy_rollback_even_when_second_in_progress(monkeypatch):
    """Legacy (no phase_id) done -> in_progress rollback as a 2nd in_progress task is also blocked."""
    previous_tasks = [
        {"task": "任务1", "status": "pending"},
        {"task": "章节大纲", "status": "done"},
    ]
    service = _mock_task_board(monkeypatch, previous_tasks=previous_tasks)
    ops = ProjectOperations(session=MagicMock(), user_id="user-1")
    tasks = [
        {"task": "任务1", "status": "in_progress"},
        {"task": "章节大纲", "status": "in_progress"},
    ]

    result = ops.execute_update_plan(session_id="sess-5b", tasks=tasks)

    assert result["status"] == "ignored"
    assert result["reason"] == "validation_rejected"
    assert "done -> in_progress rollback is not allowed" in result["error"]
    assert "章节大纲" in result["error"]
    service.save_tasks.assert_not_called()


@pytest.mark.unit
def test_execute_update_plan_returns_error_when_save_fails(monkeypatch):
    """Redis/infra failure should not raise; update_plan is best-effort metadata."""
    service = _mock_task_board(monkeypatch, previous_tasks=[], save_ok=False)
    ops = ProjectOperations(session=MagicMock(), user_id="user-1")
    tasks = [
        {"task": "任务1", "status": "in_progress"},
    ]

    result = ops.execute_update_plan(session_id="sess-save-fail", tasks=tasks)

    assert result["status"] == "error"
    assert result["reason"] == "internal_error"
    assert "Failed to save tasks to Redis" in result["error"]
    service.save_tasks.assert_called_once()


def test_update_project_schema_explains_single_active_task():
    tasks_schema = UPDATE_PROJECT_TOOL["input_schema"]["properties"]["tasks"]
    for description in (
        UPDATE_PROJECT_TOOL["description"],
        tasks_schema["description"],
        tasks_schema["items"]["properties"]["status"]["description"],
    ):
        assert "最多一个" in description
        assert "in_progress" in description
        assert "pending" in description
