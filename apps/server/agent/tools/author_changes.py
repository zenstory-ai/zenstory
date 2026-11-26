"""作者在 AI 写过之后是否手动修改过文件的轻量检测与提示渲染。

原 40 版本 ``SequenceMatcher`` 逐行回放（line_blame / touched_author_text）在每轮上下文组装与
每次读写时触发大量 SQL 往返与 CPU 开销。当前方案采用轻量元数据检测：
- 保留 ``AI`` / ``AUTHOR`` / ``ORIGINAL`` 常量与 ``version_actor`` 供跨轮快照核验使用；
- 焦点/附加文件已在 ``<turn_context>`` 给出最新全文；
- 本会话此前由 AI 写过、但不在本轮全文上下文中的文件，若在最近一次 AI 版本之后有作者手动修改
  （由 ``SessionLoader.author_edits_for_files`` 单次批量元数据查询判定），在 ``<author_edits>``
  里各给 1 行简明提醒，要求引用或续写前用 ``query_files(id=...)`` 读取最新版。
"""

from __future__ import annotations

from typing import Any

AI = "ai"
AUTHOR = "author"
ORIGINAL = "original"

MAX_SIDE_CHARS = 160
CONTEXT_CHARS = 30
_REVIEWED_AI_EDIT_SUMMARY = "AI edit (reviewed)"

AUTHOR_EDITS_HEADER = (
    "以下本会话此前写过的文件自你上次写入后已被作者手动修改（未在本轮上下文给出全文）。"
    "引用、续写或修改前请先用 read_files(ids=[...]) 读取最新版，以作者的写法为准。"
)
AUTHOR_EDITS_HEADER_EN = (
    "The following files you wrote earlier in this session were manually edited by the author "
    "(their full text is not attached in this turn). Read the latest version with read_files(ids=[...]) "
    "before quoting, continuing, or editing, and preserve the author's changes."
)

FAILED_LINE = "- 本会话部分文件的作者手改状态未能核对（不完整）：引用或修改前请用 read_files 读取最新版。"
FAILED_LINE_EN = (
    "- Could not verify manual author edits for some session files (incomplete): "
    "read the latest version with read_files before quoting or editing."
)


def version_actor(version: Any) -> str:
    """一个版本是谁写的：AI / 作者 / 原稿。"""
    change_type = str(getattr(version, "change_type", "") or "")
    source = str(getattr(version, "change_source", "") or "")
    summary = str(getattr(version, "change_summary", "") or "")
    if change_type == "create":
        return AI if source == "ai" else ORIGINAL
    if source == "ai":
        return AI
    if change_type == "ai_edit":
        return AUTHOR if summary.startswith(_REVIEWED_AI_EDIT_SUMMARY) else AI
    if change_type == "restore" and source == "system":
        return ORIGINAL
    return AUTHOR


def render_manual_edit_alerts(
    edited_files: list[tuple[str, str]],
    *,
    force_en: bool = False,
    unchecked_titles: list[str] | None = None,
    failed: bool = False,
) -> str:
    """渲染轻量 ``<author_edits>`` 手改提醒块：每个被作者手改的非焦点文件 1 行提示。"""
    if not edited_files and not unchecked_titles and not failed:
        return ""
    header = AUTHOR_EDITS_HEADER_EN if force_en else AUTHOR_EDITS_HEADER
    out = ["<author_edits>", header]
    if failed:
        out.append(FAILED_LINE_EN if force_en else FAILED_LINE)
    for file_id, title in edited_files:
        clean_title = (title or "").strip() or ("(untitled)" if force_en else "（未命名）")
        if force_en:
            out.append(
                f'- "{clean_title}" (id: {file_id}): manually edited by the author since your last write; '
                "read the latest version before quoting or continuing."
            )
        else:
            out.append(
                f"- 《{clean_title}》(id: {file_id}): 自你上次写入后已被作者手动修改，引用或续写前请先读取最新版。"
            )
    if unchecked_titles:
        shown = unchecked_titles[:8]
        if force_en:
            out.append(
                f"- {len(unchecked_titles)} additional file(s) were not checked for manual edits "
                f"({', '.join(shown)}); read with read_files before editing."
            )
        else:
            out.append(
                f"- 另有 {len(unchecked_titles)} 个文件没有核对手改（不完整）：{'、'.join(shown)}；"
                "改它们之前用 read_files 按 id 读一次。"
            )
    out.append("</author_edits>")
    return "\n".join(out)


def _flatten(text: str) -> str:
    return " / ".join(part.strip() for part in (text or "").split("\n") if part.strip())


def _cut(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    head = limit // 2
    return f"{text[:head]}…{text[-(limit - head - 1) :]}", True


def pair_clip(before: str, after: str, limit: int = MAX_SIDE_CHARS) -> tuple[str, str, bool]:
    """改动前后两边，各不超过 limit 字：只省掉两边相同的头尾，留下改动和前后一点上下文。

    供 ``file_ops/edit.py`` 的 ``StaleContentError`` 展示并发冲突差异使用。
    """
    left, right = _flatten(before), _flatten(after)
    if len(left) <= limit and len(right) <= limit:
        return left, right, False
    if not left or not right:
        clipped_left, cut_left = _cut(left, limit)
        clipped_right, cut_right = _cut(right, limit)
        return clipped_left, clipped_right, cut_left or cut_right
    prefix = 0
    while prefix < min(len(left), len(right)) and left[prefix] == right[prefix]:
        prefix += 1
    suffix = 0
    while suffix < min(len(left), len(right)) - prefix and left[-1 - suffix] == right[-1 - suffix]:
        suffix += 1
    start = max(0, prefix - CONTEXT_CHARS)

    def window(text: str) -> tuple[str, bool]:
        end = min(len(text), len(text) - suffix + CONTEXT_CHARS)
        core = text[start:end]
        core, cut = _cut(core, limit - 2)
        return f"{'…' if start > 0 else ''}{core}{'…' if end < len(text) else ''}", cut

    clipped_left, cut_left = window(left)
    clipped_right, cut_right = window(right)
    return clipped_left, clipped_right, cut_left or cut_right
