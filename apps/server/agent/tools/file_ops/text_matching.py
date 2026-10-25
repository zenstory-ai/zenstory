"""
Text matching utilities for fuzzy and approximate text matching.

This module provides robust text matching functions that handle:
- Punctuation and whitespace variations
- Case differences
- Approximate/fuzzy matching for slight word errors

These utilities are used by the file editing operations to reliably locate
text anchors in content even when there are minor differences.
"""

import re
import time
import unicodedata
from difflib import SequenceMatcher

# --- Approximate-match safety limits -------------------------------------
# Approximate (character-level fuzzy) matching is a best-effort *tertiary*
# fallback used only after exact and punctuation-insensitive fuzzy matching
# both fail. Its sliding-window + SequenceMatcher scan is O(windows × N × P),
# which on a full novel chapter can spin the CPU for seconds to minutes and,
# in the default SQLite deployment, block the asyncio event loop (stalling
# every other request served by the same process). When the input is too large
# we skip it and let the caller fall through to the helpful
# "copy a longer/unique snippet" error instead.
MAX_APPROX_CONTENT_LEN = 5000  # normalized chars; skip approximate scan above this
# 工作量上限：真实代价是「窗口尺寸种类数 × 内容长度 × pattern 长度」的字符级操作
# ——每个窗口都要做 norm_content[i:i+w] 切片、set(window) 构造、set_seq1() 与
# quick_ratio()，常数是 O(pattern_len) 而不是 O(1)。旧公式只算到
# window_count × len(norm_content)，整整漏掉了 pattern_len 这个因子（可达数百）：
# 实测 5000 归一化字 + 260 字 pattern 的估算值仍在「安全区」内，却要跑 39 秒，
# 把事件循环整段占死。这里按字符级操作数重新标定阈值。
MAX_APPROX_WORK = 5_000_000  # ceiling on window_count × len(norm_content) × pattern_len
# 兜底 wall-clock 上限：预过滤命中率随语料分布浮动（同样的工作量估算，实际耗时可
# 差数倍），静态估算不可能精确，所以扫描过程中再加一道硬超时。超时即放弃近似匹配，
# 调用方会走「请复制更长且唯一的原文」的错误分支——这比让整个进程停摆好得多。
MAX_APPROX_SECONDS = 1.0

# Minimum score gap between the best window and the best *non-overlapping*
# runner-up. If a different, distant passage scores nearly as high, the match
# is ambiguous and we refuse to auto-apply it (which would silently overwrite
# the wrong passage). Mirrors find_unique_line_span's min_gap.
MIN_APPROX_GAP = 0.08

# 段落打分里「一方包含另一方即判满分」的捷径所需的最小长度比。
# 没有这道约束时，一个 3 字的章节标题段落「第三章」只要被整条锚点顺带包含，
# 就能拿到 0.999 分，稳稳压过真正语义相关但相似度只有 0.4~0.6 的长段落，
# 于是正文被插到章节开头。只有两段长度接近时，包含关系才说明它们指的是同一件事。
CONTAINMENT_MIN_LEN_RATIO = 0.6


# --- 引号等价精确匹配 -----------------------------------------------------
# AI 写入正文时会把半角 " 规范化成全角引号（utils.cjk_quotes），模型下一次
# edit_file 却常常照旧用半角引号抄 old/anchor，逐字 exact 就找不到了。这里把
# 双引号类字符在原文和 old 里都映射成同一个占位符再找：映射一字对一字，命中
# 下标可以直接用在原文上，其余字符仍须逐字一致（比去掉全部标点的 fuzzy 严格）。
QUOTE_EQUIVALENT_CHARS = '"“”＂「」'
_QUOTE_FOLD_PLACEHOLDER = '"'
_QUOTE_FOLD_TABLE = str.maketrans(
    dict.fromkeys(QUOTE_EQUIVALENT_CHARS, _QUOTE_FOLD_PLACEHOLDER)
)


def has_quote_equivalent_chars(text: str) -> bool:
    """Whether ``text`` contains any mark that quote folding would change."""
    return any(ch in QUOTE_EQUIVALENT_CHARS for ch in text)


def fold_double_quotes(text: str) -> str:
    """Map every ``"“”＂「」`` to one placeholder; length is unchanged."""
    return text.translate(_QUOTE_FOLD_TABLE)


