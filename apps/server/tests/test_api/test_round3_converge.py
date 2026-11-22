"""Round3 收敛阶段回归测试：REST 层的跨组遗留项。

- crud #1：api/files.py 的 parent 校验与 agent 侧曾是两份实现，
  agent 那份漏了「parent 必须是 folder」，导致 AI 写的章节挂到普通文件下、
  在文件树里彻底不可见。现在统一到 services/file_tree_rules，
  这里钉死「两个入口对同一份不合法输入给出同样的判定」。
"""

import pytest
from sqlmodel import Session

from models import File, Project, User


# ---------------------------------------------------------------------------
# crud #1：REST 与 agent 共用同一份 parent 校验
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rest_parent_validation_delegates_to_shared_rules():
    """api/files.py 不得再自带一份实现——两份实现必然随时间漂移，这正是 #15 的根因。"""
    import inspect

    import api.files as files_module

    source = inspect.getsource(files_module._validate_parent_assignment)
    assert "validate_parent_assignment(" in source, "必须调用共享实现"
    assert "ParentNotFoundError" in source, "只负责把异常翻译成 REST 的 error_code"
    # 旧的本地成环检测副本必须已经删除
    assert not hasattr(files_module, "_is_descendant")


@pytest.mark.unit
def test_rest_and_agent_agree_on_non_folder_parent(db_session: Session):
    """同一份不合法输入（parent 是普通文件）在两个入口必须都被拒。"""
    from agent.tools.file_ops.crud import validate_parent_assignment as agent_validate
    from api.files import _validate_parent_assignment as rest_validate
    from core.error_codes import ErrorCode
    from core.error_handler import APIException

    owner = User(
        username="round3_converge_parent",
        email="round3_converge_parent@example.com",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    db_session.add(owner)
    db_session.commit()
    db_session.refresh(owner)

    project = Project(name="parent-rules", owner_id=owner.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    draft = File(
        project_id=project.id, title="第1章", file_type="draft", content="正文"
    )
    folder = File(project_id=project.id, title="正文", file_type="folder")
    db_session.add_all([draft, folder])
    db_session.commit()
    db_session.refresh(draft)
    db_session.refresh(folder)

    # agent 侧：ValueError
    with pytest.raises(ValueError):
        agent_validate(db_session, project.id, draft.id)
    # REST 侧：同样被拒，只是异常类型换成带 error_code 的 APIException
    with pytest.raises(APIException) as exc:
        rest_validate(db_session, project.id, draft.id)
    assert exc.value.error_code == ErrorCode.VALIDATION_ERROR

    # 合法 folder 两边都放行，返回值一致
    assert agent_validate(db_session, project.id, folder.id) == folder.id
    assert rest_validate(db_session, project.id, folder.id) == folder.id


@pytest.mark.unit
def test_rest_maps_missing_parent_to_file_not_found(db_session: Session):
    """不存在的 parent 必须仍是 FILE_NOT_FOUND，不能因为改用共享实现而漂成
    VALIDATION_ERROR——那会改变既有 REST 契约。"""
    from api.files import _validate_parent_assignment as rest_validate
    from core.error_codes import ErrorCode
    from core.error_handler import APIException

    owner = User(
        username="round3_converge_missing",
        email="round3_converge_missing@example.com",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    db_session.add(owner)
    db_session.commit()
    db_session.refresh(owner)

    project = Project(name="missing-parent", owner_id=owner.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    with pytest.raises(APIException) as exc:
        rest_validate(db_session, project.id, "does-not-exist")
    assert exc.value.error_code == ErrorCode.FILE_NOT_FOUND

    assert rest_validate(db_session, project.id, None) is None
