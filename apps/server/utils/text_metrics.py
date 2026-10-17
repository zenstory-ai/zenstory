"""Text metrics helpers shared by backend services."""

from __future__ import annotations

import re

_CHINESE_CHAR_PATTERN = re.compile(r"[\u4e00-\u9fa5]")
_LATIN_TOKEN_PATTERN = re.compile(r"[a-zA-Z]+")


def count_words(content: str | None) -> int:
    """
    Count words with the same hybrid method used by the frontend.

    - Each Chinese character in U+4E00..U+9FA5 counts as 1 word
    - Each contiguous Latin letter sequence counts as 1 word
    - Numbers/symbols are not counted
    """
    if not content:
        return 0

    # Han matches are one code point, so windows can bound the temporary list.
    # Keep variable-length Latin tokens whole below, including across windows.
    chinese_chars = sum(
        len(_CHINESE_CHAR_PATTERN.findall(content, start, start + 4096))
        for start in range(0, len(content), 4096)
    )
    latin_tokens = len(_LATIN_TOKEN_PATTERN.findall(content))
    return chinese_chars + latin_tokens
