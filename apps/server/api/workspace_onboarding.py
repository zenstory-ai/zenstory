"""Model-free first-work initialization; only the normal workspace calls POST."""
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlmodel import Session

from core.i18n import resolve_user_language
from database import get_session
from models import User
from services.core.auth_service import get_current_active_user
from services.first_work_service import (
    FirstWorkState,
    get_first_work_state,
    initialize_first_work,
    open_first_work,
)

router = APIRouter(prefix="/api/v1/workspace/first-work", tags=["workspace"])


class InitializeFirstWorkRequest(BaseModel):
    preferred_type: str | None = None


@router.get("", response_model=FirstWorkState)
def read_first_work(current_user: User = Depends(get_current_active_user), session: Session = Depends(get_session)):
    return get_first_work_state(session, current_user.id)


@router.post("", response_model=FirstWorkState)
def initialize(
    body: InitializeFirstWorkRequest, current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session), accept_language: str | None = Header(None),
):
    return initialize_first_work(session, current_user.id, preferred_type=body.preferred_type,
                                 lang=resolve_user_language(session, current_user.id, accept_language))


@router.post("/open", response_model=FirstWorkState)
def record_open(current_user: User = Depends(get_current_active_user), session: Session = Depends(get_session)):
    return open_first_work(session, current_user.id)
