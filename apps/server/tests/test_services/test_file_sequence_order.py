import pytest

from services.file_tree_rules import resolve_new_file_order


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def one(self):
        return self.value


class _OrderSession:
    def __init__(self, max_order=None):
        self.max_order = max_order
        self.queries = 0

    def exec(self, _statement):
        self.queries += 1
        return _ScalarResult(self.max_order)


@pytest.mark.unit
@pytest.mark.parametrize("requested_order", [-7, 12, 580])
def test_nonzero_requested_order_wins_all_inferred_sequences(requested_order):
    session = _OrderSession(max_order=100)

    resolved = resolve_new_file_order(
        session,
        "project-1",
        "folder-1",
        title="第58章 第一节课",
        metadata={"chapter_number": 99},
        file_type="draft",
        requested_order=requested_order,
    )

    assert resolved == requested_order
    assert session.queries == 0


@pytest.mark.unit
def test_zero_order_uses_compatible_metadata_before_leading_title():
    session = _OrderSession(max_order=100)

    resolved = resolve_new_file_order(
        session,
        "project-1",
        "folder-1",
        title="第58章 第一节课",
        metadata={"chapter_number": 99, "episode_number": 77},
        file_type="draft",
        requested_order=0,
    )

    assert resolved == 99
    assert session.queries == 0


@pytest.mark.unit
def test_unset_order_uses_ordinary_append_when_no_sequence_exists():
    session = _OrderSession(max_order=8)

    resolved = resolve_new_file_order(
        session,
        "project-1",
        "folder-1",
        title="序章",
        metadata={"episode_number": 77},
        file_type="draft",
        requested_order=0,
    )

    assert resolved == 9
    assert session.queries == 1
