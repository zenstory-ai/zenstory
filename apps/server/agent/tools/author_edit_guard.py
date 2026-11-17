"""Hard boundary for destructive app-chat operations on author-owned text.

Natural-language messages, selected references, the focused editor file, and prior assistant
suggestions are context for the model.  They are not authorization to delete or wholly replace
text whose latest persisted owner is the author, or text whose owner cannot be established
(non-empty with no version history), which is refused without claiming author ownership.

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
PRE_WRITE_HISTORY_UNAVAILABLE_ERROR = "pre_write_history_unavailable"

_AI_CHANGE_SOURCE = "ai"
_AI_EDIT_CHANGE_TYPE = "ai_edit"
_RESTORE_CHANGE_TYPE = "restore"
_USER_CHANGE_SOURCE = "user"
# 撤销 / 审阅回到 AI 正文时往前比对的版本数（写锁内执行，保持有界）。
_AI_TEXT_LOOKBACK = 5


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
            "无法确认目标文件的内容来源或保护状态，本次破坏性操作未执行。"
            "局部调整可用 edit_file；需要删除或整份替换时，请作者在文件菜单或编辑器中操作。",
        )



class PreWriteHistoryUnavailableError(AuthorContentProtectionError):
    """AI 写入前没能确认原稿已在历史里（备份或撤销锚点读取失败）：不提交这次写入。"""

    error_type = PRE_WRITE_HISTORY_UNAVAILABLE_ERROR

    def __init__(self, file_id: str, *, action: str) -> None:
        self.file_id = file_id
        self.title = ""
        self.action = action
        RuntimeError.__init__(
            self,
            "原文没能先存进历史版本，这次改动没有写入，原文保持不变；可以稍后重试。",
        )

def _latest_text_owner_is_author(session: Any, file: Any) -> bool | None:
    """True: author. False: blank body or confirmed AI text. None: non-empty with no history.

    A content/version mismatch is protected because it represents a write newer than the latest
    recorded AI state.
    ``ai_edit`` versions count as AI-written, except the editor's review save
    (``user/ai_edit/"AI edit (reviewed)"``) right after an AI-written version: the author
    reviewed that AI change and saved their own result.
    A user restore or such a review save whose text byte-equals the newest matching earlier
    version, when that version is AI-written (undo of an AI rewrite, reject-all in review), is
    AI text again; see ``_returns_to_ai_text``.
    """
    content = getattr(file, "content", None) or ""
    if not content.strip():
        return False

    from services.features.file_version_service import FileVersionService

    service = FileVersionService()
    latest = service.get_latest_version(session, file.id)
    if latest is None:
        return None
    latest_content = service._get_contents_for_versions(session, {file.id: latest})[file.id]
    if latest_content != content:
        return True
    if latest.change_source == _AI_CHANGE_SOURCE:
        return False
    if latest.change_type == _AI_EDIT_CHANGE_TYPE:
        if not _is_review_save_after_ai_write(session, service, latest):
            return False
    elif not (latest.change_type == _RESTORE_CHANGE_TYPE and latest.change_source == _USER_CHANGE_SOURCE):
        return True
    return not _returns_to_ai_text(session, service, latest, content)


def _is_review_save_after_ai_write(session: Any, service: Any, latest: Any) -> bool:
    from agent.tools.author_changes import AI, AUTHOR, version_actor

    if version_actor(latest) != AUTHOR or latest.version_number <= 1:
        return False
    previous = service.get_version_by_number(session, latest.file_id, latest.version_number - 1)
    return previous is not None and version_actor(previous) == AI


def _returns_to_ai_text(session: Any, service: Any, latest: Any, content: str) -> bool:
    """The newest of the last ``_AI_TEXT_LOOKBACK`` earlier versions with exactly this text is AI-written.

    A deterministic comparison of stored version bodies, not a reading of intent: restoring an
    author version, a mixed review result, or text no recent version holds stays the author's.
    """
    from agent.tools.author_changes import AI, version_actor

    earlier = [
        version
        for version in service.get_versions(
            session, latest.file_id, limit=_AI_TEXT_LOOKBACK + 1, include_auto_save=True
        )
        if version.version_number < latest.version_number
    ][:_AI_TEXT_LOOKBACK]
    for version in earlier:
        if service._get_contents_for_versions(session, {latest.file_id: version})[latest.file_id] == content:
            return version_actor(version) == AI
    return False


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
            protected = _latest_text_owner_is_author(session, file)
        except Exception as exc:
            raise AuthorEditProtectionUnavailableError(
                str(getattr(file, "id", "")), action=action
            ) from exc
        if protected is None:
            # 正文来源未知：不认成作者的，也不当作可以破坏。
            raise AuthorEditProtectionUnavailableError(
                str(getattr(file, "id", "")), action=action
            )
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
