"""Prompt/tool alignment regression tests.

These tests ensure that the system prompt does not instruct the model to call
tools that are not available in the Tool registry.
"""

from os.path import commonprefix
from uuid import UUID, uuid5

import pytest

from agent.prompts.base import (
    AUTHOR_REPLY_PROTOCOL,
    DIALOGUE_GUIDELINES,
    IMPACT_ANALYSIS_EXAMPLES,
    OUTPUT_PROTOCOL,
    PRODUCT_IDENTITY_PROTOCOL,
    TOOL_USAGE_PROTOCOL,
    UNIFIED_EXECUTION_PROTOCOL,
    get_base_prompt,
    get_folder_reference,
)


@pytest.mark.unit
@pytest.mark.parametrize("folder_types", [
    ("lore", "character", "outline", "draft", "script", "material"),
    ("lore", "character", "outline", "draft", "material"),
    ("lore", "character", "outline", "script", "material"),
])
def test_project_ids_preserve_shared_protocol_prefix_and_folder_isolation(folder_types):
    """项目 UUID 不能挡在共用协议之前，项目目录仍必须各自准确。"""
    project_ids = (
        "09ab1059-6f47-440c-8ef3-8a47bfb38943",
        "c236c601-6b1a-46c6-b870-f3b978d8cb16",
    )
    folders = [
        {kind: str(uuid5(UUID(project_id), kind)) for kind in folder_types}
        for project_id in project_ids
    ]
    config = {
        "role_definition": "你是一位资深网文编辑",
        "capabilities": "项目写作能力",
        "directory_structure": "\n".join(f"{kind}: {{{kind}}}" for kind in folder_types),
        "content_structure": "项目内容结构",
        "file_types": "项目文件类型",
        "writing_guidelines": "项目写作指导",
    }
    rendered = [
        get_base_prompt(project_id, folder_ids, config)
        for project_id, folder_ids in zip(project_ids, folders, strict=True)
    ]

    shared_prefix = commonprefix(rendered)
    for protocol in (
        PRODUCT_IDENTITY_PROTOCOL, OUTPUT_PROTOCOL, AUTHOR_REPLY_PROTOCOL, UNIFIED_EXECUTION_PROTOCOL,
    ):
        assert protocol in shared_prefix

    fixed_tool_rules = TOOL_USAGE_PROTOCOL.rstrip()
    assert fixed_tool_rules in shared_prefix, "项目目录 ID 不应打断固定的流式写入、标题或交互协议"

    static_sections = [
        config["role_definition"], PRODUCT_IDENTITY_PROTOCOL, OUTPUT_PROTOCOL,
        AUTHOR_REPLY_PROTOCOL, UNIFIED_EXECUTION_PROTOCOL, fixed_tool_rules,
        config["capabilities"], config["content_structure"], config["file_types"],
        config["writing_guidelines"], DIALOGUE_GUIDELINES, IMPACT_ANALYSIS_EXAMPLES,
    ]
    for section in static_sections:
        assert section in shared_prefix, "项目目录 ID 不应打断固定写作规则的公共前缀"
    assert [shared_prefix.index(section) for section in static_sections] == sorted(
        shared_prefix.index(section) for section in static_sections
    ), "静态协议与写作规则的相对顺序不能改变"

    normalized = []
    for index, prompt in enumerate(rendered):
        assert f"## 当前项目 ID: {project_ids[index]}" in prompt
        assert prompt.count(project_ids[index]) == 1
        assert project_ids[1 - index] not in prompt
        assert config["directory_structure"].format(**folders[index]) in prompt
        normalized_prompt = prompt.replace(project_ids[index], "<project_id>")
        for kind, folder_id in folders[index].items():
            assert f"parent_id='{folder_id}'" in prompt
            assert folders[1 - index][kind] not in prompt
            normalized_prompt = normalized_prompt.replace(folder_id, f"<{kind}_folder_id>")
        normalized.append(normalized_prompt)
    assert normalized[0] == normalized[1]


