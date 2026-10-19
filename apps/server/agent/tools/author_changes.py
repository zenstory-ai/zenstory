"""作者在 AI 写过之后亲手改了什么：从版本历史算出的净差异（2026-10-12 生产审计 drama）。

以前作者手改过的文件，作者这一轮没「点名」就整份拒绝 edit_file，靠字符串判断作者是不是在授权。
作者说「用第二个，直接写进第2集结尾，我那句台词保持我改的版本」仍被拒，要照念口令才写入。现在不判断
授权：把作者具体改了哪几处给模型看，由模型保留作者的写法、或按作者这一轮的新要求改；工程只核对版本
和内容事实（edit_file 的陈旧写入检查、改到了作者写的段落时如实告诉模型和作者）。

做法：在最近 MAX_VERSION_WINDOW 个版本的窗口里按行（网文一行一段）回放版本链，逐段落记来源：

- AI：change_source=ai；外部 Agent API 写入的 ai_edit；去AI味审阅后保存的「AI polish (reviewed)」
  （差异全是作者接受的 AI 润色）。
- 作者：作者编辑器保存 / 恢复（source=user，含 AI 写入后作者审阅保存的「AI edit (reviewed)」——那里的
  差异是作者拒掉、换回自己写法的段落）；系统在 AI 写入、恢复版本、快照前替作者存下的备份（edit/system，
  内容是作者没进版本的手改）；最新版本之后还没进版本的正文（作者版本额度用满时就是这样保存的）。
- 原稿：create（导入、新建、上传、快照基线）、快照恢复，以及第一次有 AI 写入之前作者写的字（那是作者
  的原稿，不是「AI 写过之后改的」）。

某一段落由作者写入时，记下它替换掉的文字（若被替换的本身是作者写的，沿用那段更早的原文），所以
A→B→C 只显示 A→C；改回原文（A→B→A）不算手改；AI 只在作者那一段末尾续写或段首补字时那一段仍算作者的。
后来 AI 改别处生成的新版本不会把作者早先的替换「吸收」掉。作者删掉的段落按回放记下删在哪一版：之后 AI
只改别处时这条删除照样列出，直到这段文字重新出现在正文里（#268）；edit_file 把记下的已删原文原样放回时，
结果里如实标出来源。整段挪了位置的不算改动。没有任何 AI 版本的文件（作者自己写的、
导入的原稿）不出差异。回放失败、窗口截断、内容过长只显示改动附近时，都明说不完整。
"""

from __future__ import annotations

import difflib
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session, col, or_, select

AI = "ai"
AUTHOR = "author"
ORIGINAL = "original"

# 回放窗口：最近这么多个版本。免费版作者版本最多 10 个，但 AI 和系统版本不限。
MAX_VERSION_WINDOW = 40
# 给模型看的上限：每个文件几处、每处两边各多少字、改动前后各留多少字上下文、整块多少字。
MAX_HUNKS_PER_FILE = 6
MAX_SIDE_CHARS = 160
CONTEXT_CHARS = 30
MAX_BLOCK_CHARS = 3000
# 太长的正文不回放（逐段比对的开销随段落数上升）：明说没算出来。
MAX_BLAME_LINES = 6000
_SENTENCE_END = "。！？!?…」』”’）)】"

_REVIEWED_AI_EDIT_SUMMARY = "AI edit (reviewed)"


class BlameReplayError(RuntimeError):
    """版本链回放失败：算不出作者改了什么（不能当作「没改过」）。"""


@dataclass
class AuthorHunk:
    before: str  # 作者改之前的文字（AI 写的或原稿）；空串 = 作者新加的段落
    after: str  # 作者写下的文字；空串 = 作者删掉了这段
    # 删除：回放时记下的删除所在版本号；None = 还没进版本的正文里删的，或无法定位。
    deleted_in_version: int | None = None


@dataclass
class AuthorDeletion:
    """作者在 AI 写过之后整段删掉的一段（AI 写的或原稿），以及删在哪一版。"""

    text: str
    version_number: int | None


