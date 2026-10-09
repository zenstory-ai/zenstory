"""
Export service for generating downloadable content.

Provides functions to export project content (drafts) to various formats.
"""
import re

from sqlmodel import Session, select

from models import File
from utils.logger import get_logger, log_with_context
from utils.title_sequence import build_sequence_sort_key

logger = get_logger(__name__)


# Chapter separator for merged TXT export
CHAPTER_SEPARATOR = "\n\n---\n\n"

# Chinese number mapping for chapter sorting
CHINESE_NUMS = {
    '零': 0, '一': 1, '二': 2, '三': 3, '四': 4,
    '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
    '十': 10, '百': 100, '千': 1000
}


def _parse_chinese_number(s: str) -> int:
    """Parse Chinese number string to integer."""
    if not s:
        return 0

    result = 0
    temp = 0
    for char in s:
        if char in CHINESE_NUMS:
            num = CHINESE_NUMS[char]
            if num == 10:
                if temp == 0:
                    temp = 1
                result += temp * 10
                temp = 0
            elif num == 100:
                if temp == 0:
                    temp = 1
                result += temp * 100
                temp = 0
            elif num == 1000:
                if temp == 0:
                    temp = 1
                result += temp * 1000
                temp = 0
            else:
                temp = num
    result += temp
    return result if result > 0 else 0


def _extract_chapter_number(title: str) -> int:
    """
    Extract chapter number from title.

    Supports formats like:
    - 第一章, 第二章 (Chinese numerals)
    - 第1章, 第2章 (Arabic numerals)
    - 1, 2, 3 (Plain numbers at start)
    """
    if not title:
        return 999999  # Put items without chapter number at the end

    # Try Chinese chapter format: 第X章
    chinese_match = re.search(r'第([零一二三四五六七八九十百千]+)章', title)
    if chinese_match:
        return _parse_chinese_number(chinese_match.group(1))

    # Try Arabic number format: 第X章
    arabic_match = re.search(r'第(\d+)章', title)
    if arabic_match:
        return int(arabic_match.group(1))

    # Try just numbers at the start
    num_match = re.match(r'^(\d+)', title)
    if num_match:
        return int(num_match.group(1))

    return 999999  # Put items without chapter number at the end


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

    # Sort with the same effective-order semantics as the file-tree endpoint:
    # - `order` is the explicit/stored ordering
    # - Historically many agent-created files had `order == 0` even when the title
    #   clearly contains a chapter/episode number (e.g. 第2集 / 第一章).
    #   In that case, treat `order == 0` as "unset" and fall back to parsing the
    #   sequence from the title for a more intuitive export ordering.
    # - For chapter-like draft/outline/script files, title sequence is canonical.
    # - Also recover from obvious order typos like 580 for "第58章".
    def sort_key(f: File):
        effective_order, seq_num = build_sequence_sort_key(
            getattr(f, "order", 0),
            title=getattr(f, "title", ""),
            metadata=f.get_metadata(),
            file_type=getattr(f, "file_type", None),
        )
        created_at = getattr(f, "created_at", None)
        created_key = created_at.isoformat() if created_at else ""
        return (effective_order, seq_num, created_key)

    drafts.sort(key=sort_key)

    return drafts


def export_drafts_to_txt(session: Session, project_id: str) -> str:
    """
    Export all drafts for a project as merged TXT content.

    Format:
    ```
    第一章 标题

    正文内容...

    ---

    第二章 标题

    正文内容...
    ```

    Args:
        session: Database session
        project_id: Project ID to export

    Returns:
        Merged text content with all chapters, or empty string if no drafts
    """
    log_with_context(
        logger,
        20,  # INFO
        "export_drafts_to_txt called",
        project_id=project_id,
    )

    drafts = get_sorted_drafts(session, project_id)

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

    chapters = []
    for draft in drafts:
        # Each chapter: title + blank line + content
        chapter_text = f"{draft.title}\n\n{_trim_chapter_body(draft.content or '')}"
        chapters.append(chapter_text)

    return CHAPTER_SEPARATOR.join(chapters)
