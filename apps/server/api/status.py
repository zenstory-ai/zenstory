"""Anonymous, aggregate service observations with no user or provider details."""

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from database import get_session
from services.infra.service_status import CACHE_SECONDS, ServiceStatus, StatusCache
from utils.logger import get_logger

router = APIRouter(prefix="/api/status", tags=["Status"])
logger = get_logger(__name__)
_status_cache = StatusCache()


@router.get("", response_model=ServiceStatus)
def get_status(response: Response, session: Session = Depends(get_session)):
    try:
        snapshot = _status_cache.get(session)
    except SQLAlchemyError:
        logger.warning("Public status observations are temporarily unavailable")
        return JSONResponse(
            status_code=503,
            content={"detail": "Status data is temporarily unavailable"},
            headers={"Cache-Control": "no-store"},
        )
    response.headers["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"
    return snapshot