@dataclass
class LineBlame:
    lines: list[str]
    labels: list[str]
    origins: list[str]
    has_ai: bool
    # 窗口从第 1 个版本开始时为 True；否则更早的手改可能没算进来。
    complete: bool
    latest_ai_content: str | None = None
    # 作者删掉、之后没有再出现的段落（按删除先后；同一段文字只记最近一次）。
    deletions: list[AuthorDeletion] = field(default_factory=list)


@dataclass
class AuthorChanges:
    hunks: list[AuthorHunk] = field(default_factory=list)
    complete: bool = True
    # 回放失败：不知道作者改了什么。
    failed: bool = False


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


def _split(content: str) -> list[str]:
    return (content or "").split("\n")


def _matcher(old_lines: list[str], new_lines: list[str]) -> difflib.SequenceMatcher:
    # 空行（段落分隔）当作 junk：它们到处都是，不用来对齐，免得比对开销接近平方。
    return difflib.SequenceMatcher(lambda line: not line.strip(), old_lines, new_lines, autojunk=False)


_PADDING = " \t\u3000"


def _extends(new: str, old: str) -> bool:
    """new 只是在 old 的末尾续写、或在开头补了一句（在句子边界上接，忽略首尾空白和全角缩进）。

    「行。」→「不行。」、「好」→「好不容易才来」不算续写：那是改了作者的字。
    """
    old_core, new_core = old.strip(_PADDING), new.strip(_PADDING)
    if not old_core or new_core == old_core:
        return False
    if new_core.startswith(old_core):
        return old_core[-1] in _SENTENCE_END
    if new_core.endswith(old_core):
        prefix = new_core[: len(new_core) - len(old_core)].rstrip(_PADDING)
        return bool(prefix) and prefix[-1] in _SENTENCE_END
    return False


def _advance(
    blame: LineBlame,
    new_lines: list[str],
    actor: str,
    *,
    non_author: str,
    version_number: int | None = None,
) -> None:
    """把 blame 从上一版推进到 new_lines：没变的段落沿用原来的来源，改过的记成 actor。

    non_author：作者把段落改回原文时，那一段回到的来源（有 AI 写过时为 AI，否则为原稿）。
    作者整段删掉 AI 写的或原稿的段落时记进 blame.deletions；任何人把同一段文字放回来，这条删除就不再列出。
    """
    matcher = _matcher(blame.lines, new_lines)
    labels: list[str] = []
    origins: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            labels.extend(blame.labels[i1:i2])
            origins.extend(blame.origins[i1:i2])
            continue
        if tag == "delete":
            if actor == AUTHOR:
                for index in range(i1, i2):
                    text = blame.lines[index].strip()
                    if text and blame.labels[index] != AUTHOR:
                        blame.deletions = [item for item in blame.deletions if item.text != text]
                        blame.deletions.append(AuthorDeletion(text, version_number))
            continue
        block = new_lines[j1:j2]
        if tag == "replace" and actor == AI and i2 - i1 == j2 - j1:
            # 逐行对齐的替换块里，AI 只在作者那一段续写 / 补字的行：作者的手改还在，仍算作者的。
            kept = [
                blame.labels[i1 + k] == AUTHOR and _extends(block[k], blame.lines[i1 + k]) for k in range(len(block))
            ]
            if any(kept):
                for k, keep in enumerate(kept):
                    if keep:
                        labels.append(AUTHOR)
                        origins.append(blame.origins[i1 + k])
                    else:
                        labels.append(actor)
                        origins.append("")
                continue
        replaced = ""
        if tag == "replace":
            # 被替换的段落若本身是作者写的，沿用它更早的原文（A→B→C 记成 A→C）。
            parts = [blame.origins[i] if blame.labels[i] == AUTHOR else blame.lines[i] for i in range(i1, i2)]
            replaced = "\n".join(part for part in parts if part.strip())
        # 原文挂在这一块第一个非空段落上（作者常在改写的段落前加空行分段）。
        anchor = next((offset for offset, line in enumerate(block) if line.strip()), 0)
        non_blank = [line for line in block if line.strip()]
        reverted = actor == AUTHOR and bool(replaced) and "\n".join(non_blank) == replaced
        for offset, _ in enumerate(block):
            if reverted:
                labels.append(non_author)
                origins.append("")
            else:
                labels.append(actor)
                origins.append(replaced if offset == anchor else "")
    present = {line.strip() for line in new_lines if line.strip()}
    blame.deletions = [item for item in blame.deletions if item.text not in present]
    blame.lines = list(new_lines)
    blame.labels = labels
    blame.origins = origins


