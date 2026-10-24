"""Protocol-only reply transfer; prose punctuation never determines workflow state."""

import pytest


@pytest.mark.parametrize("reply", ["下一步可先整理人物关系。", "你想先看哪个方向？", "结尾可以留白\n- 克制\n- 开放"])
def test_reply_tail_keeps_all_post_file_prose_without_classifying_questions(reply):
    from agent.graph.completion_evidence import author_reply_tail

    assert author_reply_tail(f"前言<file>角色说：你是谁？</file>\n{reply}\n[TASK_COMPLETE]") == reply


def test_reply_tail_does_not_leak_open_file_content_or_prior_file_body():
    from agent.graph.completion_evidence import author_reply_tail

    assert author_reply_tail("<file>旧正文</file>说明<file>未完成正文") == ""
    assert author_reply_tail("<file>正文</file>") == ""
    assert author_reply_tail("给作者的原话") == "给作者的原话"


def test_reply_tail_budget_includes_truncation_marker():
    from agent.graph.completion_evidence import author_reply_tail

    result = author_reply_tail("<file>正文</file>" + "甲" * 1000)
    assert len(result) == 600
    assert result.startswith("甲")
    assert "截断" in result


def test_long_reply_preserves_opening_and_final_author_decision_without_question_detection():
    from agent.graph.completion_evidence import author_reply_tail

    result = author_reply_tail("开头说明：情节保持不变。" + "中间解释" * 300 + "最后请作者决定保留哪条线索。")
    assert result.startswith("开头说明：情节保持不变。")
    assert result.endswith("最后请作者决定保留哪条线索。")
    assert len(result) == 600


def test_nonstandard_file_marker_normalization_never_logs_reply_content(caplog):
    from agent.graph.completion_evidence import author_reply_tail

    private = "synthetic-private-author-prose"
    assert author_reply_tail(f"{private}<File>正文</File>交接说明") == "交接说明"
    fields = [getattr(record, "custom_fields", {}) for record in caplog.records]
    assert private not in repr(fields)


def test_scope_directive_keeps_all_cumulative_constraints_without_comparing_meaning():
    from agent.graph.writing_graph import _build_scope_directive

    directive = _build_scope_directive({
        "scope": "只处理第一章", "write_content": False,
        "scope_constraints": ["只处理第一章", "只检查开头，保留结尾"],
    })
    assert "只检查开头，保留结尾" in directive
    assert "不要撰写或续写章节正文" in directive