def locate_exact_or_quote_equivalent(
    content: str, pattern: str
) -> tuple[str, str, str] | None:
    """Find ``pattern`` exactly, or exactly modulo double-quote style.

    Returns ``(match_mode, haystack, needle)`` where ``match_mode`` is
    ``"exact"`` or ``"quote_equivalent"``. Indices found by searching
    ``needle`` in ``haystack`` are valid in ``content`` (folding is one
    character for one character). ``None`` when neither matches.

    A pattern without quote marks cannot gain a match from folding (any
    folded hit would cover only unchanged characters), so it is not folded.
    """
    if not pattern:
        return None
    if pattern in content:
        return "exact", content, pattern
    if not has_quote_equivalent_chars(pattern):
        return None
    folded_pattern = fold_double_quotes(pattern)
    folded_content = fold_double_quotes(content)
    if folded_pattern in folded_content:
        return "quote_equivalent", folded_content, folded_pattern
    return None


def _length_ratio_ok(a: str, b: str, min_ratio: float = CONTAINMENT_MIN_LEN_RATIO) -> bool:
    """两段文本的长度比是否达到 ``min_ratio``（短/长）。"""
    longer = max(len(a), len(b))
    if longer == 0:
        return False
    return min(len(a), len(b)) / longer >= min_ratio


def _block_similarity(norm_pattern: str, norm_block: str) -> float:
    """段落级相似度：带长度比约束的包含捷径 + SequenceMatcher 兜底。

    包含关系只有在两段长度接近时才视为「同一段」（0.999）；长度悬殊时退回
    SequenceMatcher，其比值天然会因长度差惩罚短块，短标题不会再冒充目标段落。
    """
    if (norm_pattern in norm_block or norm_block in norm_pattern) and _length_ratio_ok(
        norm_pattern, norm_block
    ):
        return 0.999
    return SequenceMatcher(None, norm_pattern, norm_block).ratio()


def normalize_for_fuzzy_match(
    s: str,
    *,
    ignore_punct_whitespace: bool = True,
    casefold: bool = True,
) -> tuple[str, list[int]]:
    """Normalize text for fuzzy matching.

    This function preprocesses text to enable robust matching by:
    - Normalizing Unicode characters (NFKC)
    - Optionally casefolding for case-insensitive matching
    - Optionally removing whitespace and punctuation

    Args:
        s: Input string to normalize
        ignore_punct_whitespace: If True, remove whitespace and punctuation
        casefold: If True, apply casefolding for case-insensitive matching

    Returns:
        A tuple of:
            - normalized: The normalized string
            - index_map: Mapping from each normalized char position to original index
    """
    normalized_chars: list[str] = []
    index_map: list[int] = []

    for idx, ch in enumerate(s):
        chunk = unicodedata.normalize("NFKC", ch)
        if casefold:
            chunk = chunk.casefold()

        for out_ch in chunk:
            if ignore_punct_whitespace:
                # Remove whitespace and punctuation for robust matching
                if out_ch.isspace():
                    continue
                if unicodedata.category(out_ch).startswith("P"):
                    continue

            normalized_chars.append(out_ch)
            index_map.append(idx)

    return "".join(normalized_chars), index_map


def _line_break_before(s: str, index_map: list[int]) -> list[bool]:
    """``flags[k]``：原文里第 k 个归一化字符与前一个之间是否隔着换行。

    归一化会把换行和其他空白一起去掉，段落分隔在比较时就看不见了。这里把它
    记下来，调用方据此判断一个命中是否跨过了原文的段落分隔。
    """
    flags = [False] * len(index_map)
    for k in range(1, len(index_map)):
        gap = s[index_map[k - 1] + 1 : index_map[k]]
        if "\n" in gap or "\r" in gap:
            flags[k] = True
    return flags


