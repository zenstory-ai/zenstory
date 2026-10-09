"""
Materials API package.

This package provides a modular structure for the materials API endpoints,
organized by functionality:
- upload.py: File upload and retry endpoints
- library.py: List, detail, delete, tree, status, summary endpoints
- entities.py: Entity retrieval endpoints (characters, relationships, etc.)
- search.py: Search and library-summary endpoints
- preview.py: Material preview endpoint
- import_.py: Import and batch-import endpoints
- schemas.py: Pydantic request/response models
- constants.py: File upload constraints
- helpers.py: Shared helper functions
"""
from fastapi import APIRouter, Depends
from sqlmodel import Session

from database import get_session
from models import User

from .access import require_materials_library_read
from .constants import ALLOWED_EXTENSIONS, MAX_FILE_SIZE
from .entities import router as entities_router
from .import_ import router as import_router
from .library import get_materials as _get_materials
from .library import router as library_router
from .preview import router as preview_router
from .schemas import (
    BatchImportItem,
    BatchImportRequest,
    BatchImportResponse,
    BatchImportResult,
    ChapterDetailResponse,
    CharacterListItem,
    CharacterRelationshipItem,
    GoldenFingerListItem,
    JobStatusResponse,
    LibrarySummaryItem,
    MaterialDetailResponse,
    MaterialEntityType,
    MaterialImportRequest,
    MaterialImportResponse,
    MaterialListItem,
    MaterialPreviewResponse,
    MaterialSearchResult,
    MaterialUploadResponse,
    PlotListItem,
    StoryLineListItem,
    WorldViewResponse,
)
from .search import router as search_router
from .upload import router as upload_router

# Create unified router with shared prefix
router = APIRouter(prefix="/api/v1/materials", tags=["materials"])


# Root route alias for GET /api/v1/materials (maps to /list)
@router.get("", response_model=list[MaterialListItem])
@router.get("/", response_model=list[MaterialListItem])
async def get_materials_root(
    current_user: User = Depends(require_materials_library_read),
    session: Session = Depends(get_session),
):
    """Get user's material library list (root path alias for /list)."""
    return _get_materials(current_user=current_user, session=session)


# Include routers in order: static routes first, then dynamic routes
# This ensures /search, /library-summary match before /{novel_id}
router.include_router(
    search_router,
    dependencies=[Depends(require_materials_library_read)],
)  # /search, /library-summary
router.include_router(upload_router)  # /upload
router.include_router(
    preview_router,
    dependencies=[Depends(require_materials_library_read)],
)  # /preview/{novel_id}
router.include_router(
    import_router,
    dependencies=[Depends(require_materials_library_read)],
)  # /import, /batch-import
router.include_router(
    entities_router,
    dependencies=[Depends(require_materials_library_read)],
)  # /{novel_id}/characters, etc.
router.include_router(
    library_router,
    dependencies=[Depends(require_materials_library_read)],
)  # /list, /{novel_id}, etc.

__all__ = [
    # Main router
    "router",
    # Constants
    "ALLOWED_EXTENSIONS",
    "MAX_FILE_SIZE",
    # Schemas
    "BatchImportItem",
    "BatchImportRequest",
    "BatchImportResponse",
    "BatchImportResult",
    "CharacterListItem",
    "CharacterRelationshipItem",
    "ChapterDetailResponse",
    "GoldenFingerListItem",
    "JobStatusResponse",
    "LibrarySummaryItem",
    "MaterialDetailResponse",
    "MaterialEntityType",
    "MaterialImportRequest",
    "MaterialImportResponse",
    "MaterialListItem",
    "MaterialPreviewResponse",
    "MaterialSearchResult",
    "MaterialUploadResponse",
    "PlotListItem",
    "StoryLineListItem",
    "WorldViewResponse",
]
