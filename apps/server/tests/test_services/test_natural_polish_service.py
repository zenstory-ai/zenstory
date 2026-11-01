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


    async def acomplete(**kwargs):
        kwargs["served_model_sink"]["model"] = "served-model"
        return "原：" + "x" * 6000 + "\n改：rewritten"

    llm_client.acomplete = AsyncMock(side_effect=acomplete)

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
    assert result.model == "served-model"
    llm_client.acomplete.assert_awaited_once_with(
        messages=[
            {"role": "system", "content": service._resolve_prompt("zh")},
            {"role": "user", "content": "x" * 6000},
        ],
        max_tokens=NATURAL_POLISH_MAX_TOKENS,
        thinking_enabled=False,
        served_model_sink={"model": "served-model"},
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
        "旧：原文\n新：改文",
        "OLD:original\nNEW:revision\ntrailing junk",
    ],
)
async def test_natural_polish_rejects_malformed_protocol_output(model_output: str):
    service = NaturalPolishService()
    llm_client = MagicMock()
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
        ("旧：原文\n新：改文", "原文", "protocol_parse", "invalid_marker"),
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

    async def failing_call(**kwargs):
        # 调用失败前 sink 里已经记下最后尝试的供应商。
        kwargs["served_model_sink"].update(model="deepseek-flash", provider="deepseek")
        raise RuntimeError("private provider detail")

    llm_client.acomplete = AsyncMock(side_effect=failing_call)
    with (
        patch("services.features.natural_polish_service.get_llm_client", return_value=llm_client),
        pytest.raises(NaturalPolishFailure) as attributed_failure,
    ):
        await service.natural_polish(selected_text="原文", language="zh")
    assert (attributed_failure.value.provider, attributed_failure.value.model) == ("deepseek", "deepseek-flash")

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



