"""Event and payload helpers for the OpenAI Agents SDK adapter."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

# Official OpenAI FunctionDefinition.name contract (openai.types.shared_params.function_definition):
# a-z, A-Z, 0-9, underscores, or dashes, maximum length 64.
_OPENAI_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def is_valid_openai_tool_name(name: Any) -> bool:
    """Return True when ``name`` satisfies the OpenAI Chat Completions tool name syntax."""
    return isinstance(name, str) and _OPENAI_TOOL_NAME_RE.fullmatch(name) is not None


def normalize_tool_call_name(raw_name: Any) -> str:
    """Preserve valid tool names verbatim; map malformed names to a deterministic per-input ID.

    Using a hash suffix keeps distinct malformed inputs in distinct failure-breaker buckets
    while preventing raw manuscript prose or XML tags from leaking into errors, DB rows, or
    replayed ``function_call.name`` history.
    """
    if is_valid_openai_tool_name(raw_name):
        return raw_name
    text = str(raw_name or "")
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"invalid_tool_name_{digest}"


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


def parse_mcp_tool_payload(raw_result: Any, default: Any = None) -> Any:
    """Parse the JSON payload inside an MCP ``{"content": [{"type": "text", "text": ...}]}`` tool result."""
    if not isinstance(raw_result, dict):
        return default
    content_list = raw_result.get("content")
    if not isinstance(content_list, list):
        return default
    for item in content_list:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if text is None:
            continue
        raw_text = text if isinstance(text, str) else str(text)
        if not raw_text:
            continue
        try:
            return json.loads(raw_text)
        except (TypeError, json.JSONDecodeError):
            continue
    return default


def mcp_text_result(text: str) -> dict[str, Any]:
    """Wrap a text tool output in the project's MCP-style payload."""
    return {"content": [{"type": "text", "text": text}]}


def tool_error_text(
    error: str,
    *,
    error_type: str | None = None,
    tool_name: str | None = None,
    user_message: str | None = None,
) -> str:
    """Build the JSON text payload used for tool execution errors."""
    payload: dict[str, Any] = {"status": "error", "error": error}
    if error_type:
        payload["error_type"] = error_type
    if tool_name:
        payload["tool_name"] = tool_name
    if user_message:
        payload["user_message"] = user_message
    return json.dumps(payload, ensure_ascii=False)


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
