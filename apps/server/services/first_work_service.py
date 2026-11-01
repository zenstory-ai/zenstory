"""One transactional, model-free starting workspace for newly registered authors."""
from __future__ import annotations

import contextlib
import hashlib
import json
from datetime import datetime

from pydantic import BaseModel
from sqlmodel import Session, col, select, update

from config.datetime_utils import utcnow
from config.feature_flags import get_first_work_rollout_percent, is_first_work_onboarding_enabled
from config.project_templates import get_default_project_name
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import File, Project, User, UserPersonaProfile, WorkspaceOnboarding
from services.quota_service import quota_service
from utils.logger import get_logger

logger = get_logger(__name__)

VALID_TYPES = {"novel", "short", "screenplay"}
PERSONA_TYPES = {"short_story": "short", "screenwriter": "screenplay"}


class FirstWorkState(BaseModel):
    status: str
    project_id: str | None = None
    # Lets the dashboard match the selected type before its project list has refreshed.
    project_type: str | None = None
    file_id: str | None = None
    available: bool = False
    static_guidance: bool = False
    opened_at: datetime | None = None
    first_manual_write_at: datetime | None = None
    first_ai_requested_at: datetime | None = None


def should_enroll_new_user(user_id: str) -> bool:
    percent = get_first_work_rollout_percent()
    bucket = int.from_bytes(hashlib.sha256(f"first-work-v1:{user_id}".encode()).digest()[:8], "big") % 100
    return bucket < percent


def enroll_new_user(session: Session, user: User) -> None:
    """Stage qualification in the same transaction as a genuinely new account."""
    if not should_enroll_new_user(user.id):
        return
    session.flush()  # The new user must exist before its FK row; still one transaction.
    session.add(WorkspaceOnboarding(user_id=user.id))


def _state(session: Session, record: WorkspaceOnboarding | None) -> FirstWorkState:
    if record is None:
        return FirstWorkState(status="ineligible")
    project = session.get(Project, record.project_id) if record.project_id else None
    file = session.get(File, record.file_id) if record.file_id else None
    project_available = bool(project and project.owner_id == record.user_id and not project.is_deleted)
    available = bool(project_available and file and file.project_id == record.project_id and not file.is_deleted)
    return FirstWorkState(
        status=record.status, project_id=record.project_id,
        project_type=project.project_type if project_available else None, file_id=record.file_id,
        available=available, static_guidance=project_available and record.first_ai_requested_at is None,
        opened_at=record.opened_at, first_manual_write_at=record.first_manual_write_at,
        first_ai_requested_at=record.first_ai_requested_at,
    )


def get_first_work_state(session: Session, user_id: str) -> FirstWorkState:
    record = session.get(WorkspaceOnboarding, user_id)
    if record is not None and record.status == "pending" and not is_first_work_onboarding_enabled():
        return FirstWorkState(status="disabled")
    return _state(session, record)


def consume_for_author_project(session: Session, user_id: str | None) -> None:
    """Called inside the common owner creation gate, before the author's commit."""
    if user_id:
        session.exec(update(WorkspaceOnboarding).where(
            col(WorkspaceOnboarding.user_id) == user_id,
            col(WorkspaceOnboarding.status) == "pending",
        ).values(status="skipped_existing", initialized_at=utcnow()))


def initialize_first_work(
    session: Session, user_id: str, *, preferred_type: str | None = None, lang: str = "zh",
) -> FirstWorkState:
    from services.project_service import stage_project_with_default_folders

    if not is_first_work_onboarding_enabled():
        return get_first_work_state(session, user_id)

    try:
        # The shared creation gate also serializes manual/Agent/inspiration
        # creation. Its quota helpers use commit=False until our final commit.
        allowed, used, limit = quota_service.check_project_limit(session, user_id, for_creation=True)
        record = session.get(WorkspaceOnboarding, user_id, populate_existing=True)
        if record is None or record.status != "pending":
            result = _state(session, record)
            session.commit()
            return result
        user = session.get(User, user_id, populate_existing=True)
        if not user.email_verified:
            session.commit()
            return FirstWorkState(status="waiting_verification")
        profile = session.exec(select(UserPersonaProfile).where(UserPersonaProfile.user_id == user_id)).first()
        if profile is None:
            session.commit()
            return FirstWorkState(status="waiting_onboarding")
        # Include deleted rows, and persist the result even if those rows later
        # disappear. Zero active projects is never treated as new eligibility.
        if session.exec(select(Project.id).where(Project.owner_id == user_id)).first():
            consume_for_author_project(session, user_id)
            session.commit()
            return get_first_work_state(session, user_id)
        if not allowed:
            raise APIException(error_code=ErrorCode.QUOTA_PROJECTS_EXCEEDED, status_code=402,
                               detail=f"Project limit reached ({used}/{limit}). Please upgrade your plan.")
        project_type = preferred_type if preferred_type in VALID_TYPES else "novel"
        if preferred_type not in VALID_TYPES:
            try:
                personas = json.loads(profile.selected_personas)
            except (ValueError, TypeError):
                personas = []
            if isinstance(personas, list):
                project_type = next((PERSONA_TYPES[p] for p in personas if isinstance(p, str) and p in PERSONA_TYPES), "novel")
        english = lang == "en"
        # The same default name as a work of this type created from the dashboard.
        project = Project(name=get_default_project_name(project_type, lang),
                          owner_id=user_id, project_type=project_type)
        stage_project_with_default_folders(session, project, lang)
        script = project_type == "screenplay"
        file = File(project_id=project.id,
                    parent_id=f"{project.id}-{'script' if script else 'draft'}-folder",
                    title=("Script" if script else "Draft") if english else ("剧本" if script else "正文"),
                    file_type="script" if script else "draft", content="")
        file.set_metadata({"creation_source": "first_work"})
        session.add(file)
        session.flush()
        record.status = "created"
        record.initialized_at = utcnow()
        record.project_id, record.file_id = project.id, file.id
        session.add(record)
        session.commit()
        return get_first_work_state(session, user_id)
    except Exception:
        session.rollback()
        raise


def record_first_work_action(session: Session, user_id: str, project_id: str, action: str) -> None:
    """Persist real actions without a prompt, manuscript, synthetic chat or event."""
    fields = {"open": "opened_at", "manual_write": "first_manual_write_at", "ai_request": "first_ai_requested_at"}
    field = fields[action]
    column = getattr(WorkspaceOnboarding, field)
    session.exec(update(WorkspaceOnboarding).where(
        col(WorkspaceOnboarding.user_id) == user_id,
        col(WorkspaceOnboarding.project_id) == project_id,
        column.is_(None),
    ).values(**{field: utcnow()}))


def observe_first_work_ai_request(session: Session, user_id: str, project_id: str) -> None:
    """A best-effort observation must never interrupt a quota-accepted task."""
    try:
        record_first_work_action(session, user_id, project_id, "ai_request")
        session.commit()
    except Exception:
        with contextlib.suppress(Exception):
            session.rollback()
        logger.warning("Could not record first-work AI request", exc_info=True)


def open_first_work(session: Session, user_id: str) -> FirstWorkState:
    """Observe opening the durable original project, independently of its file."""
    state = get_first_work_state(session, user_id)
    project = session.get(Project, state.project_id) if state.project_id else None
    if project is not None and project.owner_id == user_id and not project.is_deleted:
        record_first_work_action(session, user_id, project.id, "open")
        session.commit()
    return get_first_work_state(session, user_id)
