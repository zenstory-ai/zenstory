"""
Project status field constraints and normalization helpers.

These fields are surfaced in the "AI Memory" panel and injected into agent context.
Keeping limits centralized prevents prompt bloat and keeps behavior consistent across:
- REST API patch endpoint
- Agent tool updates (update_project)
"""

from collections.abc import Mapping

PROJECT_TITLE_LEDGER_ACTION = "project_title"
PROJECT_TITLE_SOURCE_AI = "ai_auto"
PROJECT_TITLE_SOURCE_AUTHOR = "user"
PROJECT_TITLE_AUTHOR_TOOL = "author_project_rename"

# Per-field max lengths (characters)
PROJECT_STATUS_MAX_LENGTHS: dict[str, int] = {
    "summary": 4000,
    "current_phase": 1000,
    "writing_style": 1000,
    "notes": 4000,
}


def normalize_project_status_payload(
    payload: Mapping[str, str | None],
) -> dict[str, str]:
    """
    Normalize and validate project status field values.

    - Trims leading/trailing whitespace
    - Preserves empty string (explicit clear)
    - Raises ValueError when field exceeds max length
    """
    normalized: dict[str, str] = {}

    for field_name, max_length in PROJECT_STATUS_MAX_LENGTHS.items():
        if field_name not in payload:
            continue

        value = payload[field_name]
        if value is None:
            continue

        cleaned = value.strip()
        if len(cleaned) > max_length:
            raise ValueError(f"{field_name} exceeds max length ({max_length})")

        normalized[field_name] = cleaned

    return normalized

