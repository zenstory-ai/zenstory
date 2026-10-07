"""
Inspiration service layer.

Provides functions for managing the inspiration library:
- Create inspirations from projects
- Copy inspirations to user workspaces
- List and filter inspirations
- Get featured inspirations
"""

import json
from typing import cast

from sqlalchemy.orm import InstrumentedAttribute, defer
from sqlmodel import Session, col, func, or_, select

from config.datetime_utils import utcnow
from models.entities import Project, User
from models.file_model import File
from models.inspiration import Inspiration
from services.features.file_version_service import get_file_version_service
from utils.logger import get_logger

logger = get_logger(__name__)


def create_inspiration_from_project(
    session: Session,
    project: Project,
    files: list[File],
    source: str = "community",
    author: User | None = None,
    name: str | None = None,
    description: str | None = None,
    cover_image: str | None = None,
    tags: list[str] | None = None,
    is_featured: bool = False,
) -> Inspiration:
    """
    Create an Inspiration from a project's files.

    Serializes all files into snapshot_data as JSON, creating a template
    that can be copied to other users' workspaces.

    Args:
        session: Database session
        project: Source project to create inspiration from
        files: List of files to include in the inspiration
        source: 'official' or 'community'
        author: User who created the inspiration (for community inspirations)
        name: Display name (defaults to project name)
        description: Description text
        cover_image: URL to cover image
        tags: List of tags for filtering
        is_featured: Whether this is a featured inspiration

    Returns:
        Inspiration instance (not yet saved to database)
    """
    _ = session  # kept for signature compatibility

    # Serialize files into snapshot format
    snapshot_data = {
        "project_name": project.name,
        "project_description": project.description,
        "project_type": project.project_type,
        "project_summary": project.summary,
        "project_current_phase": project.current_phase,
        "project_writing_style": project.writing_style,
        "project_notes": project.notes,
        "files": [],
    }

    for file in files:
        file_data = {
            "id": file.id,
            "title": file.title,
            "content": file.content,
            "file_type": file.file_type,
            "parent_id": file.parent_id,
            "order": file.order,
            "file_metadata": file.file_metadata,
        }
        snapshot_data["files"].append(file_data)

    # Create inspiration instance
    inspiration = Inspiration(
        name=name or project.name,
        description=description or project.description,
        cover_image=cover_image,
        project_type=project.project_type,
        tags=json.dumps(tags or [], ensure_ascii=False),
        snapshot_data=json.dumps(snapshot_data, ensure_ascii=False),
        source=source,
        author_id=author.id if author else None,
        original_project_id=project.id,
        status="approved" if source == "official" else "pending",
        is_featured=is_featured,
    )

    logger.info(f"Created inspiration '{inspiration.name}' from project {project.id}")
    return inspiration


def copy_inspiration_to_project(
    session: Session,
    inspiration: Inspiration,
    user: User,
    project_name: str | None = None,
    commit: bool = True,
) -> Project:
    """
    Copy an inspiration to user's workspace.

    Creates a new Project for the user and recreates all files from
    the inspiration's snapshot_data. Also increments the inspiration's
    copy_count.

    Args:
        session: Database session
        inspiration: Inspiration to copy
        user: User who is copying the inspiration
        project_name: Optional custom project name (defaults to inspiration name)
        commit: Whether to commit before returning.
            Set to False when caller needs to bundle quota consumption and
            copy creation in the same transaction.

    Returns:
        Newly created Project with all files

    Raises:
        ValueError: If inspiration snapshot_data is invalid
    """
    # Parse snapshot data
    try:
        snapshot = json.loads(inspiration.snapshot_data)
    except (json.JSONDecodeError, TypeError) as e:
        logger.error(f"Invalid snapshot_data in inspiration {inspiration.id}: {e}")
        raise ValueError(f"Invalid inspiration data: {e}") from e

    # Create new project
    project = Project(
        name=project_name or inspiration.name,
        description=snapshot.get("project_description") or inspiration.description,
        owner_id=user.id,
        project_type=inspiration.project_type,
        summary=snapshot.get("project_summary"),
        current_phase=snapshot.get("project_current_phase"),
        writing_style=snapshot.get("project_writing_style"),
        notes=snapshot.get("project_notes"),
    )
    session.add(project)
    session.flush()  # Get project ID

    # Create file ID mapping for parent relationships
    old_to_new_id: dict[str, str] = {}

    # Create all files
    files_data = snapshot.get("files", [])
    for file_data in files_data:
        new_file = File(
            project_id=project.id,
            title=file_data.get("title", "Untitled"),
            content=file_data.get("content", ""),
            file_type=file_data.get("file_type", "document"),
            parent_id=None,  # Will update after all files created
            order=file_data.get("order", 0),
            file_metadata=file_data.get("file_metadata"),
        )
        session.add(new_file)
        session.flush()  # Get new file ID
        get_file_version_service().create_initial_version(session, new_file)

        # Map old ID to new ID
        old_id = file_data.get("id")
        if old_id:
            old_to_new_id[old_id] = new_file.id

    # Update parent references with new IDs
    for file_data in files_data:
        old_id = file_data.get("id")
        old_parent_id = file_data.get("parent_id")

        if old_id and old_parent_id:
            new_id = old_to_new_id.get(old_id)
            new_parent_id = old_to_new_id.get(old_parent_id)

            if new_id and new_parent_id:
                # Update the file's parent_id
                stmt = (
                    select(File)
                    .where(File.id == new_id)
                )
                file = session.exec(stmt).first()
                if file:
                    file.parent_id = new_parent_id

    # Increment copy count
    inspiration.copy_count += 1
    inspiration.updated_at = utcnow()

    if commit:
        session.commit()
        session.refresh(project)
    else:
        session.flush()
        session.refresh(project)

    logger.info(
        f"User {user.id} copied inspiration {inspiration.id} to project {project.id}"
    )
    return project


