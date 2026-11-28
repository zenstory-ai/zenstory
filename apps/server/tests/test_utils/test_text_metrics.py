"""Tests for shared text metrics helpers."""

import unicodedata

import pytest

from utils.text_metrics import count_words


def test_count_words_ignores_pure_numbers():
    """Numeric-only tokens (including Unicode No/Nl symbols) should not count as words."""
    assert count_words("123 456 7890") == 0
    assert count_words("² ½ ① Ⅻ 〇") == 0
    assert count_words("hello②world") == 2


def test_count_words_counts_latin_words_and_chinese_chars():
    """Latin words and Chinese chars should both be counted."""
    assert count_words("hello 你好 world 世界 123") == 6


@pytest.mark.parametrize(
    ("lang", "sample", "expected"),
    [
        ("zh", "春风吹过江南岸，绿叶新芽满枝头。", 14),
        ("en", "The quiet harbor slept under a pale silver moon.", 9),
        ("id", "Malam itu hujan turun deras di pelabuhan tua.", 8),
        ("pt", "A estação chuvosa trouxe lembranças antigas e corações inquietos.", 9),
        ("vi_nfc", "Người lữ hành bước qua cánh đồng sương sớm.", 9),
        ("vi_nfd", unicodedata.normalize("NFD", "Người lữ hành bước qua cánh đồng sương sớm."), 9),
        ("es", "El niño miró el horizonte mientras caía la lluvia.", 9),
        ("ru", "Привет, мир! Вечерний ветер шумел за старым окном.", 8),
        ("ko", "안녕하세요 세계 오늘 밤 별이 유난히 밝습니다.", 7),
        ("fr", "L’été dernier, le garçon a trouvé une clé dorée.", 10),
        ("pl_nfc", "Żółć i gęśla jaźń — cichy wieczór nad rzeką.", 8),
        ("pl_nfd", unicodedata.normalize("NFD", "Żółć i gęśla jaźń — cichy wieczór nad rzeką."), 8),
        ("ar", "مرحبا بالعالم في هذه الليلة الهادئة.", 6),
        ("ar_tashkeel", "مَرْحَبًا بِالْعَالَمِ الْجَمِيلِ", 3),
        ("ja", "こんにちは世界、コーヒーを飲む。", 14),
        ("el", "Γειά σου κόσμε της νύχτας.", 5),
        ("hi", "नमस्ते दुनिया आज बहुत सुंदर है।", 6),
    ],
)
def test_count_words_multilingual_scripts(lang: str, sample: str, expected: int):
    """Unicode-aware counting handles CJK/Kana per char and all alphabetic/syllabic scripts per word."""
    assert count_words(sample) == expected, f"Mismatch for {lang}: {sample!r}"


def test_count_words_does_not_split_diacritic_words():
    """Accented Polish/Vietnamese/French words must count as 1 word in both NFC and NFD."""
    for word in ("người", "żółć", "café", "coração", "mañana", "château"):
        assert count_words(word) == 1
        assert count_words(unicodedata.normalize("NFD", word)) == 1
