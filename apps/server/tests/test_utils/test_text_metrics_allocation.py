"""Count complete text without retaining every Han regex match at once."""

import gc
import re
import tracemalloc

import pytest

from utils.text_metrics import count_words


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(None, id="none"),
        pytest.param("", id="empty"),
        pytest.param("123 456\t!?", id="uncounted"),
        pytest.param("hello 你好 world 世界 123", id="mixed"),
        pytest.param("\u3400\u4dbf\u4dff\u4e00\u9fa5\u9fa6\u9fff\uf900🙂𠀀", id="han-range-edges"),
        pytest.param("café e\u0301 Ångström a  b. 中文", id="latin-and-combining"),
        pytest.param("1" * 4095 + "abcdef" + "2" * 4095, id="latin-across-window"),
        pytest.param("a" * 8193, id="one-long-latin-token"),
        pytest.param("a" * 4095 + "汉" + "b" * 4095, id="han-separates-latin"),
        pytest.param("0" * 4095 + "汉字" + "0" * 8192 + "文", id="han-on-window-edges"),
        pytest.param("汉" * 4096 + "Longword" * 1024 + "字" * 4097, id="several-full-windows"),
        pytest.param("🙂" * 20000 + "汉" + "🙂" * 20000, id="sparse-han-astral"),
    ],
)
def test_count_words_preserves_full_regex_contract(text):
    # Original whole-string definitions: do not broaden the Han range or split
    # a Latin token when it crosses an allocation window.
    original = 0 if not text else len(re.findall(r"[\u4e00-\u9fa5]", text)) + len(
        re.findall(r"[a-zA-Z]+", text)
    )
    assert count_words(text) == original


def test_count_words_bounds_transient_han_matches():
    text = "汉" * 200_000 + " Plaintext"
    gc.collect()
    tracemalloc.start()
    try:
        result = count_words(text)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert result == 200_001
    # Inputs are outside tracing. Keep a wide CPython margin over one small
    # window, but not the >10 MiB list of every one-character Han match.
    assert peak < 1024 * 1024
