"""edit_file 不破坏正文（I10）：不并段、插入保持本文的分段与缩进、近似边界不截断句子、
报告成功就必须真的改了。

样例文本都是为测试自拟的，不取自任何用户作品。
"""

import pytest
from sqlmodel import Session

from agent.tools.file_ops.edit import (
    EDIT_ERROR_ANCHOR_NOT_FOUND,
    EDIT_ERROR_NO_CHANGE,
    EditBatchError,
    EditFileError,
    FileEditor,
)
from agent.tools.file_ops.text_matching import find_approximate_match, find_fuzzy_spans
from models import File, Project, User
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import FileVersionService

P1 = "　　档案室里很静，只有顶灯在嗡嗡作响。"
P2 = "　　林舟翻到腰酸，直起身靠着书架站了一会儿。"
P3 = "　　他数了数这几年的卷宗，一共十七册。"
CHAPTER = "\n\n".join([P1, P2, P3])


@pytest.fixture
def editor() -> FileEditor:
    return FileEditor(session=None)


def _insert_after(editor, content, anchor, text, **extra):
    applied: list[dict] = []
    edit = {"op": "insert_after", "anchor": anchor, "text": text, **extra}
    return editor._apply_insert(content, edit, 0, applied, [], after=True), applied


def _insert_before(editor, content, anchor, text, **extra):
    applied: list[dict] = []
    edit = {"op": "insert_before", "anchor": anchor, "text": text, **extra}
    return editor._apply_insert(content, edit, 0, applied, [], after=False), applied


# ------------------------------------------------- 模糊/近似匹配不跨段落分隔 --


def test_fuzzy_spans_do_not_cross_a_paragraph_break_the_pattern_lacks():
    """单行 old 在归一化空间里与「段尾 + 下一段段首」相连：不能当成命中。"""
    old = "靠着书架站了一会儿。他数了数这几年的卷宗"
    stats: dict[str, int] = {}
    spans = find_fuzzy_spans(CHAPTER, old, keep_line_breaks=True, stats=stats)
    assert spans == []
    assert stats["paragraph_rejected"] == 1
    # 不要求保留换行的调用方（锚点定位）行为不变。
    assert len(find_fuzzy_spans(CHAPTER, old)) == 1


def test_fuzzy_replace_across_paragraph_break_errors_instead_of_merging(editor):
    old = "直起身靠着书架站了一会儿。他数了数这几年的卷宗"
    new = "直起身靠着铁架站了一会儿。他数了数这几年的卷宗"
    with pytest.raises(EditFileError) as exc:
        editor._apply_replace(CHAPTER, {"op": "replace", "old": old, "new": new}, 0, [], [])
    assert exc.value.error_type == EDIT_ERROR_ANCHOR_NOT_FOUND
    assert "换行" in str(exc.value)


def test_fuzzy_delete_across_paragraph_break_errors_instead_of_merging(editor):
    with pytest.raises(EditFileError) as exc:
        editor._apply_delete(
            CHAPTER, {"op": "delete", "old": "站了一会儿。他数了数这几年的卷宗，"}, 0, [], []
        )
    assert "换行" in str(exc.value)


def test_fuzzy_replace_that_writes_the_paragraph_break_keeps_both_paragraphs(editor):
    """old 写出了原文的分段（只是标点有出入）：照常命中，两段仍是两段。"""
    old = "直起身靠着书架站了一会儿.\n\n　　他数了数这几年的卷宗"
    new = "直起身靠着铁架站了一会儿。\n\n　　他数了数这几年的卷宗"
    result = editor._apply_replace(CHAPTER, {"op": "replace", "old": old, "new": new}, 0, [], [])
    assert result == "\n\n".join([P1, P2.replace("书架", "铁架"), P3])


