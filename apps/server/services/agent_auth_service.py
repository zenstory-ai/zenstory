"""
Agent authentication service.

Provides API Key generation, validation, and scope verification for
programmatic access to agent endpoints.
"""

import hashlib
import secrets

from fastapi import Depends, status
from fastapi.security import APIKeyHeader
from sqlalchemy import update
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models.agent_api_key import AgentApiKey
from models.entities import User

# API Key configuration
API_KEY_PREFIX = "eg_"
API_KEY_LENGTH = 32  # Random bytes length (will be hex-encoded to 64 chars)

# FastAPI Header dependency for X-Agent-API-Key
api_key_header = APIKeyHeader(name="X-Agent-API-Key", auto_error=False)


def generate_api_key() -> str:
    """
    Generate a new API Key in format: eg_xxxxxxxxxxxx

    Returns:
        API Key string with prefix "eg_" followed by 64 hex characters
    """
    random_bytes = secrets.token_hex(API_KEY_LENGTH)
    return f"{API_KEY_PREFIX}{random_bytes}"


def hash_api_key(key: str) -> str:
    """
    Hash an API Key using SHA256.

    Args:
        key: The plain API Key string

    Returns:
        SHA256 hash of the API Key (hex encoded)
    """
    return hashlib.sha256(key.encode()).hexdigest()


def verify_scope(api_key: AgentApiKey, required_scope: str) -> bool:
    """
    Check if an API Key has the required scope permission.

    Args:
        api_key: The AgentApiKey entity
        required_scope: The scope to verify (e.g., "read", "write", "chat")

    Returns:
        True if the API Key has the required scope, False otherwise
    """
    if not api_key.is_active:
        return False

    if api_key.scopes is None:
        return False

    return required_scope in api_key.scopes


def verify_project_access(api_key: AgentApiKey, project_id: str | None) -> bool:
    """
    Check if an API Key has access to a specific project.

    Args:
        api_key: The AgentApiKey entity
        project_id: The project ID to verify access for

    Returns:
        True if the API Key has access (None project_ids = all projects),
        False otherwise
    """
    if not api_key.is_active:
        return False

    # If project_ids is None, the key has access to all projects
    if api_key.project_ids is None:
        return True

    # If project_id is provided, check if it's in the allowed list
    if project_id is None:
        return True  # No specific project required

    return project_id in api_key.project_ids


def get_agent_user(
    x_agent_api_key: str | None = Depends(api_key_header),
    session: Session = Depends(get_session),
) -> tuple[Session, str, AgentApiKey]:
    """
    Validate Agent API Key and return user context.

    This is a FastAPI dependency that extracts and validates the X-Agent-API-Key
    header, returning the database session, user_id, and API key entity.

    Args:
        x_agent_api_key: The API Key from X-Agent-API-Key header
        session: Database session

    Returns:
        Tuple of (session, user_id, api_key_entity)

    Raises:
        APIException: If API Key is missing, invalid, expired, or inactive
    """
    # Check if API Key is provided
    if not x_agent_api_key:
        raise APIException(
            error_code=ErrorCode.AUTH_UNAUTHORIZED,
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Agent-API-Key header",
        )

    # Validate API Key format
    if not x_agent_api_key.startswith(API_KEY_PREFIX):
        raise APIException(
            error_code=ErrorCode.AUTH_TOKEN_INVALID,
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key format",
        )

    # Hash the provided key to compare with stored hash
    key_hash = hash_api_key(x_agent_api_key)

    # Look up the API Key in database
    statement = select(AgentApiKey).where(AgentApiKey.key_hash == key_hash)
    api_key = session.exec(statement).first()

    if not api_key:
        raise APIException(
            error_code=ErrorCode.AUTH_TOKEN_INVALID,
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key",
        )

    # Check if key is active
    if not api_key.is_active:
        raise APIException(
            error_code=ErrorCode.AUTH_INACTIVE_USER,
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API Key is inactive",
        )

    # Check if key has expired
    # Handle both timezone-aware and naive datetimes for comparison
    if api_key.expires_at:
        expires_at = api_key.expires_at
        now = utcnow()
        # If expires_at is naive, make it aware for comparison
        if expires_at.tzinfo is None:
            from datetime import UTC
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= now:
            raise APIException(
                error_code=ErrorCode.AUTH_TOKEN_EXPIRED,
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API Key has expired",
            )

    owner = session.get(User, api_key.user_id)
    if not owner or not owner.is_active:
        raise APIException(
            error_code=ErrorCode.AUTH_INACTIVE_USER,
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key owner is inactive",
        )

    # Update usage only after both key and owner are authorized. Keep this as an
    # exact persisted counter: assigning a value loaded by this session loses
    # increments when two authorized requests commit concurrently.
    last_used_at = utcnow()
    session.exec(
        update(AgentApiKey)
        .where(AgentApiKey.id == api_key.id)
        .values(
            last_used_at=last_used_at,
            request_count=AgentApiKey.request_count + 1,
        )
        .execution_options(synchronize_session=False)
    )
    session.commit()
    session.refresh(api_key)

    return (session, api_key.user_id, api_key)
