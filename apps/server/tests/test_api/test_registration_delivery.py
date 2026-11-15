"""Registration delivery failures and password-proven unverified-account recovery."""

from unittest.mock import MagicMock

import pytest
from resend.exceptions import ValidationError
from sqlmodel import select

from models import User
from models.subscription import UsageQuota, UserSubscription
from services.core.auth_service import hash_password
from services.features import verification_service
from services.infra import email_client


@pytest.fixture(autouse=True)
def delivery(monkeypatch):
    monkeypatch.setenv("AUTH_REGISTER_INVITE_CODE_OPTIONAL", "true")
    monkeypatch.setattr(email_client, "RESEND_API_KEY", "mock-only")
    sender = MagicMock(return_value={"id": "mock-delivery"})
    monkeypatch.setattr(email_client.resend.Emails, "send", sender)
    for name, value in {
        "get_resend_cooldown_ttl": 0, "store_verification_code": True,
        "set_resend_cooldown": True, "reset_verification_attempts": True,
        "delete_verification_code": True, "delete_resend_cooldown": True,
    }.items():
        monkeypatch.setattr(verification_service, name, MagicMock(return_value=value))
    return sender


def invalid_recipient():
    return ValidationError(code=422, error_type="validation_error", message="Invalid `to` field. Use a valid email address.")


async def register(client, email="writer@example.com", username="delivery-writer"):
    return await client.post("/api/auth/register", json={
        "username": username, "email": email, "password": "OwnedPassword1!",
    })


@pytest.mark.parametrize("email", ["writer@example.c0m", "writer@example.1om", "writer@example.co1"])
async def test_invalid_ascii_tld_is_rejected_before_creation(client, db_session, delivery, email):
    response = await register(client, email)
    assert 400 <= response.status_code < 500
    assert db_session.exec(select(User)).all() == []
    delivery.assert_not_called()


@pytest.mark.parametrize("domain", ["example.com", "例子.中国", "example.xn--fiqs8s", "example.рф"])
async def test_idna_valid_addresses_are_not_rejected_by_ascii_rules(client, delivery, domain):
    response = await register(client, f"writer@{domain}")
    assert response.status_code == 200
    assert response.json()["verification_sent"] is True
    delivery.assert_called_once()
    assert delivery.call_args.args[0]["to"][0].isascii()


async def test_recipient_rejection_rolls_back_new_account_and_allows_same_username_retry(client, db_session, delivery):
    delivery.side_effect = invalid_recipient()
    response = await register(client)
    assert response.status_code == 400
    assert response.json()["error_code"] == "ERR_AUTH_EMAIL_UNDELIVERABLE"
    assert db_session.exec(select(User)).all() == []
    assert db_session.exec(select(UserSubscription)).all() == []
    assert db_session.exec(select(UsageQuota)).all() == []
    verification_service.delete_verification_code.assert_called()
    verification_service.delete_resend_cooldown.assert_called()
    delivery.side_effect = None
    assert (await register(client, "corrected@example.com")).status_code == 200


@pytest.mark.parametrize("failure", [
    TimeoutError("provider timed out"), RuntimeError("provider unavailable"),
    ValidationError(code=422, error_type="validation_error", message="Invalid `from` field"),
    ValidationError(code=403, error_type="validation_error", message="The sending domain is not verified"),
])
async def test_other_delivery_failures_keep_recoverable_account_without_claiming_sent(client, db_session, delivery, failure):
    delivery.side_effect = failure
    response = await register(client)
    assert response.status_code == 200 and response.json()["verification_sent"] is False
    user = db_session.exec(select(User)).one()
    original_id = user.id
    assert user.email_verified is False
    delivery.side_effect = None
    assert (await client.post("/api/auth/resend-verification", json={"email": user.email})).status_code == 200
    db_session.expire_all()
    assert db_session.exec(select(User)).one().id == original_id


async def test_resend_recipient_rejection_explains_address_without_deleting_existing_account(client, db_session, delivery):
    assert (await register(client)).status_code == 200
    user_id = db_session.exec(select(User)).one().id
    delivery.side_effect = invalid_recipient()
    response = await client.post("/api/auth/resend-verification", json={"email": "writer@example.com"})
    assert response.status_code == 400
    assert response.json()["error_code"] == "ERR_AUTH_EMAIL_UNDELIVERABLE"
    assert db_session.get(User, user_id) is not None


def seed_stuck_user(session, *, verified=False, active=True):
    user = User(username="stuck-writer", email="writer@example.c0m", hashed_password=hash_password("OwnedPassword1!"),
                email_verified=verified, is_active=active)
    session.add(user)
    session.commit()
    return user.id


async def recover(client, *, password="OwnedPassword1!", new_email="corrected@example.com"):
    return await client.post("/api/auth/registration-email", json={
        "email": "writer@example.c0m", "password": password, "new_email": new_email,
    })


async def test_password_proven_recovery_keeps_account_and_revokes_old_code(client, db_session, delivery):
    user_id = seed_stuck_user(db_session)
    before = db_session.get(User, user_id).hashed_password
    response = await recover(client)
    assert response.status_code == 200
    assert response.json() == {"email": "corrected@example.com", "verification_sent": True}
    assert "token" not in str(response.json())
    db_session.expire_all()
    user = db_session.get(User, user_id)
    assert (user.username, user.email, user.hashed_password, user.email_verified) == (
        "stuck-writer", "corrected@example.com", before, False,
    )
    verification_service.delete_verification_code.assert_any_call("writer@example.c0m")
    verification_service.delete_resend_cooldown.assert_any_call("writer@example.c0m")


