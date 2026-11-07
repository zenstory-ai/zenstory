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


def _plan(blocks, *, language="zh", project_id="p1", working_set=None, tail=""):
    return plan_turn_context(
        session_id="s1",
        project_id=project_id,
        language=language,
        context_blocks=blocks,
        working_set_blocks=working_set or [],
        working_set_tail=tail,
    )


def _chapter(n: int, size: int = 1) -> dict:
    return {"key": f"【正文详情】:ch{n}", "text": f"【正文详情】\n第{n}章 (id=ch{n})\n" + "雨夜。" * size}


@pytest.mark.parametrize(
    ("second_turn", "rebuilt"),
    [
        ({"blocks": [HEADER, CARD, _chapter(1), _chapter(2)]}, False),  # a short new chapter
        ({"blocks": [HEADER, CARD, _chapter(1), _chapter(2, size=400)]}, True),  # changes outgrow it
        ({"blocks": [HEADER, CARD, _chapter(1)], "language": "en"}, True),
        ({"blocks": [HEADER, CARD, _chapter(1)], "project_id": "p2"}, True),
    ],
)
def test_snapshot_is_reused_until_changes_outgrow_it(second_turn, rebuilt):
    first = _plan([HEADER, CARD, _chapter(1)])
    assert first.rebuilt and CARD["text"] in first.snapshot_message

    second = _plan(second_turn["blocks"], **{k: v for k, v in second_turn.items() if k != "blocks"})

    assert second.rebuilt is rebuilt
    if not rebuilt:
        assert second.snapshot_message == first.snapshot_message
        assert "第2章" in second.changed_context and CARD["text"] not in second.changed_context


def test_snapshot_is_rebuilt_after_many_turns():
    blocks = [HEADER, CARD]
    plans = [_plan(blocks) for _ in range(REBUILD_AFTER_TURNS + 1)]
    assert [plan.rebuilt for plan in plans] == [True] + [False] * (REBUILD_AFTER_TURNS - 1) + [True]


def test_working_set_bodies_live_in_the_snapshot_and_the_listing_is_sent_each_turn():
    working_set = [{"key": "file:ch1", "text": "### 《第一章》 (id=ch1) [全文，3 字]\n雨夜。", "listing": "- 《第一章》 (id=ch1)（上一轮已修改，3 字）"}]
    first = _plan([HEADER], working_set=working_set, tail="未附全文：- 《第九章》")
    second = _plan([HEADER], working_set=working_set, tail="")

    assert "雨夜。" in first.snapshot_message
    for plan in (first, second):
        assert "上一轮已修改" in plan.working_set_context and "雨夜。" not in plan.working_set_context
    assert "第九章" in first.working_set_context
    assert second.changed_context == ""  # the body did not change: nothing re-sent


def _focus_chapter(n: int, *, focus: bool, size: int = 1) -> dict:
    mark = " ← 当前焦点" if focus else ""
    return {
        "key": f"【正文详情】:ch{n}",
        "text": f"【正文详情】\n第{n}章 (id=ch{n}){mark} [全文]\n" + "雨夜。" * size,
        "label": f"第{n}章 (id=ch{n})",
    }


def test_a_document_that_left_the_turn_is_flagged_as_unchecked_in_the_turn_context():
    """turn1 {A(focus), B}, turn2 {B, C(focus)}: the snapshot still carries A's old full text and focus
    mark, so this turn must say A was not checked (or rebuild); C's new focus is the only current one."""
    first = _plan([HEADER, CARD, _focus_chapter(1, focus=True), _focus_chapter(2, focus=False)])
    second = _plan([HEADER, CARD, _focus_chapter(2, focus=False), _focus_chapter(3, focus=True)])

    assert first.rebuilt
    if second.rebuilt:
        assert "第1章" not in second.snapshot_message
        assert second.snapshot_message.count("← 当前焦点") == 1
        return
    assert second.snapshot_message == first.snapshot_message  # the cached prefix is untouched
    assert "第3章 (id=ch3) ← 当前焦点" in second.changed_context
    stale_section = second.changed_context.split(turn_snapshot.GONE_CONTEXT_HEADER, 1)[1]
    assert "第1章 (id=ch1)" in stale_section
    assert "第2章" not in stale_section and "阿昼" not in stale_section