def test_approximate_match_window_does_not_cross_a_paragraph_break():
    content = (
        "　　风从窗缝里钻进来，吹得纸页沙沙地响个不停。\n"
        "　　她把台灯拧亮了一点，低头继续抄写那份名单。"
    )
    # 近似（有错字）且跨了两行的单行 pattern。
    pattern = "吹得纸张沙沙地响个不停她把台灯拧亮了一些"
    assert find_approximate_match(content, pattern, max_error_rate=0.25, min_pattern_len=8) is not None
    stats: dict[str, int] = {}
    assert (
        find_approximate_match(
            content, pattern, max_error_rate=0.25, min_pattern_len=8, keep_line_breaks=True, stats=stats
        )
        is None
    )
    assert stats["paragraph_rejected"] >= 1


def test_approximate_replace_whose_best_reading_crosses_paragraphs_is_refused(editor):
    """改写过的单行 old 盖住 P1 的段尾和 P2 的大半：段内窗口也能过阈值，但它只是
    跨段那处的一部分。拿它替换会把「嗡嗡作响。」再写一遍，必须和全部跨段一样拒绝。"""
    p1 = "　　档案室里很静，只有顶灯在嗡嗡作响。"
    p2 = "　　林舟翻到腰酸，直起身靠着书架站了一会儿，然后慢慢走到窗边看着楼下的车流发呆。"
    content = p1 + "\n\n" + p2
    old = "嗡嗡作响。林舟翻到腰疼，直起身靠着书架站了一阵子，然后慢慢走到窗边看着楼下的车流发呆。"
    stats: dict[str, int] = {}
    assert (
        find_approximate_match(
            content, old, max_error_rate=0.25, min_pattern_len=8, keep_line_breaks=True, stats=stats
        )
        is None
    )
    assert stats["paragraph_rejected"] >= 1
    with pytest.raises(EditFileError) as exc:
        editor._apply_replace(
            content,
            {"op": "replace", "old": old, "new": "嗡嗡作响。林舟翻得腰疼，走到窗边出神。"},
            0,
            [],
            [],
        )
    assert exc.value.error_type == EDIT_ERROR_ANCHOR_NOT_FOUND
    assert "段落分隔" in str(exc.value)


def test_approximate_replace_inside_one_paragraph_still_applies(editor):
    """跨段约束不误伤：old 只在第二段里、有错字，照常近似替换。"""
    p1 = "　　档案室里很静，只有顶灯在嗡嗡作响。"
    p2 = "　　林舟翻到腰酸，直起身靠着书架站了一会儿，然后慢慢走到窗边看着楼下的车流发呆。"
    content = p1 + "\n\n" + p2
    old = "林舟翻到腰疼，直起身靠着书架站了一阵子，然后慢慢走到窗边看着楼下的车流发呆。"
    applied: list[dict] = []
    result = editor._apply_replace(
        content, {"op": "replace", "old": old, "new": "林舟翻得腰疼，走到窗边出神。"}, 0, applied, []
    )
    assert applied[0]["match_mode"] == "approximate"
    assert result == p1 + "\n\n　　林舟翻得腰疼，走到窗边出神。"


def test_approximate_replace_never_merges_two_lines(editor):
    content = (
        "　　风从窗缝里钻进来，吹得纸页沙沙地响个不停。\n"
        "　　她把台灯拧亮了一点，低头继续抄写那份名单。"
    )
    old = "吹得纸张沙沙地响个不停她把台灯拧亮了一些"
    with pytest.raises(EditFileError):
        editor._apply_replace(content, {"op": "replace", "old": old, "new": "吹得纸页乱响"}, 0, [], [])


# --------------------------------------------- 插入保持本文自己的分段与缩进 --


def test_insert_after_paragraph_without_leading_break_gets_the_files_separator(editor):
    result, applied = _insert_after(editor, CHAPTER, P2, "　　窗外有人走过，脚步声很快远了。")
    assert result == "\n\n".join([P1, P2, "　　窗外有人走过，脚步声很快远了。", P3])
    assert applied[0]["layout_notes"]


