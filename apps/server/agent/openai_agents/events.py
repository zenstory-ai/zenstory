"""Event and payload helpers for the OpenAI Agents SDK adapter."""

from __future__ import annotations

import json
from typing import Any


def normalize_str_list(value: Any) -> list[str]:
    """Normalize optional list-like input to a clean list[str]."""
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def merge_unique_strings(*groups: list[str]) -> list[str]:
    """Merge list[str] groups while preserving order and removing duplicates."""
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            if item in seen:
                continue
            seen.add(item)
            merged.append(item)
    return merged


def build_handoff_packet(
    result_data: dict[str, Any],
    artifact_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Build a structured handoff packet from a tool result payload."""
    overflow_backfill = result_data.get("overflow_backfill")
    normalized_overflow_backfill = (
        [item for item in overflow_backfill if isinstance(item, dict)]
        if isinstance(overflow_backfill, list)
        else []
    )

    return {
        "target_agent": str(result_data.get("target_agent", "")).strip(),
        "reason": str(result_data.get("reason", "")).strip(),
        "context": str(result_data.get("context", "")).strip(),
        "completed": normalize_str_list(result_data.get("completed")),
        "todo": normalize_str_list(result_data.get("todo")),
        "evidence": normalize_str_list(result_data.get("evidence")),
        "artifact_refs": merge_unique_strings(
            normalize_str_list(result_data.get("artifact_refs")),
            artifact_refs or [],
        ),
        "overflow_backfill": normalized_overflow_backfill,
    }


def extract_tool_result_text(raw_result: Any) -> str:
    """Extract text from the project's MCP-style tool result payload."""
    if isinstance(raw_result, str):
        return raw_result

    if not isinstance(raw_result, dict):
        return ""

    content_list = raw_result.get("content")
    if not isinstance(content_list, list):
        return ""

    for item in content_list:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str):
            return text
        if text is not None:
            return str(text)
    return ""


def mcp_text_result(text: str) -> dict[str, Any]:
    """Wrap a text tool output in the project's MCP-style payload."""
    return {"content": [{"type": "text", "text": text}]}


def tool_error_text(error: str, *, error_type: str | None = None, tool_name: str | None = None) -> str:
    """Build the JSON text payload used for tool execution errors."""
    payload: dict[str, Any] = {"status": "error", "error": error}
    if error_type:
        payload["error_type"] = error_type
    if tool_name:
        payload["tool_name"] = tool_name
    return json.dumps(payload, ensure_ascii=False)


def extract_artifact_refs(
    tool_name: str,
    result_text: str,
    tool_input: dict[str, Any] | None = None,
) -> list[str]:
    """Extract lightweight artifact refs from successful tool result text."""
    if not result_text:
        return []

    try:
        payload = json.loads(result_text)
    except json.JSONDecodeError:
        return []

    refs: list[str] = []
    overflow_ref = payload.get("overflow_ref")
    if isinstance(overflow_ref, str) and overflow_ref.strip():
        refs.append(overflow_ref.strip())

    if payload.get("status") != "success":
        return merge_unique_strings(refs)

    data = payload.get("data")
    if tool_name in {"create_file", "edit_file", "rename_file"} and isinstance(data, dict):
        file_id = data.get("id")
        if isinstance(file_id, str) and file_id.strip():
            refs.append(file_id.strip())
    elif tool_name == "delete_file":
        deleted_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(deleted_id, str):
            deleted_id = (tool_input or {}).get("id")
        if isinstance(deleted_id, str) and deleted_id.strip():
            refs.append(deleted_id.strip())
    elif tool_name == "update_project" and isinstance(data, dict):
        project_id = data.get("project_id")
        if isinstance(project_id, str) and project_id.strip():
            refs.append(f"project:{project_id.strip()}")

    return merge_unique_strings(refs)


def parse_json_object(raw: str, *, tool_name: str = "") -> tuple[dict[str, Any], str | None, dict[str, Any]]:
    """Parse a JSON object with best-effort repair for model tool arguments."""
    if not raw:
        return {}, None, {"strategy": "empty"}

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed, None, {"strategy": "json"}
        return {}, f"tool_input must be a JSON object, got {type(parsed).__name__}", {"strategy": "json"}
    except json.JSONDecodeError as exc:
        parse_error = str(exc)

    try:
        from json_repair import loads as json_repair_loads

        repaired_obj, repair_log = json_repair_loads(
            raw,
            skip_json_loads=True,
            logging=True,
            stream_stable=True,
        )
        if isinstance(repaired_obj, dict):
            return repaired_obj, None, {
                "strategy": "json_repair",
                "repair_actions": len(repair_log),
            }
        return {}, parse_error, {
            "strategy": "json_repair",
            "repair_actions": len(repair_log),
            "repaired_type": type(repaired_obj).__name__,
        }
    except Exception as repair_exc:
        return {}, parse_error, {
            "strategy": "json_repair_failed",
            "repair_error": f"{type(repair_exc).__name__}: {repair_exc}",
            "tool_name": tool_name,
        }
