"""
Agent API Key management endpoints.

Provides CRUD operations for managing API keys used for programmatic
access to agent endpoints.
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from services.auth import get_current_active_user
from sqlmodel import Session, col, func, select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import User
from models.agent_api_key import DEFAULT_SCOPES, AgentApiKey
from services.agent_auth_service import generate_api_key, hash_api_key
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/agent-api-keys", tags=["agent-api-keys"])


# ==================== Request/Response Schemas ====================

class CreateApiKeyRequest(BaseModel):
    """Request body for creating a new API key."""
    name: str = Field(..., max_length=100, description="Human-readable name for the API key")
    description: str | None = Field(None, max_length=500, description="Optional description")
    scopes: list[str] | None = Field(None, description="Permission scopes (read, write, chat)")
    project_ids: list[str] | None = Field(None, description="Project IDs this key can access (None = all projects)")
    expires_in_days: int | None = Field(None, ge=1, le=3650, description="Expiration in days (None = no expiration)")


class UpdateApiKeyRequest(BaseModel):
    """Request body for updating an API key."""
    name: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=500)
    scopes: list[str] | None = None
    project_ids: list[str] | None = None
    is_active: bool | None = None

    @field_validator("name", "scopes", "is_active", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("This field cannot be null")
        return value


class ApiKeyResponse(BaseModel):
    """Response schema for API key (without sensitive data)."""
    id: str
    name: str
    description: str | None
    key_prefix: str
    scopes: list[str]
    project_ids: list[str] | None
    is_active: bool
    last_used_at: datetime | None
    expires_at: datetime | None
    request_count: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApiKeyWithSecretResponse(BaseModel):
    """Response schema for API key creation (includes secret key)."""
    id: str
    name: str
    description: str | None
    key: str  # Full API key - only shown once during creation
    key_prefix: str
    scopes: list[str]
    project_ids: list[str] | None
    is_active: bool
    expires_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApiKeyListResponse(BaseModel):
    """Response schema for listing API keys."""
    keys: list[ApiKeyResponse]
    total: int


class RegenerateKeyResponse(BaseModel):
    """Response schema for regenerating an API key."""
    key: str


# ==================== Helper Functions ====================

def validate_scopes(scopes: list[str] | None) -> list[str]:
    """
    Validate and return scopes, using defaults if not provided.

    Args:
        scopes: List of scope strings to validate

    Returns:
        Validated list of scopes

    Raises:
        APIException: If any scope is invalid
    """
    valid_scopes = {"read", "write", "chat"}

    if scopes is None:
        return DEFAULT_SCOPES.copy()

    if not scopes:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400,
            detail="At least one permission scope is required",
        )

    for scope in scopes:
        if scope not in valid_scopes:
            raise APIException(
                error_code=ErrorCode.VALIDATION_ERROR,
                status_code=400,
                detail=f"Invalid scope: {scope}. Valid scopes are: {', '.join(valid_scopes)}",
            )

    return scopes


# ==================== Endpoints ====================

@router.post("", response_model=ApiKeyWithSecretResponse, status_code=201)
def create_api_key(
    request: CreateApiKeyRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Create a new Agent API Key.

    - Generates a new API key with the specified permissions
    - Returns the full key only once (store it securely)
    - Keys can be scoped to specific projects or have access to all projects
    """
    log_with_context(
        logger,
        logging.INFO,
        "Creating API key",
        user_id=current_user.id,
        key_name=request.name,
    )

    # Validate scopes
    scopes = validate_scopes(request.scopes)

    # Generate API key
    plain_key = generate_api_key()
    key_hash = hash_api_key(plain_key)
    key_prefix = plain_key[:8]  # "eg_xxxxx"

    # Calculate expiration
    expires_at = None
    if request.expires_in_days:
        expires_at = utcnow() + timedelta(days=request.expires_in_days)

    # Create API key entity
    api_key = AgentApiKey(
        user_id=current_user.id,
        name=request.name,
        description=request.description,
        key_prefix=key_prefix,
        key_hash=key_hash,
        scopes=scopes,
        project_ids=request.project_ids,
        expires_at=expires_at,
    )

    session.add(api_key)
    session.commit()
    session.refresh(api_key)

    log_with_context(
        logger,
        logging.INFO,
        "API key created successfully",
        user_id=current_user.id,
        key_id=api_key.id,
        key_name=api_key.name,
    )

    return ApiKeyWithSecretResponse(
        id=api_key.id,
        name=api_key.name,
        description=api_key.description,
        key=plain_key,
        key_prefix=api_key.key_prefix,
        scopes=api_key.scopes,
        project_ids=api_key.project_ids,
        is_active=api_key.is_active,
        expires_at=api_key.expires_at,
        created_at=api_key.created_at,
    )


