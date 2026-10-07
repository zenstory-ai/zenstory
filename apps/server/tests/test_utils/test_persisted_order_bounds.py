"""Signed INTEGER bounds apply to persistence, not display sequence inference."""

import pytest

from utils.title_sequence import (
    MAX_FILE_ORDER,
    MIN_FILE_ORDER,
    build_sequence_sort_key,
    resolve_persisted_sequence_order,
    validate_persisted_file_order,
)


@pytest.mark.parametrize("value", [MIN_FILE_ORDER, -7, 0, 17, MAX_FILE_ORDER])
def test_storage_validator_preserves_valid_orders(value):
    assert validate_persisted_file_order(value) == value
    assert resolve_persisted_sequence_order(value, title="Plain", file_type="draft") == value


@pytest.mark.parametrize("value", [MIN_FILE_ORDER - 1, MAX_FILE_ORDER + 1])
def test_storage_validator_rejects_out_of_range_final_orders(value):
    with pytest.raises(ValueError):
        validate_persisted_file_order(value)
    with pytest.raises(ValueError):
        resolve_persisted_sequence_order(value, title="Plain", file_type="draft")


@pytest.mark.parametrize("metadata", [None, {"chapter_number": MAX_FILE_ORDER + 1}])
def test_final_title_override_can_replace_out_of_range_provisional_order(metadata):
    assert resolve_persisted_sequence_order(
        MAX_FILE_ORDER + 1, title=f"Chapter {MAX_FILE_ORDER}", metadata=metadata, file_type="draft",
    ) == MAX_FILE_ORDER


@pytest.mark.parametrize("source", ["title", "metadata"])
def test_oversized_inference_is_displayable_but_not_persistable(source):
    title = f"Chapter {MAX_FILE_ORDER + 1}" if source == "title" else "Plain"
    metadata = {"chapter_number": MAX_FILE_ORDER + 1} if source == "metadata" else None
    assert build_sequence_sort_key(0, title=title, metadata=metadata, file_type="draft")[0] == MAX_FILE_ORDER + 1
    with pytest.raises(ValueError):
        resolve_persisted_sequence_order(0, title=title, metadata=metadata, file_type="draft")
