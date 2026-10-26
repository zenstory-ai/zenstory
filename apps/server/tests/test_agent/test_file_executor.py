"""
测试 FileToolExecutor - Agent 文件工具执行器

测试覆盖：
- create_file - 创建文件
- update_file - 更新文件
- delete_file - 删除文件
- edit_file - 编辑文件内容（多种操作）
- query_files - 查询文件
- update_project_status - 更新项目状态
- 权限检查
- 边界情况和错误处理
"""

import json
from datetime import datetime

import pytest

from agent.tools.file_ops import (
    FileToolExecutor,
    _find_approximate_match,
    _find_fuzzy_spans,
    _normalize_for_fuzzy_match,
    serialize_file,
)
from agent.tools.permissions import (
    PermissionError,
)
from config.project_status import PROJECT_STATUS_MAX_LENGTHS
from models import File, Project, User

# ========== Fixtures ==========


@pytest.fixture
def test_user(db_session):
    """创建测试用户"""
    from services.core.auth_service import hash_password

    user = User(
        email="file_executor_test@example.com",
        username="file_executor_test",
        hashed_password=hash_password("password123"),
        name="Test User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def test_project(db_session, test_user):
    """创建测试项目"""
    project = Project(
        name="File Executor Test Project",
        description="Test project for file executor",
        owner_id=test_user.id,
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.fixture
def other_user(db_session):
    """创建另一个测试用户（用于权限测试）"""
    from services.core.auth_service import hash_password

    user = User(
        email="other_file_executor@example.com",
        username="other_file_executor",
        hashed_password=hash_password("password123"),
        name="Other User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def other_project(db_session, other_user):
    """创建另一个用户的项目（用于权限测试）"""
    project = Project(
        name="Other User Project",
        description="Another user's project",
        owner_id=other_user.id,
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.fixture
def executor(db_session, test_user):
    """创建 FileToolExecutor 实例"""
    return FileToolExecutor(db_session, test_user.id)


@pytest.fixture
def other_executor(db_session, other_user):
    """创建另一个用户的 executor"""
    return FileToolExecutor(db_session, other_user.id)


# ========== Test: serialize_file ==========


def test_serialize_file(db_session, test_project):
    """测试文件序列化"""
    file = File(
        project_id=test_project.id,
        title="Test File",
        content="Test content",
        file_type="draft",
    )
    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)

    result = serialize_file(file)

    assert result["id"] == file.id
    assert result["title"] == "Test File"
    assert result["content"] == "Test content"
    assert result["file_type"] == "draft"
    assert isinstance(result["created_at"], str)
    assert isinstance(result["updated_at"], str)
    # 验证 ISO 格式
    datetime.fromisoformat(result["created_at"])
    datetime.fromisoformat(result["updated_at"])


# ========== Test: create_file ==========


def test_create_file_basic(executor, test_project):
    """测试基本文件创建"""
    result = executor.create_file(
        project_id=test_project.id,
        title="New Draft",
        file_type="draft",
        content="Chapter 1 content",
    )

    assert result["title"] == "New Draft"
    assert result["file_type"] == "draft"
    assert result["content"] == "Chapter 1 content"
    assert result["project_id"] == test_project.id
    assert "id" in result


def test_create_file_with_parent(executor, test_project, db_session):
    """测试创建带父文件的文件"""
    # 先创建一个文件夹
    folder = File(
        project_id=test_project.id,
        title="Chapter Folder",
        file_type="folder",
    )
    db_session.add(folder)
    db_session.commit()

    result = executor.create_file(
        project_id=test_project.id,
        title="Chapter 1",
        file_type="draft",
        content="Content",
        parent_id=folder.id,
    )

    assert result["parent_id"] == folder.id


def test_create_file_with_metadata(executor, test_project):
    """测试创建带 metadata 的文件"""
    metadata = {
        "age": 25,
        "gender": "男",
        "role": "主角",
        "personality": "勇敢",
    }

    result = executor.create_file(
        project_id=test_project.id,
        title="Hero Character",
        file_type="character",
        content="Hero description",
        metadata=metadata,
    )

    assert result["file_metadata"] is not None
    parsed_metadata = json.loads(result["file_metadata"])
    assert parsed_metadata["age"] == 25
    assert parsed_metadata["role"] == "主角"


def test_create_file_invalid_parent(executor, test_project):
    """测试使用无效的 parent_id"""
    with pytest.raises(ValueError, match="parent_id「non-existent-id」不能用"):
        executor.create_file(
            project_id=test_project.id,
            title="Orphan File",
            file_type="draft",
            parent_id="non-existent-id",
        )


def test_create_file_unauthorized_project(executor, other_project):
    """测试创建文件到无权限的项目"""
    with pytest.raises(PermissionError):
        executor.create_file(
            project_id=other_project.id,
            title="Unauthorized File",
            file_type="draft",
        )


def test_create_file_deleted_project(executor, test_project, db_session):
    """测试创建文件到已删除的项目"""
    test_project.is_deleted = True
    test_project.deleted_at = datetime.utcnow()
    db_session.commit()

    with pytest.raises(PermissionError):
        executor.create_file(
            project_id=test_project.id,
            title="File in Deleted Project",
            file_type="draft",
        )


# ========== Test: update_file ==========


def test_update_file_title(executor, test_project, db_session):
    """测试更新文件标题"""
    file = File(
        project_id=test_project.id,
        title="Old Title",
        file_type="draft",
        content="Content",
    )
    db_session.add(file)
    db_session.commit()

    result = executor.update_file(
        id=file.id,
        title="New Title",
    )

    assert result["title"] == "New Title"
    assert result["content"] == "Content"


def test_update_file_content(executor, test_project, db_session):
    """测试更新文件内容"""
    file = File(
        project_id=test_project.id,
        title="Draft",
        file_type="draft",
        content="Old content",
    )
    db_session.add(file)
    db_session.commit()

    result = executor.update_file(
        id=file.id,
        content="New content",
    )

    assert result["content"] == "New content"


def test_update_file_preserves_explicit_order_for_chapter_files(executor, test_project, db_session):
    """章节型写作文件更新时显式 order 仍是结构化事实。"""
    file = File(
        project_id=test_project.id,
        title="第57章 魔君",
        file_type="draft",
        content="Old content",
        order=57,
    )
    db_session.add(file)
    db_session.commit()

    result = executor.update_file(
        id=file.id,
        title="第58章 真相",
        order=1,
    )

    assert result["title"] == "第58章 真相"
    assert result["order"] == 1


def test_update_file_move_to_root(executor, test_project, db_session):
    """测试移动文件到根目录"""
    # 创建父文件夹
    parent = File(
        project_id=test_project.id,
        title="Parent Folder",
        file_type="folder",
    )
    db_session.add(parent)
    db_session.commit()

    # 创建子文件
    file = File(
        project_id=test_project.id,
        title="Child File",
        file_type="draft",
        parent_id=parent.id,
    )
    db_session.add(file)
    db_session.commit()

    # 移动到根目录
    result = executor.update_file(
        id=file.id,
        parent_id="null",  # 特殊值表示移到根目录
    )

    assert result["parent_id"] is None


def test_update_file_not_found(executor):
    """测试更新不存在的文件"""
    with pytest.raises(ValueError, match="文件不存在或已删除"):
        executor.update_file(
            id="non-existent-id",
            title="New Title",
        )


def test_update_file_unauthorized(executor, other_project, db_session):
    """测试更新无权限的文件"""
    # 创建属于其他用户的文件
    file = File(
        project_id=other_project.id,
        title="Other User File",
        file_type="draft",
    )
    db_session.add(file)
    db_session.commit()

    with pytest.raises(PermissionError):
        executor.update_file(
            id=file.id,
            title="Attempted Update",
        )


# ========== Test: delete_file ==========


def test_delete_file_basic(executor, test_project, db_session):
    """测试基本文件删除（软删除）"""
    file = File(
        project_id=test_project.id,
        title="To Delete",
        file_type="draft",
    )
    db_session.add(file)
    db_session.commit()
    file_id = file.id

    result = executor.delete_file(id=file_id)

    assert result is True

    # 验证软删除
    db_session.refresh(file)
    assert file.is_deleted is True
    assert file.deleted_at is not None


def test_delete_file_recursive(executor, test_project, db_session):
    """测试递归删除文件及其子文件"""
    # 创建文件夹
    folder = File(
        project_id=test_project.id,
        title="Parent Folder",
        file_type="folder",
    )
    db_session.add(folder)
    db_session.commit()

    # 创建子文件
    child1 = File(
        project_id=test_project.id,
        title="Child 1",
        file_type="draft",
        parent_id=folder.id,
    )
    child2 = File(
        project_id=test_project.id,
        title="Child 2",
        file_type="draft",
        parent_id=folder.id,
    )
    db_session.add(child1)
    db_session.add(child2)
    db_session.commit()

    # 递归删除
    result = executor.delete_file(id=folder.id, recursive=True)

    assert result is True

    # 验证所有文件都被软删除
    db_session.refresh(folder)
    db_session.refresh(child1)
    db_session.refresh(child2)
    assert folder.is_deleted is True
    assert child1.is_deleted is True
    assert child2.is_deleted is True


def test_delete_file_not_found(executor):
    """测试删除不存在的文件"""
    with pytest.raises(ValueError, match="文件不存在或已删除"):
        executor.delete_file(id="non-existent-id")


def test_delete_file_already_deleted(executor, test_project, db_session):
    """测试删除已删除的文件"""
    file = File(
        project_id=test_project.id,
        title="Deleted File",
        file_type="draft",
        is_deleted=True,
        deleted_at=datetime.utcnow(),
    )
    db_session.add(file)
    db_session.commit()

    with pytest.raises(ValueError, match="文件不存在或已删除"):
        executor.delete_file(id=file.id)


def test_delete_file_unauthorized(executor, other_project, db_session):
    """测试删除无权限的文件"""
    file = File(
        project_id=other_project.id,
        title="Other User File",
        file_type="draft",
    )
    db_session.add(file)
    db_session.commit()

    with pytest.raises(PermissionError):
        executor.delete_file(id=file.id)


# ========== Test: edit_file ==========


def test_edit_file_replace(executor, test_project, db_session):
    """测试编辑文件 - 替换操作"""
    file = File(
        project_id=test_project.id,
        title="Draft",
        file_type="draft",
        content="Hello world. This is a test.",
    )
    db_session.add(file)
    db_session.commit()

    result = executor.edit_file(
        id=file.id,
        edits=[{"op": "replace", "old": "world", "new": "universe"}],
    )

    assert result["edits_applied"] == 1
    assert result["new_length"] > 0
    # new_preview 是截断的，只检查部分内容
    assert "universe" in result["details"][0]["new_preview"]


def test_edit_file_append(executor, test_project, db_session):
    """测试编辑文件 - 追加操作"""
    file = File(
        project_id=test_project.id,
        title="Draft",
        file_type="draft",
        content="First line.",
    )
    db_session.add(file)
    db_session.commit()

    result = executor.edit_file(
        id=file.id,
        edits=[{"op": "append", "text": "\nSecond line."}],
    )

    assert result["edits_applied"] == 1
    assert result["new_length"] == len("First line.\nSecond line.")

    # 验证文件内容已更新
    db_session.refresh(file)
    assert file.content == "First line.\nSecond line."


def test_edit_file_prepend(executor, test_project, db_session):
    """测试编辑文件 - 前置操作"""
    file = File(
        project_id=test_project.id,
        title="Draft",
        file_type="draft",
        content="Second line.",
    )
    db_session.add(file)
    db_session.commit()

    result = executor.edit_file(
        id=file.id,
        edits=[{"op": "prepend", "text": "First line.\n"}],
    )

    assert result["edits_applied"] == 1

    # 验证文件内容
    db_session.refresh(file)
    assert file.content.startswith("First line.\n")


def test_edit_file_insert_after(executor, test_project, db_session):
    """测试编辑文件 - 在锚点后插入"""
    file = File(
        project_id=test_project.id,
        title="Draft",
        file_type="draft",
        content="First line.\nThird line.",
    )
    db_session.add(file)
    db_session.commit()

    result = executor.edit_file(
        id=file.id,
        edits=[{"op": "insert_after", "anchor": "First line.", "text": "\nSecond line."}],
    )

    assert result["edits_applied"] == 1

    # 验证内容
    db_session.refresh(file)
    assert "First line.\nSecond line.\nThird line." == file.content


def test_edit_file_insert_before(executor, test_project, db_session):
    """测试编辑文件 - 在锚点前插入"""
    file = File(
        project_id=test_project.id,
        title="Draft",
        file_type="draft",
        content="First line.\nThird line.",
    )
    db_session.add(file)
    db_session.commit()

    result = executor.edit_file(
        id=file.id,
        edits=[{"op": "insert_before", "anchor": "Third line.", "text": "Second line.\n"}],
    )

    assert result["edits_applied"] == 1

    # 验证内容
    db_session.refresh(file)
    assert "First line.\nSecond line.\nThird line." == file.content


def test_edit_file_delete(executor, test_project, db_session):
    """测试编辑文件 - 删除操作"""
    file = File(
        project_id=test_project.id,
        title="Draft",
        file_type="draft",
        content="First line. Unwanted text. Second line.",
    )
    db_session.add(file)
    db_session.commit()

    result = executor.edit_file(
        id=file.id,
        edits=[{"op": "delete", "old": " Unwanted text."}],
    )

    assert result["edits_applied"] == 1

    # 验证内容
    db_session.refresh(file)
    assert "Unwanted text" not in file.content


def test_edit_file_multiple_edits(executor, test_project, db_session):
    """测试多个编辑操作"""
    file = File(
        project_id=test_project.id,
        title="Draft",
        file_type="draft",
        content="Hello world.",
    )
    db_session.add(file)
    db_session.commit()

    result = executor.edit_file(
        id=file.id,
        edits=[
            {"op": "replace", "old": "world", "new": "universe"},
            {"op": "append", "text": " Goodbye."},
        ],
    )

    assert result["edits_applied"] == 2

    # 验证内容
    db_session.refresh(file)
    assert "Hello universe. Goodbye." == file.content


def test_edit_file_fuzzy_match(executor, test_project, db_session):
    """测试模糊匹配编辑"""
    file = File(
        project_id=test_project.id,
        title="Draft",
        file_type="draft",
        content="Hello，world。This is a test.",
    )
    db_session.add(file)
    db_session.commit()

    # 忽略标点和空格的模糊匹配
    result = executor.edit_file(
        id=file.id,
        edits=[
            {
                "op": "replace",
                "old": "Hello world",
                "new": "Hi universe",
                "match_mode": "fuzzy",
            }
        ],
    )

    assert result["edits_applied"] == 1
    assert result["details"][0]["match_mode"] == "fuzzy"


def test_edit_file_not_found(executor):
    """测试编辑不存在的文件"""
    with pytest.raises(ValueError, match="文件不存在或已删除"):
        executor.edit_file(
            id="non-existent-id",
            edits=[{"op": "append", "text": "test"}],
        )


def test_edit_file_unauthorized(executor, other_project, db_session):
    """测试编辑无权限的文件"""
    file = File(
        project_id=other_project.id,
        title="Other File",
        file_type="draft",
        content="Content",
    )
    db_session.add(file)
    db_session.commit()

    with pytest.raises(PermissionError):
        executor.edit_file(
            id=file.id,
            edits=[{"op": "append", "text": "test"}],
        )


def test_edit_file_missing_anchor(executor, test_project, db_session):
    """测试编辑时缺少锚点"""
    file = File(
        project_id=test_project.id,
        title="Draft",
        file_type="draft",
        content="First line.\nSecond line.",
    )
    db_session.add(file)
    db_session.commit()

    with pytest.raises(ValueError, match="找不到插入锚点"):
        executor.edit_file(
            id=file.id,
            edits=[{"op": "insert_after", "anchor": "Non-existent", "text": "test"}],
        )


def test_edit_file_continue_on_error_partial_success(executor, test_project, db_session):
    """测试 continue_on_error 开启时允许部分成功。"""
    file = File(
        project_id=test_project.id,
        title="Draft",
        file_type="draft",
        content="Hello world.",
    )
    db_session.add(file)
    db_session.commit()

    result = executor.edit_file(
        id=file.id,
        edits=[
            {"op": "replace", "old": "not-found", "new": "x"},
            {"op": "append", "text": " Goodbye."},
        ],
        continue_on_error=True,
    )

    assert result["edits_applied"] == 1
    assert result["partial_success"] is True
    assert result["all_failed"] is False
    assert len(result["failed_edits"]) == 1

    db_session.refresh(file)
    assert file.content == "Hello world. Goodbye."


def test_edit_file_continue_on_error_all_failed(executor, test_project, db_session):
    """测试 continue_on_error 开启但全部失败时返回失败明细。"""
    file = File(
        project_id=test_project.id,
        title="Draft",
        file_type="draft",
        content="Hello world.",
    )
    db_session.add(file)
    db_session.commit()

    result = executor.edit_file(
        id=file.id,
        edits=[
            {"op": "replace", "old": "not-found", "new": "x"},
        ],
        continue_on_error=True,
    )

    assert result["edits_applied"] == 0
    assert result["partial_success"] is False
    assert result["all_failed"] is True
    assert len(result["failed_edits"]) == 1

    db_session.refresh(file)
    assert file.content == "Hello world."


def test_edit_file_continue_on_error_handles_invalid_edit_item(executor, test_project, db_session):
    """测试 continue_on_error 对非 dict 编辑项不会崩溃。"""
    file = File(
        project_id=test_project.id,
        title="Draft",
        file_type="draft",
        content="Hello world.",
    )
    db_session.add(file)
    db_session.commit()

    result = executor.edit_file(
        id=file.id,
        edits=[
            "invalid-edit-item",
            {"op": "append", "text": " Again."},
        ],
        continue_on_error=True,
    )

    assert result["edits_applied"] == 1
    assert len(result["failed_edits"]) == 1
    assert result["failed_edits"][0]["index"] == 0
    assert result["partial_success"] is True

    db_session.refresh(file)
    assert file.content == "Hello world. Again."


# ========== Test: query_files ==========


def test_query_files_basic(executor, test_project, db_session):
    """测试基本文件查询"""
    # 创建测试文件
    file1 = File(
        project_id=test_project.id,
        title="Draft 1",
        file_type="draft",
        content="Content 1",
    )
    file2 = File(
        project_id=test_project.id,
        title="Draft 2",
        file_type="draft",
        content="Content 2",
    )
    db_session.add(file1)
    db_session.add(file2)
    db_session.commit()

    results = executor.query_files(project_id=test_project.id)

    assert len(results) >= 2
    titles = [r["title"] for r in results]
    assert "Draft 1" in titles
    assert "Draft 2" in titles


def test_query_files_default_summary_mode(executor, test_project, db_session):
    """测试 query_files 默认返回 summary（不含 content 全文）"""
    file = File(
        project_id=test_project.id,
        title="Summary Draft",
        file_type="draft",
        content="This is a long content for summary preview.",
    )
    db_session.add(file)
    db_session.commit()

    results = executor.query_files(project_id=test_project.id)
    result = next(r for r in results if r["id"] == file.id)

    assert "content" not in result
    assert result["content_preview"] == "This is a long content for summary preview."


def test_query_files_full_mode_keeps_original_content(executor, test_project, db_session):
    """测试 response_mode=full 保持原有行为（返回完整 content）"""
    file = File(
        project_id=test_project.id,
        title="Full Draft",
        file_type="draft",
        content="Full content body",
    )
    db_session.add(file)
    db_session.commit()

    results = executor.query_files(
        project_id=test_project.id,
        response_mode="full",
    )
    result = next(r for r in results if r["id"] == file.id)

    assert result["content"] == "Full content body"
    assert "content_preview" not in result


def test_query_files_include_content_backward_compatible(executor, test_project, db_session):
    """测试 include_content=true 时即使 summary 也返回完整 content（兼容）"""
    file = File(
        project_id=test_project.id,
        title="Compat Draft",
        file_type="draft",
        content="Compatibility content",
    )
    db_session.add(file)
    db_session.commit()

    results = executor.query_files(
        project_id=test_project.id,
        response_mode="summary",
        include_content=True,
    )
    result = next(r for r in results if r["id"] == file.id)

    assert result["content"] == "Compatibility content"
    assert "content_preview" not in result


def test_query_files_content_preview_chars(executor, test_project, db_session):
    """测试 summary 模式下 content_preview 按 content_preview_chars 截断"""
    file = File(
        project_id=test_project.id,
        title="Preview Draft",
        file_type="draft",
        content="1234567890",
    )
    db_session.add(file)
    db_session.commit()

    results = executor.query_files(
        project_id=test_project.id,
        response_mode="summary",
        content_preview_chars=4,
    )
    result = next(r for r in results if r["id"] == file.id)

    assert "content" not in result
    assert result["content_preview"] == "1234"


def test_query_files_by_type(executor, test_project, db_session):
    """测试按类型查询文件"""
    file1 = File(
        project_id=test_project.id,
        title="Character",
        file_type="character",
        content="Hero",
    )
    file2 = File(
        project_id=test_project.id,
        title="Draft",
        file_type="draft",
        content="Chapter",
    )
    db_session.add_all([file1, file2])
    db_session.commit()

    results = executor.query_files(
        project_id=test_project.id,
        file_type="character",
    )

    assert len(results) == 1
    assert results[0]["file_type"] == "character"


def test_query_files_by_keyword(executor, test_project, db_session):
    """测试按关键字搜索"""
    file1 = File(
        project_id=test_project.id,
        title="Magic System",
        file_type="lore",
        content="Detailed magic rules",
    )
    file2 = File(
        project_id=test_project.id,
        title="Character Sheet",
        file_type="character",
        content="Hero info",
    )
    db_session.add_all([file1, file2])
    db_session.commit()

    results = executor.query_files(
        project_id=test_project.id,
        query="Magic",
    )

    assert len(results) == 1
    assert "Magic" in results[0]["title"]


def test_query_files_by_parent(executor, test_project, db_session):
    """测试按父文件查询"""
    parent = File(
        project_id=test_project.id,
        title="Folder",
        file_type="folder",
    )
    child = File(
        project_id=test_project.id,
        title="Child",
        file_type="draft",
        parent_id=parent.id,
    )
    db_session.add_all([parent, child])
    db_session.commit()

    results = executor.query_files(
        project_id=test_project.id,
        parent_id=parent.id,
    )

    assert len(results) == 1
    assert results[0]["title"] == "Child"


def test_query_files_by_id_returns_exact_file(executor, test_project, db_session):
    """query_files 支持按文件 id 精确查询，避免同名文件误匹配。"""
    file1 = File(
        project_id=test_project.id,
        title="Same Title",
        file_type="draft",
        content="Content A",
    )
    file2 = File(
        project_id=test_project.id,
        title="Same Title",
        file_type="draft",
        content="Content B",
    )
    db_session.add_all([file1, file2])
    db_session.commit()

    results = executor.query_files(
        project_id=test_project.id,
        id=file2.id,
        response_mode="full",
    )

    assert len(results) == 1
    assert results[0]["id"] == file2.id
    assert results[0]["content"] == "Content B"


def test_query_files_with_limit(executor, test_project, db_session):
    """测试分页限制"""
    for i in range(5):
        file = File(
            project_id=test_project.id,
            title=f"File {i}",
            file_type="draft",
        )
        db_session.add(file)
    db_session.commit()

    results = executor.query_files(
        project_id=test_project.id,
        limit=3,
    )

    assert len(results) == 3


def test_query_files_unauthorized(executor, other_project):
    """测试查询无权限的项目"""
    with pytest.raises(PermissionError):
        executor.query_files(project_id=other_project.id)


def test_query_files_deleted_project(executor, test_project, db_session):
    """测试查询已删除的项目"""
    test_project.is_deleted = True
    test_project.deleted_at = datetime.utcnow()
    db_session.commit()

    with pytest.raises(PermissionError):
        executor.query_files(project_id=test_project.id)


# ========== Test: update_project_status ==========


def test_update_project_status_summary(executor, test_project, db_session):
    """测试更新项目简介"""
    result = executor.update_project_status(
        project_id=test_project.id,
        summary="A fantasy novel about magic",
    )

    assert "summary" in result["updated_fields"]
    assert result["current_status"]["summary"] == "A fantasy novel about magic"

    # 验证数据库
    db_session.refresh(test_project)
    assert test_project.summary == "A fantasy novel about magic"


def test_update_project_status_multiple_fields(executor, test_project, db_session):
    """测试更新多个状态字段"""
    result = executor.update_project_status(
        project_id=test_project.id,
        current_phase="Chapter 3 writing",
        writing_style="Epic and descriptive",
        notes="Keep magic system consistent",
    )

    assert set(result["updated_fields"]) == {
        "current_phase",
        "writing_style",
        "notes",
    }

    # 验证数据库
    db_session.refresh(test_project)
    assert test_project.current_phase == "Chapter 3 writing"
    assert test_project.writing_style == "Epic and descriptive"
    assert test_project.notes == "Keep magic system consistent"


def test_update_project_status_unauthorized(executor, other_project):
    """测试更新无权限的项目状态"""
    with pytest.raises(PermissionError):
        executor.update_project_status(
            project_id=other_project.id,
            summary="Attempted update",
        )


def test_update_project_status_not_found(executor):
    """测试更新不存在的项目状态"""
    # NotFoundError 是 PermissionError 的子类
    with pytest.raises(PermissionError):
        executor.update_project_status(
            project_id="non-existent-id",
            summary="Test",
        )


def test_update_project_status_allows_empty_string_clear(executor, test_project, db_session):
    """测试传空字符串可清空字段"""
    test_project.summary = "Existing summary"
    db_session.add(test_project)
    db_session.commit()

    result = executor.update_project_status(
        project_id=test_project.id,
        summary="",
    )

    assert "summary" in result["updated_fields"]
    assert result["current_status"]["summary"] == ""

    db_session.refresh(test_project)
    assert test_project.summary == ""


def test_update_project_status_rejects_over_max_length(executor, test_project):
    """测试超长字段会被拒绝"""
    too_long_summary = "a" * (PROJECT_STATUS_MAX_LENGTHS["summary"] + 1)

    with pytest.raises(ValueError, match="summary exceeds max length"):
        executor.update_project_status(
            project_id=test_project.id,
            summary=too_long_summary,
        )


# ========== Test: Helper Functions ==========


def test_normalize_for_fuzzy_match():
    """测试模糊匹配文本标准化"""
    text = "Hello，世界！This is a test."
    normalized, index_map = _normalize_for_fuzzy_match(text)

    # 验证标点和空格被移除
    assert "，" not in normalized
    assert "！" not in normalized
    assert " " not in normalized

    # 验证索引映射
    assert len(index_map) > 0
    assert len(normalized) <= len(index_map)


def test_find_fuzzy_spans():
    """测试模糊匹配查找"""
    content = "Hello，world。This is a test."
    pattern = "Hello world"

    spans = _find_fuzzy_spans(content, pattern, ignore_punct_whitespace=True)

    assert len(spans) >= 1
    start, end = spans[0]
    assert start >= 0
    assert end <= len(content)


def test_find_fuzzy_spans_no_match():
    """测试模糊匹配无结果"""
    content = "This is a completely different text."
    pattern = "nonexistent pattern"

    spans = _find_fuzzy_spans(content, pattern)

    assert len(spans) == 0


def test_find_approximate_match():
    """测试近似匹配"""
    content = "The hero spoke with a strong voice."
    pattern = "The hero spoke with a loud voice"  # 'strong' vs 'loud'

    result = _find_approximate_match(content, pattern, max_error_rate=0.3)

    assert result is not None
    start, end, similarity, matched_text = result
    assert similarity >= 0.7  # 至少 70% 相似度
    assert start >= 0
    assert end <= len(content)


def test_find_approximate_match_no_good_match():
    """测试近似匹配无足够好的结果"""
    content = "Completely different content here."
    pattern = "Nothing similar at all"

    result = _find_approximate_match(content, pattern, max_error_rate=0.2)

    assert result is None


# ========== Test: Permission Checking Integration ==========


def test_executor_checks_project_ownership(executor, other_project):
    """测试 executor 正确检查项目所有权"""
    # 所有需要项目访问的操作都应该失败
    with pytest.raises(PermissionError):
        executor.create_file(
            project_id=other_project.id,
            title="Unauthorized",
            file_type="draft",
        )

    with pytest.raises(PermissionError):
        executor.query_files(project_id=other_project.id)

    with pytest.raises(PermissionError):
        executor.update_project_status(
            project_id=other_project.id,
            summary="Unauthorized",
        )


def test_executor_with_no_user_id(db_session, test_project):
    """测试没有 user_id 的 executor（匿名访问）"""
    anonymous_executor = FileToolExecutor(db_session, user_id=None)

    # 注意：实际实现中 user_id=None 时仍然允许访问（这是一个潜在的安全问题）
    # 这个测试反映了当前的实际行为
    results = anonymous_executor.query_files(project_id=test_project.id)
    # 至少应该能返回空列表而不是报错
    assert isinstance(results, list)


# ========== Test: Edge Cases ==========


def test_create_file_with_empty_content(executor, test_project):
    """测试创建空内容文件"""
    result = executor.create_file(
        project_id=test_project.id,
        title="Empty File",
        file_type="draft",
        content="",
    )

    assert result["content"] == ""


def test_update_file_with_no_changes(executor, test_project, db_session):
    """测试不更新任何字段"""
    file = File(
        project_id=test_project.id,
        title="Original",
        file_type="draft",
        content="Content",
    )
    db_session.add(file)
    db_session.commit()


    result = executor.update_file(id=file.id)

    # 即使没有更新，updated_at 也会被更新
    assert result["title"] == "Original"


def test_edit_file_with_empty_edits(executor, test_project, db_session):
    """测试空编辑列表"""
    file = File(
        project_id=test_project.id,
        title="Draft",
        file_type="draft",
        content="Original content",
    )
    db_session.add(file)
    db_session.commit()

    result = executor.edit_file(id=file.id, edits=[])

    assert result["edits_applied"] == 0

    # 内容应该保持不变
    db_session.refresh(file)
    assert file.content == "Original content"


def test_query_files_with_no_results(executor, test_project):
    """测试查询无结果的场景"""
    results = executor.query_files(
        project_id=test_project.id,
        query="nonexistent keyword that won't match anything",
    )

    assert results == []


def test_delete_file_with_no_children(executor, test_project, db_session):
    """测试递归删除没有子文件的文件"""
    file = File(
        project_id=test_project.id,
        title="Single File",
        file_type="draft",
    )
    db_session.add(file)
    db_session.commit()

    result = executor.delete_file(id=file.id, recursive=True)

    assert result is True

    db_session.refresh(file)
    assert file.is_deleted is True


# ---------------------------------------------------------------------------
# Regression tests: exact-match branch must honour the same uniqueness /
# occurrence guards as the fuzzy path (previously it silently edited the first
# of several occurrences and ignored `occurrence`).
# ---------------------------------------------------------------------------


def test_edit_insert_after_ambiguous_exact_anchor_aborts(executor, test_project, db_session):
    """Repeated exact anchor without occurrence must abort, not edit the first."""
    file = File(
        project_id=test_project.id,
        title="Draft",
        file_type="draft",
        content="第一章\n内容A\n第一章\n内容B\n第一章\n内容C",
    )
    db_session.add(file)
    db_session.commit()

    with pytest.raises(ValueError, match="多个位置"):
        executor.edit_file(
            id=file.id,
            edits=[{"op": "insert_after", "anchor": "第一章", "text": "X"}],
        )

    # File must be untouched.
    db_session.refresh(file)
    assert file.content == "第一章\n内容A\n第一章\n内容B\n第一章\n内容C"


def test_edit_insert_after_exact_anchor_honours_occurrence(executor, test_project, db_session):
    """occurrence=2 must target the second exact occurrence, not the first."""
    file = File(
        project_id=test_project.id,
        title="Draft",
        file_type="draft",
        content="AAA-1 MARK AAA-2 MARK AAA-3",
    )
    db_session.add(file)
    db_session.commit()

    result = executor.edit_file(
        id=file.id,
        edits=[{"op": "insert_after", "anchor": "MARK", "text": "[X]", "occurrence": 2}],
    )

    assert result["edits_applied"] == 1
    db_session.refresh(file)
    # Inserted after the SECOND "MARK".
    assert file.content == "AAA-1 MARK AAA-2 MARK[X] AAA-3"


def test_edit_replace_ambiguous_exact_aborts(executor, test_project, db_session):
    """Non-replace_all exact replace with multiple matches must abort."""
    file = File(
        project_id=test_project.id,
        title="Draft",
        file_type="draft",
        content="repeat token here and repeat token there",
    )
    db_session.add(file)
    db_session.commit()

    with pytest.raises(ValueError, match="多个位置"):
        executor.edit_file(
            id=file.id,
            edits=[{"op": "replace", "old": "repeat token", "new": "X"}],
        )

    db_session.refresh(file)
    assert file.content == "repeat token here and repeat token there"


def test_edit_replace_all_still_replaces_every_exact_match(executor, test_project, db_session):
    """replace_all remains the escape hatch for repeated exact text."""
    file = File(
        project_id=test_project.id,
        title="Draft",
        file_type="draft",
        content="dup and dup and dup",
    )
    db_session.add(file)
    db_session.commit()

    result = executor.edit_file(
        id=file.id,
        edits=[{"op": "replace", "old": "dup", "new": "X", "replace_all": True}],
    )

    assert result["edits_applied"] == 1
    db_session.refresh(file)
    assert file.content == "X and X and X"


def test_edit_delete_ambiguous_exact_aborts(executor, test_project, db_session):
    """Exact delete with multiple matches must abort instead of deleting the first."""
    file = File(
        project_id=test_project.id,
        title="Draft",
        file_type="draft",
        content="keep DELME keep DELME keep",
    )
    db_session.add(file)
    db_session.commit()

    with pytest.raises(ValueError, match="多个位置"):
        executor.edit_file(
            id=file.id,
            edits=[{"op": "delete", "old": "DELME"}],
        )

    db_session.refresh(file)
    assert file.content == "keep DELME keep DELME keep"


@pytest.mark.unit
def test_executor_bases_do_not_shadow_each_other():
    """FileToolExecutor 由三个基类组合；任何同名属性都会被 MRO 静默遮蔽。"""
    from agent.tools.file_ops import FileCRUD, FileEditor, FileToolExecutor, ProjectOperations

    def own_names(cls: type) -> set[str]:
        return {name for name in vars(cls) if not (name.startswith("__") and name.endswith("__"))}

    bases = (FileCRUD, FileEditor, ProjectOperations)
    assert FileToolExecutor.__bases__ == bases
    for index, left in enumerate(bases):
        for right in bases[index + 1 :]:
            assert own_names(left) & own_names(right) == set(), (left.__name__, right.__name__)
