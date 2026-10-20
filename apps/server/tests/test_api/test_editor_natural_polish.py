"""Tests for /api/v1/editor/natural-polish."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from core.error_handler import APIException
from models import Project, User
from models.subscription import UsageQuota
from services.core.auth_service import hash_password
from services.features.natural_polish_service import (
    NaturalPolishFailure,
    NaturalPolishProtocolError,
    NaturalPolishResult,
    apply_line_edits,
    build_attention_hints,
    parse_line_edits,
)

_CHARGED_PERIOD = datetime(2026, 10, 5, 16, tzinfo=UTC)


def _create_user(
    db_session: Session,
    *,
    username: str,
    email: str,
    is_superuser: bool,
) -> User:
    user = User(
        username=username,
        email=email,
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
        is_superuser=is_superuser,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


async def _login(client: AsyncClient, username: str) -> str:
    response = await client.post(
        "/api/auth/login",
        data={"username": username, "password": "password123"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _create_project(db_session: Session, owner_id: str, name: str = "np project") -> Project:
    project = Project(name=name, owner_id=owner_id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.mark.integration
async def test_natural_polish_success(client: AsyncClient, db_session: Session):
    user = _create_user(
        db_session,
        username="np_user_success",
        email="np_user_success@example.com",
        is_superuser=False,
    )
    project = _create_project(db_session, owner_id=user.id)
    token = await _login(client, user.username)

    with (
        patch(
            "api.editor.quota_service.check_ai_conversation_quota",
            return_value=(True, 0, 20),
        ) as mock_check,
        patch(
            "api.editor.quota_service.reserve_ai_conversation",
            return_value=_CHARGED_PERIOD,
        ) as mock_consume,
        patch(
            "api.editor.natural_polish_service.natural_polish",
            new=AsyncMock(return_value=NaturalPolishResult(polished_text="rewritten text", model="test-model")),
        ) as mock_polish,
    ):
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={
                "project_id": str(project.id),
                "selected_text": "原始文本",
                "metadata": {"source": "editor_natural_polish"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json() == {"text": "rewritten text", "model": "test-model", "unchanged": False}
    mock_check.assert_called_once_with(db_session, user.id)
    mock_consume.assert_called_once_with(db_session, user.id)
    mock_polish.assert_awaited_once_with(
        selected_text="原始文本",
        language="zh",
        file_type=None,
        user_id=user.id,
        project_id=str(project.id),
    )


@pytest.mark.integration
async def test_natural_polish_requires_auth(client: AsyncClient):
    response = await client.post(
        "/api/v1/editor/natural-polish",
        json={
            "project_id": "any",
            "selected_text": "text",
        },
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_natural_polish_project_access_forbidden(client: AsyncClient, db_session: Session):
    admin = _create_user(
        db_session,
        username="np_admin_project_forbidden",
        email="np_admin_project_forbidden@example.com",
        is_superuser=True,
    )
    token = await _login(client, admin.username)

    with patch("api.editor.quota_service.check_ai_conversation_quota") as mock_check:
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={
                "project_id": "00000000-0000-0000-0000-000000000000",
                "selected_text": "text",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "ERR_NOT_AUTHORIZED"
    mock_check.assert_not_called()


@pytest.mark.integration
async def test_natural_polish_quota_denied(client: AsyncClient, db_session: Session):
    admin = _create_user(
        db_session,
        username="np_admin_quota_denied",
        email="np_admin_quota_denied@example.com",
        is_superuser=True,
    )
    project = _create_project(db_session, owner_id=admin.id)
    token = await _login(client, admin.username)

    with (
        patch(
            "api.editor.quota_service.check_ai_conversation_quota",
            return_value=(False, 3, 3),
        ) as mock_check,
        patch("api.editor.quota_service.reserve_ai_conversation") as mock_consume,
    ):
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={
                "project_id": str(project.id),
                "selected_text": "text",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 402
    assert response.json()["detail"] == "ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED"
    mock_check.assert_called_once_with(db_session, admin.id)
    mock_consume.assert_not_called()


@pytest.mark.integration
async def test_natural_polish_consume_denied_after_check(client: AsyncClient, db_session: Session):
    admin = _create_user(
        db_session,
        username="np_admin_consume_denied",
        email="np_admin_consume_denied@example.com",
        is_superuser=True,
    )
    project = _create_project(db_session, owner_id=admin.id)
    token = await _login(client, admin.username)

    with (
        patch(
            "api.editor.quota_service.check_ai_conversation_quota",
            return_value=(True, 0, 20),
        ) as mock_check,
        patch(
            "api.editor.quota_service.reserve_ai_conversation",
            return_value=None,
        ) as mock_consume,
    ):
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={
                "project_id": str(project.id),
                "selected_text": "text",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 402
    assert response.json()["detail"] == "ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED"
    mock_check.assert_called_once_with(db_session, admin.id)
    mock_consume.assert_called_once_with(db_session, admin.id)


@pytest.mark.integration
async def test_natural_polish_selected_text_empty(client: AsyncClient, db_session: Session):
    admin = _create_user(
        db_session,
        username="np_admin_empty",
        email="np_admin_empty@example.com",
        is_superuser=True,
    )
    project = _create_project(db_session, owner_id=admin.id)
    token = await _login(client, admin.username)

    response = await client.post(
        "/api/v1/editor/natural-polish",
        json={
            "project_id": str(project.id),
            "selected_text": "   ",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "ERR_VALIDATION_ERROR"


@pytest.mark.integration
async def test_natural_polish_selected_text_too_long(client: AsyncClient, db_session: Session):
    admin = _create_user(
        db_session,
        username="np_admin_too_long",
        email="np_admin_too_long@example.com",
        is_superuser=True,
    )
    project = _create_project(db_session, owner_id=admin.id)
    token = await _login(client, admin.username)

    response = await client.post(
        "/api/v1/editor/natural-polish",
        json={
            "project_id": str(project.id),
            "selected_text": "x" * 6001,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "ERR_VALIDATION_ERROR"


@pytest.mark.integration
async def test_natural_polish_selected_text_6000_is_allowed(client: AsyncClient, db_session: Session):
    admin = _create_user(
        db_session,
        username="np_admin_6000_ok",
        email="np_admin_6000_ok@example.com",
        is_superuser=True,
    )
    project = _create_project(db_session, owner_id=admin.id)
    token = await _login(client, admin.username)

    with (
        patch(
            "api.editor.quota_service.check_ai_conversation_quota",
            return_value=(True, 0, 20),
        ),
        patch(
            "api.editor.quota_service.reserve_ai_conversation",
            return_value=_CHARGED_PERIOD,
        ),
        patch(
            "api.editor.natural_polish_service.natural_polish",
            new=AsyncMock(return_value=NaturalPolishResult(polished_text="ok", model="test-model")),
        ),
    ):
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={
                "project_id": str(project.id),
                "selected_text": "x" * 6000,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["text"] == "ok"


@pytest.mark.integration
async def test_natural_polish_llm_failure_returns_500(client: AsyncClient, db_session: Session):
    admin = _create_user(
        db_session,
        username="np_admin_llm_fail",
        email="np_admin_llm_fail@example.com",
        is_superuser=True,
    )
    project = _create_project(db_session, owner_id=admin.id)
    token = await _login(client, admin.username)

    with (
        patch(
            "api.editor.quota_service.check_ai_conversation_quota",
            return_value=(True, 0, 20),
        ),
        patch(
            "api.editor.quota_service.reserve_ai_conversation",
            return_value=_CHARGED_PERIOD,
        ),
        patch(
            "api.editor.quota_service.release_ai_conversation",
            return_value=True,
        ) as mock_refund,
        patch(
            "api.editor.natural_polish_service.natural_polish",
            new=AsyncMock(side_effect=RuntimeError("llm boom")),
        ),
    ):
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={
                "project_id": str(project.id),
                "selected_text": "text",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 500
    assert response.json()["detail"] == "ERR_INTERNAL_SERVER_ERROR"
    mock_refund.assert_called_once_with(
        db_session,
        admin.id,
        period_start=_CHARGED_PERIOD,
    )


@pytest.mark.integration
@pytest.mark.parametrize("refund_succeeds", [True, False])
async def test_natural_polish_protocol_failure_attempts_one_refund_and_never_returns_review_text(
    client: AsyncClient,
    db_session: Session,
    refund_succeeds: bool,
):
    admin = _create_user(
        db_session,
        username=f"np_protocol_fail_{refund_succeeds}",
        email=f"np_protocol_fail_{refund_succeeds}@example.com",
        is_superuser=True,
    )
    project = _create_project(db_session, owner_id=admin.id)
    token = await _login(client, admin.username)

    with (
        patch("api.editor.quota_service.check_ai_conversation_quota", return_value=(True, 0, 20)),
        patch(
            "api.editor.quota_service.reserve_ai_conversation",
            return_value=_CHARGED_PERIOD,
        ) as mock_reserve,
        patch(
            "api.editor.quota_service.release_ai_conversation",
            return_value=refund_succeeds,
        ) as mock_refund,
        patch(
            "api.editor.natural_polish_service.natural_polish",
            new=AsyncMock(side_effect=NaturalPolishProtocolError("invalid natural polish response")),
        ),
    ):
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={"project_id": str(project.id), "selected_text": "原文"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 500
    assert response.json()["detail"] == "ERR_INTERNAL_SERVER_ERROR"
    assert "text" not in response.json()
    assert "unchanged" not in response.json()
    mock_reserve.assert_called_once_with(db_session, admin.id)
    mock_refund.assert_called_once_with(
        db_session,
        admin.id,
        period_start=_CHARGED_PERIOD,
    )


@pytest.mark.integration
@pytest.mark.parametrize("refund_succeeds", [True, False])
async def test_natural_polish_failure_logs_only_fixed_diagnostics_and_one_refund(
    client: AsyncClient,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
    refund_succeeds: bool,
):
    user = _create_user(
        db_session,
        username=f"np_safe_diagnostics_{refund_succeeds}",
        email=f"np_safe_diagnostics_{refund_succeeds}@example.com",
        is_superuser=False,
    )
    project = _create_project(db_session, owner_id=user.id)
    token = await _login(client, user.username)
    private_text = "PRIVATE_SELECTED_TEXT"

    with (
        caplog.at_level(logging.WARNING, logger="api.editor"),
        patch("api.editor.quota_service.check_ai_conversation_quota", return_value=(True, 0, 20)),
        patch("api.editor.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD) as reserve,
        patch(
            "api.editor.quota_service.release_ai_conversation",
            return_value=refund_succeeds,
        ) as refund,
        patch(
            "api.editor.natural_polish_service.natural_polish",
            new=AsyncMock(
                side_effect=NaturalPolishFailure(
                    stage="anchor_apply",
                    category="ambiguous_anchor",
                )
            ),
        ),
    ):
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={"project_id": str(project.id), "selected_text": private_text},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 500
    reserve.assert_called_once_with(db_session, user.id)
    refund.assert_called_once_with(db_session, user.id, period_start=_CHARGED_PERIOD)
    failure_log = next(
        record for record in caplog.records if record.getMessage() == "Natural polish request failed"
    )
    fields = failure_log.custom_fields
    assert fields["request_id"] == response.headers["x-request-id"]
    assert fields["stage"] == "anchor_apply"
    assert fields["failure_category"] == "ambiguous_anchor"
    assert fields["refund_succeeded"] is refund_succeeds
    rendered_failure_log = repr((failure_log.getMessage(), fields))
    assert private_text not in rendered_failure_log
    assert str(project.id) not in rendered_failure_log
    assert user.id not in rendered_failure_log


@pytest.mark.integration
@pytest.mark.parametrize(
    ("status_code", "error_code"),
    [
        (402, "ERR_QUOTA_AI_DAILY_COST_EXCEEDED"),
        (503, "ERR_AI_COST_BUDGET_UNAVAILABLE"),
    ],
)
async def test_natural_polish_cost_guard_error_is_preserved_and_refunded_once(
    client: AsyncClient,
    db_session: Session,
    status_code: int,
    error_code: str,
):
    user = _create_user(
        db_session,
        username=f"np_cost_guard_{status_code}",
        email=f"np_cost_guard_{status_code}@example.com",
        is_superuser=False,
    )
    project = _create_project(db_session, owner_id=user.id)
    token = await _login(client, user.username)

    with (
        patch("api.editor.quota_service.check_ai_conversation_quota", return_value=(True, 0, 20)),
        patch("api.editor.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD) as reserve,
        patch("api.editor.quota_service.release_ai_conversation", return_value=True) as refund,
        patch(
            "api.editor.natural_polish_service.natural_polish",
            new=AsyncMock(
                side_effect=APIException(error_code=error_code, status_code=status_code)
            ),
        ),
    ):
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={"project_id": str(project.id), "selected_text": "合成正文"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == status_code
    assert response.json()["detail"] == error_code
    reserve.assert_called_once_with(db_session, user.id)
    refund.assert_called_once_with(db_session, user.id, period_start=_CHARGED_PERIOD)


def test_natural_polish_refund_exception_log_is_redacted(db_session: Session):
    from api.editor import _refund_ai_conversation

    with (
        patch(
            "api.editor.quota_service.release_ai_conversation",
            side_effect=RuntimeError("PRIVATE_REFUND_ERROR"),
        ) as refund,
        patch("api.editor.log_with_context") as log_event,
    ):
        refunded = _refund_ai_conversation(
            db_session,
            "private-user-id",
            _CHARGED_PERIOD,
            reason="ambiguous_anchor",
        )

    assert refunded is False
    refund.assert_called_once_with(
        db_session,
        "private-user-id",
        period_start=_CHARGED_PERIOD,
    )
    assert log_event.call_args.args[2] == "Failed to refund AI conversation quota for natural polish"
    assert log_event.call_args.kwargs == {
        "stage": "refund",
        "failure_category": "refund_failed",
        "refund_reason": "ambiguous_anchor",
    }
    rendered_log = repr(log_event.call_args)
    assert "PRIVATE_REFUND_ERROR" not in rendered_log
    assert "private-user-id" not in rendered_log


def _ai_messages_used(db_session: Session, user_id: str) -> int:
    db_session.expire_all()
    quota = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == user_id)).first()
    return quota.ai_conversations_used if quota else 0


async def _polish_with_real_quota(
    client: AsyncClient,
    db_session: Session,
    *,
    username: str,
    selected_text: str,
    polished_text: str,
) -> tuple[dict, int]:
    """Run /natural-polish against the real quota ledger; return (body, AI messages used after)."""
    user = _create_user(
        db_session,
        username=username,
        email=f"{username}@example.com",
        is_superuser=False,
    )
    project = _create_project(db_session, owner_id=user.id)
    token = await _login(client, user.username)
    assert _ai_messages_used(db_session, user.id) == 0

    with patch(
        "api.editor.natural_polish_service.natural_polish",
        new=AsyncMock(return_value=NaturalPolishResult(polished_text=polished_text, model="test-model")),
    ):
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={"project_id": str(project.id), "selected_text": selected_text},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    return response.json(), _ai_messages_used(db_session, user.id)


@pytest.mark.integration
async def test_natural_polish_no_change_refunds_the_ai_message(client: AsyncClient, db_session: Session):
    original = "\u3000\u3000他站在门口。\n\n\u3000\u3000雨还在下。"
    # 模型吃掉了全角缩进、改了换行，但一个字都没改。
    body, used_after = await _polish_with_real_quota(
        client,
        db_session,
        username="np_noop_refund",
        selected_text=original,
        polished_text="他站在门口。\n雨还在下。\n",
    )

    assert body["unchanged"] is True
    # 返回原文，旧前端即使忽略 unchanged 也不会把格式改动带进审阅。
    assert body["text"] == original
    assert used_after == 0


@pytest.mark.integration
async def test_natural_polish_quote_style_only_change_counts_as_unchanged(
    client: AsyncClient, db_session: Session
):
    body, used_after = await _polish_with_real_quota(
        client,
        db_session,
        username="np_quote_only",
        selected_text='他说："我不爱你了。"她答：\u300c好。\u300d',
        polished_text="他说：\u201c我不爱你了。\u201d她答：\u201c好。\u201d",
    )

    assert body["unchanged"] is True
    assert used_after == 0


@pytest.mark.integration
async def test_natural_polish_real_change_keeps_the_charge(client: AsyncClient, db_session: Session):
    body, used_after = await _polish_with_real_quota(
        client,
        db_session,
        username="np_real_change",
        selected_text="她唇角一勾，几不可察地笑了。",
        polished_text="她笑了一下。",
    )

    assert body == {"text": "她笑了一下。", "model": "test-model", "unchanged": False}
    assert used_after == 1


@pytest.mark.integration
async def test_natural_polish_passes_current_file_type_to_service(client: AsyncClient, db_session: Session):
    user = _create_user(
        db_session,
        username="np_file_type",
        email="np_file_type@example.com",
        is_superuser=False,
    )
    project = _create_project(db_session, owner_id=user.id)
    token = await _login(client, user.username)

    with (
        patch(
            "api.editor.quota_service.check_ai_conversation_quota",
            return_value=(True, 0, 20),
        ),
        patch(
            "api.editor.quota_service.reserve_ai_conversation",
            return_value=_CHARGED_PERIOD,
        ),
        patch(
            "api.editor.natural_polish_service.natural_polish",
            new=AsyncMock(return_value=NaturalPolishResult(polished_text="△ 她转身离开。", model="m")),
        ) as mock_polish,
    ):
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={
                "project_id": str(project.id),
                "selected_text": "△ 她唇角一勾，转身离开。",
                "metadata": {"current_file_id": "f-1", "current_file_type": "script"},
            },
            headers={"Authorization": f"Bearer {token}", "Accept-Language": "en-US,en;q=0.9"},
        )

    assert response.status_code == 200
    mock_polish.assert_awaited_once_with(
        selected_text="△ 她唇角一勾，转身离开。",
        language="en",
        file_type="script",
        user_id=user.id,
        project_id=str(project.id),
    )


def test_script_prompt_adds_format_rules_only_for_scripts():
    from services.features.natural_polish_service import NaturalPolishService

    script_prompt = NaturalPolishService._resolve_prompt("zh", "script")
    draft_prompt = NaturalPolishService._resolve_prompt("zh", "draft")

    assert "△" in script_prompt
    assert "△" not in draft_prompt
    assert script_prompt.startswith(draft_prompt)


# ---------------------------------------------------------------------------
# 行级改动协议：模型只列「原：/改：」，服务端套回原文，格式由代码保证
# ---------------------------------------------------------------------------


def test_line_edits_keep_indentation_and_untouched_lines_byte_identical():
    original = "　　她唇角一勾，没说话。\n\n　　窗外下着雨。\n\n　　空气仿佛凝固了。"
    output = "原：她唇角一勾，没说话。\n改：她没说话。\n\n原：　　空气仿佛凝固了。\n改："

    text, dropped = apply_line_edits(original, parse_line_edits(output))

    assert dropped == 0
    assert text == "　　她没说话。\n\n　　窗外下着雨。"


def test_deleted_closer_with_content_comes_back_and_a_rewrite_is_kept():
    original = "　　她把信折好。\n\n　　那一刻，她终于明白了母亲为什么一直不肯搬走。"
    deleted = parse_line_edits("原：　　那一刻，她终于明白了母亲为什么一直不肯搬走。\n改：")
    rewritten = parse_line_edits(
        "原：　　那一刻，她终于明白了母亲为什么一直不肯搬走。\n改：　　她想起母亲每晚都把院门留一条缝，等那个不会回来的人。"
    )

    assert apply_line_edits(original, deleted)[0] == original
    assert apply_line_edits(original, rewritten)[0] == (
        "　　她把信折好。\n\n　　她想起母亲每晚都把院门留一条缝，等那个不会回来的人。"
    )


def test_line_edits_restore_the_original_quote_style():
    original = '他说："我不爱你了。"她唇角一勾。'
    edits = parse_line_edits("原：他说：“我不爱你了。”她唇角一勾。\n改：他说：“我不爱你了。”她笑了笑。")

    text, _ = apply_line_edits(original, edits)

    assert text == '他说："我不爱你了。"她笑了笑。'


def test_script_line_edits_never_touch_structure():
    original = "\n".join(
        [
            "【场1】机场 · 日 · 内",
            "人物：苏晚、周野",
            "",
            "△ 苏晚唇角几不可察地一勾。",
            "",
            "苏晚（淡淡）：走吧。",
        ]
    )
    output = "\n".join(
        [
            "原：【场1】机场 · 日 · 内",
            "改：机场大厅",
            "",
            "原：△ 苏晚唇角几不可察地一勾。",
            "改：苏晚摘下墨镜。",
            "",
            "原：苏晚（淡淡）：走吧。",
            "改：苏晚：跟上。",
            "",
            "原：人物：苏晚、周野",
            "改：",
        ]
    )

    text, _ = apply_line_edits(original, parse_line_edits(output), file_type="script")

    assert text.split("\n") == [
        "【场1】机场 · 日 · 内",
        "人物：苏晚、周野",
        "",
        "△ 苏晚摘下墨镜。",
        "",
        "苏晚（淡淡）：跟上。",
    ]


def test_script_action_and_dialogue_lines_cannot_be_deleted():
    original = "△ 硬切黑屏。\n\n陆沉：签。"
    edits = parse_line_edits("原：△ 硬切黑屏。\n改：\n\n原：陆沉：签。\n改：")

    text, _ = apply_line_edits(original, edits, file_type="script")

    assert text == original


def test_no_change_marker_is_valid_and_unanchored_edits_are_protocol_failures():
    assert parse_line_edits("无改动") == []
    assert parse_line_edits("NO CHANGES") == []

    with pytest.raises(NaturalPolishProtocolError, match="unmatched OLD anchor"):
        apply_line_edits("母亲在剥蒜。", parse_line_edits("原：完全不存在的一句\n改：别的"))


def test_output_that_only_echoes_old_lines_is_a_protocol_failure():
    # 真实模型复跑（2026-10-09）里出现过：每行都抄成「原：」，一组「改：」都没有。
    # 以前当成整段改写，「原：」前缀被写进了正文。
    output = "原：　　我又想起葬礼上。\n\n原：　　不是愧疚，是心疼。"

    with pytest.raises(NaturalPolishProtocolError, match="expected NEW marker"):
        parse_line_edits(output)


def test_sentence_level_edit_is_spliced_into_its_paragraph():
    original = "　　雨停了。他心中一震，回过头。"

    text, _ = apply_line_edits(original, parse_line_edits("原：他心中一震，回过头。\n改：他回过头。"))

    assert text == "　　雨停了。他回过头。"


def test_attention_hints_point_at_stock_phrase_lines_only():
    text = "　　她指节泛白，没说话。\n\n　　窗外下着雨。\n\n　　原来，爱一直都在。"

    hints = build_attention_hints(text, "zh", "draft")

    assert "「她指节泛白，没说话。」：指节泛白" in hints
    assert "「窗外下着雨。」" not in hints
    # 最后一句不再单独点给模型：那条提示让模型删掉了结尾的主题句（第二轮审计 N1）。
    assert "原来，爱一直都在" not in hints
    assert "最后一句" not in hints


def test_attention_hints_point_at_chinese_triplets():
    text = "　　他用了一种最笨、最疼、最不体面的方式。\n\n　　她买了苹果、梨和橘子。"

    hints = build_attention_hints(text, "zh", "draft")

    assert "三连排比" in hints
    assert "「他用了一种最笨、最疼、最不体面的方式。」：最笨、最疼、最不体面" in hints
    assert "苹果" not in hints


def test_attention_hints_stay_empty_when_nothing_matches():
    assert build_attention_hints("　　窗外下着雨。\n\n　　原来，爱一直都在。", "zh", "draft") == ""
    assert build_attention_hints("Rain fell. In that moment she finally understood.", "en") == ""
    assert build_attention_hints("△ 她转身离开。\n\n苏晚：走吧。", "zh", "script") == ""
    assert build_attention_hints("x" * 6000, "zh") == ""


async def test_service_sends_attention_hints_after_the_static_prompt():
    from unittest.mock import MagicMock

    from services.features.natural_polish_service import NaturalPolishService

    llm_client = MagicMock()
    llm_client.acomplete = AsyncMock(return_value="无改动")
    text = "他心中一震。"

    with patch("services.features.natural_polish_service.get_llm_client", return_value=llm_client):
        result = await NaturalPolishService().natural_polish(selected_text=text, language="zh")

    system_prompt = llm_client.acomplete.await_args.kwargs["messages"][0]["content"]
    assert system_prompt.startswith(NaturalPolishService._resolve_prompt("zh"))
    assert "「他心中一震。」：心中一震" in system_prompt
    assert result.polished_text == text


# ---------------------------------------------------------------------------
# 整句删除守卫（第二轮审计 N1）：经真实 service 和真实额度表走完整个接口
# ---------------------------------------------------------------------------

_THEME_SAMPLE = "\n\n".join(
    [
        "　　不是愧疚，是心疼。她是在心疼我。",
        "　　她什么都能跟我解释，什么都有一肚子的委屈可以说。",
        "　　可她把所有的错都揽到了自己身上。她宁可让我恨她，也不肯让我知道，我恨错了人。",
    ]
)


async def _polish_with_model_output(
    client: AsyncClient, db_session: Session, *, username: str, selected_text: str, model_output: str
) -> tuple[dict, int]:
    from unittest.mock import MagicMock

    llm_client = MagicMock()

    async def acomplete(**kwargs):
        kwargs["served_model_sink"]["model"] = "served-model"
        return model_output

    llm_client.acomplete = AsyncMock(side_effect=acomplete)

    user = _create_user(db_session, username=username, email=f"{username}@example.com", is_superuser=False)
    project = _create_project(db_session, owner_id=user.id)
    token = await _login(client, user.username)

    with patch("services.features.natural_polish_service.get_llm_client", return_value=llm_client):
        response = await client.post(
            "/api/v1/editor/natural-polish",
            json={"project_id": str(project.id), "selected_text": selected_text},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    return response.json(), _ai_messages_used(db_session, user.id)


@pytest.mark.integration
async def test_natural_polish_keeps_the_theme_sentence_and_applies_the_trims(
    client: AsyncClient, db_session: Session
):
    body, used_after = await _polish_with_model_output(
        client,
        db_session,
        username="np_theme_kept",
        selected_text=_THEME_SAMPLE,
        model_output=(
            "原：　　不是愧疚，是心疼。她是在心疼我。\n改：　　不是愧疚，是心疼。\n\n"
            "原：　　可她把所有的错都揽到了自己身上。她宁可让我恨她，也不肯让我知道，我恨错了人。\n"
            "改：　　可她把所有的错都揽到了自己身上。"
        ),
    )

    assert body["unchanged"] is False
    assert body["text"] == "\n\n".join(
        [
            "　　不是愧疚，是心疼。",
            "　　她什么都能跟我解释，什么都有一肚子的委屈可以说。",
            "　　可她把所有的错都揽到了自己身上。她宁可让我恨她，也不肯让我知道，我恨错了人。",
        ]
    )
    assert used_after == 1


@pytest.mark.integration
async def test_natural_polish_that_only_dropped_the_theme_sentence_is_refunded(
    client: AsyncClient, db_session: Session
):
    body, used_after = await _polish_with_model_output(
        client,
        db_session,
        username="np_theme_only_drop",
        selected_text=_THEME_SAMPLE,
        model_output=(
            "原：　　可她把所有的错都揽到了自己身上。她宁可让我恨她，也不肯让我知道，我恨错了人。\n"
            "改：　　可她把所有的错都揽到了自己身上。"
        ),
    )

    # 守卫放回主题句后结果和原文一样：按「无改动」退还，额度不变。
    assert body == {"text": _THEME_SAMPLE, "model": "served-model", "unchanged": True}
    assert used_after == 0