def test_insert_after_with_separator_at_the_end_moves_it_to_the_front(editor):
    result, _ = _insert_after(editor, CHAPTER, P2, "　　窗外有人走过。\n\n")
    assert result == "\n\n".join([P1, P2, "　　窗外有人走过。", P3])


def test_insert_after_uses_the_files_own_single_newline_separator(editor):
    content = "\n".join([P1, P2, P3])
    result, _ = _insert_after(editor, content, P2, "\n\n　　窗外有人走过。")
    assert result == "\n".join([P1, P2, "　　窗外有人走过。", P3])


def test_insert_after_new_paragraph_without_indent_follows_the_files_indent(editor):
    result, _ = _insert_after(editor, CHAPTER, "靠着书架站了一会儿。", "\n\n窗外有人走过。")
    assert result == "\n\n".join([P1, P2, "　　窗外有人走过。", P3])


def test_insert_before_paragraph_keeps_the_next_paragraphs_indent_on_its_own_line(editor):
    """U068 的形状：下一段的「　　」不能接到新插入的段落行尾。"""
    result, _ = _insert_before(editor, CHAPTER, P3, "　　窗外有人走过。")
    assert result == "\n\n".join([P1, P2, "　　窗外有人走过。", P3])
    assert "。　　" not in result


def test_insert_before_unindented_anchor_places_the_paragraph_before_the_indent(editor):
    result, _ = _insert_before(editor, CHAPTER, "他数了数这几年的卷宗", "窗外有人走过。\n\n")
    assert result == "\n\n".join([P1, P2, "　　窗外有人走过。", P3])


def test_insert_after_anchor_ending_with_the_break_does_not_glue_onto_next_indent(editor):
    result, _ = _insert_after(editor, CHAPTER, P2 + "\n\n", "　　窗外有人走过。")
    assert result == "\n\n".join([P1, P2, "　　窗外有人走过。", P3])


def test_insert_after_plain_sentence_at_paragraph_end_still_continues_the_paragraph(editor):
    """没有任何分段标记的一句话：接在段尾，是原来的行为。"""
    result, applied = _insert_after(editor, CHAPTER, P2, "他喘了口气。")
    assert result == "\n\n".join([P1, P2 + "他喘了口气。", P3])
    assert "layout_notes" not in applied[0]


def test_insert_after_mid_paragraph_split_is_left_as_written(editor):
    """段中插入以换行开头：模型就是要在这里拆段，原样执行。"""
    content = "　　他推开门，屋里没人。桌上放着一封信。"
    result, _ = _insert_after(editor, content, "屋里没人。", "\n\n　　他愣住了。\n\n　　")
    assert result == "　　他推开门，屋里没人。\n\n　　他愣住了。\n\n　　桌上放着一封信。"


@pytest.mark.parametrize(
    "text",
    ["没拆的信。\n\n　　他走过去。\n", "没拆的信。他走过去。\n"],
)
def test_append_continuing_a_cut_off_sentence_is_not_split_into_a_new_paragraph(editor, text):
    """上一块停在半句话（流被截断、续写）：text 结尾有换行也只是下一块的起点，
    第一行要接在行尾，不能在半句中间另起一段。"""
    content = "　　他推开门，屋里的灯还亮着，桌上放着一封"
    notes: list[str] = []
    pos, written = editor._position_insert(
        content, len(content), text, direction="after", snap_sentence=False, notes=notes
    )
    assert (pos, written) == (len(content), text)
    assert notes == []


def test_append_paragraph_without_break_is_separated_from_the_last_line(editor):
    notes: list[str] = []
    pos, text = editor._position_insert(
        CHAPTER, len(CHAPTER), "　　夜深了。", direction="after", snap_sentence=False, notes=notes
    )
    assert (pos, text) == (len(CHAPTER), "\n\n　　夜深了。")
    assert notes


