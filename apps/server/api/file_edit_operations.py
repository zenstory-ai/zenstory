"""Recoverable AI file edit operation endpoint."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from services.auth import get_current_active_user
from sqlmodel import Session

from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import User
from services.file_edit_operations import (
    EditOperationConflictError,
    EditOperationNotFoundError,
    undo_edit_operation,
)

router = APIRouter(prefix="/api/v1", tags=["file-edit-operations"])


class EditOperationUndoRequest(BaseModel):
    expected_updated_at: datetime | None = None
    expected_after_content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    resolved_content: str | None = None
    metadata_resolution: Literal["preserve", "restore"] | None = None


@router.post("/files/{file_id}/edit-operations/{operation_id}/undo")
def undo_file_edit_operation(
    file_id: str,
    operation_id: str,
    body: EditOperationUndoRequest | None = None,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    request = body or EditOperationUndoRequest()
    try:
        return undo_edit_operation(
            session, operation_id=operation_id, file_id=file_id, user_id=current_user.id,
            expected_updated_at=request.expected_updated_at,
            expected_after_content_sha256=request.expected_after_content_sha256,
            resolved_content=request.resolved_content,
            metadata_resolution=request.metadata_resolution,
        )
    except EditOperationNotFoundError:
        session.rollback()
        raise APIException(error_code=ErrorCode.FILE_NOT_FOUND, status_code=404) from None
    except EditOperationConflictError as exc:
        session.rollback()
        raise APIException(
            error_code=ErrorCode.RESOURCE_CONFLICT, status_code=409,
            detail={"reason": exc.reason, **exc.detail},
        ) from None
    except APIException:
        session.rollback()
        raise
    except Exception as exc:
        session.rollback()
        raise APIException(error_code=ErrorCode.INTERNAL_SERVER_ERROR, status_code=500) from exc
