"""
Admin System Prompt Configuration API endpoints.

This module contains all system prompt configuration endpoints for admin operations.
"""
import logging

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import update
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import SystemPromptConfig, User
from services.admin_audit_service import admin_audit_service
from services.core.auth_service import get_current_superuser
from utils.logger import get_logger, log_with_context

from .schemas import SystemPromptConfigRequest

logger = get_logger(__name__)

router = APIRouter(tags=["admin-prompts"])


def _audit_value(prompt: SystemPromptConfig) -> dict[str, object]:
    """Return the persisted prompt fields relevant to a mutation audit."""
    return {
        "project_type": prompt.project_type,
        "role_definition": prompt.role_definition,
        "capabilities": prompt.capabilities,
        "directory_structure": prompt.directory_structure,
        "content_structure": prompt.content_structure,
        "file_types": prompt.file_types,
        "writing_guidelines": prompt.writing_guidelines,
        "include_dialogue_guidelines": prompt.include_dialogue_guidelines,
        "is_active": prompt.is_active,
        "version": prompt.version,
    }


# ==================== System Prompt Management ====================

@router.get("/prompts", response_model=list[SystemPromptConfig])
def get_prompts(
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get all system prompt configurations.

    Requires superuser privileges.
    """
    prompts = session.exec(select(SystemPromptConfig)).all()

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved all system prompt configurations",
        user_id=current_user.id,
        count=len(prompts),
    )

    return prompts


@router.get("/prompts/{project_type}", response_model=SystemPromptConfig)
def get_prompt(
    project_type: str,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get a specific system prompt configuration by project type.

    Requires superuser privileges.
    """
    prompt = session.exec(
        select(SystemPromptConfig).where(SystemPromptConfig.project_type == project_type)
    ).first()

    if not prompt:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved system prompt configuration",
        user_id=current_user.id,
        project_type=project_type,
    )

    return prompt


@router.put("/prompts/{project_type}", response_model=SystemPromptConfig)
def upsert_prompt(
    project_type: str,
    prompt_request: SystemPromptConfigRequest,
    http_request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Create or update a system prompt configuration.

    Requires superuser privileges.
    """
    # Check if configuration already exists
    existing_prompt = session.exec(
        select(SystemPromptConfig).where(SystemPromptConfig.project_type == project_type)
    ).first()

    if existing_prompt:
        if prompt_request.expected_version is None:
            raise APIException(
                error_code=ErrorCode.RESOURCE_CONFLICT,
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "expected_version is required when updating a prompt",
                    "current_version": existing_prompt.version,
                },
            )

        old_value = _audit_value(existing_prompt)
        update_data = prompt_request.model_dump(
            exclude={"expected_version"},
            exclude_unset=True,
        )
        update_data.update(
            updated_by=current_user.id,
            updated_at=utcnow(),
            version=SystemPromptConfig.version + 1,
        )

        try:
            result = session.exec(
                update(SystemPromptConfig)
                .where(SystemPromptConfig.project_type == project_type)
                .where(SystemPromptConfig.version == prompt_request.expected_version)
                .values(**update_data)
            )
            if result.rowcount != 1:
                session.rollback()
                current_prompt = session.exec(
                    select(SystemPromptConfig).where(
                        SystemPromptConfig.project_type == project_type
                    )
                ).first()
                raise APIException(
                    error_code=ErrorCode.RESOURCE_CONFLICT,
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "message": "Prompt was modified by another request",
                        "current_version": current_prompt.version if current_prompt else None,
                    },
                )

            updated_prompt = session.exec(
                select(SystemPromptConfig).where(
                    SystemPromptConfig.project_type == project_type
                )
            ).one()
            admin_audit_service.log_action(
                session,
                current_user.id,
                "update_prompt",
                "system_prompt",
                updated_prompt.id,
                old_value=old_value,
                new_value=_audit_value(updated_prompt),
                request=http_request,
                commit=False,
            )
            session.commit()
            session.refresh(updated_prompt)
        except APIException:
            raise
        except Exception:
            session.rollback()
            raise

        log_with_context(
            logger,
            logging.INFO,
            "Updated system prompt configuration",
            user_id=current_user.id,
            project_type=project_type,
            version=updated_prompt.version,
        )

        return updated_prompt
    else:
        # Create new configuration
        new_prompt = SystemPromptConfig(
            project_type=project_type,
            role_definition=prompt_request.role_definition,
            capabilities=prompt_request.capabilities,
            directory_structure=prompt_request.directory_structure,
            content_structure=prompt_request.content_structure,
            file_types=prompt_request.file_types,
            writing_guidelines=prompt_request.writing_guidelines,
            include_dialogue_guidelines=prompt_request.include_dialogue_guidelines,
            is_active=prompt_request.is_active,
            created_by=current_user.id,
            updated_by=current_user.id,
        )

        try:
            session.add(new_prompt)
            session.flush()
            admin_audit_service.log_action(
                session,
                current_user.id,
                "create_prompt",
                "system_prompt",
                new_prompt.id,
                new_value=_audit_value(new_prompt),
                request=http_request,
                commit=False,
            )
            session.commit()
            session.refresh(new_prompt)
        except Exception:
            session.rollback()
            raise

        log_with_context(
            logger,
            logging.INFO,
            "Created system prompt configuration",
            user_id=current_user.id,
            project_type=project_type,
        )

        return new_prompt


@router.delete("/prompts/{project_type}")
def delete_prompt(
    project_type: str,
    http_request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Delete a system prompt configuration.

    Requires superuser privileges.
    """
    prompt = session.exec(
        select(SystemPromptConfig).where(SystemPromptConfig.project_type == project_type)
    ).first()

    if not prompt:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    try:
        admin_audit_service.log_action(
            session,
            current_user.id,
            "delete_prompt",
            "system_prompt",
            prompt.id,
            old_value=_audit_value(prompt),
            request=http_request,
            commit=False,
        )
        session.delete(prompt)
        session.commit()
    except Exception:
        session.rollback()
        raise

    log_with_context(
        logger,
        logging.INFO,
        "Deleted system prompt configuration",
        user_id=current_user.id,
        project_type=project_type,
    )

    return {"message": "System prompt configuration deleted successfully"}


@router.post("/prompts/reload")
def reload_prompts_endpoint(
    current_user: User = Depends(get_current_superuser),
):
    """
    Reload system prompt configurations from database.

    This endpoint clears any in-memory cache and triggers a reload
    of system prompt configurations from the database.

    Requires superuser privileges.
    """
    # Import and call the reload function from agent.prompts module
    from agent.prompts import reload_prompts

    try:
        reload_result = reload_prompts()
    except Exception as exc:
        log_with_context(
            logger,
            logging.ERROR,
            "Failed to reload system prompt configurations",
            user_id=current_user.id,
            error_type=type(exc).__name__,
        )
        raise APIException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc

    log_with_context(
        logger,
        logging.INFO,
        "Reloaded system prompt configurations",
        user_id=current_user.id,
    )

    return {
        "message": "System prompt configurations reloaded successfully",
        **reload_result,
    }