def _ai_versions_before(session: Session, file_id: str, before_number: int) -> bool:
    """窗口之前有没有 AI 写入（判定口径与 version_actor 一致）。"""
    from models.file_version import FileVersion

    rows = session.exec(
        select(FileVersion)
        .where(
            FileVersion.file_id == file_id,
            FileVersion.version_number < before_number,
            or_(FileVersion.change_source == "ai", FileVersion.change_type == "ai_edit"),
        )
        .order_by(col(FileVersion.version_number).desc())
        .limit(50)
    ).all()
    return any(version_actor(row) == AI for row in rows)


def line_blame(session: Session, file_id: str, current_content: str) -> LineBlame | None:
    """当前正文每一段是谁写的（窗口内的版本链加上没进版本的正文）。

    没有任何版本时返回 None；回放出错时抛 BlameReplayError（调用方不能把它当作「作者没改过」）。
    """
    from models.file_version import FileVersion
    from services.features.file_version_service import FileVersionService

    latest = session.exec(
        select(FileVersion)
        .where(FileVersion.file_id == file_id)
        .order_by(col(FileVersion.version_number).desc())
        .limit(1)
    ).first()
    if latest is None:
        return None
    start = max(1, latest.version_number - MAX_VERSION_WINDOW + 1)
    base_number = session.exec(
        select(FileVersion.version_number)
        .where(
            FileVersion.file_id == file_id,
            col(FileVersion.is_base_version).is_(True),
            FileVersion.version_number <= start,
        )
        .order_by(col(FileVersion.version_number).desc())
        .limit(1)
    ).first()
    chain = session.exec(
        select(FileVersion)
        .where(
            FileVersion.file_id == file_id,
            FileVersion.version_number >= (base_number or 1),
        )
        .order_by(col(FileVersion.version_number))
    ).all()
    if not chain:
        return None

    service = FileVersionService()
    complete = start == 1
    if len(_split(current_content or "")) > MAX_BLAME_LINES:
        raise BlameReplayError("content too long to replay")
    seen_ai = False if complete else _ai_versions_before(session, file_id, start)
    content = ""
    blame: LineBlame | None = None
    latest_ai_content: str | None = None
    previous_actor: str | None = None
    try:
        for version in chain:
            content = version.content if version.is_base_version else service._apply_diff(content, version.content)
            if version.version_number < start:
                previous_actor = version_actor(version)
                continue
            actor = version_actor(version)
            if actor == AUTHOR and str(getattr(version, "change_type", "")) == "ai_edit" and previous_actor != AI:
                # 「AI edit (reviewed)」只在紧跟 AI 写入之后才是作者审阅 AI 修改（拒掉的段落是作者的）；
                # 否则是旧版前端把去AI味审阅也记成了它：差异是作者接受的 AI 润色。
                actor = AI
            previous_actor = actor
            # 第一次有 AI 写入之前作者写的字是作者的原稿，不是「AI 写过之后改的」。
            effective = ORIGINAL if actor == AUTHOR and not seen_ai else actor
            if blame is None:
                lines = _split(content)
                # 窗口不从第 1 版开始时，起点上的段落来源不明，按原稿算（不当作者手改）。
                first = effective if version.version_number == 1 else ORIGINAL
                blame = LineBlame(lines, [first] * len(lines), [""] * len(lines), False, complete)
            else:
                _advance(
                    blame,
                    _split(content),
                    effective,
                    non_author=AI if seen_ai else ORIGINAL,
                    version_number=version.version_number,
                )
            if actor == AI:
                seen_ai = True
                latest_ai_content = content
    except Exception as exc:
        raise BlameReplayError(str(exc)) from exc
    if blame is None:
        return None
    if (current_content or "") != content:
        _advance(
            blame,
            _split(current_content or ""),
            AUTHOR if seen_ai else ORIGINAL,
            non_author=AI if seen_ai else ORIGINAL,
        )
    blame.has_ai = seen_ai
    blame.latest_ai_content = latest_ai_content
    return blame