HEADED = "第30章 雨夜\n\n" + CHAPTER


@pytest.mark.parametrize("heading", ["第31章 天亮", "第五集", "第十二回：夜奔", "## 尾声", "Chapter 31"])
def test_append_heading_follows_the_files_unindented_headings(editor, heading):
    """本文标题顶格：追加的章节/分集标题也顶格，只有正文段落补「　　」。"""
    notes: list[str] = []
    pos, text = editor._position_insert(
        HEADED, len(HEADED), f"\n\n{heading}\n\n雨停了。", direction="after",
        snap_sentence=False, notes=notes,
    )
    assert (pos, text) == (len(HEADED), f"\n\n{heading}\n\n　　雨停了。")


def test_insert_between_paragraphs_keeps_new_heading_unindented(editor):
    result, _ = _insert_after(editor, HEADED, P3, "\n\n第31章 天亮\n\n雨停了。")
    assert result.endswith(P3 + "\n\n第31章 天亮\n\n　　雨停了。")


def test_heading_indent_follows_the_files_indented_headings(editor):
    """本文标题本身带缩进时，追加的标题照它的写法缩进。"""
    content = "　　第30章 雨夜\n\n" + CHAPTER
    notes: list[str] = []
    _pos, text = editor._position_insert(
        content, len(content), "\n\n第31章 天亮\n\n雨停了。", direction="after",
        snap_sentence=False, notes=notes,
    )
    assert text == "\n\n　　第31章 天亮\n\n　　雨停了。"


def test_body_line_that_starts_like_a_heading_is_still_indented(editor):
    """「第三部电影…」是正文不是标题：照常补段首缩进。"""
    notes: list[str] = []
    _pos, text = editor._position_insert(
        HEADED, len(HEADED), "\n\n第三部电影散场了。", direction="after",
        snap_sentence=False, notes=notes,
    )
    assert text == "\n\n　　第三部电影散场了。"


# --------------------------------------------- 插入点不落在字和标点之间 --


def test_insert_after_sentence_does_not_land_between_word_and_its_period(editor):
    result, applied = _insert_after(editor, CHAPTER, "靠着书架站了一会儿", "他喘了口气。")
    assert "站了一会儿。他喘了口气。" in result
    assert "。。" not in result
    assert applied[0]["layout_notes"]


def test_insert_after_clause_still_goes_right_after_the_anchor(editor):
    """以逗号开头的分句是要接在锚点后面的，位置不动。"""
    result, _ = _insert_after(editor, CHAPTER, "靠着书架站了一会儿", "，腿有些发麻")
    assert "站了一会儿，腿有些发麻。" in result


DIALOGUE = "　　他看着她说：“我们走吧。外面要下雨了。”她没有动。"


def test_exact_insert_before_a_line_inside_a_quote_stays_inside_the_quote(editor):
    """锚点是引语里的第一句：插进来的话也是引语，不能被挪到说话人前面当旁白。"""
    result, applied = _insert_before(editor, DIALOGUE, "我们走吧。", "别怕。")
    assert result == "　　他看着她说：“别怕。我们走吧。外面要下雨了。”她没有动。"
    assert "layout_notes" not in applied[0]


def test_exact_insert_after_a_colon_puts_the_quote_right_after_it(editor):
    """锚点以冒号收尾：新引语放在冒号后面，不能被挪进原来的引语里形成嵌套。"""
    result, _ = _insert_after(editor, DIALOGUE, "他看着她说：", "“天黑了。”")
    assert result == "　　他看着她说：“天黑了。”“我们走吧。外面要下雨了。”她没有动。"


def test_insert_after_speaker_tag_without_colon_stops_before_the_quote(editor):
    """锚点停在「说」和「：」之间：越过冒号，停在引语开始之前。"""
    result, _ = _insert_after(editor, DIALOGUE, "他看着她说", "“天黑了。”")
    assert result == "　　他看着她说：“天黑了。”“我们走吧。外面要下雨了。”她没有动。"


