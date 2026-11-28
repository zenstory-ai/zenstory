"""Unit tests for the official MiniMax-AI/minimax-code UnitRun / ReasoningLoopGuard port."""

from __future__ import annotations

import pytest

from agent.openai_agents.reasoning_loop_guard import (
    LOOP_DISTINCT,
    LOOP_WINDOW,
    RUN_ON_UNITS,
    UNIT_KEPT,
    UNIT_MOST,
    ReasoningLoopGuard,
    UnitRun,
    has_word_or_emoji,
    run_on_words,
    unit_key,
)


@pytest.mark.unit
def test_has_word_or_emoji_and_unit_key_rules():
    assert has_word_or_emoji("Now") is True
    assert has_word_or_emoji("好") is True
    assert has_word_or_emoji("Chapter 1") is True
    assert has_word_or_emoji("foo_bar") is True
    assert has_word_or_emoji("🔥") is True
    assert has_word_or_emoji("123 🔥 456") is True

    # Pure braces, numbers, hex hashes/UUIDs, and rules must be ignored.
    for ignored in ("", "   ", "{}", "[]", "12345", "3.14159", "deadbeef1234", "a1b2c3d4", "---", "===", "———", "| --- |"):
        assert has_word_or_emoji(ignored) is False
        assert unit_key(ignored) == ""

    assert unit_key("  Now...  ") == "now"
    assert unit_key("Let me check!?? ") == "let me check"
    assert unit_key("再检查一下。！？，；：… ") == "再检查一下"
    long_unit = "a" * 400
    assert len(unit_key(long_unit)) == UNIT_KEPT


@pytest.mark.unit
def test_short_phrase_cycling_trips_at_loop_window():
    guard = UnitRun()
    # 100 repetitions (300? Wait: 33 * 3 = 99 < 256) must NOT trip.
    for _ in range(33):
        assert guard.feed("Now. Let me check. OK. ") is None
    assert guard.trip is None

    # Continue cycling until hitting 256 units total (99 + 157 = 256).
    trip = None
    for _ in range(60):
        trip = guard.feed("Now. Let me check. OK. ")
        if trip is not None:
            break
    assert trip is not None
    assert trip.run_on is False
    assert trip.window == LOOP_WINDOW
    assert set(trip.units) == {"now", "let me check", "ok"}
    assert len(trip.units) <= LOOP_DISTINCT


@pytest.mark.unit
def test_cjk_sentence_cycling_trips_without_spaces():
    guard = ReasoningLoopGuard()
    # 2 units per pair -> 100 pairs = 200 units (< 256), no trip yet.
    assert guard.feed("好。再检查一下。" * 100) is None
    # Another 28 pairs -> reaches 256 units with 2 distinct keys -> trips.
    trip = guard.feed("好。再检查一下。" * 30)
    assert trip is not None
    assert trip.run_on is False
    assert trip.window == LOOP_WINDOW
    assert set(trip.units) == {"好", "再检查一下"}