def author_hunks(blame: LineBlame) -> list[AuthorHunk]:
    """作者写下的段落（连续的合成一处）和自最近一个 AI 版本以来作者删掉的段落。

    改回原文的、只是挪了位置的不列。
    """
    current_counts = Counter(line.strip() for line in blame.lines if line.strip())
    ai_counts = Counter(line.strip() for line in _split(blame.latest_ai_content or "") if line.strip())
    hunks: list[AuthorHunk] = []
    run_before: list[str] = []
    run_after: list[str] = []

    def flush() -> None:
        if run_after:
            before = "\n".join(b for b in run_before if b)
            after = "\n".join(run_after)
            moved_ai_text = not before and all(ai_counts[line.strip()] > 0 for line in run_after)
            if _flatten(before) != _flatten(after) and not moved_ai_text:
                hunks.append(AuthorHunk(before, after))
        run_before.clear()
        run_after.clear()

    for line, label, origin in zip(blame.lines, blame.labels, blame.origins, strict=True):
        if not line.strip():
            continue
        if label == AUTHOR:
            if origin.strip():
                run_before.append(origin)
            run_after.append(line)
        else:
            flush()
    flush()

    if blame.latest_ai_content is not None:
        # 作者删掉的段落：最近 AI 版本里有、现在正文里没有、也不是被作者某一段替换掉的原文。
        replaced_lines = {
            part.strip()
            for origin, label in zip(blame.origins, blame.labels, strict=True)
            if label == AUTHOR
            for part in origin.split("\n")
            if part.strip()
        }
        baseline = _split(blame.latest_ai_content)
        matcher = _matcher(baseline, blame.lines)
        for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
            if tag not in ("delete", "replace"):
                continue
            removed = [
                line
                for line in baseline[i1:i2]
                if line.strip() and current_counts[line.strip()] == 0 and line.strip() not in replaced_lines
            ]
            if removed:
                hunks.append(AuthorHunk("\n".join(removed), "", _deleted_in(blame, removed)))

    # 删在更早的版本、之后 AI 只改了别处的段落：最近 AI 版本里已经没有它，上面比不出来，
    # 按回放记下的删除补上（#268）。
    listed = {line.strip() for hunk in hunks if not hunk.after for line in hunk.before.split("\n")}
    for deletion in blame.deletions:
        if deletion.text in listed or current_counts[deletion.text] > 0:
            continue
        hunks.append(AuthorHunk(deletion.text, "", deletion.version_number))
    return hunks


def _deleted_in(blame: LineBlame, removed: list[str]) -> int | None:
    """一组删掉的段落删在哪一版：回放记下的同一段文字的版本号（都删在同一版时才给）。"""
    versions = {
        deletion.version_number
        for line in removed
        for deletion in blame.deletions
        if deletion.text == line.strip()
    }
    return versions.pop() if len(versions) == 1 else None


def author_changes(session: Session, file: Any) -> AuthorChanges | None:
    """作者在 AI 写过之后改了哪几处。文件从没被 AI 写过、或作者没改过时返回 None。

    回放失败时返回 failed=True、complete=False 的结果，调用方要明说「没算出来」。
    """
    file_id = str(getattr(file, "id", "") or "")
    if not file_id:
        return None
    try:
        blame = line_blame(session, file_id, getattr(file, "content", None) or "")
        if blame is None or not blame.has_ai:
            return None
        hunks = author_hunks(blame)
    except Exception:
        # 回放或查询出错：不知道作者改了什么，不能当作「没改过」。
        return AuthorChanges(hunks=[], complete=False, failed=True)
    if not hunks:
        # 窗口截断时，更早的手改可能在窗口外：明说没核对完，而不是当作没改过。
        return None if blame.complete else AuthorChanges(hunks=[], complete=False)
    return AuthorChanges(hunks=hunks, complete=blame.complete)


