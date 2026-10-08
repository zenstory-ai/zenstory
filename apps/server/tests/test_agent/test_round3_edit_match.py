"""Round3 回归测试：编辑与文本匹配（G8 edit-match）。

覆盖的确认缺陷：
- #6  近似匹配工作量守卫漏算 O(pattern_len) 因子 → 单次 edit_file 把事件循环占死几十秒
- #21 find_unique_line_span 的「包含即 0.999」捷径让极短段落（章节标题）击败真正的目标段落
- #31 replace/delete 完全忽略 occurrence，歧义守卫只留 replace_all 这条破坏性出路
- #28（锁侧）SQLite 下在事件循环线程上同步获取 threading.Lock，工作线程持锁时全进程停摆

外加同一 bug 模式的其他实例：
- replace_all / ignore_punct_whitespace 用朴素真值判断，LLM 传来的 "false" 会被判真
- suggest_similar_lines 的同款包含捷径让短标题挤掉真正相关的建议
"""

import threading
import time

import pytest

from agent.tools.file_ops.edit import (
    EVENT_LOOP_LOCK_WAIT_SECONDS,
    FileEditor,
    FileWriteBusyError,
    acquire_file_write_lock,
    file_write_lock,
)
from agent.tools.file_ops.text_matching import (
    find_approximate_match,
    find_unique_line_span,
    normalize_for_fuzzy_match,
    suggest_similar_lines,
)
from agent.tools.tool_schemas import EDIT_FILE_TOOL


@pytest.fixture
def editor() -> FileEditor:
    """只测纯文本编辑逻辑的 FileEditor（_apply_* 不触碰 session）。"""
    return FileEditor(session=None)


# ---------------------------------------------------------------------------
# #6 近似匹配工作量守卫
# ---------------------------------------------------------------------------

# 纯汉字语料：不含标点与空白，归一化长度 == 原始长度，构造精确长度的输入更直观。
_PLAIN = (
    "夜风从窗缝里钻进来带着河面上潮湿的腥气林晚把煤油灯的芯挑得更亮一些"
    "灯影在土墙上摇晃像一只不肯睡去的手她低头看着桌上那封没有署名的信"
    "指尖在纸角来回摩挲纸已经被汗浸得有些软了远处传来两声犬吠随后便是长久的寂静"
)


