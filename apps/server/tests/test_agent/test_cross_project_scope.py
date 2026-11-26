"""跨项目文件访问回归测试

edit_file / update_file / delete_file 按 id 定位文件时，必须校验目标文件属于
当前 ToolContext 绑定的项目：同一用户名下其它项目的文件对会话内工具视同不存在
（根文件夹 id 形如 "{project_id}-draft-folder" 可预测，仅靠"用户拥有该文件所在
项目"不足以阻止被注入文本诱导的跨项目写/删）。

覆盖：
- 生产分发路径（mcp_tools 包装 → FileToolExecutor → FileEditor/FileCRUD）
- 执行器直连路径（FileToolExecutor 在工具上下文中被直接调用）
- 错误语义：跨项目访问的用户可见信息与"文件不存在"一致，不泄漏目标项目/文件
- 未绑定工具上下文时退回文件自身项目的所有权校验（合法的非会话调用不受影响）
"""

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from agent.tools.file_ops import FileToolExecutor
from agent.tools.mcp_tools import delete_file, edit_file
from agent.tools.permissions import NotFoundError
from agent.tools.tool_context import AgentRequestContext
from models import File, Project, User

# ========== Fixtures ==========


def _parse_payload(result: dict) -> dict:
    text = result["content"][0]["text"]
    return json.loads(text)


@pytest.fixture
def test_user(db_session):
    """创建测试用户"""
    from services.core.auth_service import hash_password

    suffix = uuid4().hex[:8]
    user = User(
        email=f"cross-project-scope-{suffix}@example.com",
        username=f"cross_project_scope_{suffix}",
        hashed_password=hash_password("password123"),
        name="Cross Project Scope User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def project_a(db_session, test_user):
    """当前会话绑定的项目 A"""
    project = Project(
        name="Scope Project A",
        description="Session-bound project",
        owner_id=test_user.id,
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.fixture
def project_b(db_session, test_user):
    """同一用户的另一个项目 B（会话外目标）"""
    project = Project(
        name="Scope Project B",
        description="Other project of the same user",
        owner_id=test_user.id,
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.fixture
def file_a(db_session, project_a):
    """项目 A 中的草稿"""
    file = File(
        project_id=project_a.id,
        title="A 章节",
        file_type="draft",
        content="项目 A 原文",
    )
    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)
    return file


@pytest.fixture
def file_b(db_session, project_b):
    """项目 B 中的草稿"""
    file = File(
        project_id=project_b.id,
        title="B 章节",
        file_type="draft",
        content="项目 B 原文",
    )
    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)
    return file


@pytest.fixture
def draft_folder_b(db_session, project_b, file_b):
    """项目 B 的根草稿文件夹（id 可由 project_id 推出）+ 子文件"""
    folder = File(
        id=f"{project_b.id}-draft-folder",
        project_id=project_b.id,
        title="草稿",
        file_type="folder",
        order=0,
    )
    db_session.add(folder)
    db_session.commit()
    file_b.parent_id = folder.id
    db_session.add(file_b)
    db_session.commit()
    db_session.refresh(folder)
    return folder


# ========== 生产分发路径（mcp_tools 包装） ==========


@pytest.mark.asyncio
@pytest.mark.unit
async def test_edit_file_rejects_file_from_other_project_of_same_user(
    db_session, test_user, project_a, project_b, file_b
):
    """会话绑定项目 A 时，edit_file 不得改写同一用户项目 B 中的文件"""
    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=test_user.id,
        project_id=project_a.id,
        session_id="sess-scope-edit",
    )
    try:
        result = await edit_file(
            {
                "id": file_b.id,
                "edits": [{"op": "append", "text": "注入的内容"}],
            },
            request_context=req_ctx,
        )
    finally:
        req_ctx.clear()

    payload = _parse_payload(result)
    assert payload["status"] == "error"
    # 用户可见信息与"文件不存在"一致，不泄漏其它项目文件的存在性
    assert "文件不存在" in payload["error"]
    assert project_b.id not in payload["error"]
    assert file_b.id not in payload["error"]

    db_session.expire_all()
    fresh = db_session.get(File, file_b.id)
    assert fresh.content == "项目 B 原文"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_delete_file_rejects_predictable_root_folder_of_other_project(
    db_session, test_user, project_a, project_b, file_b, draft_folder_b
):
    """会话绑定项目 A 时，delete_file 不得递归删除项目 B 的草稿文件夹树"""
    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=test_user.id,
        project_id=project_a.id,
        session_id="sess-scope-delete",
    )
    try:
        result = await delete_file(
            {
                "id": f"{project_b.id}-draft-folder",
                "recursive": True,
            },
            request_context=req_ctx,
        )
    finally:
        req_ctx.clear()

    payload = _parse_payload(result)
    assert payload["status"] == "error"
    assert "文件不存在" in payload["error"]
    assert project_b.id not in payload["error"]

    db_session.expire_all()
    folder = db_session.get(File, draft_folder_b.id)
    child = db_session.get(File, file_b.id)
    assert folder.is_deleted is False
    assert child.is_deleted is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_edit_file_same_project_still_works(
    db_session, test_user, project_a, file_a
):
    """会话内对当前项目文件的编辑不受影响"""
    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=test_user.id,
        project_id=project_a.id,
        session_id="sess-scope-ok",
    )
    try:
        result = await edit_file(
            {
                "id": file_a.id,
                "edits": [{"op": "append", "text": "，续写"}],
            },
            request_context=req_ctx,
        )
    finally:
        req_ctx.clear()

    payload = _parse_payload(result)
    assert payload["status"] == "success"

    db_session.expire_all()
    fresh = db_session.get(File, file_a.id)
    assert fresh.content == "项目 A 原文，续写"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_edit_file_requires_project_context():
    """未绑定项目上下文时写工具直接失败，而不是绕过项目校验"""
    mock_executor = MagicMock()
    req_ctx = AgentRequestContext(session=MagicMock(), user_id="u1", project_id=None)
    with patch.object(req_ctx, "executor", return_value=mock_executor):
        result = await edit_file(
            {
                "id": "file-1",
                "edits": [{"op": "append", "text": "x"}],
            },
            request_context=req_ctx,
        )

    payload = _parse_payload(result)
    assert payload["status"] == "error"
    assert "project_id not set" in payload["error"]
    mock_executor.edit_file.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_delete_file_requires_project_context():
    """未绑定项目上下文时删除工具直接失败，而不是绕过项目校验"""
    mock_executor = MagicMock()
    req_ctx = AgentRequestContext(session=MagicMock(), user_id="u1", project_id=None)
    with patch.object(req_ctx, "executor", return_value=mock_executor):
        result = await delete_file({"id": "file-1"}, request_context=req_ctx)

    payload = _parse_payload(result)
    assert payload["status"] == "error"
    assert "project_id not set" in payload["error"]
    mock_executor.delete_file.assert_not_called()


