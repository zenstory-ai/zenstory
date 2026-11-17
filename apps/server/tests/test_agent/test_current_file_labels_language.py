"""F-30：当前文件标签跟随作者界面语言。"""


import pytest


@pytest.mark.unit
@pytest.mark.parametrize(("language", "labels", "absent"), [
    ("en", ("- File ID: f-1", "- Type: draft", "- Title: Chapter 1"), ("文件 ID", "类型", "标题")),
    ("zh", ("- 文件 ID: f-1", "- 类型: draft", "- 标题: Chapter 1"), ("File ID", "Type:", "Title:")),
])
def test_current_file_labels_follow_language(language, labels, absent):
    from agent.core.message_manager import MessageManager

    manager = MessageManager(project_id="project-1")
    section = "\n".join(manager._build_metadata_section(
        {"current_file_id": "f-1", "current_file_type": "draft", "current_file_title": "Chapter 1"},
        manager._force_en(language),
    ))

    for label in labels:
        assert label in section
    for label in absent:
        assert label not in section
