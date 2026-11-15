from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest

from tests.test_flows.conftest import FakeFuture

se_mod = importlib.import_module("flows.pipelines.stages.stage_executor")
story_mod = importlib.import_module("flows.pipelines.subflows.story_aggregate_flow")
relationship_mod = importlib.import_module("flows.pipelines.subflows.relationship_flow")


class _SubmitTask:
    def __init__(self, result):
        self.calls = []
        self.result = result

    def submit(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return FakeFuture(self.result.copy())


def test_task_wrappers_forward_job_id_to_subflows(monkeypatch):
    story_calls = []
    relationship_calls = []
    monkeypatch.setattr(
        story_mod,
        "story_aggregate_flow",
        lambda **kwargs: story_calls.append(kwargs) or {"status": "completed"},
    )
    monkeypatch.setattr(
        relationship_mod,
        "relationship_flow",
        lambda **kwargs: relationship_calls.append(kwargs) or {"status": "completed"},
    )

    assert se_mod._task_run_story_aggregate.fn(1, [2], 33)["status"] == "completed"
    assert se_mod._task_run_relationship.fn(1, [2], 33)["status"] == "completed"

    expected = {"novel_id": 1, "chapter_ids": [2], "job_id": 33}
    assert story_calls == [expected]
    assert relationship_calls == [expected]


@pytest.mark.parametrize("job_id", [33, None])
def test_stage_executor_forwards_current_job_id_to_stage2_tasks(monkeypatch, job_id):
    monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
    story_task = _SubmitTask({"status": "completed"})
    relationship_task = _SubmitTask({"status": "completed"})
    monkeypatch.setattr(se_mod, "_task_run_story_aggregate", story_task)
    monkeypatch.setattr(se_mod, "_task_run_relationship", relationship_task)

    executor = se_mod.StageExecutor(
        novel_id=1,
        chapter_ids=[2],
        checkpoint_manager=MagicMock(),
        correlation_id="corr",
        job_id=job_id,
    )
    executor.stages = MagicMock(characters=False, relationships=True, story_flow_needed=True)

    executor._execute_parallel_stages(stage2a_done=False, stage2c_done=True)
    executor._execute_relationship_stage(stage2b_done=False)

    expected_kwargs = {} if job_id is None else {"job_id": job_id}
    assert story_task.calls == [((1, [2]), expected_kwargs)]
    assert relationship_task.calls == [((1, [2]), expected_kwargs)]
