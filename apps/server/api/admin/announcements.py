"""Superuser management API for the singleton announcement."""

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session

from api.announcement_schemas import (
    AdminAnnouncementResponse,
    AnnouncementUpdateRequest,
    EmptyAdminAnnouncementResponse,
)
from database import get_session
from models import User
from services.core.auth_service import get_current_superuser
from services.features.announcement_service import (
    get_announcement_config,
    update_announcement_config,
)

router = APIRouter(tags=["admin-announcements"])


@router.get(
    "/announcements/current",
    response_model=AdminAnnouncementResponse | EmptyAdminAnnouncementResponse,
)
def read_current_announcement(
    _current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    return get_announcement_config(session) or EmptyAdminAnnouncementResponse()


@router.put("/announcements/current", response_model=AdminAnnouncementResponse)
def put_current_announcement(
    body: AnnouncementUpdateRequest,
    request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    return update_announcement_config(session, body, current_user, request)