# Live-model merges (synthetic text): the model folded a protected sentence into its
# neighbour and dropped part of it. Putting the original back next to the merged
# sentence repeated the merged fragment; the merge is undone instead.
_MERGE_DROPS = [
    (
        "他慢慢坐下来，眼底闪过一丝寒光。他决定，无论如何都要找到真相。",
        "他慢慢坐下来，决定无论如何都要找到真相。",
        "他慢慢坐下来",
    ),
    (
        "客厅的钟滴答滴答地走着，仿佛在诉说着什么。他忽然意识到，这个家，早就不是他记忆中的那个家了。"
        "这让他感到一种前所未有的孤独和迷茫，内心深处涌起一股难以言喻的酸楚。",
        "客厅的钟滴答滴答地走着。他忽然意识到，这个家早就不是他记忆中的那个家了，一股难以言喻的酸楚涌上来。",
        "难以言喻的酸楚",
    ),
    (
        "客厅的钟滴答滴答地走着，仿佛在诉说着什么。他忽然意识到，这个家，早就不是他记忆中的那个家了。"
        "这让他感到一种前所未有的孤独和迷茫，内心深处涌起一股难以言喻的酸楚。",
        "客厅的钟滴答滴答地走着。他忽然意识到，这个家早就不是他记忆中的那个家了，一种前所未有的孤独和迷茫涌了上来。",
        "前所未有的孤独和迷茫",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(("original", "merged", "fragment"), _MERGE_DROPS)
def test_restoring_a_merged_sentence_does_not_repeat_the_merged_fragment(original, merged, fragment):
    text, restored = guard_line_sentences(original, merged)

    assert restored >= 1
    assert text.count(fragment) == 1
    # The sentence the merge dropped is back, word for word.
    [dropped] = [sentence for sentence in split_sentences(original) if fragment in sentence]
    assert dropped in text


@pytest.mark.unit
def test_an_untouched_rewrite_before_the_merge_is_kept():
    original, merged, _ = _MERGE_DROPS[1]

    text, _ = guard_line_sentences(original, merged)

    # The first sentence was only trimmed, not merged: the model's version stays.
    assert text.startswith("客厅的钟滴答滴答地走着。")


# ---------------------------------------------------------------------------
# 失败分型 invalid_marker、missing_anchor、ambiguous_anchor。下面的回复全是合成样例，覆盖每一类里按协议结构可以确定意图的偏差。
# ---------------------------------------------------------------------------

_TWO_PARAGRAPHS = "　　她推门。屋里没开灯。她摸到桌边。\n\n　　他心中一震，半晌没有说话。"
_TWO_PARAGRAPHS_POLISHED = "　　进屋后，她摸黑走到桌边。\n\n　　他半晌没有说话。"


@pytest.mark.unit
@pytest.mark.parametrize(
    "reply",
    [
        # 整个回复包在代码块里。
        "```\n原：她推门。屋里没开灯。她摸到桌边。\n改：进屋后，她摸黑走到桌边。\n\n"
        "原：他心中一震，半晌没有说话。\n改：他半晌没有说话。\n```",
        # 标记加粗、带编号或列表符号。
        "**原：**她推门。屋里没开灯。她摸到桌边。\n**改：**进屋后，她摸黑走到桌边。\n\n"
        "1. 原：他心中一震，半晌没有说话。\n2. 改：他半晌没有说话。",
        "- 原：她推门。屋里没开灯。她摸到桌边。\n- 改：进屋后，她摸黑走到桌边。\n\n"
        "- 原：他心中一震，半晌没有说话。\n- 改：他半晌没有说话。",
        # 半角冒号、常见别名。
        "原:她推门。屋里没开灯。她摸到桌边。\n改:进屋后，她摸黑走到桌边。\n\n"
        "原文：他心中一震，半晌没有说话。\n修改后：他半晌没有说话。",
        # 开头一句说明、结尾一段总结（和选区几乎不共享内容）。
        "以下是需要修改的地方：\n\n原：她推门。屋里没开灯。她摸到桌边。\n改：进屋后，她摸黑走到桌边。\n\n"
        "原：他心中一震，半晌没有说话。\n改：他半晌没有说话。\n\n以上调整让节奏更连贯，其余保持不变。",
        # 标记单独占一行，照抄的原文写在下一行。
        "原：\n她推门。屋里没开灯。她摸到桌边。\n改：\n进屋后，她摸黑走到桌边。\n\n"
        "原：\n他心中一震，半晌没有说话。\n改：\n他半晌没有说话。",
    ],
    ids=["code-fence", "bold-numbered", "bullets", "half-width-alias", "notes-around", "marker-own-line"],
)
def test_invalid_marker_shapes_with_clear_pairs_now_apply(reply: str):
    edits = parse_line_edits(reply, _TWO_PARAGRAPHS)

    text, _ = apply_line_edits(_TWO_PARAGRAPHS, edits)

    assert text == _TWO_PARAGRAPHS_POLISHED


@pytest.mark.unit
def test_english_markers_ignore_case_and_full_width_colons():
    original = "She opened the door. The room was dark.\n\nHis heart lurched. He said nothing."
    reply = "old：She opened the door. The room was dark.\nnew：She opened the door to a dark room."

    text, _ = apply_line_edits(original, parse_line_edits(reply, original))

    assert text == "She opened the door to a dark room.\n\nHis heart lurched. He said nothing."


@pytest.mark.unit
@pytest.mark.parametrize("reply", ["无改动。", "无改动（这段已经很自然）", "No changes.", "NO CHANGE"])
def test_no_change_wording_variants_without_any_marker_mean_no_change(reply: str):
    assert parse_line_edits(reply, _TWO_PARAGRAPHS) == []


@pytest.mark.unit
def test_reply_cut_off_after_its_last_old_marker_keeps_the_complete_pairs():
    reply = "原：她推门。屋里没开灯。她摸到桌边。\n改：进屋后，她摸黑走到桌边。\n\n原：他心中一震，半晌没有说话。"

    text, _ = apply_line_edits(_TWO_PARAGRAPHS, parse_line_edits(reply, _TWO_PARAGRAPHS))

    assert text == "　　进屋后，她摸黑走到桌边。\n\n　　他心中一震，半晌没有说话。"


@pytest.mark.unit
def test_trailing_old_followed_by_prose_is_a_protocol_error_not_truncation():
    # 最后的「原：」后面还有别的文字：回复没有断，是格式错了，不能悄悄套用前面的几组。
    reply = "原：她推门。屋里没开灯。她摸到桌边。\n改：进屋后，她摸黑走到桌边。\n\n原：他心中一震，半晌没有说话。\n我觉得这句也可以改"

    with pytest.raises(NaturalPolishProtocolError, match="expected NEW marker") as caught:
        parse_line_edits(reply, _TWO_PARAGRAPHS)
    assert caught.value.category == "invalid_marker"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_reply_cut_off_by_the_token_limit_keeps_complete_pairs_even_mid_block():
    # 供应商报告 finish_reason=length 时，最后一组「原：」停在多行照抄的中途也算截断。
    service = NaturalPolishService()
    llm_client = MagicMock()

    async def acomplete(**kwargs):
        kwargs["served_model_sink"].update(model="deepseek-flash", provider="deepseek", finish_reason="length")
        return "原：她推门。屋里没开灯。她摸到桌边。\n改：进屋后，她摸黑走到桌边。\n\n原：\n他心中一震，"

    llm_client.acomplete = AsyncMock(side_effect=acomplete)
    with patch("services.features.natural_polish_service.get_llm_client", return_value=llm_client):
        result = await service.natural_polish(selected_text=_TWO_PARAGRAPHS, language="zh")

    assert result.polished_text == "　　进屋后，她摸黑走到桌边。\n\n　　他心中一震，半晌没有说话。"


@pytest.mark.unit
def test_no_change_line_does_not_excuse_a_lead_in_that_copies_the_selection():
    # 「无改动」开头的前言同样要过说明检查：后面大段照抄原文时不当说明丢掉。
    reply = "无改动\n她推门。屋里没开灯。她摸到桌边。\n原：他心中一震，半晌没有说话。\n改：他半晌没有说话。"

    with pytest.raises(NaturalPolishProtocolError, match="expected OLD marker"):
        parse_line_edits(reply, _TWO_PARAGRAPHS)


@pytest.mark.unit
@pytest.mark.parametrize(
    "reply",
    [
        # 说明紧贴在改写后面、没有空行隔开：分不清哪部分是改写。
        "原：他心中一震，半晌没有说话。\n改：他半晌没有说话。\n（删去了套话）",
        # 空行后面跟的是正文内容（模型把一段拆成了两段），不是说明：不能丢，也不能套用。
        "原：他心中一震，半晌没有说话。\n改：他半晌没有说话。\n\n她推门。屋里没开灯。她摸到桌边。",
        # 说明里大段引用了选区原文，也不当说明丢掉。
        "以下修改针对「她推门。屋里没开灯。她摸到桌边。」\n原：他心中一震，半晌没有说话。\n改：他半晌没有说话。",
        # 单行「原：」后的「改：」单独占一行而「原：」没有：分不清是删除还是改写。
        "原：他心中一震，半晌没有说话。\n改：\n他半晌没有说话。",
    ],
    ids=["glued-note", "split-paragraph", "quoting-note", "unpaired-own-line"],
)
def test_replies_whose_intent_is_unclear_still_fail(reply: str):
    with pytest.raises(NaturalPolishProtocolError):
        parse_line_edits(reply, _TWO_PARAGRAPHS)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("original", "reply", "expected"),
    [
        # 模型把全角逗号、冒号抄成了半角。
        ("他说：好，我去。\n\n窗外下着雨。", "原：他说:好,我去。\n改：他说：好。", "他说：好。\n\n窗外下着雨。"),
        # 省略号和破折号写法不同。
        ("她等了很久……没有人来——一个都没有。\n\n窗外下着雨。", "原：她等了很久...没有人来—一个都没有。\n改：她等了很久，一个人都没来。",
         "她等了很久，一个人都没来。\n\n窗外下着雨。"),
        # 给照抄的整行加了一层引号。
        ("他推开门，屋里没开灯。\n\n窗外下着雨。", "原：「他推开门，屋里没开灯。」\n改：「他推开门，屋里一片黑。」",
         "他推开门，屋里一片黑。\n\n窗外下着雨。"),
        # 只抄了一句，且半角化了标点：在唯一包含它的行里做子串替换，句外文字逐字不动。
        ("　　雨停了。他心中一震，回过头。她没动。", "原：他心中一震,回过头。\n改：他回过头。", "　　雨停了。他回过头。她没动。"),
        # 长句里抄错了一个字（模型顺手改了字再抄）：唯一高度相似的行。
        ("　　那天傍晚，母亲站在院门口，一直望着通往镇上的那条土路。\n\n窗外下着雨。",
         "原：那天傍晚，母亲站在院门口，一直望着通向镇上的那条土路。\n改：那天傍晚，母亲站在院门口望着去镇上的土路。",
         "　　那天傍晚，母亲站在院门口望着去镇上的土路。\n\n窗外下着雨。"),
    ],
    ids=["half-width-punctuation", "ellipsis-dash", "wrapped-quotes", "substring-width", "fuzzy-one-char"],
)
def test_missing_anchor_shapes_now_find_their_line(original: str, reply: str, expected: str):
    text, _ = apply_line_edits(original, parse_line_edits(reply, original))

    assert text == expected


@pytest.mark.unit
def test_fuzzy_anchor_refuses_when_two_lines_are_equally_close():
    original = (
        "那天傍晚，母亲站在院门口，一直望着通往镇上的那条土路。\n"
        "那天傍晚，父亲站在院门口，一直望着通往镇上的那条土路。"
    )
    reply = "原：那天傍晚，站在院门口，一直望着通往镇上的那条土路。\n改：那天傍晚，有人站在院门口。"

    with pytest.raises(NaturalPolishProtocolError, match="unmatched OLD anchor"):
        apply_line_edits(original, parse_line_edits(reply, original))


_REPEATED = "“好。”\n他转身离开。\n“好。”\n她点了点头，心头一紧。"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        # 重复行排在一个唯一锚点之前：只能是前一处。
        ("原：“好。”\n改：“行。”\n\n原：他转身离开。\n改：他转身走了。", "“行。”\n他转身走了。\n“好。”\n她点了点头，心头一紧。"),
        # 重复行排在唯一锚点之后、另一个唯一锚点之前：只能是后一处。
        ("原：他转身离开。\n改：他转身走了。\n\n原：“好。”\n改：“行。”\n\n原：她点了点头，心头一紧。\n改：她点了点头。",
         "“好。”\n他转身走了。\n“行。”\n她点了点头。"),
        # 两组同样的锚点对两处重复行：按顺序各改一处。
        ("原：“好。”\n改：“行。”\n\n原：“好。”\n改：“嗯。”", "“行。”\n他转身离开。\n“嗯。”\n她点了点头，心头一紧。"),
    ],
    ids=["before-unique", "between-unique", "k-pairs-k-copies"],
)
def test_ambiguous_anchor_is_settled_by_the_listed_order(reply: str, expected: str):
    text, _ = apply_line_edits(_REPEATED, parse_line_edits(reply, _REPEATED))

    assert text == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "reply",
    [
        # 只有一组、没有任何位置线索。
        "原：“好。”\n改：“行。”",
        # 唯一锚点本身就没按原文顺序列出：顺序不可信，不拿它消歧。
        "原：她点了点头，心头一紧。\n改：她点了点头。\n\n原：他转身离开。\n改：他转身走了。\n\n原：“好。”\n改：“行。”",
    ],
    ids=["no-context", "out-of-order"],
)
def test_ambiguous_anchor_without_trustworthy_order_still_fails(reply: str):
    with pytest.raises(NaturalPolishProtocolError, match="ambiguous OLD anchor"):
        apply_line_edits(_REPEATED, parse_line_edits(reply, _REPEATED))