def find_fuzzy_spans(
    content: str,
    pattern: str,
    *,
    ignore_punct_whitespace: bool = True,
    casefold: bool = True,
    min_normalized_len: int = 6,
    max_matches: int = 20,
    stats: dict[str, int] | None = None,
    keep_line_breaks: bool = False,
) -> list[tuple[int, int]]:
    """Find match spans in original content, ignoring punctuation/whitespace.

    This function finds all occurrences of a pattern in content, using
    fuzzy matching that ignores punctuation and whitespace differences.

    Args:
        content: The text to search in
        pattern: The pattern to search for
        ignore_punct_whitespace: If True, ignore punctuation/whitespace differences
        casefold: If True, perform case-insensitive matching
        min_normalized_len: Minimum normalized pattern length to search
        max_matches: Maximum number of matches to return
        stats: Optional dict receiving match diagnostics; "boundary_rejected"
            counts candidates dropped because the span started/ended inside an
            NFKC-expanded original character; "paragraph_rejected" counts
            candidates dropped by ``keep_line_breaks``
        keep_line_breaks: replace/delete 用。忽略空白时换行也被忽略，单行的
            pattern 会和「上一段段尾 + 下一段段首」连成一处命中，整段替换后两段
            就并成一行。开启后，原文在命中范围内的每一处换行，pattern 在同一
            位置也必须有换行，否则这个候选作废。

    Returns:
        List of (start_index, end_index) tuples in the ORIGINAL content.
        Spans are non-overlapping and sorted by position.
    """
    if stats is not None:
        stats["boundary_rejected"] = 0
        stats["paragraph_rejected"] = 0

    normalized_content, map_content = normalize_for_fuzzy_match(
        content,
        ignore_punct_whitespace=ignore_punct_whitespace,
        casefold=casefold,
    )
    normalized_pattern, map_pattern = normalize_for_fuzzy_match(
        pattern,
        ignore_punct_whitespace=ignore_punct_whitespace,
        casefold=casefold,
    )

    if not normalized_pattern:
        return []
    if len(normalized_pattern) < min_normalized_len:
        return []

    content_breaks: list[bool] = []
    pattern_breaks: list[bool] = []
    if keep_line_breaks and ignore_punct_whitespace:
        content_breaks = _line_break_before(content, map_content)
        pattern_breaks = _line_break_before(pattern, map_pattern)

    spans: list[tuple[int, int]] = []
    boundary_rejected = 0
    paragraph_rejected = 0
    pos = 0
    while pos < len(normalized_content):
        found = normalized_content.find(normalized_pattern, pos)
        if found < 0:
            break

        last = found + len(normalized_pattern) - 1
        # NFKC can expand one original char into several normalized chars
        # (Ⅻ→xii, ﬁ→fi, ㎞→km); index_map maps all of them back to the same
        # original index. A span starting/ending inside such an expansion
        # would pull the WHOLE original char into the span and destroy its
        # unmatched remainder, so only accept spans whose normalized
        # boundaries align with original-char boundaries.
        start_aligned = found == 0 or map_content[found] != map_content[found - 1]
        end_aligned = (
            last + 1 >= len(map_content) or map_content[last] != map_content[last + 1]
        )
        if not (start_aligned and end_aligned):
            boundary_rejected += 1
            pos = found + 1
            continue

        if content_breaks and any(
            content_breaks[found + k] and not pattern_breaks[k]
            for k in range(1, len(normalized_pattern))
        ):
            paragraph_rejected += 1
            pos = found + 1
            continue

        start_orig = map_content[found]
        end_orig = map_content[last] + 1
        spans.append((start_orig, end_orig))

        if len(spans) >= max_matches:
            break

        # Non-overlapping by default for stability
        pos = found + len(normalized_pattern)

    if stats is not None:
        stats["boundary_rejected"] = boundary_rejected
        stats["paragraph_rejected"] = paragraph_rejected
    return spans


