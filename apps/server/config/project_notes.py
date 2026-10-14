"""
项目备注（AI 记忆里的 notes 字段）的来源分区。

notes 仍是一段纯文本（AI 记忆面板里的一个文本框，不需要改表或迁移数据），
用两行固定标题把它分成三块：

    <没有标题的条目>            未标来源：作者在面板里直接写的，或本规则之前记下的
    【作者确认】
    <条目>                     作者在对话里说过、update_project 逐条核对过作者原话的约定
    【AI 提议·待作者确认】
    <条目>                     AI 提出或推断、作者还没同意的内容

写入（agent 的 update_project）由 :func:`merge_project_notes` 决定每条进哪一块：
已在「作者确认」或「未标来源」里的照旧；其余条目逐条核对，只有那一条本身就是作者
本轮原话（:func:`_quote_supports_entry`），或作者明确引用上一轮呈现的同批固定提议，
才进「作者确认」，否则记成 AI 提议。
注入上下文时 :func:`render_project_notes` 把三块分开渲染、标题行后面附说明；
:func:`split_project_notes` 认得这些渲染出来的标题行，模型把上下文里的备注块照抄回来时
分区不变。作者在面板里怎么改都行，解析只认这几种标题行。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CONFIRMED_HEADER = "【作者确认】"
PROPOSALS_HEADER = "【AI 提议·待作者确认】"
# 只在注入上下文时用：上下文里未标来源一块排在作者确认后面，照抄回来时靠它切回未标来源。
# 存储时未标来源一块在最前面，不写标题（兼容旧备注）。
UNLABELED_HEADER = "【未标来源】"

# 注入上下文时标题行后面的说明（解析时整行仍按标题处理）
CONFIRMED_EXPLANATION = "作者在对话里说过、记录时逐条核对过作者原话的约定"
UNLABELED_EXPLANATION = (
    "作者在 AI 记忆里写的，或以前记下、没区分来源的条目；"
    "和作者本轮的话或稿件冲突时以作者和稿件为准，不要说成「作者已确认」"
)
PROPOSALS_EXPLANATION = (
    "之前 AI 提出或推断的内容，不是设定：不要当定论写进正文、文件或回复，"
    "也不要说成作者定的；作者明确同意后才算数"
)

# 标题行：可带「备注·」前缀，后面可跟一段括号说明（上下文里渲染的样子）
_HEADER_PREFIX = r"^\s*【\s*(?:备注\s*[·・]\s*)?"
_HEADER_SUFFIX = r"\s*】\s*(?:[（(].*[)）])?\s*$"
_CONFIRMED_HEADER_RE = re.compile(_HEADER_PREFIX + r"作者(?:已)?确认" + _HEADER_SUFFIX)
_PROPOSALS_HEADER_RE = re.compile(
    _HEADER_PREFIX + r"AI\s*提议[^】]*" + _HEADER_SUFFIX, re.IGNORECASE
)
_UNLABELED_HEADER_RE = re.compile(_HEADER_PREFIX + r"未标来源" + _HEADER_SUFFIX)

# 一行里用分号隔开的多条约定，按条比较（「A；B」与「A」「B」是同两条）
_ENTRY_SPLIT_RE = re.compile(r"[；;]")
# 行首的列表符号 / 序号
_BULLET_RE = re.compile(r"^\s*(?:[-*•·]+|\d+[.、)）]|[（(]\d+[)）])\s*")
# 模型自己给条目贴的来源标签（来源由分区表达，条目里的自称不算数）
_SELF_LABEL_RE = re.compile(
    r"[【\[（(]\s*(?:作者)?(?:已)?(?:确认|定|拍板)(?:设定|的?事实)?\s*[】\]）)]"
)
# 比较 / 核对原话时忽略的空白与标点
_IGNORABLE_RE = re.compile(r"[\s　，,。.！!？?；;：:、\"'“”‘’「」『』《》〈〉（）()【】\[\]…—\-~～·]+")

_MIN_QUOTE_CHARS = 2
# 作者同意某条 AI 提议时，原话里至少要复述这条提议里连续这么多个字，才算指的是这一条
# （「可以」「就这样」说不出同意的是哪条；见 _quote_supports_entry）
_MIN_PROPOSAL_ECHO_CHARS = 4

# 中文数字和阿拉伯数字视为同一写法（「第五章」=「第5章」），两边按同一规则转换
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}
_CN_NUMERAL_RUN_RE = re.compile(r"[零〇一二两三四五六七八九十百千万]+")


@dataclass
class ProjectNotesSections:
    """notes 按来源拆开后的三块，每块是若干行文本。"""

    unlabeled: list[str] = field(default_factory=list)
    confirmed: list[str] = field(default_factory=list)
    proposals: list[str] = field(default_factory=list)


@dataclass
class ProjectNotesMerge:
    """merge_project_notes 的结果。"""

    text: str
    # 作为「作者确认」传进来、但找不到作者原话依据，改记成 AI 提议的条目
    downgraded: list[str] = field(default_factory=list)
    # 这次新记进「作者确认」的条目
    confirmed_added: list[str] = field(default_factory=list)


def split_project_notes(notes: str | None) -> ProjectNotesSections:
    """按标题行把 notes 拆成 未标来源 / 作者确认 / AI 提议 三块（空行丢掉）。

    存储用的两行标题和 :func:`render_project_notes` 渲染出来的标题行（带「备注·」前缀、
    后面跟括号说明、未标来源一块的标题）都认。
    """
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
    """把三块拼回一段文本；空块不写标题。"""
    blocks: list[str] = []
    if sections.unlabeled:
        blocks.append("\n".join(sections.unlabeled))
    if sections.confirmed:
        blocks.append("\n".join([CONFIRMED_HEADER, *sections.confirmed]))
    if sections.proposals:
        blocks.append("\n".join([PROPOSALS_HEADER, *sections.proposals]))
    return "\n\n".join(blocks)


def render_project_notes(notes: str | None) -> list[str]:
    """注入上下文用：三块分开，标题行后附说明；空块不写。

    标题行是 :func:`split_project_notes` 认得的形式，模型把这几行连同标题照抄回 notes 时，
    条目仍回到原来那块，标题不会变成条目。
    """
    sections = split_project_notes(notes)
    lines: list[str] = []
    for header, explanation, entries in (
        (CONFIRMED_HEADER, CONFIRMED_EXPLANATION, sections.confirmed),
        (UNLABELED_HEADER, UNLABELED_EXPLANATION, sections.unlabeled),
        (PROPOSALS_HEADER, PROPOSALS_EXPLANATION, sections.proposals),
    ):
        if entries:
            # 上下文里和【项目简介】【写作风格】并列，标题带「备注·」说明这是备注的一块
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
            else:  # 万
                total += (section + number) * 10000
                section = number = 0
        return str(total + section + number)
    # 没有单位（「二零二六」）按位转换
    return "".join(str(_CN_DIGITS[ch]) for ch in run)


def _normalize(text: str) -> str:
    stripped = _IGNORABLE_RE.sub("", text or "").lower()
    return _CN_NUMERAL_RUN_RE.sub(lambda match: _cn_numeral_value(match.group(0)), stripped)


def _entry_key(entry: str) -> str:
    return _normalize(_SELF_LABEL_RE.sub("", _BULLET_RE.sub("", entry)))


def _split_line(line: str) -> tuple[str, list[str]]:
    """一行拆成若干条；返回 (拼回用的分隔符, 条目)。"""
    separator = "；" if "；" in line or ";" not in line else ";"
    parts = [part.strip() for part in _ENTRY_SPLIT_RE.split(line)]
    return separator, [part for part in parts if _entry_key(part)]


def _keys_of(lines: list[str]) -> set[str]:
    keys: set[str] = set()
    for line in lines:
        _, entries = _split_line(line)
        keys.update(_entry_key(entry) for entry in entries)
    return keys


def verified_author_quotes(quotes: list[str] | None, author_messages: list[str]) -> list[str]:
    """留下逐字出现在作者消息里的原话（忽略空白和标点，至少 2 个字）。"""
    normalized_messages = [_normalize(message) for message in author_messages if message]
    verified: list[str] = []
    for quote in quotes or []:
        if not isinstance(quote, str):
            continue
        key = _normalize(quote)
        if len(key) < _MIN_QUOTE_CHARS:
            continue
        if any(key in message for message in normalized_messages):
            verified.append(quote.strip())
    return verified


def _quote_supports_entry(entry_key: str, quote_key: str, *, is_proposal: bool) -> bool:
    """这句（已核对过的）作者原话是不是这一条本身。

    - 新条目：条目整条都得出自这句原话（条目是原话或原话里连续的一段）。
      在原话后面接上自己的推断、或拿一句无关的原话给别的条目作保，都不算。
    - 已有的 AI 提议：提议原文作者早先就看得到，作者复述其中连续至少
      ``_MIN_PROPOSAL_ECHO_CHARS`` 个字（「就按你说的，点一次少活一年」）算同意这一条；
      只回「可以」「好」说不出是哪一条，不算。
    """
    if not entry_key or not quote_key:
        return False
    if entry_key in quote_key:
        return True
    if not is_proposal:
        return False
    # 原话里出现了这条提议里连续 echo 个字（「就按你说的，点一次少活一年」里的「点一次少活一年」）
    echo = min(_MIN_PROPOSAL_ECHO_CHARS, len(entry_key))
    return any(
        entry_key[start : start + echo] in quote_key for start in range(len(entry_key) - echo + 1)
    )


def _clean_entry(entry: str) -> str:
    """去掉模型自己贴的「已定 / 作者确认」标签，留下条目本身。"""
    cleaned = _SELF_LABEL_RE.sub("", entry).strip()
    return cleaned or entry.strip()


def presented_project_proposals(reply: str) -> list[tuple[int, str]]:
    """备注专用：完整提取明确待确认列表，主题标题不作选项，文件授权解析不受影响。"""
    lines = re.sub(r"\*\*|__|`|^#+\s*", "", reply or "", flags=re.MULTILINE).splitlines()
    shown: list[tuple[int, str]] = []
    lead: list[str] = []
    for line in lines:
        match = re.match(r"^\s*(\d+)[.、)）]\s*(.+?)\s*$", line)
        if match:
            shown.append((int(match.group(1)), match.group(2)))
        elif not shown:
            lead.append(line)
    pending_marker = re.compile(
        r"待(?:作者)?确认|请确认(?:以下|下列)的?(?:提议|条目|设定)"
        r"|pending\s+(?:proposals|(?:author\s+)?confirmation)"
        r"|please\s+confirm\s+(?:the\s+)?(?:following\s+)?proposals",
        re.IGNORECASE,
    )
    lead = [line.strip() for line in lead if line.strip()]
    # 允许「一、世界」这类主题标题；较早提到待确认、不代表末尾“下一步”列表也是它。
    while (
        lead
        and re.match(r"^[一二三四五六七八九十]+[、.．]", lead[-1])
        and not pending_marker.search(lead[-1])
    ):
        lead.pop()
    introduction = lead[-1] if lead else ""
    if not pending_marker.search(introduction):
        return []
    # 待确认内容只在完成报告里被提到，不等于报告中的列表就是那批提议。
    if re.search(
        r"(?:已(?:经)?(?:全部)?(?:完成|确认)|这轮做了|本轮完成|改动如下|完成报告)"
        r"|already\s+(?:confirmed|completed)|completed\s+(?:work|report)|changes\s+made",
        introduction, re.IGNORECASE,
    ):
        return []
    if [index for index, _ in shown] != list(range(1, len(shown) + 1)):
        return []
    return shown


def _confirmed_proposal_references(
    proposals: list[str],
    presented: list[tuple[int, str]] | None,
    author_quotes: list[str] | None,
    author_messages: list[str],
) -> set[str]:
    """只解析明确编号 / 全批确认；编号来自服务端抽取的上一轮选项，不来自工具参数。"""
    keys = [_entry_key(entry) for line in proposals for entry in _split_line(line)[1]]
    shown = [
        (index, [_entry_key(entry) for entry in _split_line(text)[1]])
        for index, text in (presented or [])
    ]
    shown_keys = [key for _, group in shown for key in group]
    # 必须是当前尚未确认的同一整批；新增、删减、改写、重复或截断时不猜所指。
    if (
        not keys
        or len(keys) != len(set(keys))
        or [index for index, _ in shown] != list(range(1, len(shown) + 1))
        or any(not group for _, group in shown)
        or len(shown_keys) != len(keys)
        or set(shown_keys) != set(keys)
        or not author_messages
    ):
        return set()
    # 核对完整的最新答复，不能从「不要确认第1条」摘出「确认第1条」作为授权。
    reply = author_messages[-1]
    if "?" in reply or "？" in reply or not any(
        _normalize(quote) == _normalize(reply) for quote in (author_quotes or [])
    ):
        return set()
    bare = _normalize(reply)
    if bare in {
        "全部确认", "确认全部", "确认全部提议", "确认以上全部提议", "以上提议全部确认",
        "confirmall", "confirmallproposals",
    }:
        return set(keys)
    # 小而明确的选择合同，不把「好」、客套、带条件或另一份清单的答复解释为确认。
    selection = _CN_NUMERAL_RUN_RE.sub(
        lambda match: _cn_numeral_value(match.group(0)),
        re.sub(r"\s+", "", reply).lower().strip("。.!！"),
    )
    match = re.fullmatch(
        r"(?:确认|同意|选|选择|confirm(?:proposals?)?|select)?"
        r"(?P<indices>第?[1-9]\d*(?:条|项)?"
        r"(?:(?:[、,，和及与]|and)第?[1-9]\d*(?:条|项)?)*)",
        selection,
    )
    if not match:
        return set()
    indices = {int(number) for number in re.findall(r"\d+", match.group("indices"))}
    if not indices.issubset({index for index, _ in shown}):
        return set()
    return {key for index, group in shown if index in indices for key in group}


def merge_project_notes(
    existing: str | None,
    *,
    notes: str | None,
    proposals: str | None,
    author_quotes: list[str] | None,
    author_messages: list[str] | None,
    previous_proposals: list[tuple[int, str]] | None = None,
) -> ProjectNotesMerge:
    """按来源合并 update_project 传来的 notes / proposals，返回要存的整段文本。

    - ``notes`` 是要保留的全部约定（整体替换，和以前一样：没传回来的旧条目视为删除）。
      其中已在「作者确认」或「未标来源」里的条目原样留在原来那块；
      其余条目（新条目、或把 AI 提议改记成约定）逐条核对：``author_quotes`` 里得有一句
      逐字出现在 ``author_messages`` 里、并且就是这一条本身的原话（见
      :func:`_quote_supports_entry`），这一条才进「作者确认」，否则记成 AI 提议。
      一句原话只给它自己那条作保，同一次调用里的其他条目各自核对。
      已有提议可按上一轮完整呈现的同批编号确认；``previous_proposals`` 仅接收服务端
      从上一轮回复抽取的全部编号项，当前列表有增删改时不据此确认。
    - ``proposals`` 是 AI 提议块的全部内容（整体替换）；不传时保留原有提议。
      已经进了「作者确认」的条目从提议里去掉。
    - ``notes`` 传空串且没传 ``proposals``：整个字段清空（和以前一样）。
    - ``author_messages`` 为 None 表示拿不到作者原话（不是应用内对话），不做核对，
      新条目和以前一样记成未标来源的文本（不贴「作者确认」）。
    """
    if notes is not None and not notes.strip() and proposals is None:
        # 清空备注：和以前一样整个字段清空（含 AI 提议）
        return ProjectNotesMerge(text="")

    current = split_project_notes(existing)
    unlabeled_keys = _keys_of(current.unlabeled)
    confirmed_keys = _keys_of(current.confirmed)
    proposal_keys = _keys_of(current.proposals)

    # 拿不到作者原话时不核对，也不贴「作者确认」：新条目和以前一样记成未标来源的文本
    unverifiable = author_messages is None
    quote_keys = (
        []
        if unverifiable
        else [_normalize(quote) for quote in verified_author_quotes(author_quotes, author_messages or [])]
    )
    referenced_keys = _confirmed_proposal_references(
        current.proposals, previous_proposals, author_quotes, author_messages or []
    )

    def backed_by_author_words(key: str) -> bool:
        if key in referenced_keys:
            return True
        is_proposal = key in proposal_keys
        return any(
            _quote_supports_entry(key, quote_key, is_proposal=is_proposal) for quote_key in quote_keys
        )

    result = ProjectNotesSections()
    downgraded: list[str] = []
    confirmed_added: list[str] = []

    if notes is None:
        result.unlabeled = list(current.unlabeled)
        result.confirmed = list(current.confirmed)
    else:
        incoming = split_project_notes(notes)
        # 传进来的 notes 里若带着 AI 提议块（照抄了上下文），那部分按提议处理
        incoming_lines = [*incoming.unlabeled, *incoming.confirmed]
        for line in incoming_lines:
            separator, entries = _split_line(line)
            kept_unlabeled: list[str] = []
            kept_confirmed: list[str] = []
            for raw_entry in entries:
                entry = _clean_entry(raw_entry)
                key = _entry_key(entry)
                if key in confirmed_keys:
                    kept_confirmed.append(entry)
                elif key in unlabeled_keys or unverifiable:
                    kept_unlabeled.append(entry)
                elif backed_by_author_words(key):
                    kept_confirmed.append(entry)
                    confirmed_added.append(entry)
                else:
                    downgraded.append(entry)
            if kept_unlabeled:
                result.unlabeled.append(separator.join(kept_unlabeled))
            if kept_confirmed:
                result.confirmed.append(separator.join(kept_confirmed))
        # notes 里照抄回来的 AI 提议块仍是提议；只有模型接在这一块后面的新条目、
        # 并且就是作者本轮原话的，才进作者确认（模型本意是记一条约定，只是写在了块末尾）
        copied_proposals: list[str] = []
        for line in incoming.proposals:
            separator, entries = _split_line(line)
            still_proposed: list[str] = []
            for raw_entry in entries:
                entry = _clean_entry(raw_entry)
                key = _entry_key(entry)
                if (
                    not unverifiable
                    and key not in proposal_keys
                    and key not in unlabeled_keys
                    and key not in confirmed_keys
                    and backed_by_author_words(key)
                ):
                    result.confirmed.append(entry)
                    confirmed_added.append(entry)
                else:
                    still_proposed.append(raw_entry)
            if still_proposed:
                copied_proposals.append(separator.join(still_proposed))
        if copied_proposals and proposals is None:
            proposals = "\n".join(copied_proposals)

    if proposals is None:
        proposal_lines = list(current.proposals)
    else:
        # 传进来的提议不管带没带标题，整段都是提议
        given = split_project_notes(proposals)
        proposal_lines = [*given.unlabeled, *given.confirmed, *given.proposals]
    proposal_lines.extend(downgraded)

    settled_keys = _keys_of(result.unlabeled) | _keys_of(result.confirmed)
    seen: set[str] = set()
    for line in proposal_lines:
        separator, entries = _split_line(line)
        kept: list[str] = []
        for entry in entries:
            key = _entry_key(entry)
            if key in settled_keys or key in seen:
                continue
            seen.add(key)
            kept.append(entry)
        if kept:
            result.proposals.append(separator.join(kept))

    return ProjectNotesMerge(
        text=compose_project_notes(result),
        downgraded=downgraded,
        confirmed_added=confirmed_added,
    )
