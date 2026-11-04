"""Regression tests for approximate text matching safety.

Covers:
- The work-budget guard that stops find_approximate_match from spinning the
  CPU (and blocking the event loop) on large chapters.
- The ambiguity guard that refuses to auto-apply a merely-similar window when a
  different, distant passage scores nearly as high.
- Preservation of the legitimate word-error match on reasonably-sized content.
"""

import random
import time

from agent.tools.file_ops.text_matching import (
    MAX_APPROX_CONTENT_LEN,
    find_approximate_match,
)


def test_word_error_match_still_found():
    """A near-duplicate (minor drift) should still match on small content."""
    content = "他缓缓转过身，看着窗外的大雨，心中一片茫然。"
    pattern = "他缓缓转过身看着窗外的大雨心中一片茫然"  # punctuation/word drift
    match = find_approximate_match(content, pattern, max_error_rate=0.25, min_pattern_len=8)
    assert match is not None
    start, end, score, _matched = match
    assert score >= 0.75
    assert 0 <= start < end <= len(content)


def test_ambiguous_match_is_rejected():
    """Two similar, distant passages must be treated as ambiguous (return None)."""
    content = "她缓缓转过身，看着门外的细雨。\n\n" + ("x" * 40) + "\n\n他缓缓转过身，看着窗外的大雨。"
    pattern = "缓缓转过身看着外的雨天气不错"
    assert find_approximate_match(content, pattern, max_error_rate=0.25, min_pattern_len=8) is None


def test_large_content_is_searched_quickly_instead_of_skipped():
    """Content over the full-scan limit uses the seeded scan: no skip, still fast."""
    big = "这是一个测试段落，用于验证性能守卫是否生效。" * 500
    assert len(big) > MAX_APPROX_CONTENT_LEN
    start = time.time()
    stats: dict[str, int] = {}
    result = find_approximate_match(
        big,
        "一个并不存在于文中的很长很长的锚点句子写在这里现在结束",
        max_error_rate=0.25,
        min_pattern_len=8,
        stats=stats,
    )
    elapsed = time.time() - start
    assert result is None
    assert stats["workload_skipped"] == 0
    assert stats["deadline_exceeded"] == 0
    assert elapsed < 1.0


def test_long_anchor_with_a_slip_in_a_long_chapter_is_found_fast():
    rng = random.Random(3)
    who, act, what = ["林舟", "她", "苏晚", "老人", "少年"], ["看着", "走向", "想起", "握紧", "推开"], ["那扇门", "窗外的雪", "手里的剑", "桌上的信", "灯火"]
    paragraphs = [
        "\u3000\u3000" + "".join(rng.choice(who) + rng.choice(act) + rng.choice(what) + "，没有说话。" for _ in range(6))
        for _ in range(400)
    ]
    content = "\n\n".join(paragraphs)
    start_index = content.index("\n\n", 12000) + 2
    passage = content[start_index:start_index + 400]
    slipped = passage[:200] + ("的" if passage[200] != "的" else "地") + passage[201:]
    stats: dict[str, int] = {}
    began = time.time()
    result = find_approximate_match(content, slipped, keep_line_breaks=True, stats=stats)
    assert time.time() - began < 1.0
    assert result is not None
    assert abs(result[0] - start_index) <= 2 and result[2] >= 0.95
    assert stats["deadline_exceeded"] == 0


def test_long_chapter_refuses_two_equally_close_passages():
    filler = "".join(chr(0x4E00 + (i * 37) % 5000) for i in range(12000))
    passage = "".join(chr(0x5E00 + (i * 11) % 900) for i in range(300))
    content = filler[:4000] + passage + filler[4000:9000] + passage + filler[9000:]
    slipped = passage[:150] + "某" + passage[151:]
    assert find_approximate_match(content, slipped) is None


def test_short_pattern_is_ignored():
    """Patterns below min_pattern_len are not matched approximately."""
    assert find_approximate_match("some content here", "abc", min_pattern_len=8) is None


def test_completed_no_match_is_distinct_from_guard_or_deadline():
    stats: dict[str, int] = {}

    result = find_approximate_match(
        "甲乙丙丁戊己庚辛壬癸",
        "完全不同而且没有命中的片段",
        max_error_rate=0.25,
        min_pattern_len=8,
        stats=stats,
    )

    assert result is None
    assert stats["workload_skipped"] == 0
    assert stats["deadline_exceeded"] == 0


def test_deadline_before_next_window_size_reports_incomplete_scan(monkeypatch):
    clock = iter((100.0, float("inf")))
    monkeypatch.setattr(
        "agent.tools.file_ops.text_matching.time.monotonic",
        lambda: next(clock),
    )
    stats: dict[str, int] = {}

    result = find_approximate_match(
        "甲乙丙丁戊己庚辛壬癸",
        "甲乙丙丁戊己庚辛",
        max_error_rate=0.25,
        min_pattern_len=8,
        stats=stats,
    )

    assert result is None
    assert stats["deadline_exceeded"] == 1


def test_near_tie_through_quick_ratio_gate_is_rejected():
    """A near-tie runner-up must not be hidden by the perf gate.

    The earlier passage becomes `best` first; the runner-up's optimistic
    quick_ratio bound is below best, but it must still be scored and counted so
    the ambiguity guard sees the small gap and rejects (no silent wrong match).
    """
    pattern = "the quick brown fox jumps over"
    a = "the quick brown fox jumps over"       # identical -> 1.0
    b = "the quick brown fox jumped over!!"    # near tie
    content = a + "\n\n" + ("z" * 50) + "\n\n" + b
    assert find_approximate_match(content, pattern, max_error_rate=0.30, min_pattern_len=8) is None


def test_shifted_window_does_not_inflate_runner_up():
    """A unique passage with no distant competitor still matches (order-independent).

    A shifted window of the same true match must not be recorded as a runner-up
    and cause a false rejection.
    """
    content = ("q" * 80) + "\n\n" + "the quick brown fox jumps over the fence" + "\n\n" + ("w" * 80)
    result = find_approximate_match(
        content, "the quick brown fox jumps over the fence", max_error_rate=0.25, min_pattern_len=8
    )
    assert result is not None
    assert result[2] >= 0.9