def list_inspirations(
    session: Session,
    project_type: str | None = None,
    search: str | None = None,
    tags: list[str] | None = None,
    page: int = 1,
    page_size: int = 12,
    featured_only: bool = False,
    *,
    metadata_only: bool = False,
) -> tuple[list[Inspiration], int]:
    """
    List approved inspirations with filtering and pagination.

    Args:
        session: Database session
        project_type: Filter by project type (novel/short/screenplay)
        search: Search term for name/description
        tags: Filter by tags (inspiration must have ALL specified tags)
        page: Page number (1-indexed)
        page_size: Number of results per page
        featured_only: Only return featured inspirations
        metadata_only: Defer template bodies for metadata-only API responses

    Returns:
        Tuple of (list of inspirations, total count)
    """
    # Base query - only approved inspirations
    stmt = select(Inspiration).where(Inspiration.status == "approved")
    if metadata_only:
        stmt = stmt.options(defer(cast(InstrumentedAttribute[str], Inspiration.snapshot_data)))

    # Filter by project type
    if project_type:
        stmt = stmt.where(Inspiration.project_type == project_type)

    # Filter by featured
    if featured_only:
        stmt = stmt.where(Inspiration.is_featured.is_(True))

    # Search in name and description
    if search:
        search_pattern = f"%{search}%"
        stmt = stmt.where(
            or_(
                Inspiration.name.ilike(search_pattern),
                Inspiration.description.ilike(search_pattern),
            )
        )

    # Filter by tags (JSON contains check)
    # Note: This is a simple string match, not proper JSON query
    # For production, consider using proper JSON query operators
    if tags:
        for tag in tags:
            stmt = stmt.where(Inspiration.tags.contains(f'"{tag}"'))

    # Get total count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_count = session.exec(count_stmt).one() or 0

    # Order by featured first, then by copy_count, then by created_at
    stmt = stmt.order_by(
        Inspiration.is_featured.desc(),
        Inspiration.copy_count.desc(),
        Inspiration.created_at.desc(),
        col(Inspiration.id).desc(),
    )

    # Paginate
    offset = (page - 1) * page_size
    stmt = stmt.offset(offset).limit(page_size)

    inspirations = session.exec(stmt).all()

    return list(inspirations), total_count


def get_inspiration_detail(
    session: Session,
    inspiration_id: str,
) -> Inspiration | None:
    """
    Get single inspiration by ID.

    Only returns approved inspirations.

    Args:
        session: Database session
        inspiration_id: Inspiration ID

    Returns:
        Inspiration if found and approved, None otherwise
    """
    stmt = select(Inspiration).where(
        Inspiration.id == inspiration_id,
        Inspiration.status == "approved",
    )
    return session.exec(stmt).first()


def get_featured_inspirations(
    session: Session,
    limit: int = 6,
    *,
    metadata_only: bool = False,
) -> list[Inspiration]:
    """
    Get featured inspirations for homepage.

    Args:
        session: Database session
        limit: Maximum number of inspirations to return
        metadata_only: Defer template bodies for metadata-only API responses

    Returns:
        List of featured inspirations ordered by sort_order
    """
    stmt = (
        select(Inspiration)
        .where(
            Inspiration.status == "approved",
            Inspiration.is_featured.is_(True),
        )
        .order_by(Inspiration.sort_order, Inspiration.copy_count.desc(), col(Inspiration.id).desc())
        .limit(limit)
    )
    if metadata_only:
        stmt = stmt.options(defer(cast(InstrumentedAttribute[str], Inspiration.snapshot_data)))
    return list(session.exec(stmt).all())


