"""Tests for Accept-Language parsing and unified fallback behavior."""

import pytest

from api.materials.helpers import _resolve_lang
from core.i18n import get_accept_language, resolve_accept_language
from services.project_service import resolve_template_lang


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, "zh"),
        ("", "zh"),
        ("   ", "zh"),
        ("zh-CN,zh;q=0.9,en;q=0.8", "zh"),
        ("zh-TW", "zh"),
        ("en-US,en;q=0.9", "en"),
        ("fr-FR,zh-CN;q=0.8", "zh"),
        ("fr-FR,fr;q=0.9", "en"),
        ("ja", "en"),
        ("de-DE,es;q=0.8", "en"),
    ],
)
def test_accept_language_resolution_is_unified(header: str | None, expected: str):
    assert resolve_accept_language(header) == expected
    assert get_accept_language(header) == expected
    assert resolve_template_lang(header) == expected
    assert _resolve_lang(header) == expected
