"""Round3 收敛阶段回归测试：REST 层的跨组遗留项。

- crud #1：api/files.py 的 parent 校验与 agent 侧曾是两份实现，
  agent 那份漏了「parent 必须是 folder」，导致 AI 写的章节挂到普通文件下、
  在文件树里彻底不可见。现在统一到 services/file_tree_rules，
  这里钉死「两个入口对同一份不合法输入给出同样的判定」。
- agent-api #1：/api/v1/editor/natural-polish 同样直连 LLM，
  却既没有按用户限流、失败也不退还已预扣的 AI 对话额度。
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from models import File, Project, UsageQuota, User
from services.core.auth_service import hash_password

FREE_AI_CONVERSATION_LIMIT = 10  # 无订阅计划时 quota_service 的兜底日限额


async def _make_user_and_project(
    client: AsyncClient, db_session: Session, name: str
) -> tuple[User, Project, str]:
    user = User(
        username=name,
        email=f"{name}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    login_response = await client.post(
        "/api/auth/login",
        data={"username": name, "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name=f"{name}-project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return user, project, token


def _read_quota_used(db_session: Session, user_id: str) -> int:
    db_session.expire_all()
    quota = db_session.exec(
        select(UsageQuota).where(UsageQuota.user_id == user_id)
    ).first()
    return quota.ai_conversations_used if quota else 0


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


# ---------------------------------------------------------------------------
# agent-api #1：/editor/natural-polish 的限流与失败退款
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_natural_polish_has_user_scoped_rate_limit():
    """守卫：natural-polish 直连 LLM，必须挂按用户限流依赖。"""
    from api.editor import (
        NATURAL_POLISH_RATE_LIMIT_MAX_REQUESTS,
        NATURAL_POLISH_RATE_LIMIT_WINDOW_SECONDS,
    )
    from api.editor import router as editor_router

    route = next(
        r for r in editor_router.routes if r.path == "/api/v1/editor/natural-polish"
    )
    rate_limiters = [
        dep.call
        for dep in route.dependant.dependencies
        if getattr(dep.call, "rate_limit_key", None) is not None
    ]
    assert len(rate_limiters) == 1, "natural-polish 缺少按用户限流依赖"
    assert (
        rate_limiters[0].rate_limit_max_requests
        == NATURAL_POLISH_RATE_LIMIT_MAX_REQUESTS
    )
    assert NATURAL_POLISH_RATE_LIMIT_WINDOW_SECONDS == 3600


@pytest.mark.integration
async def test_natural_polish_refunds_quota_on_failure(
    client: AsyncClient, db_session: Session
):
    """润色失败必须退还已预扣的额度，否则用户为一次没拿到结果的调用买单。"""
    user, project, token = await _make_user_and_project(
        client, db_session, "round3_polish_refund"
    )

    service = MagicMock()
    service.natural_polish = AsyncMock(side_effect=RuntimeError("upstream down"))

    with patch("api.editor.natural_polish_service", service):
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={"project_id": project.id, "selected_text": "这段话有点 AI 味"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 500
    assert _read_quota_used(db_session, user.id) == 0, "失败必须退款"


@pytest.mark.integration
async def test_natural_polish_consumes_quota_on_success(
    client: AsyncClient, db_session: Session
):
    """成功路径仍然要扣一次额度（不能被退款逻辑误伤）。"""
    user, project, token = await _make_user_and_project(
        client, db_session, "round3_polish_success"
    )

    service = MagicMock()
    service.natural_polish = AsyncMock(
        return_value=MagicMock(polished_text="改好了", model="deepseek")
    )

    with patch("api.editor.natural_polish_service", service):
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={"project_id": project.id, "selected_text": "这段话有点 AI 味"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["text"] == "改好了"
    assert _read_quota_used(db_session, user.id) == 1


@pytest.mark.integration
async def test_natural_polish_rejected_when_quota_exhausted(
    client: AsyncClient, db_session: Session
):
    """额度耗尽时直接拒绝，不得触达 LLM。"""
    user, project, token = await _make_user_and_project(
        client, db_session, "round3_polish_quota_out"
    )
    now = datetime.utcnow()
    db_session.add(
        UsageQuota(
            user_id=user.id,
            period_start=now,
            period_end=now + timedelta(hours=24),
            ai_conversations_used=FREE_AI_CONVERSATION_LIMIT,
            last_reset_at=now,
        )
    )
    db_session.commit()

    service = MagicMock()
    service.natural_polish = AsyncMock(return_value=MagicMock(polished_text="不该生成"))

    with patch("api.editor.natural_polish_service", service):
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={"project_id": project.id, "selected_text": "文本"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 402
    service.natural_polish.assert_not_awaited()