def test_exact_insert_after_an_interrupted_line_stays_after_it(editor):
    content = "　　“你——”他没说完，转身走了。"
    result, _ = _insert_after(editor, content, "“你——”", "她愣住了。")
    assert result == "　　“你——”她愣住了。他没说完，转身走了。"


def test_exact_insert_before_half_a_sentence_moves_to_the_sentence_start(editor):
    result, applied = _insert_before(editor, "　　他转身走了。她哭了。", "转身走了", "她叹了口气。")
    assert result == "　　她叹了口气。他转身走了。她哭了。"
    assert applied[0]["layout_notes"]


def test_approximate_insert_before_inside_a_quote_snaps_to_the_quote_start(editor):
    """近似窗口停在引语中间：往前挪到引语开头，不越过引号挪到整行开头。"""
    content = "　　他看着她说：“我们现在就走吧，外面马上要下雨了。”她没有动。"
    result, applied = _insert_before(editor, content, "现在就走吧，外头马上要下雨了", "别怕。")
    assert applied[0]["match_mode"] == "approximate"
    assert result == "　　他看着她说：“别怕。我们现在就走吧，外面马上要下雨了。”她没有动。"


def test_approximate_insert_after_snaps_to_the_end_of_the_sentence(editor):
    content = (
        "　　林舟翻到腰酸，直起身靠着书架站了一会儿，才想起水杯还落在楼下。\n\n"
        "　　他数了数这几年的卷宗，一共十七册。"
    )
    # 有错字的锚点，只覆盖到句子中间。
    anchor = "林舟翻得腰酸，直起身倚着书架站了一会儿，才想起水杯"
    result, applied = _insert_after(editor, content, anchor, "他喘了口气。")
    assert applied[0]["match_mode"] == "approximate"
    assert "落在楼下。他喘了口气。\n\n" in result


def test_approximate_replace_ending_mid_sentence_is_refused(editor):
    """old 以句号收尾，近似命中的范围却停在字中间：拒绝，不留半句残尾。"""
    content = (
        "　　风从窗缝里钻进来，吹得纸页沙沙地响个不停，像有人在翻一本很旧很旧的书。\n"
        "　　她把台灯拧亮了一点。"
    )
    # 近似窗口停在「翻」：以前替换成「风停了。一本很旧很旧的书。」，留下半句残尾。
    old = "风从窗缝里吹进来，吹得纸页沙沙作响个不停，像有人在翻书。"
    with pytest.raises(EditFileError) as exc:
        editor._apply_replace(content, {"op": "replace", "old": old, "new": "风停了。"}, 0, [], [])
    assert "句子中间" in str(exc.value)


# --------------------------------- 模糊命中时 new 的段首缩进跟原文走（live run 2 场景 C）--
#
# 原文段落没有缩进，模型写的 old 却带「　　」：模糊匹配忽略空白照样命中，new 也带着「　　」
# 写进去，一章从没有缩进变成混用。反过来原文有缩进、old 没写时，new 的后续段落会丢缩进。

PLAIN = "炉子烧了一整夜。\n\n他把钢坯夹出来，放上铁砧。\n\n窗外天快亮了。"
INDENTED = "　　炉子烧了一整夜。\n\n　　他把钢坯夹出来，放上铁砧。\n\n　　窗外天快亮了。"


def test_fuzzy_replace_with_an_invented_indent_writes_new_without_it(editor):
    applied: list[dict] = []
    edit = {"op": "replace", "old": "　　他把钢坯夹出来,放上铁砧。", "new": "　　他把钢坯夹出来，搁上铁砧。"}
    result = editor._apply_replace(PLAIN, edit, 0, applied, [])
    assert applied[0]["match_mode"] == "fuzzy"
    assert result == "炉子烧了一整夜。\n\n他把钢坯夹出来，搁上铁砧。\n\n窗外天快亮了。"
    assert "　" not in result
    assert applied[0]["layout_notes"]


