"""Authority/state must not be inferred from presentation prose."""


from agent.core.task_board_completion import _build_completion_task_payload
from agent.tools.task_progress import ArtifactEvidence


def test_legacy_range_title_cannot_prove_body_delivery():
    task = {"task": "写第1-10章正文", "status": "in_progress"}

    assert _build_completion_task_payload([task], ["第1-10章大纲"]) is None


def test_wrong_typed_artifact_cannot_complete_a_body_range():
    task = {
        "task": "写第1-10章正文",
        "status": "in_progress",
        "artifact_file_type": "draft",
        "unit_spec": {"unit": "章", "numbers": list(range(1, 11))},
    }
    outline = ArtifactEvidence(id="outline-1", title="第1-10章大纲", file_type="outline", metadata={})

    assert _build_completion_task_payload([task], [outline]) is None