def increment_copy_count(
    session: Session,
    inspiration_id: str,
) -> None:
    """
    Increment the copy count for an inspiration.

    Args:
        session: Database session
        inspiration_id: Inspiration ID
    """
    stmt = select(Inspiration).where(Inspiration.id == inspiration_id)
    inspiration = session.exec(stmt).first()

    if inspiration:
        inspiration.copy_count += 1
        inspiration.updated_at = utcnow()
        session.commit()
        logger.debug(f"Incremented copy count for inspiration {inspiration_id}")


def upsert_official_inspiration(
    session: Session,
    source_id: str,
    name: str,
    description: str,
    project_type: str,
    tags: list[str],
    snapshot_data: str,
    cover_image: str | None = None,
    is_featured: bool = False,
    sort_order: int = 0,
) -> Inspiration:
    """
    Idempotently upsert an official inspiration by source_id.

    Used by sync scripts to import inspirations from external sources
    (e.g. MongoDB). If an inspiration with the same source_id exists,
    its mutable fields are updated. Otherwise a new row is created.

    Args:
        session: Database session
        source_id: Unique external ID for dedup (e.g. 'qimao:1979486')
        name: Display name
        description: Brief description (caller must truncate to <=950 chars)
        project_type: novel/short/screenplay
        tags: List of tags (serialized to JSON internally)
        snapshot_data: JSON string with file tree snapshot
        cover_image: Optional cover image URL
        is_featured: Whether featured
        sort_order: Display sort order

    Returns:
        Upserted Inspiration instance
    """
    existing = session.exec(
        select(Inspiration).where(Inspiration.source_id == source_id)
    ).first()

    tags_str = json.dumps(tags or [], ensure_ascii=False)
    now = utcnow()

    if existing:
        existing.name = name
        existing.description = description
        existing.project_type = project_type
        existing.tags = tags_str
        existing.snapshot_data = snapshot_data
        existing.is_featured = is_featured
        existing.sort_order = sort_order
        existing.cover_image = cover_image
        existing.updated_at = now
        session.add(existing)
        session.commit()
        session.refresh(existing)
        logger.info(f"Updated official inspiration source_id={source_id}")
        return existing

    inspiration = Inspiration(
        source_id=source_id,
        name=name,
        description=description,
        project_type=project_type,
        tags=tags_str,
        snapshot_data=snapshot_data,
        source="official",
        status="approved",
        is_featured=is_featured,
        sort_order=sort_order,
        cover_image=cover_image,
        created_at=now,
        updated_at=now,
    )
    session.add(inspiration)
    session.commit()
    session.refresh(inspiration)
    logger.info(f"Created official inspiration source_id={source_id}")
    return inspiration


def create_inspiration_with_review(
    session: Session,
    project: Project,
    files: list[File],
    author: User,
    name: str | None = None,
    description: str | None = None,
    cover_image: str | None = None,
    tags: list[str] | None = None,
) -> Inspiration:
    """
    Create a community inspiration that requires admin review.

    This is a convenience wrapper that sets source='community' and status='pending'.

    Args:
        session: Database session
        project: Source project
        files: Files to include
        author: User creating the inspiration
        name: Display name
        description: Description text
        cover_image: Cover image URL
        tags: List of tags

    Returns:
        Created Inspiration with pending status
    """
    inspiration = create_inspiration_from_project(
        session=session,
        project=project,
        files=files,
        source="community",
        author=author,
        name=name,
        description=description,
        cover_image=cover_image,
        tags=tags,
        is_featured=False,
    )

    session.add(inspiration)
    session.commit()
    session.refresh(inspiration)

    logger.info(
        f"User {author.id} created community inspiration '{inspiration.name}' (pending review)"
    )
    return inspiration


def review_inspiration(
    session: Session,
    inspiration: Inspiration,
    reviewer: User,
    approve: bool,
    rejection_reason: str | None = None,
    *,
    commit: bool = True,
) -> Inspiration:
    """
    Review a community inspiration.

    Args:
        session: Database session
        inspiration: Inspiration to review
        reviewer: Admin user performing the review
        approve: True to approve, False to reject
        rejection_reason: Required if approve=False
        commit: Commit immediately by default; otherwise stage for the caller transaction

    Returns:
        Updated Inspiration

    """
    cleaned_rejection_reason = (rejection_reason or "").strip()
    if not approve and not cleaned_rejection_reason:
        raise ValueError("Rejection reason is required when rejecting inspiration")

    inspiration.status = "approved" if approve else "rejected"
    inspiration.reviewed_by = reviewer.id
    inspiration.reviewed_at = utcnow()

    if approve:
        inspiration.rejection_reason = None
    else:
        inspiration.rejection_reason = cleaned_rejection_reason

    inspiration.updated_at = utcnow()

    if commit:
        session.commit()
        session.refresh(inspiration)
        status_text = "approved" if approve else "rejected"
        logger.info(f"Admin {reviewer.id} {status_text} inspiration {inspiration.id}")
    else:
        session.flush()

    return inspiration
