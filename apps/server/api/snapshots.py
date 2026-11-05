"""Snapshot management API endpoints"""
import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from services.auth import get_current_active_user
from services.version import get_version_service
from sqlmodel import Session

from core.error_codes import ErrorCode
from core.error_handler import APIException
from core.project_access import verify_project_ownership
from database import get_session
from models import File, Snapshot, User
from services.file_tree_rules import lock_project_for_files
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["snapshots"])


# Request schemas
class CreateSnapshotRequest(BaseModel):
    """Request body for creating a snapshot"""
    description: str | None = None
    file_id: str | None = None
    snapshot_type: str | None = "manual"


class UpdateSnapshotRequest(BaseModel):
    """Request body for updating a snapshot"""
    description: str | None = None


# ==================== Snapshot CRUD ====================
@router.get("/projects/{project_id}/snapshots", response_model=list[Snapshot])
def get_snapshots(
    project_id: str,
    file_id: str | None = Query(None, description="Filter by file ID"),
    limit: int = Query(50, ge=1, le=100, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """Get all snapshots for a project."""
    # Check project ownership
    verify_project_ownership(project_id, current_user, session)

    version_service = get_version_service()
    return version_service.get_snapshots(
        session=session,
        project_id=project_id,
        file_id=file_id,
        limit=limit,
        offset=offset,
    )


@router.post("/projects/{project_id}/snapshots", response_model=Snapshot)
def create_snapshot(
    project_id: str,
    request: CreateSnapshotRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """Create a new snapshot for a project."""
    log_with_context(
        logger,
        logging.INFO,
        "Creating snapshot",
        user_id=current_user.id,
        project_id=project_id,
        file_id=request.file_id,
        snapshot_type=request.snapshot_type or "manual",
    )

    # Check project ownership
    verify_project_ownership(project_id, current_user, session)

    # Validate file_id belongs to the same project when provided
    # Revalidate only after the gate has refreshed state committed while waiting.
    lock_project_for_files(session, project_id)
    verify_project_ownership(project_id, current_user, session)
    if request.file_id:
        target_file = session.get(File, request.file_id, populate_existing=True)
        if (
            not target_file
            or target_file.is_deleted
            or target_file.project_id != project_id
        ):
            raise APIException(error_code=ErrorCode.FILE_NOT_FOUND, status_code=400)

    version_service = get_version_service()
    snapshot = version_service.create_snapshot(
        session=session,
        project_id=project_id,
        file_id=request.file_id,
        description=request.description,
        snapshot_type=request.snapshot_type or "manual"
    )

    log_with_context(
        logger,
        logging.INFO,
        "Snapshot created successfully",
        snapshot_id=snapshot.id,
        project_id=project_id,
        file_id=request.file_id,
    )

    return snapshot


@router.get("/snapshots/{snapshot_id}", response_model=Snapshot)
def get_snapshot(
    snapshot_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """Get a specific snapshot."""
    version_service = get_version_service()
    snapshot = version_service.get_snapshot(session=session, snapshot_id=snapshot_id)

    if not snapshot:
        raise APIException(error_code=ErrorCode.SNAPSHOT_NOT_FOUND, status_code=404)

    # Check project ownership
    verify_project_ownership(snapshot.project_id, current_user, session)

    return snapshot


@router.put("/snapshots/{snapshot_id}", response_model=Snapshot)
def update_snapshot(
    snapshot_id: str,
    request: UpdateSnapshotRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """Update a snapshot's description."""
    version_service = get_version_service()
    snapshot = version_service.get_snapshot(session=session, snapshot_id=snapshot_id)

    if not snapshot:
        raise APIException(error_code=ErrorCode.SNAPSHOT_NOT_FOUND, status_code=404)

    # Check project ownership
    verify_project_ownership(snapshot.project_id, current_user, session)

    # Update description
    if request.description is not None:
        snapshot = version_service.update_description(
            session=session,
            snapshot_id=snapshot_id,
            description=request.description
        )

    return snapshot


@router.post("/snapshots/{snapshot_id}/rollback")
def rollback_to_snapshot(
    snapshot_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """Rollback to a specific snapshot."""
    log_with_context(
        logger,
        logging.INFO,
        "Rolling back to snapshot",
        user_id=current_user.id,
        snapshot_id=snapshot_id,
    )

    version_service = get_version_service()
    snapshot = version_service.get_snapshot(session=session, snapshot_id=snapshot_id)

    if not snapshot:
        log_with_context(
            logger,
            logging.WARNING,
            "Attempted to rollback to non-existent snapshot",
            user_id=current_user.id,
            snapshot_id=snapshot_id,
        )
        raise APIException(error_code=ErrorCode.SNAPSHOT_NOT_FOUND, status_code=404)

    # Check project ownership
    verify_project_ownership(snapshot.project_id, current_user, session)

    try:
        lock_project_for_files(session, snapshot.project_id, rollback=True)
        verify_project_ownership(snapshot.project_id, current_user, session)
        result = version_service.rollback_to_snapshot(
            session=session,
            snapshot_id=snapshot_id
        )
        # The protected service view includes creators that committed while
        # rollback waited for its gate. Keep this internal field off the API.
        result.pop("affected_file_ids")
        log_with_context(
            logger,
            logging.INFO,
            "Rolled back to snapshot successfully",
            user_id=current_user.id,
            snapshot_id=snapshot_id,
            project_id=snapshot.project_id,
        )
        return result
    except ValueError as e:
        log_with_context(
            logger,
            logging.ERROR,
            "Failed to rollback to snapshot",
            user_id=current_user.id,
            snapshot_id=snapshot_id,
            error=str(e),
        )
        raise APIException(error_code=ErrorCode.SNAPSHOT_DIFF_FAILED, status_code=400) from e


@router.get("/snapshots/{snapshot_id_1}/compare/{snapshot_id_2}")
def compare_snapshots(
    snapshot_id_1: str,
    snapshot_id_2: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """Compare two snapshots."""
    version_service = get_version_service()

    # Get both snapshots directly (avoid service layer duplicate queries)
    snapshot1 = session.get(Snapshot, snapshot_id_1)
    snapshot2 = session.get(Snapshot, snapshot_id_2)

    if not snapshot1 or not snapshot2:
        raise APIException(error_code=ErrorCode.SNAPSHOT_ONE_OR_BOTH_NOT_FOUND, status_code=404)

    # Check both snapshots' permissions
    verify_project_ownership(snapshot1.project_id, current_user, session)
    verify_project_ownership(snapshot2.project_id, current_user, session)

    try:
        # Pass snapshot objects directly to avoid duplicate queries in service layer
        return version_service.compare_snapshots(
            session=session,
            snapshot1=snapshot1,
            snapshot2=snapshot2
        )
    except ValueError as e:
        raise APIException(error_code=ErrorCode.SNAPSHOT_DIFF_FAILED, status_code=400) from e
