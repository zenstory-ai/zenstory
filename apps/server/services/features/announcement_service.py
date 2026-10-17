"""Persistence and concurrency rules for the singleton announcement config."""

from fastapi import Request, status
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from api.announcement_schemas import AnnouncementUpdateRequest
from config.datetime_utils import advance_timestamp, normalize_datetime_to_utc, utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import AnnouncementConfig, User
from models.announcement import ANNOUNCEMENT_CONFIG_ID
from services.admin_audit_service import admin_audit_service


def get_announcement_config(session: Session) -> AnnouncementConfig | None:
    return session.get(AnnouncementConfig, ANNOUNCEMENT_CONFIG_ID)


def get_enabled_announcement(session: Session) -> AnnouncementConfig | None:
    return session.exec(
        select(AnnouncementConfig).where(
            AnnouncementConfig.id == ANNOUNCEMENT_CONFIG_ID,
            AnnouncementConfig.enabled.is_(True),
        )
    ).first()


def _audit_value(config: AnnouncementConfig) -> dict[str, object]:
    return {
        "title": config.title,
        "description": config.description,
        "action_label": config.action_label,
        "action_url": config.action_url,
        "enabled": config.enabled,
        "version": config.version,
        "updated_by": config.updated_by,
    }


def _conflict(current_version: int | None) -> APIException:
    return APIException(
        error_code=ErrorCode.RESOURCE_CONFLICT,
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "message": "Announcement was modified by another request",
            "current_version": current_version,
        },
    )


def update_announcement_config(
    session: Session,
    body: AnnouncementUpdateRequest,
    admin: User,
    request: Request,
) -> AnnouncementConfig:
    existing = get_announcement_config(session)
    if existing is None:
        if body.expected_version != 0 or body.expected_updated_at is not None:
            raise _conflict(0)
        version = int(
            any((body.title, body.description, body.action_label, body.action_url))
        )
        config = AnnouncementConfig(
            title=body.title,
            description=body.description,
            action_label=body.action_label,
            action_url=body.action_url,
            enabled=body.enabled,
            version=version,
            updated_by=admin.id,
        )
        try:
            session.add(config)
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                current = get_announcement_config(session)
                raise _conflict(current.version if current else None) from None
            admin_audit_service.log_action(
                session,
                admin.id,
                "create_announcement",
                "announcement",
                config.id,
                new_value=_audit_value(config),
                request=request,
                commit=False,
            )
            session.commit()
            session.refresh(config)
            return config
        except IntegrityError as exc:
            session.rollback()
            # The global IntegrityError handler means 409. Audit/commit failures
            # are not create collisions: retain the cause and return a true 500.
            raise RuntimeError("Announcement transaction failed") from exc
        except Exception:
            session.rollback()
            raise

    if body.expected_updated_at is None:
        raise _conflict(existing.version)
    expected_updated_at = normalize_datetime_to_utc(body.expected_updated_at)
    if (
        existing.version != body.expected_version
        or normalize_datetime_to_utc(existing.updated_at) != expected_updated_at
    ):
        raise _conflict(existing.version)

    # SQLite stores this DateTime column as naive UTC while PostgreSQL may return
    # an aware value. Keep the client's exact instant, adapting only its timezone
    # representation to the value type used by the active database driver.
    expected_updated_at_for_db = (
        expected_updated_at.replace(tzinfo=None)
        if existing.updated_at.tzinfo is None
        else expected_updated_at
    )

    old_value = _audit_value(existing)
    content_changed = any(
        (
            existing.title != body.title,
            existing.description != body.description,
            existing.action_label != body.action_label,
            existing.action_url != body.action_url,
        )
    )
    next_version = existing.version + 1 if content_changed else existing.version
    changed_at = advance_timestamp(existing.updated_at, now=utcnow())

    try:
        result = session.exec(
            update(AnnouncementConfig)
            .where(AnnouncementConfig.id == ANNOUNCEMENT_CONFIG_ID)
            .where(AnnouncementConfig.version == body.expected_version)
            .where(AnnouncementConfig.updated_at == expected_updated_at_for_db)
            .values(
                title=body.title,
                description=body.description,
                action_label=body.action_label,
                action_url=body.action_url,
                enabled=body.enabled,
                version=next_version,
                updated_at=changed_at,
                updated_by=admin.id,
            )
        )
        if result.rowcount != 1:
            session.rollback()
            current = get_announcement_config(session)
            raise _conflict(current.version if current else None)

        session.expire_all()
        updated = get_announcement_config(session)
        if updated is None:  # pragma: no cover - protected by the rowcount check
            raise _conflict(None)
        admin_audit_service.log_action(
            session,
            admin.id,
            "update_announcement",
            "announcement",
            updated.id,
            old_value=old_value,
            new_value=_audit_value(updated),
            request=request,
            commit=False,
        )
        session.commit()
        session.refresh(updated)
        return updated
    except APIException:
        session.rollback()
        raise
    except IntegrityError as exc:
        session.rollback()
        raise RuntimeError("Announcement transaction failed") from exc
    except Exception:
        session.rollback()
        raise