def find_approximate_match(
    content: str,
    pattern: str,
    *,
    max_error_rate: float = 0.2,
    min_pattern_len: int = 10,
    keep_line_breaks: bool = False,
    stats: dict[str, int] | None = None,
) -> tuple[int, int, float, str] | None:
    """Find best approximate match using sliding window + similarity.

    This handles cases where the model has slight word errors (e.g. "发言" vs "声音").
    Uses a sliding window approach with SequenceMatcher for similarity scoring.

    Args:
        content: The text to search in
        pattern: The pattern to search for
        max_error_rate: Maximum allowed error rate (0.0 to 1.0)
        min_pattern_len: Minimum normalized pattern length to search
        keep_line_breaks: 同 find_fuzzy_spans。近似窗口没有逐字对齐，所以按数量
            约束：窗口内部跨过的原文换行不能多于 pattern 自己的换行。
        stats: 收到 "paragraph_rejected"（因跨段被丢弃的达标窗口数）。跨段窗口里
            最好的一个不比段内最好的差时返回 None：段内窗口只是跨段那处的一部分。

    Returns:
        A tuple of (start, end, similarity, matched_text) if a good match is found,
        or None if no match meets the similarity threshold.
    """
    if stats is not None:
        stats["paragraph_rejected"] = 0
    # Normalize both for comparison
    norm_content, map_content = normalize_for_fuzzy_match(content)
    norm_pattern, map_pattern = normalize_for_fuzzy_match(pattern)

    if len(norm_pattern) < min_pattern_len:
        return None

    pattern_len = len(norm_pattern)

    # Sliding window with some tolerance for length variation
    window_min = max(min_pattern_len, int(pattern_len * 0.7))
    window_max = int(pattern_len * 1.3)
    window_count = max(0, window_max - window_min + 1)

    # Safety guard: approximate matching is a best-effort fallback. On large
    # inputs the scan is prohibitively expensive (and would block the event
    # loop), so bail out and let the caller surface the helpful
    # "copy a longer/unique snippet" error instead of hanging.
    # 工作量必须带上 pattern_len：每个窗口内部的切片/set/quick_ratio 都是
    # O(pattern_len)，漏掉它会让「守卫放行的那一档」恰好是最慢的一档。
    if (
        len(norm_content) > MAX_APPROX_CONTENT_LEN
        or window_count * len(norm_content) * pattern_len > MAX_APPROX_WORK
    ):
        return None

    # 静态估算之外再加一道 wall-clock 硬超时（见 MAX_APPROX_SECONDS）。
    deadline = time.monotonic() + MAX_APPROX_SECONDS

    pattern_char_set = set(norm_pattern)
    min_common = len(pattern_char_set) * 0.5
    min_similarity = 1.0 - max_error_rate

    # Reuse a single SequenceMatcher with a fixed second sequence so its
    # autojunk/b2j index is built once instead of per-window, and gate the
    # expensive ratio() behind the cheap real_quick_ratio()/quick_ratio()
    # upper bounds.
    matcher = SequenceMatcher(None)
    matcher.set_seq2(norm_pattern)

    # Collect every window at/above the similarity threshold, then decide the best
    # window and its best NON-OVERLAPPING runner-up AFTER the scan. The gate must
    # prune only below-threshold windows (not below the running best) — otherwise a
    # genuine near-tie runner-up would be gated out and the ambiguity guard would
    # never see it. Deciding after the scan also makes the runner-up order-
    # independent (a shifted window of the true match no longer inflates it).
    candidates: list[tuple[tuple[int, int], float]] = []  # ((start, end), score)

    # break_prefix[k] = 归一化位置 1..k-1 里有几个字符前面隔着换行；窗口 [i, i+w)
    # 内部的换行数 = break_prefix[i+w] - break_prefix[i+1]。
    break_prefix: list[int] = []
    allowed_breaks = 0
    if keep_line_breaks:
        content_breaks = _line_break_before(content, map_content)
        break_prefix = [0] * (len(content_breaks) + 1)
        for k, flag in enumerate(content_breaks):
            break_prefix[k + 1] = break_prefix[k] + (1 if flag and k > 0 else 0)
        allowed_breaks = sum(_line_break_before(pattern, map_pattern))
    paragraph_rejected = 0
    best_rejected_score = 0.0

    for window_size in range(window_min, window_max + 1):
        if time.monotonic() > deadline:
            # 扫描不完整就无法判定歧义（可能漏掉真正的最佳窗口或它的竞争者），
            # 因此超时一律放弃匹配，绝不返回「半程最佳」。
            return None
        for i in range(len(norm_content) - window_size + 1):
            # 单个 window_size 的内层循环本身也可能很长（通过预过滤的窗口还要跑
            # 一次 O(N×P) 的 ratio()），所以内层每 64 个窗口也检查一次超时。
            if (i & 0x3F) == 0 and time.monotonic() > deadline:
                return None
            window = norm_content[i:i + window_size]

            # Quick pre-filter: check if at least some characters overlap
            if len(pattern_char_set & set(window)) < min_common:
                continue

            matcher.set_seq1(window)
            # Cheap upper bounds first: skip windows that cannot even reach the
            # similarity threshold (quick_ratio/real_quick_ratio are upper bounds).
            if (
                matcher.real_quick_ratio() < min_similarity
                or matcher.quick_ratio() < min_similarity
            ):
                continue

            score = matcher.ratio()
            if score >= min_similarity:
                if (
                    break_prefix
                    and break_prefix[i + window_size] - break_prefix[i + 1] > allowed_breaks
                ):
                    paragraph_rejected += 1
                    best_rejected_score = max(best_rejected_score, score)
                    continue
                candidates.append(((i, i + window_size), score))

    if stats is not None:
        stats["paragraph_rejected"] = paragraph_rejected
    if not candidates:
        return None

    best_span, best_score = max(candidates, key=lambda c: c[1])

    # 跨段的窗口比段内最好的窗口还像（或一样像）：old 本来对应的就是跨段那一处，
    # 段内窗口只是它的一部分。用段内窗口去替换，会把 new 里属于另一段的内容再写
    # 一遍（「嗡嗡作响。\n\n　　嗡嗡作响。林舟……」），所以和全部被排除一样放弃，
    # 由调用方按 paragraph_rejected 报「缺换行」。
    if best_rejected_score >= best_score:
        return None

    # Best score among windows that do NOT overlap the chosen best window.
    def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
        return a[0] < b[1] and b[0] < a[1]

    second_best_score = 0.0
    for span, score in candidates:
        if span == best_span:
            continue
        if not _overlaps(span, best_span):
            second_best_score = max(second_best_score, score)

    # Refuse ambiguous matches: if a different, distant passage scores nearly as
    # high, auto-applying would risk silently overwriting the wrong text.
    if (best_score - second_best_score) < MIN_APPROX_GAP:
        return None

    start_i, end_i = best_span
    end_i = min(end_i, len(map_content))
    # Snap the window to original-char boundaries: a boundary landing inside
    # an NFKC expansion (Ⅻ→xii etc.) would otherwise swallow the whole
    # original char including its unmatched remainder, so shrink the span to
    # exclude partially-covered chars instead.
    while start_i < end_i and start_i > 0 and map_content[start_i] == map_content[start_i - 1]:
        start_i += 1
    while end_i > start_i and end_i < len(map_content) and map_content[end_i - 1] == map_content[end_i]:
        end_i -= 1
    if start_i >= end_i:
        return None
    start_orig = map_content[start_i]
    end_orig = map_content[end_i - 1] + 1
    matched_text = content[start_orig:end_orig]
    return (start_orig, end_orig, best_score, matched_text)