@pytest.mark.unit
def test_tolerated_reply_leaves_unmentioned_lines_byte_identical_and_drops_notes():
    original = "　　第一段，原样。\t\n\n　　他心中一震，半晌没有说话。\n\n  第三段  \n"
    reply = "```text\n以下是修改：\n**原：**他心中一震，半晌没有说话。\n**改：**他半晌没有说话。\n\n说明：去掉了套话。\n```"

    text, _ = apply_line_edits(original, parse_line_edits(reply, original))

    assert text == "　　第一段，原样。\t\n\n　　他半晌没有说话。\n\n  第三段  \n"
    assert "说明" not in text and "以下" not in text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failure_and_tolerated_logs_carry_provider_and_model_only(caplog: pytest.LogCaptureFixture):
    service = NaturalPolishService()
    llm_client = MagicMock()
    replies = iter(["原：完全不存在的一句话，和选区毫无关系。\n改：别的", "```\n原：他心中一震，半晌没有说话。\n改：他半晌没有说话。\n```"])

    async def acomplete(**kwargs):
        kwargs["served_model_sink"].update(model="deepseek-flash", provider="deepseek", finish_reason="stop")
        return next(replies)

    llm_client.acomplete = AsyncMock(side_effect=acomplete)
    with patch("services.features.natural_polish_service.get_llm_client", return_value=llm_client):
        with pytest.raises(NaturalPolishFailure) as caught:
            await service.natural_polish(selected_text=_TWO_PARAGRAPHS, language="zh")
        with caplog.at_level("INFO", logger="services.features.natural_polish_service"):
            result = await service.natural_polish(selected_text=_TWO_PARAGRAPHS, language="zh")

    assert (caught.value.stage, caught.value.category) == ("anchor_apply", "missing_anchor")
    assert (caught.value.model, caught.value.provider, caught.value.finish_reason) == ("deepseek-flash", "deepseek", "stop")
    assert result.polished_text == "　　她推门。屋里没开灯。她摸到桌边。\n\n　　他半晌没有说话。"
    record = next(r for r in caplog.records if r.getMessage() == "Natural polish accepted a reply with format deviations")
    assert record.custom_fields["tolerated"] == ["code_fence"]
    assert record.custom_fields["provider"] == "deepseek"
    assert "心中一震" not in repr(record.custom_fields)


