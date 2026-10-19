"""Unit tests for NaturalPolishService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.error_handler import APIException
from services.features.natural_polish_service import (
    DEFAULT_NATURAL_POLISH_PROMPT_EN,
    DEFAULT_NATURAL_POLISH_PROMPT_ZH,
    NATURAL_POLISH_MAX_TOKENS,
    NaturalPolishFailure,
    NaturalPolishProtocolError,
    NaturalPolishService,
    apply_line_edits,
    guard_line_sentences,
    is_protected_sentence,
    join_sentences,
    parse_line_edits,
    split_sentences,
)
from services.usage.llm_usage_service import LLMUsageAttribution


@pytest.mark.unit
def test_natural_polish_max_tokens_is_large_enough():
    assert NATURAL_POLISH_MAX_TOKENS == 16000


@pytest.mark.unit
def test_resolve_prompt_uses_server_defaults_by_language():
    service = NaturalPolishService()

    assert service._resolve_prompt("zh") == DEFAULT_NATURAL_POLISH_PROMPT_ZH
    assert service._resolve_prompt("en-US") == DEFAULT_NATURAL_POLISH_PROMPT_EN


@pytest.mark.unit
@pytest.mark.asyncio
async def test_natural_polish_calls_llm_with_single_round_settings():
    service = NaturalPolishService()
    llm_client = MagicMock()
    llm_client.MODEL_QUALITY = "quality-model"
    llm_client.acomplete = AsyncMock(return_value="原：" + "x" * 6000 + "\n改：rewritten")

    with patch(
        "services.features.natural_polish_service.get_llm_client",
        return_value=llm_client,
    ):
        result = await service.natural_polish(
            selected_text="x" * 6000,
            language="zh",
            user_id="user-1",
            project_id="project-1",
        )

    assert result.polished_text == "rewritten"
    assert result.model == "quality-model"
    llm_client.acomplete.assert_awaited_once_with(
        messages=[
            {"role": "system", "content": service._resolve_prompt("zh")},
            {"role": "user", "content": "x" * 6000},
        ],
        model="quality-model",
        max_tokens=NATURAL_POLISH_MAX_TOKENS,
        thinking_enabled=False,
        usage_attribution=LLMUsageAttribution(
            user_id="user-1", source="polish", project_id="project-1"
        ),
    )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_output",
    [
        "rewritten prose",
        "原：原文",
        "原：原文\n改：改文\n解释：这样更自然",
        "原文：原文\n改写：改文",
        "OLD:original\nNEW:revision\ntrailing junk",
    ],
)
async def test_natural_polish_rejects_malformed_protocol_output(model_output: str):
    service = NaturalPolishService()
    llm_client = MagicMock()
    llm_client.MODEL_QUALITY = "quality-model"
    llm_client.acomplete = AsyncMock(return_value=model_output)

    with (
        patch("services.features.natural_polish_service.get_llm_client", return_value=llm_client),
        pytest.raises(NaturalPolishFailure),
    ):
        await service.natural_polish(selected_text="原文", language="zh")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_natural_polish_rejects_an_ambiguous_duplicate_old_anchor():
    service = NaturalPolishService()
    llm_client = MagicMock()
    llm_client.MODEL_QUALITY = "quality-model"
    llm_client.acomplete = AsyncMock(return_value="原：重复的一行。\n改：改过的一行。")

    with (
        patch("services.features.natural_polish_service.get_llm_client", return_value=llm_client),
        pytest.raises(NaturalPolishFailure),
    ):
        await service.natural_polish(
            selected_text="重复的一行。\n重复的一行。",
            language="zh",
        )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_output", "selected_text", "stage", "category"),
    [
        ("原文：原文\n改写：改文", "原文", "protocol_parse", "invalid_marker"),
        ("原：原文", "原文", "protocol_parse", "truncated_output"),
        ("原：不存在\n改：改文", "原文", "anchor_apply", "missing_anchor"),
        ("原：重复\n改：改文", "重复\n重复", "anchor_apply", "ambiguous_anchor"),
        (
            "原：原文\n改：改文\n\n原：原文\n改：另一改文",
            "原文",
            "anchor_apply",
            "duplicate_anchor",
        ),
    ],
)
async def test_natural_polish_failure_has_a_fixed_stage_and_category(
    model_output: str,
    selected_text: str,
    stage: str,
    category: str,
):
    service = NaturalPolishService()
    llm_client = MagicMock()
    llm_client.MODEL_QUALITY = "quality-model"
    llm_client.acomplete = AsyncMock(return_value=model_output)

    with (
        patch("services.features.natural_polish_service.get_llm_client", return_value=llm_client),
        pytest.raises(NaturalPolishFailure) as caught,
    ):
        await service.natural_polish(selected_text=selected_text, language="zh")

    assert caught.value.stage == stage
    assert caught.value.category == category


@pytest.mark.unit
@pytest.mark.asyncio
async def test_natural_polish_classifies_model_and_content_protection_failures():
    service = NaturalPolishService()
    llm_client = MagicMock()
    llm_client.MODEL_QUALITY = "quality-model"
    llm_client.acomplete = AsyncMock(side_effect=RuntimeError("private provider detail"))

    with (
        patch("services.features.natural_polish_service.get_llm_client", return_value=llm_client),
        pytest.raises(NaturalPolishFailure) as model_failure,
    ):
        await service.natural_polish(selected_text="原文", language="zh")
    assert (model_failure.value.stage, model_failure.value.category) == (
        "model_call",
        "model_call_failed",
    )

    llm_client.acomplete = AsyncMock(return_value="原：原文\n改：改文")
    with (
        patch("services.features.natural_polish_service.get_llm_client", return_value=llm_client),
        patch(
            "services.features.natural_polish_service._keep_protected_sentences",
            side_effect=RuntimeError("private document detail"),
        ),
        pytest.raises(NaturalPolishFailure) as protection_failure,
    ):
        await service.natural_polish(selected_text="原文", language="zh")
    assert (protection_failure.value.stage, protection_failure.value.category) == (
        "content_protection",
        "content_protection_failed",
    )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_code"),
    [
        (402, "ERR_QUOTA_AI_DAILY_COST_EXCEEDED"),
        (503, "ERR_AI_COST_BUDGET_UNAVAILABLE"),
    ],
)
async def test_natural_polish_preserves_cost_guard_api_exception(status_code, error_code):
    service = NaturalPolishService()
    llm_client = MagicMock()
    llm_client.MODEL_QUALITY = "quality-model"
    llm_client.acomplete = AsyncMock(
        side_effect=APIException(error_code=error_code, status_code=status_code)
    )

    with (
        patch("services.features.natural_polish_service.get_llm_client", return_value=llm_client),
        pytest.raises(APIException) as caught,
    ):
        await service.natural_polish(selected_text="合成正文", language="zh")

    assert caught.value.status_code == status_code
    assert caught.value.error_code == error_code
    llm_client.acomplete.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("first", "second", "revised_first", "revised_second"),
    [
        (
            "他，站在那里，很久，没有动。",
            "他，站在那里， 很久，没有动。",
            "他在那儿站了很久，没动。",
            "他站了很久，始终没动。",
        ),
        (
            "他说：“你，不要，动。”",
            "他说：「你，不要，动。」",
            "他说：“你不要动。”",
            "他说：「你别动。」",
        ),
    ],
    ids=["internal-space", "quote-style"],
)
@pytest.mark.parametrize("edit_both", [False, True])
def test_exact_line_anchors_distinguish_normalized_collisions(
    first: str,
    second: str,
    revised_first: str,
    revised_second: str,
    edit_both: bool,
):
    original = f"{first}\n\n{second}"
    output = f"原：{first}\n改：{revised_first}"
    if edit_both:
        output += f"\n\n原：{second}\n改：{revised_second}"

    text, _ = apply_line_edits(original, parse_line_edits(output))

    assert text == f"{revised_first}\n\n{revised_second if edit_both else second}"


@pytest.mark.unit
@pytest.mark.parametrize("indent", [" ", "    "], ids=["one-space", "four-spaces"])
def test_optional_marker_space_cannot_select_the_wrong_indented_line(indent: str):
    line = "他，站在那里，很久，没有动。"
    first = f"{indent}{line}"
    second = f"{indent[1:]}{line}"
    output = f"原：{first}\n改：他在那儿站了很久，没动。"

    with pytest.raises(NaturalPolishProtocolError, match="ambiguous OLD anchor"):
        apply_line_edits(f"{first}\n{second}", parse_line_edits(output))


@pytest.mark.unit
def test_trailing_whitespace_does_not_disambiguate_otherwise_identical_lines():
    line = "他，站在那里，很久，没有动。"
    output = f"原：{line} \n改：他在那儿站了很久，没动。"

    with pytest.raises(NaturalPolishProtocolError, match="ambiguous OLD anchor"):
        apply_line_edits(f"{line} \n{line}  ", parse_line_edits(output))


@pytest.mark.unit
def test_unique_line_anchor_preserves_original_indentation_and_trailing_whitespace():
    original = "    他，站在那里，很久，没有动。  \n\n　　窗外下着雨。"
    output = "原：    他，站在那里，很久，没有动。  \n改：他在那儿站了很久，没动。"

    text, _ = apply_line_edits(original, parse_line_edits(output))

    assert text == "    他在那儿站了很久，没动。  \n\n　　窗外下着雨。"


@pytest.mark.unit
def test_normalized_line_anchor_still_matches_a_unique_original():
    original = '　　他说："不。"他心中一震。\n\n　　窗外下着雨。'
    output = "原：他说：“不。”他心中一震。\n改：他说：“不。”"

    text, _ = apply_line_edits(original, parse_line_edits(output))

    assert text == '　　他说："不。"\n\n　　窗外下着雨。'


@pytest.mark.unit
def test_normalized_line_anchor_rejects_multiple_candidates_without_an_exact_match():
    original = "他，站在那里，很久，没有动。\n他，站在那里， 很久，没有动。"
    output = "原：他，站在那里，  很久，没有动。\n改：他站了很久，没动。"

    with pytest.raises(NaturalPolishProtocolError, match="ambiguous OLD anchor"):
        apply_line_edits(original, parse_line_edits(output))


@pytest.mark.unit
def test_one_original_line_cannot_be_replaced_twice_using_different_quote_styles():
    original = '他说："你，不要，动。"'
    output = (
        f'原：{original}\n改：他说："你不要动。"\n\n'
        "原：他说：“你，不要，动。”\n改：他说：“你别动。”"
    )

    with pytest.raises(NaturalPolishProtocolError, match="duplicate OLD anchor"):
        apply_line_edits(original, parse_line_edits(output))


@pytest.mark.unit
def test_distinct_substring_edits_in_one_line_remain_supported():
    original = "　　他心中一震，回头。她倒吸一口凉气，退后。"
    output = "原：他心中一震，回头。\n改：他回头。\n\n原：她倒吸一口凉气，退后。\n改：她退后。"

    text, _ = apply_line_edits(original, parse_line_edits(output))

    assert text == "　　他回头。她退后。"


@pytest.mark.unit
@pytest.mark.parametrize("reverse", [False, True])
def test_line_edit_response_rejects_dialect_switch_between_complete_pairs(reverse):
    pairs = ["原：第一行。\n改：甲。", "OLD:Second.\nNEW:Beta."]
    if reverse:
        pairs.reverse()

    with pytest.raises(NaturalPolishProtocolError):
        parse_line_edits("\n\n".join(pairs))


@pytest.mark.unit
@pytest.mark.parametrize("markers", [("原：", "改："), ("OLD:", "NEW:")])
def test_line_edit_response_preserves_multiple_pairs_in_one_dialect(markers):
    old_marker, new_marker = markers
    output = f"{old_marker}First.\n{new_marker}Alpha.\n\n{old_marker}Second.\n{new_marker}Beta."

    assert [(edit.old, edit.new) for edit in parse_line_edits(output)] == [
        ("First.", "Alpha."),
        ("Second.", "Beta."),
    ]


# ---------------------------------------------------------------------------
# 只改写不删减（第二轮审计 N1）：提示词 + 整句删除守卫
# ---------------------------------------------------------------------------

_THEME_LINE = "　　可她把所有的错都揽到了自己身上。她宁可让我恨她，也不肯让我知道，我恨错了人。"


@pytest.mark.unit
def test_split_sentences_round_trips_and_keeps_closing_quotes():
    text = '“走吧。”她说。他没动……雨还在下 He said "no." Then 3.5 left.'

    sentences = split_sentences(text)

    assert "".join(sentences) == text
    assert sentences[:3] == ["“走吧。”", "她说。", "他没动……"]
    assert "3.5" in sentences[-1]


@pytest.mark.unit
def test_protected_sentence_needs_twelve_cjk_chars_outside_stock_phrases():
    assert is_protected_sentence("她宁可让我恨她，也不肯让我知道，我恨错了人。")
    assert not is_protected_sentence("她是在心疼我。")
    # 去掉「唇角…勾」「几不可察」「指节泛白」后只剩几个字：整句都是套话，可以删。
    assert not is_protected_sentence("她唇角几不可察地勾起一抹弧度，指节泛白。")
    assert is_protected_sentence("他心中一震，终于明白姐姐这五年一直在替他还债。")
    assert is_protected_sentence("She never told me, because she would rather I hated her than knew the truth about him.")


@pytest.mark.unit
def test_dropped_theme_sentence_is_put_back_at_its_position():
    trimmed = "　　可她把所有的错都揽到了自己身上。"

    assert guard_line_sentences(_THEME_LINE, trimmed) == (_THEME_LINE, 1)
    # 在中间删掉的句子也回到原来的位置。
    original = "　　她没回头。她宁可让我恨她，也不肯让我知道，我恨错了人。门在她身后关上。"
    assert guard_line_sentences(original, "　　她没回头。门在她身后关上。")[0] == original


@pytest.mark.unit
def test_restored_english_sentence_keeps_the_space_before_it():
    # 模型改写的最后一句没有句末空白；放回的原句不能和它粘在一起。
    original = "She left. She would rather I hated her than ever learned the truth about my father and the money."

    text, _ = apply_line_edits(original, parse_line_edits(f"OLD: {original}\nNEW: She left."))

    assert text == original
    indented = f"    {original}  "
    assert guard_line_sentences(indented, "    She left.")[0] == indented
    # 句中被删的英文句子放回后，前后各一个空格，不多不少。
    middle = (
        "He sat down. She would rather I hated her than ever learned the truth about my father and the money. "
        'The door shut. "Fine," he said.'
    )
    assert guard_line_sentences(middle, 'He sat down. The door shut. "Fine," he said.')[0] == middle


@pytest.mark.unit
def test_join_sentences_adds_a_space_only_at_latin_boundaries():
    assert join_sentences(["She left.", "Then the rain came."]) == "She left. Then the rain came."
    assert join_sentences(['He said "no."', "Then 3.5 left."]) == 'He said "no." Then 3.5 left.'
    assert join_sentences(["She left. ", "Then"]) == "She left. Then"
    # 中文边界照旧直接相连，即使中文里用了半角问号或下一句以英文开头。
    assert join_sentences(["“走吧。”", "她说。"]) == "“走吧。”她说。"
    assert join_sentences(["他走了?", "OK，就这样。"]) == "他走了?OK，就这样。"
    assert join_sentences(["他没动……", "雨还在下。"]) == "他没动……雨还在下。"


@pytest.mark.unit
def test_deleted_line_with_a_theme_sentence_comes_back_whole():
    original = f"{_THEME_LINE}\n\n　　窗外下着雨。"

    text, _ = apply_line_edits(original, parse_line_edits(f"原：{_THEME_LINE}\n改："))

    assert text == original


@pytest.mark.unit
def test_stock_phrases_and_short_sentences_may_still_be_trimmed():
    original = "　　她唇角几不可察地勾起一抹弧度，指节泛白。不是愧疚，是心疼。她是在心疼我。"
    output = "原：" + original + "\n改：　　不是愧疚，是心疼。"

    text, _ = apply_line_edits(original, parse_line_edits(output))

    assert text == "　　不是愧疚，是心疼。"
    # 整行都是套话的行可以删。
    stock_only = "　　他心中一震，倒吸一口凉气。\n\n　　门开了。"
    text, _ = apply_line_edits(stock_only, parse_line_edits("原：　　他心中一震，倒吸一口凉气。\n改："))
    assert text == "　　门开了。"


@pytest.mark.unit
def test_trimmed_or_rewritten_sentences_are_not_restored():
    # 句内删掉排比的一截：还是同一句。
    line = "　　她一个人在外头，做着最累的活，熬着最深的夜，攒着给妈做手术的钱。"
    trimmed = "　　她一个人在外头，做着最累的活，攒着给妈做手术的钱。"
    assert guard_line_sentences(line, trimmed) == (trimmed, 0)
    # 升华句改成分量相当的具体画面：算改写。
    closer = "　　原来，最深的爱，往往藏在最笨拙的沉默里，等着被人看见。"
    concrete = "　　她把那张汇款单压在妈的枕头底下，压了整整五年。"
    assert guard_line_sentences(closer, concrete) == (concrete, 0)
    # 两句并成一句，内容都在：不重复放回。
    merged_from = "　　她每个月往家里打钱，让我在家守着妈。她一个人在外头，攒着给妈做手术的钱。"
    merged = "　　她每个月往家里打钱让我在家守着妈，自己一个人在外头攒着给妈做手术的钱。"
    assert guard_line_sentences(merged_from, merged) == (merged, 0)


@pytest.mark.unit
def test_theme_sentence_cut_down_to_a_few_words_is_put_back():
    shortened = "　　可她把所有的错都揽到了自己身上。我恨错了人。"

    assert guard_line_sentences(_THEME_LINE, shortened)[0] == _THEME_LINE


@pytest.mark.unit
def test_script_lines_keep_one_prefix_when_a_sentence_is_restored():
    dialogue = "苏晚（低声）：我不怪你。我宁愿你恨我一辈子，也不想让你知道那天晚上的事。"
    action = "△ 苏晚站在雨里。她把那张写着周野名字的欠条撕成两半，扔进了排水沟。"

    text, _ = apply_line_edits(
        f"{dialogue}\n\n{action}",
        parse_line_edits(
            f"原：{dialogue}\n改：苏晚（低声）：我不怪你。\n\n原：{action}\n改：△ 苏晚站在雨里。"
        ),
        file_type="script",
    )

    assert text == f"{dialogue}\n\n{action}"


def test_latin_sentence_end_check_is_linear_on_long_punctuation_runs():
    """CodeQL py/polynomial-redos: long runs of '!' must not make the join check quadratic."""
    import time

    from services.features.natural_polish_service import _ends_latin_sentence, join_sentences

    adversarial = "a" + "!" * 200_000 + "x"
    started = time.perf_counter()
    assert _ends_latin_sentence(adversarial) is False
    assert _ends_latin_sentence(adversarial[:-1]) is True
    assert join_sentences([adversarial[:-1], "Next one."]).endswith("! Next one.")
    assert time.perf_counter() - started < 1.0
    assert _ends_latin_sentence("他走了。") is False
    assert _ends_latin_sentence("他走了!") is False
    assert _ends_latin_sentence('She left."') is True


# Shapes below were returned by the live model (synthetic selections) and used to
# fail the whole request; each pair is still complete and anchors one original line.
_BLANK_SEPARATED = "顾言推开门。屋里没开灯。\n\n药盒是空的。他愣住了。心中一震。"


@pytest.mark.unit
def test_blank_line_between_old_and_new_markers_is_accepted():
    output = (
        "原：顾言推开门。屋里没开灯。\n\n改：顾言推开门，屋里没开灯。\n\n"
        "原：药盒是空的。他愣住了。心中一震。\n\n改：药盒是空的。他愣住了。"
    )

    text, _ = apply_line_edits(_BLANK_SEPARATED, parse_line_edits(output))

    assert text == "顾言推开门，屋里没开灯。\n\n药盒是空的。他愣住了。"


@pytest.mark.unit
def test_unchanged_pair_on_a_repeated_line_is_skipped_instead_of_failing():
    original = "“好。”\n\n他转身离开。\n\n“好。”\n\n她也点了点头，眼泪却止不住地往下流，心头一紧。"
    output = (
        "原：“好。”\n\n改：“好。”\n\n"
        "原：她也点了点头，眼泪却止不住地往下流，心头一紧。\n\n改：她也点了点头，眼泪却止不住地往下流。"
    )

    text, _ = apply_line_edits(original, parse_line_edits(output))

    assert text == "“好。”\n\n他转身离开。\n\n“好。”\n\n她也点了点头，眼泪却止不住地往下流。"


@pytest.mark.unit
def test_multi_line_block_anchors_a_repeated_line_by_its_context():
    original = "“好。”\n他转身离开。\n“好。”\n她点了点头。"
    output = "原：“好。”\n她点了点头。\n改：“好吧。”\n她点了点头。"

    text, _ = apply_line_edits(original, parse_line_edits(output))

    assert text == "“好。”\n他转身离开。\n“好吧。”\n她点了点头。"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("original", "output", "match"),
    [
        # Same context twice: still ambiguous.
        ("甲\n乙\n甲\n乙", "原：甲\n乙\n改：丙\n乙", "ambiguous OLD anchor"),
        # Context that is not in the selection.
        ("甲\n乙", "原：甲\n丁\n改：丙\n丁", "unmatched OLD anchor"),
        # Writing into a blank original line adds a paragraph.
        ("甲\n\n乙", "原：甲\n\n乙\n改：甲\n新段\n乙", "blank line"),
    ],
)
def test_multi_line_block_keeps_anchor_safety(original: str, output: str, match: str):
    with pytest.raises(NaturalPolishProtocolError, match=match):
        apply_line_edits(original, parse_line_edits(output))


@pytest.mark.unit
@pytest.mark.parametrize(
    "output",
    [
        # A revision with a different number of lines merges or splits lines.
        "原：甲\n乙\n改：甲乙",
        # Explanation after a blank-separated pair is still extra text.
        "原：甲\n\n改：丙\n\n说明：改得更自然",
        # Two old markers in a row leave the first one without a revision.
        "原：甲\n\n原：乙\n\n改：丙",
    ],
)
def test_block_parser_still_rejects_extra_or_unpaired_text(output: str):
    with pytest.raises(NaturalPolishProtocolError):
        parse_line_edits(output)


_DIALOGUE = "「你来做什么？」苏棠冷冷地看着他。\n\n「来还你东西。」陆川把木盒放在桌上。"


async def _polish_with_reply(reply: str, selected_text: str):
    service = NaturalPolishService()
    llm_client = MagicMock()
    llm_client.MODEL_QUALITY = "quality-model"
    llm_client.acomplete = AsyncMock(return_value=reply)
    with patch("services.features.natural_polish_service.get_llm_client", return_value=llm_client):
        return await service.natural_polish(selected_text=selected_text, language="zh")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_line_aligned_reply_without_markers_is_applied_through_the_guards():
    reply = "「你来做什么？」苏棠看着他。\n\n「来还你东西。」陆川把木盒放在桌上。"

    result = await _polish_with_reply(reply, _DIALOGUE)

    assert result.polished_text == reply


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        # Lines merged: the reply no longer lines up with the selection.
        "「你来做什么？」苏棠看着他。「来还你东西。」陆川把木盒放在桌上。",
        # A blank line moved.
        "「你来做什么？」苏棠看着他。\n「来还你东西。」\n陆川把木盒放在桌上。",
        # Same shape, but a line is a different text (a refusal, not an edit).
        "这段文字已经很自然了。\n\n「来还你东西。」陆川把木盒放在桌上。",
    ],
)
async def test_marker_less_reply_that_does_not_line_up_still_fails(reply: str):
    with pytest.raises(NaturalPolishFailure) as caught:
        await _polish_with_reply(reply, _DIALOGUE)

    assert (caught.value.stage, caught.value.category) == ("protocol_parse", "invalid_marker")

