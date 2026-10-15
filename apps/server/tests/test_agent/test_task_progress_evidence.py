import pytest

from agent.tools.task_progress import (
    ArtifactEvidence,
    artifact_units,
    task_artifact_type,
    task_evidence_spec,
    validate_task_artifact_fields,
)


@pytest.mark.unit
def test_typed_outline_range_only_proves_outline_units():
    outline = ArtifactEvidence(
        id="outline-1",
        title="第1-10章大纲",
        file_type="outline",
    )

    assert artifact_units([outline], "章", "outline") == set(range(1, 11))
    assert artifact_units([outline], "章", "draft") == set()
    character = ArtifactEvidence(
        id="character-1",
        title="第1章人物",
        file_type="character",
    )
    assert artifact_units([character], "章", "character") == set()


@pytest.mark.unit
def test_body_range_title_never_claims_multiple_delivered_units():
    draft = ArtifactEvidence(
        id="draft-1",
        title="第1-10章正文",
        file_type="draft",
    )

    assert artifact_units([draft], "章", "draft") == set()

    explicit_range = ArtifactEvidence(
        id="draft-explicit-range",
        title="第1章-第10章正文",
        file_type="draft",
    )
    assert artifact_units([explicit_range], "章", "draft") == set()

    english_range = ArtifactEvidence(
        id="draft-2",
        title="Chapter 1-10",
        file_type="draft",
    )
    assert artifact_units([english_range], "章", "draft") == set()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("title", "unit", "number"),
    [("Chapter 12", "章", 12), ("Episode 7", "集", 7), ("Ep. 9", "集", 9)],
)
def test_body_artifact_supports_leading_english_unit_headings(title, unit, number):
    artifact = ArtifactEvidence(id="body-1", title=title, file_type="script")
    assert artifact_units([artifact], unit, "script") == {number}


@pytest.mark.unit
def test_body_artifact_uses_one_compatible_metadata_unit_before_title():
    draft = ArtifactEvidence(
        id="draft-12",
        title="第2章 第一场雪",
        file_type="draft",
        metadata={"chapter_number": 12, "episode_number": 99},
    )

    assert artifact_units([draft], "章", "draft") == {12}
    assert artifact_units([draft], "集", "draft") == set()


@pytest.mark.unit
def test_task_evidence_requires_typed_artifact_for_legacy_range_text():
    untyped = {"task": "写第1-10章正文"}
    typed = {"task": "写第1-10章正文", "artifact_file_type": "draft"}

    assert task_artifact_type(untyped) is None
    assert task_evidence_spec(untyped) is None
    assert task_evidence_spec(typed).numbers == frozenset(range(1, 11))


@pytest.mark.unit
def test_structured_task_evidence_is_validated_fail_closed():
    task = {
        "artifact_file_type": "script",
        "artifact_refs": ["file-1", "file-2"],
        "unit_spec": {"unit": "集", "numbers": [1, 3, 2]},
    }

    validate_task_artifact_fields(task)
    assert task_artifact_type(task) == "script"
    assert task_evidence_spec(task).numbers == frozenset({1, 2, 3})

    validate_task_artifact_fields({"artifact_refs": []})


@pytest.mark.unit
@pytest.mark.parametrize(
    "task",
    [
        {"artifact_file_type": "folder"},
        {"artifact_refs": "file-1"},
        {"artifact_refs": [""]},
        {"unit_spec": {"unit": "章", "numbers": [True]}},
        {"unit_spec": {"unit": "章", "numbers": ["1"]}},
        {"unit_spec": {"unit": "章", "numbers": [1, 1]}},
        {"unit_spec": {"unit": "场", "numbers": [1]}},
        {"unit_spec": {"unit": "章集", "numbers": [1]}},
        {"unit_spec": {"unit": "章", "numbers": [1, 2002]}},
    ],
)
def test_malformed_task_artifact_fields_are_rejected(task):
    with pytest.raises(ValueError):
        validate_task_artifact_fields(task)
