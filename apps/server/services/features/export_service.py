"""
Export service for generating downloadable content.

Provides functions to export project content (drafts) to various formats.
"""
import re
from collections.abc import Iterator

from sqlmodel import Session, select

from models import File
from utils.logger import get_logger, log_with_context
from utils.title_sequence import build_sequence_sort_key

logger = get_logger(__name__)


# Chapter separator for merged TXT export
CHAPTER_SEPARATOR = "\n\n---\n\n"

_LEADING_BLANK_LINES = re.compile(r"^(?:[ \t\u3000]*\r?\n)+")


def _trim_chapter_body(content: str) -> str:
    """去掉正文开头的空行和末尾空白，保留首段的全角空格（U+3000）缩进。

    str.strip() 会把 U+3000 当空白一起削掉，导出的每章首段就顶格了，
    和编辑器里看到的排版不一致。
    """
    return _LEADING_BLANK_LINES.sub("", content).rstrip()


def get_sorted_drafts(session: Session, project_id: str) -> list[File]:
    """
    Get all draft files for a project, sorted by chapter order.

    NOTE: For screenplay projects, the primary writing artifacts are stored as
    `file_type="script"`. Some legacy data may still use `draft`. The export
    endpoint intentionally includes both types so users can download complete
    content even when file types were mixed during migrations.

    Sorting priority:
    1. order field (explicit ordering)
    2. Chapter number extracted from title
    3. Creation date

    Args:
        session: Database session
        project_id: Project ID to get drafts from

    Returns:
        List of File objects sorted by chapter order
    """
    # Get all exportable writing files for the project.
    # - Novel/short story: drafts
    # - Screenplay/mini-drama: scripts (plus legacy drafts)
    exportable_types = ("draft", "script")
    query = select(File).where(
        File.project_id == project_id,
        File.file_type.in_(exportable_types),  # type: ignore[arg-type]
        File.is_deleted.is_(False)
    )

    drafts = list(session.exec(query).all())
    _sort_by_sequence(drafts)
    return drafts


def _sort_by_sequence(files: list[File]) -> None:
    """Sort in place by effective order, title sequence, then creation time."""
    # Sort with the same effective-order semantics as the file-tree endpoint:
    # - `order` is the explicit/stored ordering
    # - Historically many agent-created files had `order == 0` even when the title
    #   clearly contains a chapter/episode number (e.g. 第2集 / 第一章).
    #   In that case, treat `order == 0` as "unset" and fall back to parsing the
    #   sequence from the title for a more intuitive export ordering.
    # - Explicit nonzero order wins; compatible metadata precedes leading titles.
    def sort_key(f: File):
        effective_order, seq_num = build_sequence_sort_key(
            f.order,
            title=f.title,
            metadata=f.get_metadata(),
            file_type=f.file_type,
        )
        created_key = f.created_at.isoformat() if f.created_at else ""
        return (effective_order, seq_num, created_key)

    files.sort(key=sort_key)


def _file_parts(files: list[File]) -> Iterator[str]:
    for index, file in enumerate(files):
        if index:
            yield CHAPTER_SEPARATOR
        yield f"{file.title}\n\n"
        yield _trim_chapter_body(file.content or "")


def export_drafts_to_txt(
    session: Session,
    project_id: str,
    *,
    title: str | None = None,
) -> str:
    """
    Export all drafts for a project as merged TXT content.

    Format:
    ```
    作品名            (when ``title`` is given)

    第一章 标题

    正文内容...

    ---

    第二章 标题

    正文内容...
    ```

    Args:
        session: Database session
        project_id: Project ID to export
        title: Work title printed on the first line

    Returns:
        Merged text content, or empty string when there is nothing to export
    """
    log_with_context(
        logger,
        20,  # INFO
        "export_drafts_to_txt called",
        project_id=project_id,
    )

    # The first-work starter is an editing entry point, not an unwritten
    # chapter in the deliverable. Keep ordinary empty files and every written
    # starter; this affects only export and never deletes the source file.
    drafts = [
        draft for draft in get_sorted_drafts(session, project_id)
        if (draft.content or "").strip()
        or draft.get_metadata().get("creation_source") != "first_work"
    ]

    if not drafts:
        log_with_context(
            logger,
            20,  # INFO
            "export_drafts_to_txt: No drafts found",
            project_id=project_id,
        )
        return ""

    log_with_context(
        logger,
        20,  # INFO
        "export_drafts_to_txt completed",
        project_id=project_id,
        draft_count=len(drafts),
        content_length=sum(len(d.title or "") + len(d.content or "") for d in drafts),
    )

    # Join once: chapter bodies can be large, so avoid formatting a copy of
    # each body and then copying the full work again to add headings/title.
    clean_title = (title or "").strip()
    parts = [clean_title, "\n\n"] if clean_title else []
    parts.extend(_file_parts(drafts))

    return "".join(parts)
