"""When the per-session context snapshot is reused, and when it is rebuilt (each rebuild is a cache miss)."""

import json

import pytest

from agent.context import turn_snapshot
from agent.context.turn_snapshot import REBUILD_AFTER_TURNS, plan_turn_context

pytestmark = pytest.mark.unit

CARD = {"key": "【角色信息】:c1", "text": "【角色信息】\n阿昼 (id=c1)\n" + "码头邮差，只在雾天送信。" * 40}
HEADER = {"key": "header", "text": "项目状态 [核心信息]\n雾港来信"}


@pytest.fixture(autouse=True)
def _memory_store(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    turn_snapshot.reset_snapshot_memory()
    yield
    turn_snapshot.reset_snapshot_memory()


def _plan(blocks, *, language="zh", project_id="p1"):
    return plan_turn_context(
        session_id="s1",
        project_id=project_id,
        language=language,
        context_blocks=blocks,
    )


def _lore(n: int, size: int = 1) -> dict:
    return {
        "key": f"【世界设定】:lore{n}",
        "text": f"【世界设定】\n设定{n} (id=lore{n})\n" + "雾港往事。" * size,
        "label": f"设定{n} (id=lore{n})",
    }


@pytest.mark.parametrize(
    ("second_turn", "rebuilt"),
    [
        ({"blocks": [HEADER, CARD, _lore(1), _lore(2)]}, False),
        ({"blocks": [HEADER, CARD, _lore(1), _lore(2, size=400)]}, True),
        ({"blocks": [HEADER, CARD, _lore(1)], "language": "en"}, True),
        ({"blocks": [HEADER, CARD, _lore(1)], "project_id": "p2"}, True),
    ],
)
def test_snapshot_is_reused_until_changes_outgrow_it(second_turn, rebuilt):
    first = _plan([HEADER, CARD, _lore(1)])
    assert first.rebuilt and CARD["text"] in first.snapshot_message

    second = _plan(second_turn["blocks"], **{k: v for k, v in second_turn.items() if k != "blocks"})

    assert second.rebuilt is rebuilt
    if not rebuilt:
        assert second.snapshot_message == first.snapshot_message
        assert "设定2" in second.changed_context and CARD["text"] not in second.changed_context


def test_snapshot_is_rebuilt_after_many_turns():
    blocks = [HEADER, CARD]
    plans = [_plan(blocks) for _ in range(REBUILD_AFTER_TURNS + 1)]
    assert [plan.rebuilt for plan in plans] == [True] + [False] * (REBUILD_AFTER_TURNS - 1) + [True]


def test_a_document_that_left_the_turn_is_flagged_as_unchecked_in_the_turn_context():
    first = _plan([HEADER, CARD, _lore(1), _lore(2)])
    second = _plan([HEADER, CARD, _lore(2), _lore(3)])

    assert not second.rebuilt
    assert second.snapshot_message == first.snapshot_message
    assert _lore(3)["text"] in second.changed_context
    stale_section = second.changed_context.split(turn_snapshot.GONE_CONTEXT_HEADER, 1)[1]
    assert _lore(1)["label"] in stale_section
    assert "设定2" not in stale_section and "阿昼" not in stale_section


def test_a_snapshot_document_moved_to_this_turn_is_not_flagged_as_unchecked():
    old = _lore(1)
    first = _plan([HEADER, CARD, old, _lore(2)])
    focused = {**old, "text": "【世界设定】\n设定1 (id=lore1) ← 当前焦点 [全文]\n新版正文。", "placement": "turn"}

    second = _plan([HEADER, CARD, focused])

    assert not second.rebuilt
    assert second.snapshot_message == first.snapshot_message
    current, unchecked = second.changed_context.split(turn_snapshot.GONE_CONTEXT_HEADER, 1)
    assert focused["text"] in current
    assert old["label"] not in unchecked
    assert _lore(2)["label"] in unchecked


def test_documents_that_left_the_turn_count_toward_the_rebuild_ratio():
    """A large document that is no longer in the turn is dead weight in the snapshot: rebuild it away."""
    _plan([HEADER, CARD, _lore(1, size=400)])
    second = _plan([HEADER, CARD, _lore(2)])

    assert second.rebuilt
    assert "设定1" not in second.snapshot_message


def test_an_unchanged_turn_sends_no_stale_listing():
    blocks = [HEADER, CARD, _lore(1)]
    _plan(blocks)
    assert _plan(blocks).changed_context == ""


def _snapshot_logs(monkeypatch):
    logged: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        turn_snapshot,
        "log_with_context",
        lambda _logger, _level, message, **fields: logged.append((message, fields)),
    )
    return logged


