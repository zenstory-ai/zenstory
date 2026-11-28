"""
i18n 基础设施
用于解析语言偏好和获取国际化消息
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Header

if TYPE_CHECKING:
    from sqlmodel import Session

SUPPORTED_LANGUAGES: list[str] = ['zh', 'en']
DEFAULT_LANGUAGE: str = 'zh'
UNSUPPORTED_FALLBACK_LANGUAGE: str = 'en'

DEFAULT_CHAT_SESSION_TITLE_ZH: str = "AI 助手对话"
DEFAULT_CHAT_SESSION_TITLE_EN: str = "AI Assistant"
NEW_CHAT_SESSION_TITLE_ZH: str = "新对话"
NEW_CHAT_SESSION_TITLE_EN: str = "New Chat"


def has_explicit_accept_language(accept_language: str | None) -> bool:
    """Return True when ``accept_language`` carries at least one non-empty, non-wildcard tag."""
    if not isinstance(accept_language, str) or not accept_language.strip():
        return False
    for part in accept_language.split(","):
        tag = part.split(";")[0].strip()
        if tag and tag != "*":
            return True
    return False


_has_explicit_accept_language = has_explicit_accept_language


def resolve_accept_language(accept_language: str | None) -> str:
    """
    从 Accept-Language 头部解析支持的语言代码。

    规则：
    - 头部缺失或为空白 -> 保持 DEFAULT_LANGUAGE ('zh')，兼容老客户端与支付回调等无头调用；
    - 头部存在且命中支持的语言 -> 返回第一个命中的主语言 ('zh' 或 'en')；
    - 头部存在但无任何支持的语言 -> 回退 UNSUPPORTED_FALLBACK_LANGUAGE ('en')。
    """
    if not isinstance(accept_language, str) or not accept_language.strip():
        return DEFAULT_LANGUAGE

    # 解析 Accept-Language 头部
    # 格式: "zh-CN,zh;q=0.9,en;q=0.8" 或 "en-US,en"
    languages = accept_language.split(',')

    for language in languages:
        # 移除权重参数 (q=0.9)
        lang_code = language.split(';')[0].strip().lower()

        # 提取主要语言代码 (zh-CN -> zh, en-US -> en)
        primary_lang = lang_code.split('-')[0]

        # 检查是否支持
        if primary_lang in SUPPORTED_LANGUAGES:
            return primary_lang

    # 提供了 Accept-Language 但没有任何支持的语言，回退英文
    return UNSUPPORTED_FALLBACK_LANGUAGE


def resolve_user_language(
    session: Session | None,
    user_id: str | None,
    accept_language: str | None,
) -> str:
    """Resolve user language with account-preference fallback when ``Accept-Language`` is absent/blank.

    Precedence:
    1. Explicit non-wildcard ``Accept-Language`` header (via ``resolve_accept_language``)
    2. Stored account ``ui_language`` (via ``get_ui_language(session, user_id)``)
    3. ``DEFAULT_LANGUAGE`` ('zh')
    """
    if has_explicit_accept_language(accept_language):
        return resolve_accept_language(accept_language)

    if session is not None and user_id:
        from services.features.user_preference_service import (
            get_ui_language,
            normalize_ui_language,
        )

        stored = normalize_ui_language(get_ui_language(session, user_id))
        if stored is not None:
            return stored

    return DEFAULT_LANGUAGE


def default_chat_session_title(language: str | None) -> str:
    """Localized default title for auto-created project chat sessions."""
    if (language or "").strip().lower().startswith("en"):
        return DEFAULT_CHAT_SESSION_TITLE_EN
    return DEFAULT_CHAT_SESSION_TITLE_ZH


def new_chat_session_title(language: str | None) -> str:
    """Localized default title when the author starts a fresh chat session without a custom title."""
    if (language or "").strip().lower().startswith("en"):
        return NEW_CHAT_SESSION_TITLE_EN
    return NEW_CHAT_SESSION_TITLE_ZH


def get_accept_language(accept_language: str | None = Header(None)) -> str:
    """
    FastAPI 依赖函数,从 Accept-Language 头部解析语言代码

    Args:
        accept_language: Accept-Language 头部值,格式如 "zh-CN,zh;q=0.9,en;q=0.8"

    Returns:
        支持的语言代码 ('zh' 或 'en')；头部存在但无支持语言时返回 'en'，完全未提供时返回 'zh'

    Examples:
        >>> get_accept_language("zh-CN,zh;q=0.9,en;q=0.8")
        'zh'
        >>> get_accept_language("en-US,en;q=0.9")
        'en'
        >>> get_accept_language("fr-FR,fr;q=0.9")
        'en'  # 有头部但不支持时回退英文
        >>> get_accept_language(None)
        'zh'  # 未提供头部时保持默认中文
    """
    return resolve_accept_language(accept_language)
