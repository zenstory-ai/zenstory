"""Hard boundary for destructive app-chat operations on author-owned text.

Natural-language messages, selected references, the focused editor file, and prior assistant
suggestions are context for the model.  They are not authorization to delete or wholly replace
text whose latest persisted owner is the author.

The guard applies only to application chat, identified by ``ToolContext`` carrying an author
message.  Direct human APIs and non-chat integrations do not pass through this boundary.
Ordinary ``edit_file`` operations are intentionally outside this module; their concurrency and
author-delta reporting are handled by the edit executor and ``author_changes``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

AUTHOR_EDIT_PROTECTED_ERROR = "author_edit_protected"
AUTHOR_EDIT_PROTECTION_UNAVAILABLE_ERROR = "author_edit_protection_unavailable"

_AI_CHANGE_SOURCE = "ai"
_AI_EDIT_CHANGE_TYPE = "ai_edit"


@dataclass(frozen=True)
class AuthorScope:
    """Presence of an instance means the operation originated in application chat.

    The fields remain available to callers that use them as model context, but none of them grant
    destructive authority.
    """

    messages: tuple[str, ...] = ()
    referenced_file_ids: frozenset[str] = frozenset()
    focus_file_id: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(message for message in self.messages if message)


class AuthorContentProtectionError(RuntimeError):
    """Typed domain refusal raised while holding the target write lock."""

    error_type: str

    def __init__(self, file_id: str, title: str, *, action: str) -> None:
        self.file_id = file_id
        self.title = title
        self.action = action
        super().__init__(protected_refusal_message(title, action=action))


class AuthorEditProtectedError(AuthorContentProtectionError):
    error_type = AUTHOR_EDIT_PROTECTED_ERROR


class AuthorEditProtectionUnavailableError(AuthorContentProtectionError):
    error_type = AUTHOR_EDIT_PROTECTION_UNAVAILABLE_ERROR

    def __init__(self, file_id: str, *, action: str) -> None:
        self.file_id = file_id
        self.title = ""
        self.action = action
        RuntimeError.__init__(
            self,
            "无法确认目标文件的保护状态，本次破坏性操作未执行。"
            "请稍后重试；需要删除时也可以由作者使用文件菜单。",
        )


def latest_text_is_authors(session: Any, file: Any) -> bool:
    """Whether the current non-empty body most recently came from an author-side write.

    Unknown legacy provenance (no versions) is not claimed as author-owned.  A content/version
    mismatch is protected because it represents a write newer than the latest recorded AI state.
    """
    content = getattr(file, "content", None) or ""
    if not content.strip():
        return False

    from services.features.file_version_service import FileVersionService

    service = FileVersionService()
    latest = service.get_latest_version(session, file.id)
    if latest is None:
        return False
    latest_content = service._get_contents_for_versions(session, {file.id: latest})[file.id]
    if latest_content != content:
        return True
    return not (
        latest.change_source == _AI_CHANGE_SOURCE
        or latest.change_type == _AI_EDIT_CHANGE_TYPE
    )


def enforce_author_content_protection(
    session: Any,
    files: list[Any],
    *,
    action: str,
) -> None:
    """Reject protected content while the caller holds every relevant write lock."""
    for file in files:
        if getattr(file, "file_type", None) == "folder":
            continue
        try:
            protected = latest_text_is_authors(session, file)
        except Exception as exc:
            raise AuthorEditProtectionUnavailableError(
                str(getattr(file, "id", "")), action=action
            ) from exc
        if protected:
            raise AuthorEditProtectedError(
                str(getattr(file, "id", "")),
                str(getattr(file, "title", "") or ""),
                action=action,
            )


def protected_refusal_message(title: str | None, *, action: str = "delete") -> str:
    """Actionable refusal that points to explicit human controls, never to magic wording."""
    label = f"《{title}》" if title else "这个文件"
    if action == "rewrite":
        return (
            f"{label}有作者手动保存的内容，应用内对话不能整份覆盖它，本次没有复用该文件。"
            "如需调整其中内容，请用 edit_file 做局部编辑；如需整份替换，请作者在编辑器中操作。"
        )
    if action == "folder":
        return (
            f"该文件夹包含作者手动保存的{label}，应用内对话不能递归删除它，本次没有执行删除。"
            "如需删除，请作者使用文件菜单。"
        )
    return (
        f"{label}有作者手动保存的内容，应用内对话不能删除它，本次没有执行删除。"
        "如需删除，请作者使用文件菜单。"
    )


def protected_user_message(title: str | None, *, action: str = "delete") -> str:
    label = f"《{title}》" if title else "这个文件"
    if action == "rewrite":
        return f"{label}有你手动保存的内容，已保留原样；局部调整可在编辑器中修改。"
    return f"{label}有你手动保存的内容，已保留原样；删除请使用文件菜单。"


@dataclass
class AuthorEditRefusals:
    """Protected files refused during this request (shared across tool worker threads)."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _file_ids: list[str] = field(default_factory=list)

    def add(self, file_id: str) -> None:
        if not file_id:
            return
        with self._lock:
            if file_id not in self._file_ids:
                self._file_ids.append(file_id)

    def file_ids(self) -> list[str]:
        with self._lock:
            return list(self._file_ids)


def referenced_file_ids_from_metadata(metadata: dict[str, Any] | None) -> list[str]:
    """Collect attached/quoted file IDs for context only, never destructive authority."""
    if not isinstance(metadata, dict):
        return []
    ids: list[str] = []
    attached = metadata.get("attached_file_ids")
    if isinstance(attached, list):
        ids.extend(str(item) for item in attached if isinstance(item, str) and item)
    quotes = metadata.get("text_quotes")
    if isinstance(quotes, list):
        for quote in quotes:
            if isinstance(quote, dict):
                quote_file_id = quote.get("fileId") or quote.get("file_id")
                if isinstance(quote_file_id, str) and quote_file_id:
                    ids.append(quote_file_id)
    return ids