# ========== 执行器直连路径（FileToolExecutor/FileEditor/FileCRUD） ==========


@pytest.mark.unit
def test_executor_edit_file_rejects_cross_project_under_tool_context(
    db_session, test_user, project_a, project_b, file_b
):
    """绕过 mcp_tools 包装、直接调用执行器同样受当前上下文项目约束"""
    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=test_user.id,
        project_id=project_a.id,
        session_id="sess-scope-exec-edit",
    )
    executor = FileToolExecutor(db_session, test_user.id, request_context=req_ctx)
    try:
        with pytest.raises(NotFoundError, match="文件不存在"):
            executor.edit_file(
                id=file_b.id,
                edits=[{"op": "append", "text": "注入"}],
            )
    finally:
        req_ctx.clear()

    db_session.expire_all()
    assert db_session.get(File, file_b.id).content == "项目 B 原文"


@pytest.mark.unit
def test_executor_update_file_rejects_cross_project_under_tool_context(
    db_session, test_user, project_a, project_b, file_b
):
    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=test_user.id,
        project_id=project_a.id,
        session_id="sess-scope-exec-update",
    )
    executor = FileToolExecutor(db_session, test_user.id, request_context=req_ctx)
    try:
        with pytest.raises(NotFoundError, match="文件不存在"):
            executor.update_file(id=file_b.id, content="整体覆盖")
    finally:
        req_ctx.clear()

    db_session.expire_all()
    assert db_session.get(File, file_b.id).content == "项目 B 原文"


@pytest.mark.unit
def test_executor_delete_file_rejects_cross_project_under_tool_context(
    db_session, test_user, project_a, project_b, file_b
):
    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=test_user.id,
        project_id=project_a.id,
        session_id="sess-scope-exec-delete",
    )
    executor = FileToolExecutor(db_session, test_user.id, request_context=req_ctx)
    try:
        with pytest.raises(NotFoundError, match="文件不存在"):
            executor.delete_file(id=file_b.id)
    finally:
        req_ctx.clear()

    db_session.expire_all()
    assert db_session.get(File, file_b.id).is_deleted is False


# ========== 未绑定工具上下文的合法调用不受影响 ==========


@pytest.mark.unit
def test_executor_without_tool_context_falls_back_to_ownership_check(
    db_session, test_user, project_b, file_b
):
    """非会话调用（如 stream_adapter 落盘、后台任务）仍按文件所属项目做所有权校验"""
    executor = FileToolExecutor(db_session, test_user.id)

    result = executor.update_file(id=file_b.id, content="项目 B 新内容")

    assert result["id"] == file_b.id
    db_session.expire_all()
    assert db_session.get(File, file_b.id).content == "项目 B 新内容"
