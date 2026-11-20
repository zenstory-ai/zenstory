"""
Token estimation utilities.

Provides unified token estimation for the agent system.
Prefers tiktoken when available; falls back to a language-aware ratio
(~2 chars/token for predominantly-Chinese text, ~4 for Latin).
"""

# Approximate characters per token for Latin text
CHARS_PER_TOKEN = 4

# ---------------------------------------------------------------------------
# tiktoken encoder cache
# ---------------------------------------------------------------------------

_tiktoken_encoder = None
_tiktoken_available: bool | None = None  # None = not yet probed


def _get_tiktoken_encoder():
    """Return a cached tiktoken encoder, or None if tiktoken is unavailable."""
    global _tiktoken_encoder, _tiktoken_available
    if _tiktoken_available is None:
        try:
            import tiktoken  # noqa: PLC0415

            _tiktoken_encoder = tiktoken.get_encoding("cl100k_base")
            _tiktoken_available = True
        except Exception:
            _tiktoken_available = False
    return _tiktoken_encoder if _tiktoken_available else None


# ---------------------------------------------------------------------------
# CJK detection helpers
# ---------------------------------------------------------------------------

def _cjk_fraction(text: str) -> float:
    """Return the fraction of characters that are CJK codepoints."""
    if not text:
        return 0.0
    cjk_count = sum(
        1
        for ch in text
        if (
            "一" <= ch <= "鿿"  # CJK Unified Ideographs
            or "㐀" <= ch <= "䶿"  # CJK Extension A
            or "\U00020000" <= ch <= "\U0002A6DF"  # CJK Extension B
            or "　" <= ch <= "〿"  # CJK Symbols and Punctuation
            or "＀" <= ch <= "￯"  # Fullwidth / Halfwidth Forms
        )
    )
    return cjk_count / len(text)


def _chars_per_token_for(text: str) -> float:
    """Return the appropriate chars-per-token ratio for the given text."""
    fraction = _cjk_fraction(text)
    # Treat as predominantly Chinese when >40 % of chars are CJK
    if fraction > 0.4:
        return 2.0
    return float(CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def estimate_text_tokens(text: str) -> int:
    """
    Estimate token count for plain text.

    Uses tiktoken (cl100k_base) when available; otherwise falls back to a
    language-aware character ratio (~2 chars/token for Chinese-heavy text,
    ~4 chars/token for Latin text).

    Args:
        text: Text to estimate

    Returns:
        Estimated token count
    """
    if not text:
        return 0

    enc = _get_tiktoken_encoder()
    if enc is not None:
        try:
            return max(1, len(enc.encode(text)))
        except Exception:
            pass  # fall through to heuristic

    # Language-aware fallback
    cpt = _chars_per_token_for(text)
    return max(1, int(len(text) / cpt))
