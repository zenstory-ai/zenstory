"""
Material import API endpoints.

Handles importing material library entities into project files:
- Import single material entity
- Batch import multiple materials
"""
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from services.auth import get_current_active_user
from sqlmodel import Session, select

from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import User
from services.features.file_version_service import get_file_version_service
from services.file_tree_rules import lock_project_for_files
from utils.logger import get_logger

from .helpers import _get_novel_or_404
from .preview import get_material_preview
from .schemas import (
    BatchImportRequest,
    BatchImportResponse,
    BatchImportResult,
    MaterialImportRequest,
    MaterialImportResponse,
)

logger = get_logger(__name__)

# Router without prefix/tags - will be set by parent router
router = APIRouter()


def _resolve_folder_title_candidates(project_type: str, file_type: str) -> list[str]:
    """Resolve possible folder titles for a target file type across zh/en templates."""
    from config.project_templates import get_file_type_mapping

    candidates: list[str] = []
    for lang in ("zh", "en"):
        mapping = get_file_type_mapping(project_type, lang)
        candidates.extend(
            folder_title
            for folder_title, mapped_file_type in mapping.items()
            if mapped_file_type == file_type
        )
    # Preserve order and remove duplicates
    return list(dict.fromkeys(candidates))


def _validate_target_folder_or_raise(
    session: Session,
    project_id: str,
    folder_id: str,
) -> str:
    """Validate that target folder exists in project and return its title."""
    from models.file_model import File as ProjectFile

    target_folder = session.get(ProjectFile, folder_id, populate_existing=True)
    if not target_folder or target_folder.is_deleted:
        raise APIException(error_code=ErrorCode.FILE_NOT_FOUND, status_code=404)
    if target_folder.project_id != project_id:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400,
            detail="target_folder_id does not belong to this project",
        )
    if target_folder.file_type != "folder":
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400,
            detail="target_folder_id must be a folder",
        )
    return target_folder.title


# ==================== Import Endpoints ====================

@router.post("/import", response_model=MaterialImportResponse)
def import_material(
    request: MaterialImportRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
    accept_language: Annotated[str | None, Header(alias="Accept-Language")] = None,
):
    """
    Import material entity as a project file.

    Creates a new file in the project with the material content.
    """
    from models import Project
    from models.file_model import File as ProjectFile
    from models.utils import generate_uuid

    # Verify novel ownership
    _get_novel_or_404(session, request.novel_id, current_user.id)

    # Verify project ownership
    project = session.get(Project, request.project_id)
    # Admin/superuser can import into any project for support/debugging.
    if (
        not project
        or project.is_deleted
        or (project.owner_id != current_user.id and not current_user.is_superuser)
    ):
        raise APIException(error_code=ErrorCode.NOT_AUTHORIZED, status_code=403)

    project = lock_project_for_files(session, request.project_id)
    if (
        not project
        or project.is_deleted
        or (project.owner_id != current_user.id and not current_user.is_superuser)
    ):
        raise APIException(error_code=ErrorCode.NOT_AUTHORIZED, status_code=403)

    # Get preview data (reuse preview logic)
    preview = get_material_preview(
        novel_id=request.novel_id,
        entity_type=request.entity_type,
        entity_id=request.entity_id,
        accept_language=accept_language,
        current_user=current_user,
        session=session,
    )

    # Use provided file_name or suggested
    file_name = request.file_name or preview.suggested_file_name

    # Find or create target folder (use flush instead of commit to keep in same transaction)
    folder_id = request.target_folder_id
    folder_name = preview.suggested_folder_name
    if folder_id:
        folder_name = _validate_target_folder_or_raise(session, request.project_id, folder_id)
    else:
        folder_title_candidates = [
            preview.suggested_folder_name,
            *_resolve_folder_title_candidates(project.project_type, preview.suggested_file_type),
        ]
        folder_title_candidates = list(dict.fromkeys(folder_title_candidates))
        # Try to find existing folder with matching name
        existing_folder = session.exec(
            select(ProjectFile)
            .where(ProjectFile.project_id == request.project_id)
            .where(ProjectFile.file_type == "folder")
            .where(ProjectFile.is_deleted.is_(False))
            .where(ProjectFile.title.in_(folder_title_candidates))
            .execution_options(populate_existing=True)
        ).first()

        if existing_folder:
            folder_id = existing_folder.id
            folder_name = existing_folder.title
        else:
            # Create new folder (flush but don't commit yet)
            new_folder = ProjectFile(
                id=generate_uuid(),
                project_id=request.project_id,
                title=preview.suggested_folder_name,
                file_type="folder",
                content="",
                parent_id=None,
            )
            session.add(new_folder)
            session.flush()  # Flush to get ID without committing
            folder_id = new_folder.id
            folder_name = new_folder.title

    # Create the file with material content
    file_metadata = {
        "source": "material_library",
        "novel_id": request.novel_id,
        "entity_type": request.entity_type,
        "entity_id": request.entity_id,
    }

    new_file = ProjectFile(
        id=generate_uuid(),
        project_id=request.project_id,
        title=file_name,
        file_type=preview.suggested_file_type,
        content=preview.markdown,
        parent_id=folder_id,
        file_metadata=json.dumps(file_metadata),
    )
    session.add(new_file)
    get_file_version_service().create_initial_version(session, new_file)

    # Commit folder, populated file and its initial history in one transaction.
    session.commit()
    session.refresh(new_file)

    logger.info(f"Material imported: file_id={new_file.id}, novel_id={request.novel_id}, entity_type={request.entity_type}")

    return MaterialImportResponse(
        file_id=new_file.id,
        title=new_file.title,
        folder_name=folder_name,
        file_type=new_file.file_type,
    )


@router.post("/batch-import", response_model=BatchImportResponse)
def batch_import_materials(
    request: BatchImportRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
    accept_language: Annotated[str | None, Header(alias="Accept-Language")] = None,
):
    """Batch import multiple materials to a project."""
    from models import Project

    # Verify project ownership
    project = session.get(Project, request.project_id)
    if (
        not project
        or project.is_deleted
        or (project.owner_id != current_user.id and not current_user.is_superuser)
    ):
        raise APIException(error_code=ErrorCode.NOT_AUTHORIZED, status_code=403)

    results = []
    failed_count = 0

    for item in request.items:
        try:
            # Reuse the single import logic
            single_request = MaterialImportRequest(
                project_id=request.project_id,
                novel_id=item.novel_id,
                entity_type=item.entity_type,
                entity_id=item.entity_id,
            )
            result = import_material(
                single_request,
                current_user,
                session,
                accept_language=accept_language,
            )
            results.append(BatchImportResult(
                file_id=result.file_id,
                title=result.title,
                folder_name=result.folder_name,
                file_type=result.file_type,
            ))
        except Exception as e:
            session.rollback()
            failed_count += 1
            logger.error(f"Failed to import {item.entity_type}/{item.entity_id}: {e}")

    return BatchImportResponse(results=results, failed_count=failed_count)


__all__ = ["router"]
