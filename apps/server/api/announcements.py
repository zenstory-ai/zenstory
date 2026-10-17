"""Authenticated read API for the current enabled announcement."""

from fastapi import APIRouter, Depends
from sqlmodel import Session

from api.announcement_schemas import AnnouncementResponse
from database import get_session
from models import User
from services.core.auth_service import get_current_active_user
from services.features.announcement_service import get_enabled_announcement

router = APIRouter(prefix="/api/announcements", tags=["announcements"])


@router.get("/current", response_model=AnnouncementResponse | None)
def read_current_announcement(
    _current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    return get_enabled_announcement(session)