@pytest.mark.parametrize("verified,active,password", [(True, True, "OwnedPassword1!"), (False, False, "OwnedPassword1!"), (False, True, "wrong-password")])
async def test_recovery_does_not_claim_verified_disabled_or_unowned_account(client, db_session, delivery, verified, active, password):
    user_id = seed_stuck_user(db_session, verified=verified, active=active)
    before = db_session.get(User, user_id).model_dump()
    response = await recover(client, password=password)
    assert response.status_code == 401
    assert response.json()["error_code"] == "ERR_AUTH_INVALID_CREDENTIALS"
    db_session.expire_all()
    assert db_session.get(User, user_id).model_dump() == before
    delivery.assert_not_called()


@pytest.mark.parametrize("failure", [invalid_recipient(), TimeoutError("timeout")])
async def test_recovery_delivery_failure_preserves_original_account(client, db_session, delivery, failure):
    user_id = seed_stuck_user(db_session)
    before = db_session.get(User, user_id).model_dump()
    delivery.side_effect = failure
    response = await recover(client)
    assert response.status_code in [400, 503]
    db_session.expire_all()
    assert db_session.get(User, user_id).model_dump() == before


async def test_recovery_unknown_account_has_the_same_credential_error(client, delivery):
    response = await recover(client)
    assert response.status_code == 401
    assert response.json()["error_code"] == "ERR_AUTH_INVALID_CREDENTIALS"
    delivery.assert_not_called()


async def test_recovery_cannot_take_another_accounts_address(client, db_session, delivery):
    user_id = seed_stuck_user(db_session)
    db_session.add(User(username="other-writer", email="taken@example.com", hashed_password="unused", email_verified=True))
    db_session.commit()
    response = await recover(client, new_email="taken@example.com")
    assert response.status_code == 400
    assert response.json()["error_code"] == "ERR_AUTH_EMAIL_EXISTS"
    db_session.expire_all()
    assert db_session.get(User, user_id).email == "writer@example.c0m"
    delivery.assert_not_called()


async def test_recovery_password_attempts_are_rate_limited(client, db_session, delivery, monkeypatch):
    seed_stuck_user(db_session)
    monkeypatch.setenv("AUTH_RATE_LIMIT_ENABLED", "true")
    for _ in range(5):
        assert (await recover(client, password="wrong-password")).status_code == 401
    response = await recover(client)
    assert response.status_code == 429
    delivery.assert_not_called()


async def test_failed_old_code_cleanup_keeps_address_reserved_and_allows_recovery_retry(client, db_session, delivery):
    user_id = seed_stuck_user(db_session)
    verification_service.delete_verification_code.side_effect = ConnectionError("Redis unavailable")
    response = await recover(client)
    assert response.status_code == 503
    db_session.expire_all()
    assert db_session.get(User, user_id).email == "writer@example.c0m"
    verification_service.delete_verification_code.side_effect = None
    assert (await recover(client)).status_code == 200


@pytest.mark.parametrize("cleanup", ["delete_verification_code", "delete_resend_cooldown"])
@pytest.mark.parametrize("operation", ["register", "resend", "recover"])
async def test_permanent_recipient_error_survives_redis_cleanup_failure(client, db_session, delivery, cleanup, operation):
    if operation == "resend":
        assert (await register(client)).status_code == 200
    elif operation == "recover":
        seed_stuck_user(db_session)
    prior_calls = delivery.call_count

    def fail_after_provider(*_args):
        if delivery.call_count > prior_calls:
            raise ConnectionError("Redis unavailable")
        return True

    getattr(verification_service, cleanup).side_effect = fail_after_provider
    delivery.side_effect = invalid_recipient()
    if operation == "register":
        response = await register(client)
    elif operation == "resend":
        response = await client.post("/api/auth/resend-verification", json={"email": "writer@example.com"})
    else:
        response = await recover(client)
    assert response.status_code == 400
    assert response.json()["error_code"] == "ERR_AUTH_EMAIL_UNDELIVERABLE"
    assert delivery.call_count == prior_calls + 1
    db_session.expire_all()
    users = db_session.exec(select(User)).all()
    if operation == "register":
        assert users == []
        getattr(verification_service, cleanup).side_effect = None
        delivery.side_effect = None
        assert (await register(client, "corrected@example.com")).status_code == 200
    elif operation == "recover":
        assert users[0].email == "writer@example.c0m"
    else:
        assert users[0].email == "writer@example.com"


async def test_new_registration_replaces_orphaned_code_before_cooldown_check(client, delivery, monkeypatch):
    codes, cooldowns = {"writer@example.com": "orphaned-code"}, {"writer@example.com"}
    monkeypatch.setattr(verification_service, "delete_verification_code", lambda email: codes.pop(email, None) is not None)
    monkeypatch.setattr(verification_service, "delete_resend_cooldown", lambda email: bool(cooldowns.discard(email) is None))
    monkeypatch.setattr(verification_service, "get_resend_cooldown_ttl", lambda email: 60 if email in cooldowns else 0)
    monkeypatch.setattr(verification_service, "store_verification_code", lambda email, code, _ttl: bool(codes.update({email: code}) is None))
    response = await register(client)
    assert response.status_code == 200 and response.json()["verification_sent"] is True
    assert codes["writer@example.com"] != "orphaned-code"
    delivery.assert_called_once()


@pytest.mark.parametrize("cleanup", ["delete_verification_code", "delete_resend_cooldown"])
async def test_new_registration_fails_closed_when_orphan_invalidation_is_unavailable(client, db_session, delivery, cleanup):
    getattr(verification_service, cleanup).return_value = False
    response = await register(client)
    assert response.status_code == 503
    assert db_session.exec(select(User)).all() == []
    delivery.assert_not_called()