@router.get("", response_model=ApiKeyListResponse)
def list_api_keys(
    is_active: bool | None = Query(None, description="Filter by active status"),
    limit: int = Query(50, ge=1, le=100, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    List all API keys for the current user.

    - Returns API keys without the secret key (only prefix is shown)
    - Supports filtering by active status
    - Supports pagination
    """
    # Build query
    statement = select(AgentApiKey).where(AgentApiKey.user_id == current_user.id)

    if is_active is not None:
        statement = statement.where(AgentApiKey.is_active == is_active)

    # Get total count
    count_statement = select(func.count()).select_from(AgentApiKey).where(
        AgentApiKey.user_id == current_user.id
    )
    if is_active is not None:
        count_statement = count_statement.where(AgentApiKey.is_active == is_active)
    total = int(session.exec(count_statement).one())

    # Apply pagination
    statement = statement.order_by(
        col(AgentApiKey.created_at).desc(), col(AgentApiKey.id).desc()
    ).offset(offset).limit(limit)

    keys = session.exec(statement).all()

    return ApiKeyListResponse(
        keys=[ApiKeyResponse.model_validate(key) for key in keys],
        total=total,
    )


@router.get("/{key_id}", response_model=ApiKeyResponse)
def get_api_key(
    key_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Get a specific API key by ID.

    - Returns the API key without the secret key
    - Only the key owner can access it
    """
    api_key = session.get(AgentApiKey, key_id)

    if not api_key:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=404,
            detail="API key not found",
        )

    # Check ownership
    if api_key.user_id != current_user.id:
        raise APIException(
            error_code=ErrorCode.NOT_AUTHORIZED,
            status_code=403,
            detail="You don't have access to this API key",
        )

    return ApiKeyResponse.model_validate(api_key)


@router.put("/{key_id}", response_model=ApiKeyResponse)
def update_api_key(
    key_id: str,
    request: UpdateApiKeyRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Update an API key's metadata.

    - Can update name, description, scopes, project_ids, and active status
    - Cannot update the secret key itself (use regenerate endpoint)
    """
    api_key = session.get(AgentApiKey, key_id)

    if not api_key:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=404,
            detail="API key not found",
        )

    # Check ownership
    if api_key.user_id != current_user.id:
        raise APIException(
            error_code=ErrorCode.NOT_AUTHORIZED,
            status_code=403,
            detail="You don't have access to this API key",
        )

    log_with_context(
        logger,
        logging.INFO,
        "Updating API key",
        user_id=current_user.id,
        key_id=key_id,
    )

    # Update fields
    update_data = request.model_dump(exclude_unset=True)

    # Validate scopes if provided
    if "scopes" in update_data:
        update_data["scopes"] = validate_scopes(update_data["scopes"])

    for key, value in update_data.items():
        setattr(api_key, key, value)

    api_key.updated_at = utcnow()
    session.add(api_key)
    session.commit()
    session.refresh(api_key)

    log_with_context(
        logger,
        logging.INFO,
        "API key updated successfully",
        user_id=current_user.id,
        key_id=key_id,
    )

    return ApiKeyResponse.model_validate(api_key)


@router.delete("/{key_id}")
def delete_api_key(
    key_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Delete (revoke) an API key.

    - Permanently deletes the API key
    - The key will immediately stop working
    - This action cannot be undone
    """
    api_key = session.get(AgentApiKey, key_id)

    if not api_key:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=404,
            detail="API key not found",
        )

    # Check ownership
    if api_key.user_id != current_user.id:
        raise APIException(
            error_code=ErrorCode.NOT_AUTHORIZED,
            status_code=403,
            detail="You don't have access to this API key",
        )

    log_with_context(
        logger,
        logging.INFO,
        "Deleting API key",
        user_id=current_user.id,
        key_id=key_id,
        key_name=api_key.name,
    )

    session.delete(api_key)
    session.commit()

    log_with_context(
        logger,
        logging.INFO,
        "API key deleted successfully",
        user_id=current_user.id,
        key_id=key_id,
    )

    return {"message": "API key deleted successfully"}


@router.post("/{key_id}/regenerate", response_model=RegenerateKeyResponse)
def regenerate_api_key(
    key_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Regenerate an API key's secret.

    - Generates a new secret key while keeping all other settings
    - The old key will immediately stop working
    - Returns the new key (only shown once)
    """
    api_key = session.get(AgentApiKey, key_id)

    if not api_key:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=404,
            detail="API key not found",
        )

    # Check ownership
    if api_key.user_id != current_user.id:
        raise APIException(
            error_code=ErrorCode.NOT_AUTHORIZED,
            status_code=403,
            detail="You don't have access to this API key",
        )

    log_with_context(
        logger,
        logging.INFO,
        "Regenerating API key",
        user_id=current_user.id,
        key_id=key_id,
        key_name=api_key.name,
    )

    # Generate new key
    plain_key = generate_api_key()
    key_hash = hash_api_key(plain_key)
    key_prefix = plain_key[:8]

    # Update key
    api_key.key_hash = key_hash
    api_key.key_prefix = key_prefix
    api_key.updated_at = utcnow()
    api_key.request_count = 0  # Reset request count
    api_key.last_used_at = None  # Reset last used

    session.add(api_key)
    session.commit()

    log_with_context(
        logger,
        logging.INFO,
        "API key regenerated successfully",
        user_id=current_user.id,
        key_id=key_id,
    )

    return RegenerateKeyResponse(key=plain_key)
