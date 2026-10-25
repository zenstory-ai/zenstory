"""Source-aware project-note storage.

Notes are author content: entries without a header (saved by the author in the AI 记忆 panel,
recorded before sources existed, or appended by the chat AI when the author asked it to remember
something) and the 【作者确认】 section.  For application-chat tools notes are append-only: stored
text is kept byte-for-byte (never deleted, rewritten, reordered or promoted) and entries not already
present are appended as plain notes; the chat card shows them with an undo.  Direct non-chat
callers keep the whole-field editing behavior.

Notes saved before 2026-10-25 may still contain an 【AI 提议·待作者确认】 block (a removed
feature).  Every read treats it as absent and no write keeps it, so it disappears on the next save.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from config.project_status import PROJECT_STATUS_MAX_LENGTHS

CONFIRMED_HEADER = "【作者确认】"
UNLABELED_HEADER = "【未标来源】"

# No 「；」 in the explanations: a header copied back flattened onto one line must not split into
# fragments that look like entries.
CONFIRMED_EXPLANATION = "作者确认过的约定，你不能修改或删除，作者可在 AI 记忆里编辑"
UNLABELED_EXPLANATION = (
    "作者在记忆面板保存、在对话里让你记下或历史遗留的约定，你不能修改或删除，作者可在 AI 记忆里编辑"
)

_HEADER_PREFIX = r"^\s*【\s*(?:备注\s*[·・]\s*)?"
_HEADER_SUFFIX = r"\s*】\s*(?:[（(].*[)）])?\s*$"
_CONFIRMED_HEADER_RE = re.compile(_HEADER_PREFIX + r"作者(?:已)?确认" + _HEADER_SUFFIX)
_UNLABELED_HEADER_RE = re.compile(_HEADER_PREFIX + r"未标来源" + _HEADER_SUFFIX)
# Legacy 【AI 提议·待作者确认】 block and the context-only 「作者已忽略的 AI 提议」 shape: the header and
# everything under it until the next author header are not notes.
_LEGACY_PROPOSALS_HEADER_RE = re.compile(
    _HEADER_PREFIX + r"(?:作者已忽略的\s*)?AI\s*提议[^】]*" + _HEADER_SUFFIX, re.I
)
_ENTRY_SPLIT_RE = re.compile(r"[；;]")
_BULLET_RE = re.compile(r"^\s*(?:[-*•·]+|\d+[.、)）]|[（(]\d+[)）])\s*")
_SELF_LABEL_RE = re.compile(
    r"[【\[（(]\s*(?:作者)?(?:已)?(?:确认|定|拍板)(?:设定|的?事实)?\s*[】\]）)]"
)
_IGNORABLE_RE = re.compile(r"[\s　，,。.！!？?；;：:、\"'“”‘’「」『』《》〈〉（）()【】\[\]…—\-~～·]+")
_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}
_CN_NUMERAL_RUN_RE = re.compile(r"[零〇一二两三四五六七八九十百千万]+")


@dataclass
class ProjectNotesSections:
    unlabeled: list[str] = field(default_factory=list)
    confirmed: list[str] = field(default_factory=list)


@dataclass
class ProjectNotesMerge:
    text: str
    # Entries appended by this chat call, exactly as stored (one per line), in order.
    added: list[str] = field(default_factory=list)
    # Appending would exceed the notes limit, so nothing was appended.
    over_limit: bool = False


def _is_author_header(line: str) -> bool:
    return bool(_CONFIRMED_HEADER_RE.match(line) or _UNLABELED_HEADER_RE.match(line))


def without_legacy_proposals(notes: str | None) -> str | None:
    """``notes`` without any legacy AI-proposal block; otherwise byte-for-byte unchanged.

    Used on every read (API responses, agent context) and every write, so the block is never shown,
    counted or written back.  Notes without such a block are returned as given.
    """
    if not notes:
        return notes
    kept: list[str] = []
    skipping = dropped = False
    for line in notes.splitlines(keepends=True):
        if _LEGACY_PROPOSALS_HEADER_RE.match(line):
            skipping = dropped = True
            continue
        if skipping and _is_author_header(line):
            skipping = False
        if not skipping:
            kept.append(line)
    return "".join(kept).strip() if dropped else notes


def split_project_notes(notes: str | None) -> ProjectNotesSections:
    sections = ProjectNotesSections()
    current = sections.unlabeled
    for raw_line in (without_legacy_proposals(notes) or "").splitlines():
        if _CONFIRMED_HEADER_RE.match(raw_line):
            current = sections.confirmed
            continue
        if _UNLABELED_HEADER_RE.match(raw_line):
            current = sections.unlabeled
            continue
        line = raw_line.rstrip()
        if line.strip():
            current.append(line)
    return sections


def render_project_notes(notes: str | None) -> list[str]:
    sections = split_project_notes(notes)
    lines: list[str] = []
    for header, explanation, entries in (
        (CONFIRMED_HEADER, CONFIRMED_EXPLANATION, sections.confirmed),
        (UNLABELED_HEADER, UNLABELED_EXPLANATION, sections.unlabeled),
    ):
        if entries:
            lines.append(f"{header.replace('【', '【备注·', 1)}（{explanation}）")
            lines.extend(entries)
            lines.append("")
    return lines


def _cn_numeral_value(run: str) -> str:
    if "万" in run or any(ch in _CN_UNITS for ch in run):
        total = section = number = 0
        for ch in run:
            if ch in _CN_DIGITS:
                number = _CN_DIGITS[ch]
            elif ch in _CN_UNITS:
                section += (number or 1) * _CN_UNITS[ch]
                number = 0
            else:
                total += (section + number) * 10000
                section = number = 0
        return str(total + section + number)
    return "".join(str(_CN_DIGITS[ch]) for ch in run)


def _normalize(text: str) -> str:
    stripped = _IGNORABLE_RE.sub("", text or "").lower()
    return _CN_NUMERAL_RUN_RE.sub(lambda match: _cn_numeral_value(match.group(0)), stripped)


def _entry_key(entry: str) -> str:
    return _normalize(_SELF_LABEL_RE.sub("", _BULLET_RE.sub("", entry)))


def _is_section_header(text: str) -> bool:
    return _is_author_header(text) or bool(_LEGACY_PROPOSALS_HEADER_RE.match(text))


def _entries(lines: list[str]) -> list[str]:
    # A fragment shaped like a section header (e.g. a header flattened onto one line with 「；」) is
    # not an entry: stored on its own line it would start a section and relabel what follows.
    return [
        part.strip()
        for line in lines
        for part in _ENTRY_SPLIT_RE.split(line)
        if _entry_key(part) and not _is_section_header(part)
    ]


def _ends_in_confirmed_section(notes: str) -> bool:
    confirmed = False
    for line in notes.splitlines():
        if _CONFIRMED_HEADER_RE.match(line):
            confirmed = True
        elif _UNLABELED_HEADER_RE.match(line):
            confirmed = False
    return confirmed


def merge_project_notes(existing: str | None, notes: str, *, from_chat: bool) -> ProjectNotesMerge:
    """Apply an update_project ``notes`` argument.

    ``from_chat`` (an application-chat model call): append-only.  The stored author content is kept
    byte-for-byte; every entry of ``notes`` not already there (by entry key, so a self-label such
    as 「【已定】」 changes nothing) is appended on its own line as a plain (unlabeled) note, without
    bullets or self-labels.  Section-header-shaped lines or 「；」 fragments (a copied-back rendered
    header, 【作者确认】, a legacy 【AI 提议】 header) are never appended, so appended text can never
    open a confirmed or hidden section.  If that would exceed the notes
    limit, nothing is appended.

    Otherwise (a direct non-chat caller) ``notes`` replaces the whole field.  Either way no legacy
    proposal block survives.
    """
    if not from_chat:
        return ProjectNotesMerge(text=without_legacy_proposals(notes.strip()) or "")
    base = without_legacy_proposals(existing) or ""
    current = split_project_notes(base)
    known = {_entry_key(entry) for entry in _entries([*current.unlabeled, *current.confirmed])}
    incoming = split_project_notes(notes)
    added: list[str] = []
    for entry in _entries([*incoming.unlabeled, *incoming.confirmed]):
        text = _SELF_LABEL_RE.sub("", _BULLET_RE.sub("", entry)).strip()
        key = _entry_key(text)
        if key and key not in known:
            known.add(key)
            added.append(text)
    if not added:
        return ProjectNotesMerge(text=base)
    tail = "\n".join(added)
    if _ends_in_confirmed_section(base):
        tail = f"{UNLABELED_HEADER}\n{tail}"
    separator = "" if not base.strip() or base.endswith("\n") else "\n"
    text = f"{base}{separator}{tail}" if base.strip() else tail
    if len(text.strip()) > PROJECT_STATUS_MAX_LENGTHS["notes"]:
        return ProjectNotesMerge(text=base, over_limit=True)
    return ProjectNotesMerge(text=text, added=added)