def test_fuzzy_replace_that_adds_a_paragraph_follows_the_unindented_file(editor):
    edit = {
        "op": "replace",
        "old": "　　他把钢坯夹出来,放上铁砧。",
        "new": "　　他把钢坯夹出来，搁上铁砧。\n\n　　锤子落下。",
    }
    result = editor._apply_replace(PLAIN, edit, 0, [], [])
    assert "\n\n他把钢坯夹出来，搁上铁砧。\n\n锤子落下。\n\n窗外" in result
    assert "　" not in result


def test_fuzzy_replace_without_indent_in_an_indented_file_keeps_every_paragraph_indented(editor):
    edit = {
        "op": "replace",
        "old": "他把钢坯夹出来,放上铁砧。\n\n窗外天快亮了",
        "new": "他把钢坯夹出来，搁上铁砧。\n\n锤子落下。\n\n窗外天亮了",
    }
    result = editor._apply_replace(INDENTED, edit, 0, [], [])
    assert result == "　　炉子烧了一整夜。\n\n　　他把钢坯夹出来，搁上铁砧。\n\n　　锤子落下。\n\n　　窗外天亮了。"


def test_fuzzy_replace_with_the_files_own_indent_does_not_double_it(editor):
    edit = {"op": "replace", "old": "　　他把钢坯夹出来,放上铁砧。", "new": "　　他把钢坯夹出来，搁上铁砧。"}
    result = editor._apply_replace(INDENTED, edit, 0, [], [])
    assert "\n\n　　他把钢坯夹出来，搁上铁砧。\n\n" in result
    assert "　　　　" not in result


def test_approximate_replace_with_an_invented_indent_writes_new_without_it(editor):
    applied: list[dict] = []
    content = "风从窗缝里钻进来，吹得纸页沙沙地响。\n\n她把台灯拧亮了一点，又低头去看那封信。"
    edit = {"op": "replace", "old": "　　她把台灯拧亮了一些，又低头去看那封信。", "new": "　　她把台灯关了。"}
    result = editor._apply_replace(content, edit, 0, applied, [])
    assert applied[0]["match_mode"] == "approximate"
    assert result.endswith("\n\n她把台灯关了。")


def test_exact_and_mid_line_replacements_keep_new_byte_for_byte(editor):
    # 逐字命中：new 原样写入，哪怕带着缩进。
    exact = editor._apply_replace(
        PLAIN, {"op": "replace", "old": "他把钢坯夹出来，放上铁砧。", "new": "　　他把钢坯夹出来。"}, 0, [], []
    )
    assert "\n\n　　他把钢坯夹出来。\n\n" in exact
    # 从句中间开始的模糊命中不是段首，不动。
    applied: list[dict] = []
    mid = editor._apply_replace(
        PLAIN, {"op": "replace", "old": "钢坯夹出来,放上铁砧", "new": "钢坯夹了出来，搁上铁砧"}, 0, applied, []
    )
    assert applied[0]["match_mode"] == "fuzzy" and "layout_notes" not in applied[0]
    assert "\n\n他把钢坯夹了出来，搁上铁砧。\n\n" in mid
    # 原文自己就混用缩进：不猜哪一种对。
    mixed = "炉子烧了一整夜。\n　　他把钢坯夹出来，放上铁砧。"
    edit = {"op": "replace", "old": "　　炉子烧了一整夜.\n　　他把钢坯夹出来，放上铁砧。", "new": "　　炉子灭了。\n　　他走了。"}
    assert editor._apply_replace(mixed, edit, 0, [], []) == "　　炉子灭了。\n　　他走了。"



