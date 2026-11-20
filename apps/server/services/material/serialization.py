"""Shared serialization helpers for material domain services."""

from __future__ import annotations

import json
from typing import Any


def serialize_optional_json(value: Any) -> str | None:
    """Serialize a dict/list value to JSON string, passing through None or str."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)