@pytest.mark.unit
def test_prompts_are_static_prefixes_with_paired_rules():
    for prompt in (DEFAULT_NATURAL_POLISH_PROMPT_ZH, DEFAULT_NATURAL_POLISH_PROMPT_EN):
        assert "{" not in prompt  # static prefix: nothing per request is formatted in
    # 中英文是成对版本：规则条数一致，改一边漏改另一边时这里会发现。
    zh_rules = DEFAULT_NATURAL_POLISH_PROMPT_ZH.count("\n- ")
    en_rules = DEFAULT_NATURAL_POLISH_PROMPT_EN.count("\n- ")
    assert zh_rules == en_rules > 0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("original", "reply", "expected"),
    [
        ("他刚要开口——门开了。她回头。", "原：他刚要开口——\n改：他正要说话——", "他正要说话——门开了。她回头。"),
        ("他走了……她没回头，雨一直下着。", "原：他走了……\n改：他离开了……", "他离开了……她没回头，雨一直下着。"),
        ("He left... she did not turn back.", "OLD:He left...\nNEW:He walked away...", "He walked away... she did not turn back."),
    ],
    ids=["dash-run", "ellipsis-run", "dot-run"],
)
def test_partial_anchor_ending_in_a_dash_or_ellipsis_run_replaces_the_whole_run(original, reply, expected):
    # 锚点规范化把一串「—」「…」「.」并成一个字符：句内替换要切到整串的末尾，不留下多出来的标点。
    text, _ = apply_line_edits(original, parse_line_edits(reply, original))

    assert text == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "reply",
    [
        "原：\n他心中一震。\n改：\n\n（这一句是套话，删去）",
        "原：\n他心中一震。\n\n改：\n\n\n说明：此句为套话，已删除。",
    ],
    ids=["note-after-blank", "note-after-blank-lines"],
)
def test_own_line_empty_new_followed_by_a_note_deletes_the_line_and_drops_the_note(reply):
    # 「改：」单独一行又是空的：这是删掉这一行。空行后面的说明不能当成改写写进选区。
    original = "他心中一震。\n她转身离开了房间。"
    text, _ = apply_line_edits(original, parse_line_edits(reply, original))

    assert "套话" not in text
    assert "他心中一震" not in text
    assert "她转身离开了房间。" in text