def test_fuzzy_replace_starting_mid_line_drops_the_indent_copied_from_old(editor):
    """2026-10-12 生产审计（short T2）：原文「相框。相框里…」在同一行，模型把「相框里」当段首，
    old/new 都带「　　」；模糊命中从行中间开始，new 的缩进原样写进去成了「相框。　　相框里」。"""
    content = "　　我抬头，看见墙上挂着相框。相框里是父亲，腕上戴着一块旧表。\n\n　　那块表，和钥匙是一对。\n\n　　门响了。"
    applied: list[dict] = []
    edit = {
        "op": "replace",
        "old": "　　相框里是父亲，腕上戴着一块旧表。\n\n　　那块表，和钥匙是一对。",
        "new": "　　相框里是父亲，腕上戴着一块旧表。\n\n　　表壳上有半朵云。",
    }
    result = editor._apply_replace(content, edit, 0, applied, [])
    assert applied[0]["match_mode"] == "fuzzy"
    assert result == "　　我抬头，看见墙上挂着相框。相框里是父亲，腕上戴着一块旧表。\n\n　　表壳上有半朵云。\n\n　　门响了。"


def test_fuzzy_replace_starting_on_inline_spacing_keeps_that_spacing(editor):
    """命中范围自己从行中的空白开始：那段空白属于原文，new 照抄的同一段空白要保留。"""
    applied: list[dict] = []
    edit = {"op": "replace", "old": "  hello there friend", "new": "  goodbye friend"}
    result = editor._apply_replace("He said  hello there, friend. Then left.", edit, 0, applied, [])
    assert applied[0]["match_mode"] == "fuzzy"
    assert result == "He said  goodbye friend. Then left."

# ----------------------------------------------------- DB 级：成功就必须改了 --


@pytest.fixture
def owner_project(db_session, monkeypatch):
    import database

    user = User(username="i10-owner", email="i10-owner@example.test", hashed_password="unused", email_verified=True)
    project = Project(name="编辑完整性", owner_id=user.id)
    db_session.add_all([user, project])
    db_session.commit()
    engine = db_session.get_bind()
    monkeypatch.setattr(database, "create_session", lambda: Session(engine))
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_once", lambda *_a, **_kw: None)
    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (True, 0, 10))
    return user, project


def _add_file(session, project, content, file_type="draft"):
    file = File(project_id=project.id, title="第1章", content=content, file_type=file_type)
    session.add(file)
    session.commit()
    session.refresh(file)
    return file


def test_replace_with_identical_old_and_new_reports_unchanged_instead_of_failing(db_session, owner_project):
    """old 与 new 一字不差：原文已是目标内容，如实报未修改，但不是失败（生产里模型把它原样重发到熔断）。"""
    user, project = owner_project
    chapter = _add_file(db_session, project, CHAPTER)

    result = FileEditor(db_session, user.id).edit_file(
        chapter.id, [{"op": "replace", "old": "一共十七册", "new": "一共十七册"}]
    )

    assert result["edits_applied"] == 0
    assert result["mutation_applied"] is False
    assert result["failed_edits"] == []
    assert [item["index"] for item in result["unchanged_edits"]] == [0]
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == CHAPTER


def test_identical_replace_does_not_roll_back_the_rest_of_the_batch(db_session, owner_project):
    user, project = owner_project
    chapter = _add_file(db_session, project, CHAPTER)

    result = FileEditor(db_session, user.id).edit_file(
        chapter.id,
        [
            {"op": "replace", "old": "一共十七册", "new": "一共十八册"},
            {"op": "replace", "old": "顶灯在嗡嗡作响", "new": "顶灯在嗡嗡作响"},
        ],
    )

    assert result["edits_applied"] == 1
    assert result["failed_edits"] == []
    assert [item["index"] for item in result["unchanged_edits"]] == [1]
    db_session.expire_all()
    assert "一共十八册" in db_session.get(File, chapter.id).content


