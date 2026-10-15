"""Source-aware project-note storage.

Application-chat tools may manage only the AI-proposal section.  Existing unlabeled and
author-confirmed entries are durable author facts: model output cannot omit, rewrite, clear, or
promote them.  Direct human APIs call the merge without author-message context and retain the
legacy whole-field editing behavior.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CONFIRMED_HEADER = "【作者确认】"
PROPOSALS_HEADER = "【AI 提议·待作者确认】"
UNLABELED_HEADER = "【未标来源】"

CONFIRMED_EXPLANATION = "作者确认过的约定；应用内 AI 不能修改或删除"
UNLABELED_EXPLANATION = "作者在记忆面板保存或历史遗留的约定；应用内 AI 不能修改或删除"
PROPOSALS_EXPLANATION = "尚未存为作者约定；本轮按作者要求使用，长期采用请在 AI 记忆中采纳"

_HEADER_PREFIX = r"^\s*【\s*(?:备注\s*[·・]\s*)?"
_HEADER_SUFFIX = r"\s*】\s*(?:[（(].*[)）])?\s*$"
_CONFIRMED_HEADER_RE = re.compile(_HEADER_PREFIX + r"作者(?:已)?确认" + _HEADER_SUFFIX)
_PROPOSALS_HEADER_RE = re.compile(_HEADER_PREFIX + r"AI\s*提议[^】]*" + _HEADER_SUFFIX, re.I)
_UNLABELED_HEADER_RE = re.compile(_HEADER_PREFIX + r"未标来源" + _HEADER_SUFFIX)
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
    proposals: list[str] = field(default_factory=list)


@dataclass
class ProjectNotesMerge:
    text: str
    downgraded: list[str] = field(default_factory=list)
    confirmed_added: list[str] = field(default_factory=list)


def split_project_notes(notes: str | None) -> ProjectNotesSections:
    sections = ProjectNotesSections()
    current = sections.unlabeled
    for raw_line in (notes or "").splitlines():
        if _CONFIRMED_HEADER_RE.match(raw_line):
            current = sections.confirmed
            continue
        if _PROPOSALS_HEADER_RE.match(raw_line):
            current = sections.proposals
            continue
        if _UNLABELED_HEADER_RE.match(raw_line):
            current = sections.unlabeled
            continue
        line = raw_line.rstrip()
        if line.strip():
            current.append(line)
    return sections


def compose_project_notes(sections: ProjectNotesSections) -> str:
    blocks: list[str] = []
    if sections.unlabeled:
        blocks.append("\n".join(sections.unlabeled))
    if sections.confirmed:
        blocks.append("\n".join([CONFIRMED_HEADER, *sections.confirmed]))
    if sections.proposals:
        blocks.append("\n".join([PROPOSALS_HEADER, *sections.proposals]))
    return "\n\n".join(blocks)


def render_project_notes(notes: str | None) -> list[str]:
    sections = split_project_notes(notes)
    lines: list[str] = []
    for header, explanation, entries in (
        (CONFIRMED_HEADER, CONFIRMED_EXPLANATION, sections.confirmed),
        (UNLABELED_HEADER, UNLABELED_EXPLANATION, sections.unlabeled),
        (PROPOSALS_HEADER, PROPOSALS_EXPLANATION, sections.proposals),
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


def _split_line(line: str) -> tuple[str, list[str]]:
    separator = "；" if "；" in line or ";" not in line else ";"
    parts = [part.strip() for part in _ENTRY_SPLIT_RE.split(line)]
    return separator, [part for part in parts if _entry_key(part)]


def _keys_of(lines: list[str]) -> set[str]:
    return {_entry_key(entry) for line in lines for entry in _split_line(line)[1]}


def _flatten_unique(lines: list[str], *, excluded: set[str] | None = None) -> list[str]:
    excluded = excluded or set()
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        separator, entries = _split_line(line)
        kept: list[str] = []
        for entry in entries:
            key = _entry_key(entry)
            if not key or key in excluded or key in seen:
                continue
            seen.add(key)
            kept.append(entry)
        if kept:
            result.append(separator.join(kept))
    return result


def merge_project_notes(
    existing: str | None,
    *,
    notes: str | None,
    proposals: str | None,
    author_quotes: list[str] | None,
    author_messages: list[str] | None,
    previous_proposals: list[tuple[int, str]] | None = None,
) -> ProjectNotesMerge:
    """Merge note updates without deriving authority from prose.

    ``author_messages is not None`` identifies an application-chat/model call.  In that path all
    existing unlabeled and confirmed entries are preserved byte-for-byte.  Every new or changed
    entry from ``notes`` is an AI proposal.  ``author_quotes`` and numbered prior replies are
    intentionally ignored.  Only an explicit ``proposals`` argument replaces/clears the proposal
    section.

    With no author-message context, the caller is a direct/non-chat path and the legacy whole-field
    editing contract is retained.
    """
    del author_quotes, previous_proposals
    current = split_project_notes(existing)

    if author_messages is None:
        if notes is not None and not notes.strip() and proposals is None:
            return ProjectNotesMerge(text="")
        result = split_project_notes(notes) if notes is not None else ProjectNotesSections(
            unlabeled=list(current.unlabeled), confirmed=list(current.confirmed)
        )
        if proposals is None:
            if notes is None or not result.proposals:
                result.proposals = list(current.proposals)
        else:
            given = split_project_notes(proposals)
            result.proposals = _flatten_unique([*given.unlabeled, *given.confirmed, *given.proposals])
        return ProjectNotesMerge(text=compose_project_notes(result))

    authoritative_keys = _keys_of(current.unlabeled) | _keys_of(current.confirmed)
    incoming_note_lines: list[str] = []
    if notes is not None:
        incoming = split_project_notes(notes)
        incoming_note_lines = [*incoming.unlabeled, *incoming.confirmed]
        # When the dedicated proposals argument is absent, accept a copied/rendered proposal
        # section for backward compatibility.  When it is present (including an empty string),
        # that explicit field owns replacement/clearing and the copy inside notes cannot re-add it.
        if proposals is None:
            incoming_note_lines.extend(incoming.proposals)
    proposed_from_notes = _flatten_unique(incoming_note_lines, excluded=authoritative_keys)

    if proposals is None:
        proposal_lines = [*current.proposals, *proposed_from_notes]
    else:
        given = split_project_notes(proposals)
        proposal_lines = [*given.unlabeled, *given.confirmed, *given.proposals, *proposed_from_notes]

    result = ProjectNotesSections(
        unlabeled=list(current.unlabeled),
        confirmed=list(current.confirmed),
        proposals=_flatten_unique(proposal_lines, excluded=authoritative_keys),
    )
    current_proposal_keys = _keys_of(current.proposals)
    downgraded = [
        entry
        for line in proposed_from_notes
        for entry in _split_line(line)[1]
        if _entry_key(entry) not in current_proposal_keys
    ]
    return ProjectNotesMerge(text=compose_project_notes(result), downgraded=downgraded)