def test_reused_snapshot_log_separates_live_changed_and_gone_chars(monkeypatch):
    """The cost of keeping an old snapshot depends on its dead share, so logs must show it."""
    logged = _snapshot_logs(monkeypatch)
    old_lore = _lore(1, size=2)
    new_lore = _lore(2)
    first = _plan([HEADER, CARD, old_lore])
    logged.clear()

    second = _plan([HEADER, CARD, new_lore])

    assert not second.rebuilt
    message, fields = logged[-1]
    assert message == "Context snapshot reused"
    assert fields["live_chars"] == len(HEADER["text"]) + len(CARD["text"])
    assert fields["changed_chars"] == len(new_lore["text"])
    assert fields["gone_chars"] == len(old_lore["text"])
    assert fields["stale_chars"] == fields["changed_chars"] + fields["gone_chars"]
    assert fields["snapshot_chars"] == len(first.snapshot_message)
    assert fields["changed_chars_by_section"] == {"【世界设定】": len(new_lore["text"])}


def test_outgrown_rebuild_log_describes_the_snapshot_it_replaces(monkeypatch):
    logged = _snapshot_logs(monkeypatch)
    old_lore = _lore(1)
    new_lore = _lore(2, size=400)
    first = _plan([HEADER, CARD, old_lore])
    logged.clear()

    second = _plan([HEADER, CARD, new_lore])

    assert second.rebuilt
    message, fields = logged[-1]
    assert message == "Context snapshot rebuilt"
    assert fields["reason"] == "outgrown"
    assert fields["previous_snapshot_chars"] == len(first.snapshot_message)
    assert fields["live_chars"] == len(HEADER["text"]) + len(CARD["text"])
    assert fields["stale_chars"] == len(new_lore["text"]) + len(old_lore["text"])


def test_first_build_log_has_no_previous_snapshot_to_describe(monkeypatch):
    logged = _snapshot_logs(monkeypatch)

    _plan([HEADER, CARD])

    message, fields = logged[-1]
    assert message == "Context snapshot rebuilt" and fields["reason"] == "missing"
    assert "previous_snapshot_chars" not in fields and "stale_chars" not in fields


def test_stale_accounting_log_never_contains_document_titles(monkeypatch):
    logged = _snapshot_logs(monkeypatch)
    secret_title = {"key": "【世界设定】:秘密书名", "text": "x" * 20}
    _plan([HEADER, CARD])
    logged.clear()

    _plan([HEADER, CARD, secret_title])

    assert "秘密书名" not in repr(logged)


def test_edited_document_counts_its_old_copy_as_dead(monkeypatch):
    logged = _snapshot_logs(monkeypatch)
    edited = {"key": "【角色信息】:c2", "text": "x" * 30}
    _plan([HEADER, CARD, edited])
    logged.clear()

    _plan([HEADER, CARD, {**edited, "text": "y" * 40}])

    _message, fields = logged[-1]
    assert fields["dead_chars"] == 30 and fields["gone_chars"] == 0 and fields["changed_chars"] == 40


def test_previous_snapshot_format_does_not_replay_old_chapter_bodies():
    old_text = "旧快照里的章节全文"
    turn_snapshot._store_set(
        turn_snapshot._snapshot_key("s1"),
        json.dumps({
            "v": 2, "project_id": "p1", "language": "zh", "text": old_text,
            "created_at": turn_snapshot.time.time(), "turns": 1,
        }),
    )

    plan = _plan([HEADER, CARD])
    assert plan.rebuilt and CARD["text"] in plan.snapshot_message
    assert old_text not in plan.snapshot_message and old_text not in plan.changed_context


def test_malformed_stored_state_still_rebuilds_instead_of_failing_the_turn():
    """日志统计不能影响这一轮：存储里的旧格式只该触发重建。"""
    turn_snapshot._store_set(
        turn_snapshot._snapshot_key("s1"),
        json.dumps({"v": -1, "project_id": "p1", "language": "zh", "hashes": ["bad"], "sizes": [1], "text": "old"}),
    )

    assert _plan([HEADER, CARD]).rebuilt
