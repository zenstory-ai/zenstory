"""Unit tests for NaturalPolishService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.features.natural_polish_service import (
    DEFAULT_NATURAL_POLISH_PROMPT_EN,
    DEFAULT_NATURAL_POLISH_PROMPT_ZH,
    NATURAL_POLISH_MAX_TOKENS,
    NaturalPolishService,
    apply_full_rewrite,
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
    llm_client.acomplete = AsyncMock(return_value="rewritten")

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


@pytest.mark.unit
def test_full_rewrite_with_other_line_count_that_drops_a_theme_sentence_returns_the_original():
    original = f"　　窗外下着雨。\n\n{_THEME_LINE}"

    assert apply_full_rewrite(original, "窗外下着雨。可她把所有的错都揽到了自己身上。") == original
    # 没删有内容的句子时，整段改写照常保留（只还原引号体例）。
    assert apply_full_rewrite("　　他心中一震。\n\n　　门开了。", "他回过头。门开了。") == "他回过头。门开了。"


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
