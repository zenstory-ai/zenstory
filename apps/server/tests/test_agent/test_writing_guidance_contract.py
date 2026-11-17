"""Runtime contracts and author-scope precedence for active shared guidance.

These checks protect prompt policy/assembly, not generated literary quality.
"""
from agent.loop.prompts import LOOP_WORKING_RULES
from agent.prompts.base import DIALOGUE_GUIDELINES, UNIFIED_EXECUTION_PROTOCOL, get_base_prompt

_CONFIG = {
    "role_definition": "R", "capabilities": "C", "directory_structure": "{draft}",
    "content_structure": "S", "file_types": "T", "writing_guidelines": "G",
}


def test_scope_and_canon_outrank_generic_craft():
    assert "只讨论" in UNIFIED_EXECUTION_PROTOCOL
    assert "未确认" in UNIFIED_EXECUTION_PROTOCOL


def test_no_universal_creative_quotas_or_word_blacklists():
    combined = "\n".join([get_base_prompt("project-test", {"draft": "d-id"}, _CONFIG), LOOP_WORKING_RULES, DIALOGUE_GUIDELINES])
    for obsolete in ["每章至少", "每 7 章", "每 3 章", "绝对禁止使用以下AI", "九维期待感评分", "追更指数达到 7 分以上", "禁止反复使用'XX说'"]:
        assert obsolete not in combined


def test_renderer_keeps_real_file_protocol_and_folders():
    prompt = get_base_prompt("project-test", {"draft": "d-id", "character": "c-id"}, {
        "role_definition": "DB ROLE", "capabilities": "DB CAPABILITIES",
        "directory_structure": "DB {draft}", "content_structure": "DB STRUCTURE",
        "file_types": "DB TYPES", "writing_guidelines": "DB GUIDELINES",
        "include_dialogue_guidelines": False,
    })
    for expected in ["project-test", "DB d-id", "DB GUIDELINES", "<file>", "</file>", "edit_file", "query_files", "write_chapter", "parent_id='c-id'"]:
        assert expected in prompt
    assert DIALOGUE_GUIDELINES not in prompt


def test_read_budget_prevents_redundant_rereads():
    """生产事故回归：模型反复 query_files 重读已在上下文里的全文。"""
    assert "### 读取预算" in UNIFIED_EXECUTION_PROTOCOL
    assert "同一文件本轮最多读取一次全文" in UNIFIED_EXECUTION_PROTOCOL
    assert "修改前用 query_files 读取目标" not in UNIFIED_EXECUTION_PROTOCOL
    assert "修改前确保持有目标原文" in UNIFIED_EXECUTION_PROTOCOL

    prompt = get_base_prompt("project-test", {"draft": "d-id"}, {
        "role_definition": "R", "capabilities": "C", "directory_structure": "{draft}",
        "content_structure": "S", "file_types": "T", "writing_guidelines": "G",
    })
    # 已移除语义工具，保留文件查询协议。
    assert "混合检索（向量 + 关键词融合）" not in prompt
    assert "hybrid_search" not in prompt
    assert "query_files" in prompt
    # 旁白：只在本轮第一次调用工具前说一句
    assert "本轮第一次调用工具前" in prompt


def test_file_protocol_is_stated_once_without_contradicting_the_one_line_rule():
    """O-4：<file> 协议只在一处说明；「整轮一句过程说明」不再和「写正文前禁止说话」打架。"""
    prompt = get_base_prompt("project-test", {"draft": "d-id"}, {
        "role_definition": "R", "capabilities": "C", "directory_structure": "{draft}",
        "content_structure": "S", "file_types": "T", "writing_guidelines": "G",
    })
    assert prompt.count("`<file/>`") == 1
    assert prompt.count("下一次回复第一行就输出 `<file>`") == 1
    assert "禁止**说话" not in prompt
    assert "严格静默" not in prompt
