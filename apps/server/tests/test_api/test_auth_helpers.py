from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlmodel import Session

import api.auth as auth_module
from config.datetime_utils import utcnow
from models import RefreshTokenRecord, User
from services.core.auth_service import hash_password


def _create_user(db_session: Session, suffix: str) -> User:
    user = User(
        email=f"auth-helper-{suffix}@example.com",
        username=f"auth-helper-{suffix}",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_normalize_invite_code_and_policy_resolution(monkeypatch):
    monkeypatch.setenv("AUTH_REGISTER_INVITE_CODE_OPTIONAL", "false")
    monkeypatch.setenv("AUTH_REGISTER_INVITE_GRAY_PERCENT", "25")
    monkeypatch.setenv("AUTH_REGISTER_INVITE_GRAY_SALT", "salt")

    assert auth_module._normalize_invite_code(" abcd-1234 ") == "ABCD-1234"

    monkeypatch.setattr(auth_module, "_invite_gray_bucket", lambda identity, salt: 10)
    assert auth_module._resolve_registration_invite_policy(email="user@example.com", username=None) == (
        True,
        "treatment_optional",
        25,
    )
    monkeypatch.setattr(auth_module, "_invite_gray_bucket", lambda identity, salt: 80)
    assert auth_module._resolve_registration_invite_policy(email="user@example.com", username=None) == (
        False,
        "control_required",
        25,
    )


def test_register_request_rejects_a_short_username():
    with pytest.raises(ValidationError):
        auth_module.RegisterRequest(username="ab", email="valid@example.com", password="abcdef")


@pytest.mark.parametrize("username", [None, "", "  "])
def test_register_request_treats_a_missing_or_blank_username_as_not_chosen(username):
    request = auth_module.RegisterRequest(username=username, email="valid@example.com", password="abcdef")
    assert request.username is None


@pytest.mark.parametrize("password", ["", "abcde", "é" * 37])
def test_register_request_rejects_invalid_password_length_or_bcrypt_bytes(password: str):
    with pytest.raises(ValidationError):
        auth_module.RegisterRequest(username="valid-user", email="valid@example.com", password=password)


def test_request_models_trim_username_and_enforce_password_contract():
    request = auth_module.RegisterRequest(username="  valid-user  ", email="valid@example.com", password="abcdef")
    assert request.username == "valid-user"
    with pytest.raises(ValidationError):
        auth_module.UpdateUserRequest(username="  ")
    with pytest.raises(ValidationError):
        auth_module.ChangePasswordRequest(old_password="old-password", new_password="short")

def test_safe_int_from_env_clamps_invalid_values(monkeypatch):
    monkeypatch.setenv("AUTH_REFRESH_TOKEN_RETENTION_DAYS", "0")
    monkeypatch.setenv("AUTH_REFRESH_TOKEN_CLEANUP_INTERVAL_SECONDS", "not-a-number")

    assert auth_module._get_refresh_token_retention_days() == 1
    assert auth_module._get_refresh_token_cleanup_interval_seconds() == 3600


def test_revoke_refresh_family_updates_only_active_family_records(db_session: Session):
    user = _create_user(db_session, "family")
    db_session.add_all(
        [
            RefreshTokenRecord(
                user_id=user.id,
                token_jti="active-1",
                family_id="family-1",
                expires_at=utcnow() + timedelta(days=7),
            ),
            RefreshTokenRecord(
                user_id=user.id,
                token_jti="active-2",
                family_id="family-1",
                expires_at=utcnow() + timedelta(days=7),
            ),
            RefreshTokenRecord(
                user_id=user.id,
                token_jti="revoked",
                family_id="family-1",
                expires_at=utcnow() + timedelta(days=7),
                revoked_at=utcnow(),
            ),
            RefreshTokenRecord(
                user_id=user.id,
                token_jti="other-family",
                family_id="family-2",
                expires_at=utcnow() + timedelta(days=7),
            ),
        ]
    )
    db_session.commit()

    revoked = auth_module._revoke_refresh_family(db_session, family_id="family-1", reason="wave-b")
    db_session.commit()

    assert revoked == 2


def test_cleanup_refresh_token_records_deletes_stale_entries(monkeypatch, db_session: Session):
    user = _create_user(db_session, "cleanup")
    now = utcnow()
    monkeypatch.setattr(auth_module, "_REFRESH_TOKEN_CLEANUP_LAST_RUN_AT", None)
    monkeypatch.setenv("AUTH_REFRESH_TOKEN_RETENTION_DAYS", "1")
    monkeypatch.setenv("AUTH_REFRESH_TOKEN_CLEANUP_INTERVAL_SECONDS", "60")

    db_session.add_all(
        [
            RefreshTokenRecord(
                user_id=user.id,
                token_jti="expired-old",
                family_id="family-expired",
                expires_at=now - timedelta(days=10),
            ),
            RefreshTokenRecord(
                user_id=user.id,
                token_jti="revoked-old",
                family_id="family-revoked",
                expires_at=now + timedelta(days=5),
                revoked_at=now - timedelta(days=10),
            ),
            RefreshTokenRecord(
                user_id=user.id,
                token_jti="fresh",
                family_id="family-fresh",
                expires_at=now + timedelta(days=5),
            ),
        ]
    )
    db_session.commit()

    deleted = auth_module._maybe_cleanup_refresh_token_records(db_session)
    remaining = db_session.exec(auth_module.select(RefreshTokenRecord)).all()

    assert deleted == 2
    assert [record.token_jti for record in remaining] == ["fresh"]