@pytest.mark.unit
@pytest.mark.parametrize("folder_ids", [{}, {"draft": "actual-draft-id"}, {"character": "actual-character-id"}])
@pytest.mark.parametrize("character_detection", [None, "数据库自定义角色规则：只按作者要求建立档案。"])
def test_base_prompt_preserves_character_rules_and_missing_folder_behavior(folder_ids, character_detection):
    config = {
        "role_definition": "数据库角色", "capabilities": "数据库能力",
        "directory_structure": "\n".join(f"{kind}: {{{kind}}}" for kind in folder_ids),
        "content_structure": "数据库内容结构", "file_types": "数据库文件类型",
        "writing_guidelines": "数据库写作指导", "include_dialogue_guidelines": False,
    }
    if character_detection is not None:
        config["character_detection"] = character_detection

    prompt = get_base_prompt("current-project", folder_ids, config)

    if character_detection is not None:
        assert prompt.count(character_detection) == 1
        assert "## 角色资料与范围" not in prompt
        assert "不得猜测角色目录 ID" not in prompt
    elif "character" in folder_ids:
        assert "只在作者要求建立资料" in prompt
        assert "新文件仍遵守 create_file → <file> → 内容 → </file>，不得留空。" in prompt
        assert "parent_id='actual-character-id'" in prompt
    else:
        assert "新角色资料仅在作者要求时创建；不得猜测角色目录 ID。" in prompt
        assert "## 角色资料与范围" not in prompt
    assert DIALOGUE_GUIDELINES not in prompt
    for field in ("role_definition", "capabilities", "content_structure", "file_types", "writing_guidelines"):
        assert config[field] in prompt


@pytest.mark.unit
@pytest.mark.parametrize("folder_types", [
    (),
    ("lore",),
    ("character",),
    ("outline",),
    ("draft",),
    ("script",),
    ("material",),
    ("material", "draft", "character"),
    ("lore", "character", "outline", "draft", "script", "material"),
])
def test_tool_guide_keeps_only_available_folder_mappings(folder_types):
    labels = {
        "lore": "创建设定(lore)",
        "character": "创建角色(character)",
        "outline": "创建大纲(outline)",
        "draft": "创建正文(draft)",
        "script": "创建剧本(script)",
        "material": "创建素材(snippet)",
    }
    folder_ids = {kind: str(uuid5(UUID("09ab1059-6f47-440c-8ef3-8a47bfb38943"), kind)) for kind in folder_types}
    guide = get_folder_reference(folder_ids)
    mappings = [line for line in guide.splitlines() if line.startswith("- 创建") and " → parent_id=" in line]

    assert mappings == [f"- {label} → parent_id='{folder_ids[kind]}'" for kind, label in labels.items() if kind in folder_ids]


@pytest.mark.unit
def test_base_prompt_does_not_suggest_removed_tools():
    prompt = get_base_prompt(
        project_id="proj-1",
        folder_ids={
            "draft": "draft-folder",
            "outline": "outline-folder",
            "character": "character-folder",
            "lore": "lore-folder",
            "material": "material-folder",
        },
        config={"role_definition": "Test role", "capabilities": "Test capabilities",
                "directory_structure": "Draft folder: {draft}", "content_structure": "Test structure",
                "file_types": "draft", "writing_guidelines": "Test guidelines"},
    )

    # We allow a plain-language note that tools don't exist, but the prompt
    # must not present them as callables (e.g. backticks, examples, tool list).
    assert "`update_file`" not in prompt
    assert "`read_file`" not in prompt
    assert "\n- update_file" not in prompt
    assert "update_file(" not in prompt
    assert "read_file(" not in prompt



@pytest.mark.unit
def test_product_identity_does_not_depend_on_database_role_definition():
    """数据库里的角色定义没写身份时，系统提示照样带上 ZenStory 写作助手的身份说明。"""
    from agent.prompts.base import OUTPUT_PROTOCOL, PRODUCT_IDENTITY_PROTOCOL

    prompt = get_base_prompt(
        project_id="proj-1",
        folder_ids={"draft": "draft-folder"},
        config={"role_definition": "你是一位资深网文编辑", "capabilities": "",
                "directory_structure": "{draft}", "content_structure": "",
                "file_types": "draft", "writing_guidelines": ""},
    )

    role_at = prompt.index("你是一位资深网文编辑")
    identity_at = prompt.index(PRODUCT_IDENTITY_PROTOCOL)
    assert role_at < identity_at < prompt.index(OUTPUT_PROTOCOL)


@pytest.mark.unit
def test_base_prompt_rename_rule_points_only_to_rename_file():
    """#273/F-13：只改标题时只走 rename_file，提示词里不能再有新建副本搬运正文的旧规则。"""
    prompt = get_base_prompt(
        project_id="proj-1",
        folder_ids={"draft": "draft-folder"},
        config={"role_definition": "Test role", "capabilities": "", "directory_structure": "{draft}",
                "content_structure": "", "file_types": "draft", "writing_guidelines": ""},
    )

    tool_list = TOOL_USAGE_PROTOCOL.split("### parallel_execute")[0]
    assert "\n- rename_file" in tool_list
    assert "改标题" not in prompt
    assert "建一个新标题的文件" not in prompt
    assert "只改文件名（文件标题）时用 rename_file" in prompt