def _plain_content(length: int) -> str:
    text = (_PLAIN * (length // len(_PLAIN) + 2))[:length]
    assert len(normalize_for_fuzzy_match(text)[0]) == length
    return text


def _mutated_pattern(content: str, start: int, length: int) -> str:
    """从 content 截一段并改掉几个字，模拟模型记错个别字（近似匹配的目标场景）。"""
    chars = list(content[start:start + length])
    for i in range(0, len(chars), 12):
        chars[i] = "霜"
    return "".join(chars)


def test_approx_guard_accounts_for_pattern_len_factor():
    """守卫必须把 pattern_len 计入工作量，否则「放行的那一档」恰是最慢的一档。

    该输入的旧公式估算值 window_count(121) × 5000 = 605000 ≤ 800000（放行），
    真实字符级操作量却是 121 × 5000 × 200 ≈ 1.21 亿，实测阻塞 20 秒以上。
    """
    content = _plain_content(5000)
    pattern = _mutated_pattern(content, 2000, 200)
    assert len(normalize_for_fuzzy_match(pattern)[0]) == 200

    started = time.monotonic()
    result = find_approximate_match(
        content, pattern, max_error_rate=0.25, min_pattern_len=8
    )
    elapsed = time.monotonic() - started

    assert result is None
    assert elapsed < 1.0, f"守卫未拦截，耗时 {elapsed:.2f}s"


def test_approx_wall_clock_deadline_stops_a_long_scan(monkeypatch):
    """即便静态工作量估算放行，wall-clock 超时也必须把扫描掐断。"""
    monkeypatch.setattr(
        "agent.tools.file_ops.text_matching.MAX_APPROX_WORK", 10**12
    )
    monkeypatch.setattr(
        "agent.tools.file_ops.text_matching.MAX_APPROX_SECONDS", 0.05
    )

    content = _plain_content(5000)
    pattern = _mutated_pattern(content, 2000, 200)

    started = time.monotonic()
    result = find_approximate_match(
        content, pattern, max_error_rate=0.25, min_pattern_len=8
    )
    elapsed = time.monotonic() - started

    assert result is None
    assert elapsed < 1.0, f"超时未生效，耗时 {elapsed:.2f}s"


def test_approx_guard_does_not_over_block_normal_edits():
    """守卫收紧后，正常尺寸的「个别字记错」仍必须能近似命中。"""
    content = "他缓缓转过身，看着窗外的大雨，心中一片茫然，久久没有说话。"
    pattern = "他缓缓转过身看着窗外的大雨心中一片茫然久久没有讲话"
    match = find_approximate_match(
        content, pattern, max_error_rate=0.25, min_pattern_len=8
    )
    assert match is not None
    assert match[2] >= 0.75


# ---------------------------------------------------------------------------
# #21 段落兜底匹配的包含捷径
# ---------------------------------------------------------------------------

_BUG21_CONTENT = "第三章\n\n雨下了一整夜，屋檐滴水不停。\n\n天亮时，林风背上行囊，推门而出。"
_BUG21_ANCHOR = "第三章结尾林风推门而出之后"


def test_short_title_paragraph_cannot_win_by_containment():
    """3 字的章节标题被锚点顺带包含，不得再拿满分击败真正的目标段落。"""
    assert find_unique_line_span(_BUG21_CONTENT, _BUG21_ANCHOR) is None


def test_insert_after_refuses_instead_of_landing_on_chapter_title(editor):
    """insert_after 必须报错让模型换锚点，而不是静默插到章节标题之后。"""
    applied: list[dict] = []
    warnings: list[str] = []
    with pytest.raises(ValueError, match="找不到插入锚点"):
        editor._apply_insert_after(
            _BUG21_CONTENT,
            {"op": "insert_after", "anchor": _BUG21_ANCHOR, "text": "\n\n他回望院子。"},
            0,
            applied,
            warnings,
        )
    assert applied == []


def test_insert_before_refuses_instead_of_landing_on_chapter_title(editor):
    """insert_before 走同一条兜底链路，同样不得被短标题骗到。"""
    with pytest.raises(ValueError, match="找不到插入锚点"):
        editor._apply_insert_before(
            _BUG21_CONTENT,
            {"op": "insert_before", "anchor": _BUG21_ANCHOR, "text": "X"},
            0,
            [],
            [],
        )


def test_containment_shortcut_still_applies_to_similar_lengths():
    """长度接近的包含关系（模型少抄了结尾几个字）仍应判为同一段。"""
    content = "第三章\n\n天亮时，林风背上行囊，推门而出，走进濛濛细雨里。"
    span = find_unique_line_span(content, "天亮时，林风背上行囊，推门而出，走进濛濛细雨")
    assert span is not None
    start, end, score = span
    assert content[start:end] == "天亮时，林风背上行囊，推门而出，走进濛濛细雨里。"
    assert score >= 0.9


def _long_content_forcing_paragraph_fallback() -> tuple[str, str]:
    """构造一份 > MAX_APPROX_CONTENT_LEN 的正文，使近似匹配被跳过、只剩段落兜底。"""
    filler = "\n\n".join(_plain_content(200) for _ in range(30))
    target = "天亮时林风背上行囊推门而出走进濛濛细雨里再没有回头看那座空荡荡的院子"
    content = filler + "\n\n" + target
    assert len(normalize_for_fuzzy_match(content)[0]) > 5000
    return content, target


def test_fuzzy_paragraph_reports_confidence_and_fallback(editor):
    """兜底段落匹配必须自报置信度与兜底性质，不能伪装成确定无疑的唯一匹配。"""
    content, target = _long_content_forcing_paragraph_fallback()
    # 中间改一个字：既不是精确/模糊子串命中，也让 SequenceMatcher 仍有 ~0.97
    anchor = target.replace("推门而出", "排门而出")
    applied: list[dict] = []

    result = editor._apply_insert_after(
        content, {"op": "insert_after", "anchor": anchor, "text": "【新增】"}, 0, applied, []
    )

    assert result == content.replace(target, target + "【新增】")
    assert len(applied) == 1
    detail = applied[0]
    assert detail["match_mode"] == "fuzzy_paragraph"
    assert detail["fallback"] is True
    assert 0.9 <= detail["confidence"] < 1.0


def test_suggest_similar_lines_does_not_rank_short_title_first():
    """同款包含捷径也污染了建议列表：短标题不得挤到真正相关的段落前面。"""
    suggestions = suggest_similar_lines(_BUG21_CONTENT, _BUG21_ANCHOR, max_items=3)
    assert suggestions
    assert suggestions[0] != "第三章"


# ---------------------------------------------------------------------------
# #31 replace/delete 的 occurrence 支持
# ---------------------------------------------------------------------------

_REPEATED = "A他默默地点了点头。B他默默地点了点头。C他默默地点了点头。D他默默地点了点头。E他默默地点了点头。"


def test_replace_exact_honours_occurrence(editor):
    applied: list[dict] = []
    result = editor._apply_replace(
        _REPEATED,
        {"op": "replace", "old": "他默默地点了点头。", "new": "NEW", "occurrence": 3},
        0,
        applied,
        [],
    )
    assert result == "A他默默地点了点头。B他默默地点了点头。CNEWD他默默地点了点头。E他默默地点了点头。"
    assert applied[0]["occurrence"] == 3
    assert applied[0]["match_count"] == 5


def test_replace_fuzzy_honours_occurrence(editor):
    """模糊匹配分支（标点差异导致 exact 落空）同样要支持 occurrence。"""
    applied: list[dict] = []
    result = editor._apply_replace(
        _REPEATED,
        {"op": "replace", "old": "他默默地，点了点头", "new": "NEW", "occurrence": 2},
        0,
        applied,
        [],
    )
    assert result == "A他默默地点了点头。BNEW。C他默默地点了点头。D他默默地点了点头。E他默默地点了点头。"
    assert applied[0]["match_mode"] == "fuzzy"
    assert applied[0]["occurrence"] == 2


def test_delete_exact_honours_occurrence(editor):
    applied: list[dict] = []
    result = editor._apply_delete(
        _REPEATED,
        {"op": "delete", "old": "他默默地点了点头。", "occurrence": 2},
        0,
        applied,
        [],
    )
    assert result == "A他默默地点了点头。BC他默默地点了点头。D他默默地点了点头。E他默默地点了点头。"
    assert applied[0]["occurrence"] == 2


def test_delete_fuzzy_honours_occurrence(editor):
    applied: list[dict] = []
    result = editor._apply_delete(
        _REPEATED,
        {"op": "delete", "old": "他默默地，点了点头。", "occurrence": 5},
        0,
        applied,
        [],
    )
    # old 以「。」结尾：模糊命中后原文对应的句号一并删除，不再留下孤立的「。」。
    assert result == "A他默默地点了点头。B他默默地点了点头。C他默默地点了点头。D他默默地点了点头。E"
    assert applied[0]["match_mode"] == "fuzzy"
    assert applied[0]["matched_original"] == "他默默地点了点头。"


def test_replace_ambiguity_message_recommends_occurrence(editor):
    """歧义提示必须先给出非破坏性的 occurrence 出路，并标出每处序号。"""
    with pytest.raises(ValueError) as exc:
        editor._apply_replace(
            _REPEATED, {"op": "replace", "old": "他默默地点了点头。", "new": "N"}, 0, [], []
        )
    message = str(exc.value)
    assert "多个位置" in message
    assert "occurrence=N" in message
    assert "[occurrence=1]" in message
    # replace_all 只能作为「确实要全改」的次选出现在 occurrence 之后
    assert message.index("occurrence=N") < message.index("replace_all")


def test_delete_ambiguity_message_recommends_occurrence(editor):
    with pytest.raises(ValueError) as exc:
        editor._apply_delete(
            _REPEATED, {"op": "delete", "old": "他默默地点了点头。"}, 0, [], []
        )
    message = str(exc.value)
    assert "occurrence=N" in message
    assert "[occurrence=1]" in message


def test_replace_occurrence_out_of_range_aborts(editor):
    with pytest.raises(ValueError, match="occurrence out of range"):
        editor._apply_replace(
            _REPEATED,
            {"op": "replace", "old": "他默默地点了点头。", "new": "N", "occurrence": 9},
            0,
            [],
            [],
        )


def test_replace_all_and_occurrence_are_mutually_exclusive(editor):
    with pytest.raises(ValueError, match="不能同时使用"):
        editor._apply_replace(
            _REPEATED,
            {
                "op": "replace",
                "old": "他默默地点了点头。",
                "new": "N",
                "occurrence": 2,
                "replace_all": True,
            },
            0,
            [],
            [],
        )


def test_edit_file_schema_declares_occurrence_and_match_options():
    """模型看不见的参数等于不存在：occurrence 等必须出现在 schema 里。"""
    item_props = EDIT_FILE_TOOL["input_schema"]["properties"]["edits"]["items"]["properties"]
    assert item_props["occurrence"]["type"] == "integer"
    assert item_props["occurrence"]["minimum"] == 1
    assert item_props["match_mode"]["type"] == "string"
    assert item_props["ignore_punct_whitespace"]["type"] == "boolean"
    # 所有布尔参数都必须声明为 boolean（strict_json_schema=False 时这是模型的唯一提示）
    assert item_props["replace_all"]["type"] == "boolean"
    assert EDIT_FILE_TOOL["input_schema"]["properties"]["continue_on_error"]["type"] == "boolean"
    # occurrence 是首选出路，描述里必须点名它优先于 replace_all
    assert "occurrence" in item_props["replace_all"]["description"]


# ---------------------------------------------------------------------------
# 同一 bug 模式的其他实例：LLM 传来的字符串布尔
# ---------------------------------------------------------------------------


def test_replace_all_string_false_is_not_truthy(editor):
    """replace_all="false" 必须当假处理，否则「改一处」会变成「全改」。"""
    with pytest.raises(ValueError, match="多个位置"):
        editor._apply_replace(
            "dup and dup",
            {"op": "replace", "old": "dup", "new": "X", "replace_all": "false"},
            0,
            [],
            [],
        )


def test_ignore_punct_whitespace_string_false_is_honoured(editor):
    """ignore_punct_whitespace="false" 必须真的关掉标点忽略。"""
    content = "他说：走吧我们出发。"
    edit = {
        "op": "replace",
        "old": "他说走吧我们",  # 归一化后 6 字：够模糊匹配，但短于近似匹配的下限
        "new": "X",
        "ignore_punct_whitespace": "false",
    }
    with pytest.raises(ValueError, match="找不到要替换的原文片段"):
        editor._apply_replace(content, edit, 0, [], [])

    # 默认（未指定）仍然忽略标点，能够命中
    applied: list[dict] = []
    result = editor._apply_replace(
        content,
        {"op": "replace", "old": "他说走吧我们", "new": "X"},
        0,
        applied,
        [],
    )
    assert result == "X出发。"
    assert applied[0]["ignore_punct_whitespace"] is True


# ---------------------------------------------------------------------------
# #28 锁侧：事件循环线程上不得同步等待写锁
# ---------------------------------------------------------------------------


async def test_event_loop_thread_does_not_block_on_held_write_lock():
    """工作线程持锁时，事件循环线程必须有界等待后失败，而不是陪着一起停摆。"""
    file_id = "round3-edit-match-lock-a"
    lock = file_write_lock(file_id)
    release = threading.Event()

    def holder() -> None:
        with lock:
            release.wait(5.0)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    try:
        time.sleep(0.05)  # 确保工作线程已经拿到锁
        started = time.monotonic()
        with pytest.raises(FileWriteBusyError):
            with acquire_file_write_lock(file_id):
                pass
        elapsed = time.monotonic() - started
        assert elapsed < EVENT_LOOP_LOCK_WAIT_SECONDS + 0.5
    finally:
        release.set()
        thread.join(5)


async def test_edit_file_fails_fast_on_event_loop_when_lock_held():
    """FileEditor.edit_file 走的就是这条获取路径（SQLite 分支）。"""
    file_id = "round3-edit-match-lock-b"
    lock = file_write_lock(file_id)
    release = threading.Event()

    def holder() -> None:
        with lock:
            release.wait(5.0)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    try:
        time.sleep(0.05)
        started = time.monotonic()
        # session=None：拿不到锁就必须在碰数据库之前失败
        editor = FileEditor(session=None)
        with pytest.raises(FileWriteBusyError):
            editor.edit_file(id=file_id, edits=[{"op": "append", "text": "x"}])
        assert time.monotonic() - started < EVENT_LOOP_LOCK_WAIT_SECONDS + 0.5
    finally:
        release.set()
        thread.join(5)


def test_worker_thread_still_waits_for_the_lock():
    """非事件循环线程上语义不变：照常阻塞等待，不会被 busy 错误踢出。"""
    file_id = "round3-edit-match-lock-c"
    lock = file_write_lock(file_id)
    outcome: list[str] = []

    def waiter() -> None:
        try:
            with acquire_file_write_lock(file_id):
                outcome.append("acquired")
        except Exception as exc:  # pragma: no cover - 出现即测试失败
            outcome.append(f"error:{exc!r}")

    lock.acquire()
    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    try:
        time.sleep(0.2)
        assert outcome == [], "工作线程不应被有界等待踢出"
    finally:
        lock.release()
    thread.join(5)
    assert outcome == ["acquired"]
