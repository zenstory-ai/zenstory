"""Prompt/tool alignment regression tests.

These tests ensure that the system prompt does not instruct the model to call
tools that are not available in the Tool registry.
"""

from os.path import commonprefix
from uuid import UUID, uuid5

import pytest

from agent.prompts.base import (
    AUTHOR_REPLY_PROTOCOL,
    OUTPUT_PROTOCOL,
    PRODUCT_IDENTITY_PROTOCOL,
    UNIFIED_EXECUTION_PROTOCOL,
    get_base_prompt,
)


@pytest.mark.unit
def test_project_ids_preserve_shared_protocol_prefix_and_folder_isolation():
    """项目 UUID 不能挡在共用协议之前，项目目录仍必须各自准确。"""
    project_ids = (
        "09ab1059-6f47-440c-8ef3-8a47bfb38943",
        "c236c601-6b1a-46c6-b870-f3b978d8cb16",
    )
    folder_types = ("lore", "character", "outline", "draft", "script", "material")
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

    normalized = []
    for index, prompt in enumerate(rendered):
        assert f"## 当前项目 ID: {project_ids[index]}" in prompt
        assert prompt.count(project_ids[index]) == 1
        assert project_ids[1 - index] not in prompt
        normalized_prompt = prompt.replace(project_ids[index], "<project_id>")
        for kind, folder_id in folders[index].items():
            assert f"parent_id='{folder_id}'" in prompt
            assert folders[1 - index][kind] not in prompt
            normalized_prompt = normalized_prompt.replace(folder_id, f"<{kind}_folder_id>")
        normalized.append(normalized_prompt)
    assert normalized[0] == normalized[1]


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