def test_identical_replace_never_takes_the_fuzzy_path(db_session, owner_project):
    """F-01：old 与 new 相同但漏抄了原文的逗号和句号。原来走模糊命中，把原文的「，」「。」
    删掉还报成功；现在不做近似改动，原文逐字找不到就如实报找不到，正文一字不动。"""
    user, project = owner_project
    original = "　　林舟点头说：“这个方案很好，真的。”然后走了。"
    chapter = _add_file(db_session, project, original)

    with pytest.raises(EditBatchError) as exc:
        FileEditor(db_session, user.id).edit_file(
            chapter.id, [{"op": "replace", "old": "这个方案很好真的”", "new": "这个方案很好真的”"}]
        )

    assert exc.value.failed_edits[0]["error_type"] == EDIT_ERROR_ANCHOR_NOT_FOUND
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == original


@pytest.mark.parametrize("new_key", ["text", "content", "new_text", "replacement"])
def test_identical_replace_under_a_new_alias_never_takes_the_fuzzy_path(db_session, owner_project, new_key):
    """F-01 补漏：模型把替换文本写在 new 的别名键下，old 与它相同时同样不能走模糊命中删标点。"""
    user, project = owner_project
    original = "　　林舟点头说：“这个方案很好，真的。”然后走了。"
    chapter = _add_file(db_session, project, original)

    with pytest.raises(EditBatchError) as exc:
        FileEditor(db_session, user.id).edit_file(
            chapter.id, [{"op": "replace", "old": "这个方案很好真的”", new_key: "这个方案很好真的”"}]
        )

    assert exc.value.failed_edits[0]["error_type"] == EDIT_ERROR_ANCHOR_NOT_FOUND
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == original


def test_corner_to_curly_conversion_is_applied_not_normalized_back(db_session, owner_project):
    """U079 的形状：「」稿件里把对白改成 “”，规范化器不能再把它改回「」还报成功。"""
    user, project = owner_project
    original = "　　「回来了？」母亲问。\n\n　　「嗯。」他把伞靠在门边。"
    chapter = _add_file(db_session, project, original)

    result = FileEditor(db_session, user.id).edit_file(
        chapter.id,
        [
            {"op": "replace", "old": "「回来了？」母亲问。", "new": '"回来了？"母亲问。'},
            {"op": "replace", "old": "「嗯。」他把伞", "new": '"嗯。"他把伞'},
        ],
        normalize_quotes=True,
    )

    assert result["edits_applied"] == 2
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == (
        "　　“回来了？”母亲问。\n\n　　“嗯。”他把伞靠在门边。"
    )


def test_halfwidth_rewrite_in_corner_draft_still_follows_the_draft_style(db_session, owner_project):
    """改了字的编辑照旧按稿件体例规范引号（只有「只改引号」才视为体例转换）。"""
    user, project = owner_project
    chapter = _add_file(db_session, project, "　　「回来了？」母亲问。")

    FileEditor(db_session, user.id).edit_file(
        chapter.id,
        [{"op": "replace", "old": "「回来了？」母亲问。", "new": '"吃了吗？"母亲问。'}],
        normalize_quotes=True,
    )

    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == "　　「吃了吗？」母亲问。"


def test_curly_to_ascii_noop_is_reported_honestly(db_session, owner_project):
    user, project = owner_project
    original = "　　“回来了？”母亲问。"
    chapter = _add_file(db_session, project, original)

    with pytest.raises(EditBatchError) as exc:
        FileEditor(db_session, user.id).edit_file(
            chapter.id,
            [{"op": "replace", "old": "“回来了？”母亲问。", "new": '"回来了？"母亲问。'}],
            normalize_quotes=True,
        )
    assert exc.value.failed_edits[0]["error_type"] == EDIT_ERROR_NO_CHANGE
    assert "引号" in exc.value.failed_edits[0]["error"]
    db_session.expire_all()
    assert db_session.get(File, chapter.id).content == original