# --- 模糊命中后的边缘标点 -------------------------------------------------
# 模糊/近似匹配在归一化空间里比较（去掉了全部标点与空白），映射回原文的 span
# 只覆盖首尾两个「非标点」字符，原文边缘的引号、句号落在 span 外面。模型的
# old 以引号开头、以句号结尾（new 同样带着），替换后原文的边缘标点就残留下来：
# 「“"有事？"他声音很冷。。」，删除则留下孤立的「“。」。这里按「同类标点」把
# span 向两侧扩展，只吞掉与参照串边缘逐个同类的原文标点，不跨越参照串没有的换行。
_QUOTE_CHARS = frozenset("\"'“”‘’「」『』«»‹›〝〞〟＂＇")
_SENTENCE_END_CHARS = frozenset("。.！!？?…｡")
_PAUSE_CHARS = frozenset("，,、；;：:")
# 能确定是开引号的字符；直引号分不出开闭，按可闭合处理。
_OPENING_QUOTE_CHARS = frozenset("“‘「『«‹〝")
_DASH_CHARS = frozenset("—–-")
_BRACKET_CHARS = frozenset("（）()【】[]《》〈〉{}〔〕")


def _edge_punct_class(ch: str) -> str | None:
    """边缘标点的「同类」判定；非标点非空白返回 None。

    只认 normalize_for_fuzzy_match 会去掉的字符（空白与 Unicode P* 标点），
    这样参与扩展的正好是模糊匹配时被忽略、因而落在 span 外的那些字符。
    同类即可互相抵消：直引号与弯引号、问号的全角与半角、句号与感叹号都视为
    同一个位置上的标点（模型记错的通常就是这一类差异）。换行单独成类，参照串
    没有换行时不会把段落分隔吞进替换范围。
    """
    if ch in "\r\n":
        return "newline"
    if ch.isspace():
        return "space"
    folded = unicodedata.normalize("NFKC", ch)
    if not any(unicodedata.category(c).startswith("P") for c in folded or ch):
        return None
    for candidate in (ch, folded):
        if candidate in _QUOTE_CHARS:
            return "quote"
        if candidate in _SENTENCE_END_CHARS:
            return "end"
        if candidate in _PAUSE_CHARS:
            return "pause"
        if candidate in _DASH_CHARS:
            return "dash"
        if candidate in _BRACKET_CHARS:
            return "bracket"
    if unicodedata.category(ch).startswith("P"):
        return f"punct:{folded}"
    return None


