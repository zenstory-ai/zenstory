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

# Per-field max lengths (characters) — aligned with PostgreSQL column limits.
PROJECT_STATUS_MAX_LENGTHS: dict[str, int] = {
    "summary": 4000,
    "current_phase": 1000,
    "writing_style": 1000,
    "notes": 4000,
}

# Recommended target budgets (~25%-30% headroom below hard caps) so iterative
# updates compress older settled details instead of oscillating near the limit.
PROJECT_STATUS_RECOMMENDED_TARGETS: dict[str, int] = {
    "summary": 3000,
    "current_phase": 700,
    "writing_style": 700,
    "notes": 3000,
}

# Proactive soft warning thresholds (~80% of hard caps): when a successful update
# reaches or exceeds this length, emit a model-facing headroom_hint advising compression.
PROJECT_STATUS_SOFT_WARN_THRESHOLDS: dict[str, int] = {
    "summary": 3200,
    "current_phase": 800,
    "writing_style": 800,
    "notes": 3200,
}


class ProjectStatusFieldTooLongError(ValueError):
    """Raised when a project status field exceeds its hard character limit.

    Subclasses ``ValueError`` so REST handlers and existing callers catching
    ``ValueError`` stay 100% compatible, while agent tools can surface exact
    character counts and headroom targets.
    """

    def __init__(
        self,
        field_name: str,
        *,
        actual_length: int,
        max_length: int,
        recommended_target: int | None = None,
    ) -> None:
        self.field_name = field_name
        self.actual_length = actual_length
        self.max_length = max_length
        self.excess = max(actual_length - max_length, 0)
        self.recommended_target = (
            recommended_target
            if recommended_target is not None
            else PROJECT_STATUS_RECOMMENDED_TARGETS.get(field_name, max_length)
        )
        super().__init__(f"{field_name} exceeds max length ({max_length})")


def normalize_project_status_payload(
    payload: Mapping[str, str | None],
) -> dict[str, str]:
    """
    Normalize and validate project status field values.

    - Trims leading/trailing whitespace
    - Preserves empty string (explicit clear)
    - Raises ProjectStatusFieldTooLongError (a ValueError subclass) when field exceeds max length
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
            raise ProjectStatusFieldTooLongError(
                field_name,
                actual_length=len(cleaned),
                max_length=max_length,
            )

        normalized[field_name] = cleaned

    return normalized

