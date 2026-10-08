"""Runtime contracts and author-scope precedence for active shared guidance.

These checks protect prompt policy/assembly, not generated literary quality.
"""
from agent.prompts.base import DIALOGUE_GUIDELINES, UNIFIED_EXECUTION_PROTOCOL, get_base_prompt
from agent.prompts.subagents import HOOK_DESIGNER_PROMPT, PLANNER_PROMPT, QUALITY_REVIEWER_PROMPT, WRITER_PROMPT


def test_scope_and_canon_outrank_generic_craft():
    assert "只讨论" in UNIFIED_EXECUTION_PROTOCOL
    assert "未确认" in UNIFIED_EXECUTION_PROTOCOL
    assert "因果" in WRITER_PROMPT
    assert "视角" in WRITER_PROMPT
    assert "证据" in HOOK_DESIGNER_PROMPT
    assert "局部兑现" in HOOK_DESIGNER_PROMPT
    assert "保留" in QUALITY_REVIEWER_PROMPT
    assert "原文" in QUALITY_REVIEWER_PROMPT


def test_no_universal_creative_quotas_or_word_blacklists():
    combined = "\n".join([PLANNER_PROMPT, WRITER_PROMPT, HOOK_DESIGNER_PROMPT, QUALITY_REVIEWER_PROMPT, DIALOGUE_GUIDELINES])
    for obsolete in ["每章至少", "每 7 章", "每 3 章", "绝对禁止使用以下AI", "九维期待感评分", "追更指数达到 7 分以上", "禁止反复使用'XX说'"]:
        assert obsolete not in combined


def test_renderer_keeps_real_file_protocol_and_folders():
    prompt = get_base_prompt("project-test", {"draft": "d-id", "character": "c-id"}, {
        "role_definition": "DB ROLE", "capabilities": "DB CAPABILITIES",
        "directory_structure": "DB {draft}", "content_structure": "DB STRUCTURE",
        "file_types": "DB TYPES", "writing_guidelines": "DB GUIDELINES",
        "include_dialogue_guidelines": False,
    })
    for expected in ["project-test", "DB d-id", "DB GUIDELINES", "<file>", "</file>", "edit_file", "query_files", "parallel_execute", "parent_id='c-id'"]:
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
    # 语义检索可能被禁用：不能再把 hybrid_search 宣传成首选
    assert "混合检索（向量 + 关键词融合）" not in prompt
    assert "可能未启用" in prompt
    # 旁白：只在本轮第一次调用工具前说一句
    assert "本轮第一次调用工具前" in prompt