def edge_punct_run(text: str, *, leading: bool) -> str:
    """``text`` 开头（leading=True）或结尾的标点/空白连续串。"""
    if leading:
        end = 0
        while end < len(text) and _edge_punct_class(text[end]) is not None:
            end += 1
        return text[:end]
    start = len(text)
    while start > 0 and _edge_punct_class(text[start - 1]) is not None:
        start -= 1
    return text[start:]


def extend_span_over_edge_punct(
    content: str,
    start: int,
    end: int,
    *,
    leading_ref: str,
    trailing_ref: str,
) -> tuple[int, int]:
    """把模糊命中的 span 向两侧扩展到与参照串边缘同类的原文标点。

    ``leading_ref`` 是参照串开头的标点串（从紧贴 span 的那一个往外逐个配对），
    ``trailing_ref`` 是结尾的标点串（同样从内往外）。原文字符与参照字符同类
    才吞进去，遇到第一个不同类或非标点字符即停。唯一的例外在结尾一侧：参照字符
    是闭引号、原文先是同一行内的句末/停顿标点再接闭引号时（「很好”」对「很好。”」），
    这串标点和闭引号一起吞进去。
    """
    new_start = start
    ref_index = len(leading_ref) - 1
    while ref_index >= 0 and new_start > 0:
        orig_class = _edge_punct_class(content[new_start - 1])
        if orig_class is None or orig_class != _edge_punct_class(leading_ref[ref_index]):
            break
        new_start -= 1
        ref_index -= 1

    new_end = end
    ref_index = 0
    while ref_index < len(trailing_ref) and new_end < len(content):
        ref_char = trailing_ref[ref_index]
        ref_class = _edge_punct_class(ref_char)
        orig_class = _edge_punct_class(content[new_end])
        if orig_class is not None and orig_class == ref_class:
            new_end += 1
            ref_index += 1
            continue
        if ref_class == "quote" and ref_char not in _OPENING_QUOTE_CHARS:
            # 参照串在这里是闭引号，原文却先有「。」「，」再闭引号（模型漏抄了引号里的
            # 句末标点）：连同这串标点一起吞到闭引号，否则 new 自带的闭引号会再写一遍。
            quote_at = _closing_quote_after_inner_punct(content, new_end)
            if quote_at is not None:
                new_end = quote_at + 1
                ref_index += 1
                continue
        break
    return new_start, new_end


def _closing_quote_after_inner_punct(content: str, pos: int) -> int | None:
    """``pos`` 起是一串句末/停顿标点、紧跟一个闭引号时，返回该闭引号的下标。

    只跨这两类标点（不跨换行、空白和文字），找到的引号不能是开引号——那是下一句
    对白的开头，不属于这一处命中。
    """
    i = pos
    while i < len(content) and _edge_punct_class(content[i]) in ("end", "pause"):
        i += 1
    if i == pos or i >= len(content):
        return None
    if _edge_punct_class(content[i]) != "quote" or content[i] in _OPENING_QUOTE_CHARS:
        return None
    return i


def build_span_previews(
    content: str,
    spans: list[tuple[int, int]],
    *,
    window: int = 40,
    max_items: int = 3,
) -> list[str]:
    """Build short previews around spans for debugging/hints.

    Args:
        content: The original content
        spans: List of (start, end) tuples
        window: Number of characters to include on each side
        max_items: Maximum number of previews to generate

    Returns:
        List of preview strings around each span
    """
    previews: list[str] = []
    for start, end in spans[:max_items]:
        left = max(0, start - window)
        right = min(len(content), end + window)
        snippet = content[left:right]
        previews.append(snippet)
    return previews