def test_documents_that_left_the_turn_count_toward_the_rebuild_ratio():
    """A large document that is no longer in the turn is dead weight in the snapshot: rebuild it away."""
    _plan([HEADER, CARD, _focus_chapter(1, focus=True, size=400)])
    second = _plan([HEADER, CARD, _focus_chapter(2, focus=True)])

    assert second.rebuilt
    assert "第1章" not in second.snapshot_message


def test_an_unchanged_turn_sends_no_stale_listing():
    blocks = [HEADER, CARD, _focus_chapter(1, focus=True)]
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
    old_chapter = _chapter(1, size=2)
    new_chapter = _chapter(2, size=1)
    first = _plan([HEADER, CARD, old_chapter])
    logged.clear()

    second = _plan([HEADER, CARD, new_chapter])

    assert not second.rebuilt
    message, fields = logged[-1]
    assert message == "Context snapshot reused"
    assert fields["live_chars"] == len(HEADER["text"]) + len(CARD["text"])
    assert fields["changed_chars"] == len(new_chapter["text"])
    assert fields["gone_chars"] == len(old_chapter["text"])
    assert fields["stale_chars"] == fields["changed_chars"] + fields["gone_chars"]
    assert fields["snapshot_chars"] == len(first.snapshot_message)
    assert fields["changed_chars_by_section"] == {new_chapter["key"].split(":", 1)[0]: len(new_chapter["text"])}


def test_outgrown_rebuild_log_describes_the_snapshot_it_replaces(monkeypatch):
    logged = _snapshot_logs(monkeypatch)
    old_chapter = _chapter(1)
    new_chapter = _chapter(2, size=400)
    first = _plan([HEADER, CARD, old_chapter])
    logged.clear()

    second = _plan([HEADER, CARD, new_chapter])

    assert second.rebuilt
    message, fields = logged[-1]
    assert message == "Context snapshot rebuilt"
    assert fields["reason"] == "outgrown"
    assert fields["previous_snapshot_chars"] == len(first.snapshot_message)
    assert fields["live_chars"] == len(HEADER["text"]) + len(CARD["text"])
    assert fields["stale_chars"] == len(new_chapter["text"]) + len(old_chapter["text"])


def test_first_build_log_has_no_previous_snapshot_to_describe(monkeypatch):
    logged = _snapshot_logs(monkeypatch)

    _plan([HEADER, CARD])

    message, fields = logged[-1]
    assert message == "Context snapshot rebuilt" and fields["reason"] == "missing"
    assert "previous_snapshot_chars" not in fields and "stale_chars" not in fields


def test_stale_accounting_log_never_contains_document_titles(monkeypatch):
    logged = _snapshot_logs(monkeypatch)
    secret_title = {"key": "【正文详情】:秘密书名", "text": "x" * 20}
    _plan([HEADER, CARD])
    logged.clear()

    _plan([HEADER, CARD, secret_title])

    assert "秘密书名" not in repr(logged)


def test_edited_document_counts_its_old_copy_as_dead(monkeypatch):
    logged = _snapshot_logs(monkeypatch)
    edited = {"key": "file:f1", "text": "x" * 30}
    _plan([HEADER, CARD, edited])
    logged.clear()

    _plan([HEADER, CARD, {"key": "file:f1", "text": "y" * 40}])

    _message, fields = logged[-1]
    assert fields["dead_chars"] == 30 and fields["gone_chars"] == 0 and fields["changed_chars"] == 40


def test_malformed_stored_state_still_rebuilds_instead_of_failing_the_turn():
    """日志统计不能影响这一轮：存储里的旧格式只该触发重建。"""
    turn_snapshot._store_set(
        turn_snapshot._snapshot_key("s1"),
        json.dumps({"v": -1, "project_id": "p1", "language": "zh", "hashes": ["bad"], "sizes": [1], "text": "old"}),
    )

    assert _plan([HEADER, CARD]).rebuilt
