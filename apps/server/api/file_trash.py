"""Recoverable file deletion, confirmation, trash listing, and restoration."""

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from services.auth import get_current_active_user
from sqlmodel import Session

from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import User
from services.file_trash import FileTrashConflictError, FileTrashError, FileTrashService

router = APIRouter(prefix="/api/v1", tags=["files"])


class DeletionPreviewRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)
    recursive: bool = False


class DeleteBatchRequest(DeletionPreviewRequest):
    target_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def _service(session: Session, user: User, project_id: str) -> FileTrashService:
    return FileTrashService(session, user_id=user.id, project_id=project_id)


def _translate_error(exc: FileTrashError) -> APIException:
    return APIException(
        error_code=ErrorCode.RESOURCE_CONFLICT if isinstance(exc, FileTrashConflictError) else ErrorCode.VALIDATION_ERROR,
        status_code=409 if isinstance(exc, FileTrashConflictError) else 400,
        detail=str(exc),
    )


@router.post("/projects/{project_id}/files/deletion-preview")
def preview_file_deletion(
    project_id: str,
    request: DeletionPreviewRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        return _service(session, current_user, project_id).preview(request.ids, recursive=request.recursive)
    except FileTrashError as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/files/delete-batch")
def delete_file_batch(
    project_id: str,
    request: DeleteBatchRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        return _service(session, current_user, project_id).delete(
            request.ids,
            recursive=request.recursive,
            source="agent_confirmed",
            expected_target_hash=request.target_hash,
        )
    except FileTrashError as exc:
        raise _translate_error(exc) from exc


@router.get("/projects/{project_id}/files/trash")
def list_file_trash(
    project_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        return _service(session, current_user, project_id).list_batches(limit=limit, offset=offset)
    except FileTrashError as exc:
        raise _translate_error(exc) from exc


@router.post("/projects/{project_id}/files/trash/{batch_id}/restore")
def restore_file_batch(
    project_id: str,
    batch_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        return _service(session, current_user, project_id).restore(batch_id)
    except FileTrashError as exc:
        raise _translate_error(exc) from exc