# 段落按任意连续换行切分：只用单个换行分段的文件（网文常见）也要逐段比较，
# 不能整篇当成一段。
_PARAGRAPH_BREAKS = re.compile(r"\n+")
# 候选片段的相似度下限：低于它的段落与 old/anchor 无关，列出来只会误导模型。
_SUGGEST_MIN_SCORE = 0.3


def _paragraph_blocks(content: str) -> list[str]:
    return [b for b in _PARAGRAPH_BREAKS.split(content) if b.strip()]


def suggest_similar_lines(
    content: str,
    pattern: str,
    *,
    ignore_punct_whitespace: bool = True,
    max_items: int = 3,
) -> list[str]:
    """Suggest similar lines/paragraphs when direct match fails.

    This function helps provide helpful suggestions when a match fails,
    by finding paragraphs that are similar to the pattern.

    Args:
        content: The content to search
        pattern: The pattern that failed to match
        ignore_punct_whitespace: If True, ignore punctuation/whitespace
        max_items: Maximum number of suggestions to return

    Returns:
        List of similar paragraph snippets
    """
    norm_pat, _ = normalize_for_fuzzy_match(
        pattern,
        ignore_punct_whitespace=ignore_punct_whitespace,
    )
    if not norm_pat:
        return []

    # Use paragraphs first (more stable for novels)
    blocks = _paragraph_blocks(content)
    candidates: list[tuple[float, str]] = []

    for b in blocks:
        snippet = b.strip()
        if not snippet:
            continue
        norm_b, _ = normalize_for_fuzzy_match(
            snippet,
            ignore_punct_whitespace=ignore_punct_whitespace,
        )
        if not norm_b:
            continue

        # 与 find_unique_line_span 共用同一套打分：包含捷径必须过长度比约束，
        # 否则一个 3 字的章节标题会把真正相关的长段落挤出建议列表。
        score = _block_similarity(norm_pat, norm_b)

        if score < _SUGGEST_MIN_SCORE:
            continue
        candidates.append((score, snippet[:160] + ("..." if len(snippet) > 160 else "")))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return [c[1] for c in candidates[: max_items]]


def find_unique_line_span(
    content: str,
    anchor: str,
    *,
    ignore_punct_whitespace: bool = True,
    min_score: float = 0.9,
    min_gap: float = 0.08,
) -> tuple[int, int, float] | None:
    """Find a unique best-matching paragraph span for anchor.

    This function finds a single, unique paragraph that best matches the anchor,
    with confidence scoring to ensure it's not ambiguous.

    这是 insert_after/insert_before 在 exact/fuzzy/approximate 全部失败后的
    第三级兜底，命中的是「最像的整段」而非逐字原文，因此把置信度一并返回，
    让调用方把兜底性质写进 applied_edits，模型才能判断要不要换更精确的锚点。

    Args:
        content: The content to search
        anchor: The anchor text to match
        ignore_punct_whitespace: If True, ignore punctuation/whitespace
        min_score: Minimum similarity score required
        min_gap: Minimum gap between best and second-best scores

    Returns:
        (start, end, score) tuple in original content if confident, otherwise None
    """
    norm_anchor, _ = normalize_for_fuzzy_match(
        anchor,
        ignore_punct_whitespace=ignore_punct_whitespace,
    )
    if not norm_anchor:
        return None

    blocks = _paragraph_blocks(content)
    if not blocks:
        return None

    scored: list[tuple[float, str]] = []
    for b in blocks:
        norm_b, _ = normalize_for_fuzzy_match(
            b,
            ignore_punct_whitespace=ignore_punct_whitespace,
        )
        if not norm_b:
            continue
        score = _block_similarity(norm_anchor, norm_b)
        scored.append((score, b))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_block = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0

    if best_score < min_score:
        return None
    if (best_score - second_score) < min_gap:
        return None

    # Ensure it maps to a single occurrence
    idx = content.find(best_block)
    if idx < 0:
        return None
    if content.find(best_block, idx + 1) >= 0:
        return None

    return idx, idx + len(best_block), best_score


__all__ = [
    "QUOTE_EQUIVALENT_CHARS",
    "fold_double_quotes",
    "has_quote_equivalent_chars",
    "locate_exact_or_quote_equivalent",
    "normalize_for_fuzzy_match",
    "find_fuzzy_spans",
    "find_approximate_match",
    "build_span_previews",
    "edge_punct_run",
    "extend_span_over_edge_punct",
    "suggest_similar_lines",
    "find_unique_line_span",
]
