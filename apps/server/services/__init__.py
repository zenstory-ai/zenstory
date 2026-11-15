"""
Services module - Backward compatibility module aliases.

Structure:
- core/ - Core business services
- features/ - Feature-specific services
- infra/ - Infrastructure services
"""

import sys
from importlib import import_module

# Backward-compatible module aliases for existing `services.<alias>` imports
sys.modules["services.auth"] = import_module(".core.auth_service", package="services")
sys.modules["services.verification_service"] = import_module(".features.verification_service", package="services")
sys.modules["services.file_version"] = import_module(".features.file_version_service", package="services")
sys.modules["services.export_service"] = import_module(".features.export_service", package="services")
