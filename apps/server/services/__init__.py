"""
Services module - Backward compatibility layer.

This module provides backward-compatible exports from the refactored services structure.
All imports from the old `services.*` paths are re-exported here.

New structure:
- core/ - Core business services
- features/ - Feature-specific services
- infra/ - Infrastructure services
- utils/ - Utility modules
"""

import sys
from importlib import import_module

# Core services (from core/)
from .core.auth_service import (
    create_access_token,
    create_refresh_token,
    get_current_active_user,
    get_current_user,
    get_optional_current_user,
    hash_password,
    verify_password,
    verify_token,
)
from .features.export_service import (
    export_drafts_to_txt,
)
from .features.file_version_service import (
    FileVersionService,
    get_file_version_service,
)
from .features.snapshot_service import (
    VersionService,
    get_version_service,
)

# Feature services (from features/)
from .features.verification_service import (
    generate_verification_code,
    get_code_ttl,
    get_remaining_cooldown,
    send_verification_code,
    verify_code,
)
from .infra.email_client import (
    send_verification_email,
)

# Infrastructure services (from infra/)
from .infra.redis_client import (
    check_resend_cooldown,
    delete_verification_code,
    get_verification_attempts,
    get_verification_code,
    increment_verification_attempts,
    reset_verification_attempts,
    set_resend_cooldown,
    store_verification_code,
)

# Create backward-compatible module aliases for old imports
# This allows code like `from services.auth import ...` to still work
sys.modules['services.auth'] = import_module('.core.auth_service', package='services')
sys.modules['services.verification_service'] = import_module('.features.verification_service', package='services')
sys.modules['services.file_version'] = import_module('.features.file_version_service', package='services')
sys.modules['services.version'] = import_module('.features.snapshot_service', package='services')
sys.modules['services.export_service'] = import_module('.features.export_service', package='services')
sys.modules['services.email_service'] = import_module('.infra.email_client', package='services')
sys.modules['services.redis_client'] = import_module('.infra.redis_client', package='services')


_VECTOR_EXPORTS = {
    "LlamaIndexService",
    "get_llama_index_service",
    "schedule_index_delete",
    "schedule_index_upsert",
}


def __getattr__(name: str):
    """Load the optional vector stack only when a vector export is requested."""
    if name not in _VECTOR_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module = import_module('.infra.vector_search_service', package=__name__)
    value = getattr(module, name)
    globals()[name] = value
    return value

__all__ = [
    # Auth service
    "hash_password",
    "verify_password",
    "create_access_token",
    "create_refresh_token",
    "verify_token",
    "get_current_user",
    "get_current_active_user",
    "get_optional_current_user",
    # Verification service
    "generate_verification_code",
    "send_verification_code",
    "verify_code",
    "get_remaining_cooldown",
    "get_code_ttl",
    # File version service
    "FileVersionService",
    "get_file_version_service",
    # Version/Snapshot service
    "VersionService",
    "get_version_service",
    # Export service
    "export_drafts_to_txt",
    # Redis client
    "store_verification_code",
    "get_verification_code",
    "delete_verification_code",
    "check_resend_cooldown",
    "set_resend_cooldown",
    "get_verification_attempts",
    "increment_verification_attempts",
    "reset_verification_attempts",
    # Email client
    "send_verification_email",
    # Vector search service (LlamaIndex)
    "LlamaIndexService",
    "get_llama_index_service",
    "schedule_index_upsert",
    "schedule_index_delete",
]