@pytest.mark.unit
def test_unbroken_run_on_words_emojis_and_symbols_trip_after_three_units():
    import random

    # 1a) Exact short-period unbroken words without sentence stops (3 * 4096 chars).
    word_guard = UnitRun()
    chunk = ("check wait ok " * 400)[:UNIT_MOST]
    assert run_on_words(chunk) == ["check wait ok check wait ok check wait ok"]
    assert word_guard.feed(chunk) is None
    assert word_guard.feed(chunk) is None
    trip = word_guard.feed(chunk)
    assert trip is not None
    assert trip.run_on is True
    assert trip.window == RUN_ON_UNITS * UNIT_MOST
    assert trip.units == ["check wait ok check wait ok check wait ok"]

    # 1b) Non-periodic unbroken run-on words (<= RUN_ON_DISTINCT distinct tokens) exercises token counting.
    rng = random.Random(42)
    raw_varied = " ".join(rng.choice(["check", "wait", "ok"]) for _ in range(1200))
    # Cut cleanly on a word boundary and pad with trailing spaces to exact UNIT_MOST length.
    cut = raw_varied.rfind(" ", 0, UNIT_MOST)
    varied_words = raw_varied[:cut].ljust(UNIT_MOST)
    varied_tokens = run_on_words(varied_words)
    assert varied_tokens is not None
    assert set(varied_tokens) == {"check", "wait", "ok"}
    varied_guard = UnitRun()
    assert varied_guard.feed(varied_words * 2) is None
    varied_trip = varied_guard.feed(varied_words)
    assert varied_trip is not None
    assert varied_trip.run_on is True
    assert varied_trip.window == RUN_ON_UNITS * UNIT_MOST
    assert set(varied_trip.units) == {"check", "wait", "ok"}

    # 2) Unbroken repeated emojis (3 * 4096 chars).
    emoji_guard = UnitRun()
    emoji_chunk = "🔥🚀" * (UNIT_MOST // 2)
    assert run_on_words(emoji_chunk) is not None
    assert emoji_guard.feed(emoji_chunk * 2) is None
    emoji_trip = emoji_guard.feed(emoji_chunk)
    assert emoji_trip is not None
    assert emoji_trip.run_on is True
    assert emoji_trip.window == RUN_ON_UNITS * UNIT_MOST

    # 3) Unbroken short periodic symbols (no letters/digits, e.g. "——" * 2048 * 3).
    sym_guard = UnitRun()
    sym_chunk = "—" * UNIT_MOST
    assert run_on_words(sym_chunk) == ["———"]
    assert sym_guard.feed(sym_chunk * 2) is None
    sym_trip = sym_guard.feed(sym_chunk)
    assert sym_trip is not None
    assert sym_trip.run_on is True
    assert sym_trip.window == RUN_ON_UNITS * UNIT_MOST
    assert sym_trip.units == ["———"]

    # 4) Short normal sentence between run-on chunks resets run_on counter.
    reset_guard = UnitRun()
    assert reset_guard.feed(chunk * 2) is None
    assert reset_guard.feed("Short break.\n") is None
    assert reset_guard.feed(chunk * 2) is None
    assert reset_guard.trip is None


@pytest.mark.unit
def test_non_false_positive_diverse_reasoning_lists_decimals_hashes_json_tables_and_sub_window_repeats():
    # 1) 320 varied reasoning sentences (checking Chapter 1..320) never trip.
    guard = UnitRun()
    for i in range(1, 321):
        assert (
            guard.feed(
                f"Checking Chapter {i}: verify character motivation and scene pacing for Chapter {i}. "
            )
            is None
        )
    assert guard.trip is None

    # 2) Numbered lists ("1. ") and decimals ("3.14") do not split prematurely on "."
    list_guard = UnitRun()
    for i in range(1, 300):
        # Without lettered guard, "1." would split into a tiny unit; here each line stays distinct!
        assert list_guard.feed(f"{i}. Item number {i} has score {i}.14 and ratio 0.{i}\n") is None
    assert list_guard.trip is None

    # 3) Pure hashes, JSON punctuation, markdown tables, and whitespace are ignored (never trip).
    noise_guard = UnitRun()
    noise_block = (
        "{\n"
        "}\n"
        "deadbeef12345678\n"
        "| --- | --- |\n"
        "1234567890\n"
        "---\n"
    )
    assert noise_guard.feed(noise_block * 200) is None
    # Unbroken 20k hex hash must also NOT trigger run-on (contains L/Nd, not worded).
    assert noise_guard.feed("a1b2c3d4" * 2500) is None
    # Unbroken 20k whitespace must NOT trigger run-on.
    assert noise_guard.feed(" " * 20000) is None
    assert noise_guard.trip is None

    # 4) 100 repetitions (< 256 window) must NOT trip.
    short_repeat_guard = UnitRun()
    assert short_repeat_guard.feed("Now. " * 100) is None
    assert short_repeat_guard.trip is None

    # 5) 17 distinct cycling sentences (> LOOP_DISTINCT = 16) must NOT trip.
    distinct17_guard = UnitRun()
    block17 = "".join(f"Distinct thought number {k}.\n" for k in range(17))
    assert distinct17_guard.feed(block17 * 20) is None
    assert distinct17_guard.trip is None


@pytest.mark.unit
def test_reset_clears_ring_and_trip_state():
    guard = UnitRun()
    assert guard.feed("Now. " * 260) is not None
    assert guard.trip is not None
    guard.reset()
    assert guard.trip is None
    assert guard.feed("Now. " * 100) is None
    assert guard.trip is None