@pytest.mark.asyncio
@pytest.mark.unit
async def test_token_limit_drops_a_last_pair_whose_revision_was_cut_off():
    # finish_reason=length 时最后一组「改：」可能停在半句：这一组不套用，前面完整的照常套用。
    service = NaturalPolishService()
    llm_client = MagicMock()
    selected = "她推门进去，屋里没开灯。\n她摸到桌边，坐了很久。她才想起要给母亲打电话，电话那头一直没人接。"

    async def acomplete(**kwargs):
        kwargs["served_model_sink"].update(model="deepseek-flash", provider="deepseek", finish_reason="length")
        return (
            "原：她推门进去，屋里没开灯。\n改：她推门进去，屋里很黑。\n\n"
            "原：她摸到桌边，坐了很久。她才想起要给母亲打电话，电话那头一直没人接。\n"
            "改：她在桌边坐了很久，才想起给母亲打电"
        )

    llm_client.acomplete = AsyncMock(side_effect=acomplete)
    with patch("services.features.natural_polish_service.get_llm_client", return_value=llm_client):
        result = await service.natural_polish(selected_text=selected, language="zh")

    assert result.polished_text == (
        "她推门进去，屋里很黑。\n她摸到桌边，坐了很久。她才想起要给母亲打电话，电话那头一直没人接。"
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_token_limit_with_only_one_cut_off_pair_fails_as_truncated_output():
    service = NaturalPolishService()
    llm_client = MagicMock()
    selected = "她摸到桌边，坐了很久。她才想起要给母亲打电话，电话那头一直没人接。"

    async def acomplete(**kwargs):
        kwargs["served_model_sink"].update(model="deepseek-flash", provider="deepseek", finish_reason="length")
        return f"原：{selected}\n改：她在桌边坐了很久，才想起给母亲打电"

    llm_client.acomplete = AsyncMock(side_effect=acomplete)
    with patch("services.features.natural_polish_service.get_llm_client", return_value=llm_client):
        with pytest.raises(NaturalPolishFailure) as caught:
            await service.natural_polish(selected_text=selected, language="zh")

    assert caught.value.category == "truncated_output"


@pytest.mark.unit
def test_english_partial_anchor_does_not_match_across_word_boundaries():
    # 英文锚点不分空白时 "therapist" 会对上 "the rapist"：词边界不同的不算同一处。
    original = "Intro line here.\nI saw the rapist leave the room quietly.\nEnd."

    with pytest.raises(NaturalPolishProtocolError) as caught:
        apply_line_edits(original, parse_line_edits("OLD:therapist\nNEW:doctor", original))
    assert caught.value.category == "missing_anchor"

    text, _ = apply_line_edits(original, parse_line_edits("OLD:the  Rapist\nNEW:the man", original))
    assert text == "Intro line here.\nI saw the man leave the room quietly.\nEnd."


@pytest.mark.unit
def test_own_line_empty_new_followed_by_blank_line_then_a_rewrite_applies_the_rewrite():
    # 「改：」单独一行，空一行之后才是改写：空行后面是改写不是说明，照常套用。
    original = "他推开门，走进了那个昏暗的房间。"
    reply = "原：\n他推开门，走进了那个昏暗的房间。\n改：\n\n他推开门，走进昏暗的房间。"

    text, _ = apply_line_edits(original, parse_line_edits(reply, original))

    assert text == "他推开门，走进昏暗的房间。"


@pytest.mark.unit
def test_partial_anchor_rejected_at_a_word_boundary_keeps_searching_inside_it():
    # "ha haha" 里第一个候选跨了词边界被拒；后面的 "haha" 和它重叠，仍要找到。
    original = "Intro.\nha haha end\nOutro."
    text, _ = apply_line_edits(original, parse_line_edits("OLD:haha\nNEW:hoho", original))
    assert text == "Intro.\nha hoho end\nOutro."


@pytest.mark.unit
def test_partial_anchor_with_two_overlapping_word_matches_stays_ambiguous():
    original = "Intro.\nha hahaha end\nOutro."

    with pytest.raises(NaturalPolishProtocolError) as caught:
        apply_line_edits(original, parse_line_edits("OLD:haha\nNEW:hoho", original))
    assert caught.value.category == "ambiguous_anchor"