def _flatten(text: str) -> str:
    return " / ".join(part.strip() for part in (text or "").split("\n") if part.strip())


def _cut(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    head = limit // 2
    return f"{text[:head]}…{text[-(limit - head - 1) :]}", True


def pair_clip(before: str, after: str, limit: int = MAX_SIDE_CHARS) -> tuple[str, str, bool]:
    """改动前后两边，各不超过 limit 字：只省掉两边相同的头尾，留下改动和前后一点上下文。

    返回 (before, after, 截断了改动本身)。只省掉相同的头尾不算截断（改了什么仍然完整）。
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


def _rendered_pairs(changes: AuthorChanges, max_hunks: int) -> tuple[list[tuple[str, str]], bool]:
    pairs: list[tuple[str, str]] = []
    truncated = False
    for hunk in changes.hunks[:max_hunks]:
        before, after, cut = pair_clip(hunk.before, hunk.after)
        pairs.append((before, after))
        truncated = truncated or cut
    return pairs, truncated


def _deleted_suffix(version: int | None) -> str:
    return f"（第 {version} 版删除）" if version is not None else ""


INCOMPLETE_LONG_LINE = "- 有的地方太长，只显示了改动附近（不完整，需要时用 query_files 读全文）"
FAILED_LINE = "- 这个文件的手改没算出来（不完整）：改之前用 query_files 读全文核对"


def render_hunks(changes: AuthorChanges, *, max_hunks: int = MAX_HUNKS_PER_FILE) -> list[str]:
    """给模型看的几行；列不全时明说。"""
    if changes.failed:
        return [FAILED_LINE]
    pairs, truncated = _rendered_pairs(changes, max_hunks)
    lines: list[str] = []
    for (before, after), version in zip(
        pairs, (hunk.deleted_in_version for hunk in changes.hunks), strict=False
    ):
        if not after:
            lines.append(f"- 删掉了：「{before}」{_deleted_suffix(version)}")
        elif not before:
            lines.append(f"- 新加了：「{after}」")
        else:
            lines.append(f"- 「{before}」→「{after}」")
    omitted = len(changes.hunks) - len(pairs)
    if omitted:
        lines.append(f"- 另有 {omitted} 处没列出（不完整，需要时用 query_files 读全文）")
    if truncated:
        lines.append(INCOMPLETE_LONG_LINE)
    if not changes.complete:
        lines.append(f"- 只看了最近 {MAX_VERSION_WINDOW} 个版本，更早的手改可能没列出")
    return lines


AUTHOR_EDITS_HEADER = (
    "以下是作者在 AI 写过之后亲手改的地方（从版本历史算出的净差异，截至本轮开始）。"
    "这是作者的选择：之后的修改保留作者的写法，不要改回去；作者这一轮明确要改这些地方时，按作者的新要求改；"
    "只有拿不准作者是不是要改它们时才问，不要让作者照着念某句话。"
)


def render_author_edits_block(
    entries: list[tuple[str, str, AuthorChanges]], *, unchecked_titles: list[str] | None = None
) -> str:
    """<author_edits> 块：每个文件一节，整块不超过 MAX_BLOCK_CHARS 字。

    unchecked_titles：超过文件数上限、没有算手改的文件（明说不完整）。
    """
    if not entries and not unchecked_titles:
        return ""
    out = ["<author_edits>", AUTHOR_EDITS_HEADER, ""]
    used = sum(len(line) for line in out)
    omitted_files: list[str] = []
    for file_id, title, changes in entries:
        section = [f"### 《{title}》 (id={file_id})", *render_hunks(changes), ""]
        size = sum(len(line) + 1 for line in section)
        if used + size > MAX_BLOCK_CHARS:
            omitted_files.append(f"《{title}》")
            continue
        out.extend(section)
        used += size
    if omitted_files:
        out.append(
            f"另有 {len(omitted_files)} 个文件的手改没列出（不完整）：{'、'.join(omitted_files)}；"
            "改这些文件前用 query_files 按 id 读一次，返回里带 author_edits。"
        )
    if unchecked_titles:
        out.append(
            f"另有 {len(unchecked_titles)} 个文件没有核对手改（不完整）：{'、'.join(unchecked_titles[:8])}；"
            "改它们之前用 query_files 按 id 读一次。"
        )
    out.append("</author_edits>")
    return "\n".join(out)


def author_edits_payload(changes: AuthorChanges) -> dict[str, Any]:
    """query_files 返回里附带的 author_edits（同样的上限和完整性标记）。"""
    if changes.failed:
        return {"note": "作者手改没算出来，改之前请核对全文。", "changes": [], "complete": False}
    pairs, truncated = _rendered_pairs(changes, MAX_HUNKS_PER_FILE)
    return {
        "note": "作者在 AI 写过之后亲手改的地方：保留作者的写法；作者这一轮明确要改时按新要求改。",
        "changes": [
            {"before": before, "after": after}
            | ({"deleted_in_version": hunk.deleted_in_version} if hunk.deleted_in_version is not None else {})
            for (before, after), hunk in zip(pairs, changes.hunks, strict=False)
        ],
        "complete": changes.complete and len(pairs) == len(changes.hunks) and not truncated,
    }


def touched_author_text(blame: LineBlame | None, old_content: str, new_content: str) -> list[dict[str, Any]]:
    """这次修改改掉或删掉了哪些作者写的段落（按段落位置对齐，不靠字符串是否出现），
    以及原样放回了哪些作者删掉的段落（``restored_deleted``，带删除所在版本）。

    blame 必须是 old_content 的。只在段尾续写（新段落以原段落开头）或段首补字（以原段落结尾）、
    或整段原样挪到别处的，不算改了作者的字。
    """
    if blame is None or not blame.has_ai or blame.lines != _split(old_content):
        return []
    old_lines = blame.lines
    new_lines = _split(new_content)
    opcodes = _matcher(old_lines, new_lines).get_opcodes()
    # 新正文里不在「相同」块中的段落：整段挪过去的作者段落会出现在这里。
    unmatched_new = Counter(
        line.strip() for tag, _i1, _i2, j1, j2 in opcodes if tag != "equal" for line in new_lines[j1:j2] if line.strip()
    )
    touched: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag not in ("replace", "delete"):
            continue
        replacement = new_lines[j1:j2]
        for index in range(i1, i2):
            line = old_lines[index]
            if blame.labels[index] != AUTHOR or not line.strip():
                continue
            if any(_extends(new, line) for new in replacement):
                continue
            if unmatched_new[line.strip()] > 0:
                unmatched_new[line.strip()] -= 1
                continue
            before, after, _ = pair_clip(line, "\n".join(new for new in replacement if new.strip()))
            touched.append({"before": before, "after": after})
    touched.extend(_restored_deletions(blame, old_lines, new_lines))
    return touched[:MAX_HUNKS_PER_FILE]


def _restored_deletions(blame: LineBlame, old_lines: list[str], new_lines: list[str]) -> list[dict[str, Any]]:
    """这次修改把作者删掉的哪几段原样放了回来（逐字相同，且改之前正文里没有这段）。

    只认回放记下的整段原文：同义改写、普通新增、正文里已有的段落挪位置都不算。
    """
    if not blame.deletions:
        return []
    deleted = {item.text: item.version_number for item in blame.deletions}
    before = {line.strip() for line in old_lines if line.strip()}
    restored: list[dict[str, Any]] = []
    for line in new_lines:
        text = line.strip()
        if not text or text in before or text not in deleted:
            continue
        clipped, _ = _cut(_flatten(text), MAX_SIDE_CHARS)
        entry: dict[str, Any] = {"before": "", "after": clipped, "restored_deleted": True}
        if deleted[text] is not None:
            entry["deleted_in_version"] = deleted[text]
        restored.append(entry)
        before.add(text)
    return restored
