"""Comprehensive tests for Pydantic v2 tool input models & compact schema export."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from agent.openai_agents.tool_failure_breaker import (
    MAX_IDENTICAL_TOOL_FAILURES,
    TRIP_REASON_IDENTICAL,
    ToolFailureBreaker,
)
from agent.openai_agents.tools_adapter import (
    _cancels_overdue_file,
    build_agent_function_tools,
    invoke_project_tool,
)
from agent.tools import mcp_tools
from agent.tools.registry import LOOP_TOOL_NAMES
from agent.tools.tool_context import AgentRequestContext
from agent.tools.tool_inputs import (
    TOOL_INPUT_MODELS,
    TOOL_INPUT_VALIDATION_ERROR_TYPE,
    CreateFileInput,
    DeleteFileInput,
    EditFileInput,
    EditOperationInput,
    LoadSkillInput,
    QueryFilesInput,
    ReadSkillResourceInput,
    RenameFileInput,
    UnitSpecInput,
    UpdateProjectInput,
    WriteChapterInput,
    export_tool_schema,
    validate_tool_input,
)
from agent.tools.tool_schemas import TOOL_SCHEMAS

# Compact JSON char count of the hand-written TOOL_SCHEMAS baseline before Pydantic unification.
BASELINE_TOOL_SCHEMAS_COMPACT_CHARS = 11_453


def _parse_mcp(result: dict[str, Any]) -> dict[str, Any]:
    return json.loads(result["content"][0]["text"])


def _walk_schema_objects(node: Any):
    """Yield every JSON Schema object node (skipping property-name mapping dicts)."""
    if not isinstance(node, dict):
        return
    yield node
    props = node.get("properties")
    if isinstance(props, dict):
        for prop_schema in props.values():
            yield from _walk_schema_objects(prop_schema)
    items = node.get("items")
    if isinstance(items, dict):
        yield from _walk_schema_objects(items)
    any_of = node.get("anyOf")
    if isinstance(any_of, list):
        for branch in any_of:
            yield from _walk_schema_objects(branch)


# ---------------------------------------------------------------------------
# 1. Schema parity, open metadata: dict, zero Pydantic bloat, token budget
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tool_input_models_cover_every_loop_tool_in_order():
    assert list(TOOL_INPUT_MODELS.keys()) == LOOP_TOOL_NAMES
    assert list(TOOL_SCHEMAS.keys()) == LOOP_TOOL_NAMES


@pytest.mark.unit
def test_exported_schemas_have_zero_pydantic_bloat_and_stay_within_baseline_budget():
    compact = json.dumps(TOOL_SCHEMAS, ensure_ascii=False, separators=(",", ":"))
    assert len(compact) <= BASELINE_TOOL_SCHEMAS_COMPACT_CHARS

    for tool_name, entry in TOOL_SCHEMAS.items():
        schema = entry["input_schema"]
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema
        assert "description" not in schema, f"{tool_name} leaked class docstring into input_schema"

        for node in _walk_schema_objects(schema):
            assert "title" not in node, f"{tool_name} leaked Pydantic title: {node}"
            assert "$defs" not in node, f"{tool_name} leaked $defs"
            assert "$ref" not in node, f"{tool_name} leaked $ref"
            assert "anyOf" not in node, f"{tool_name} leaked verbose anyOf union: {node}"
            if "default" in node:
                assert node["default"] is not None, f"{tool_name} leaked redundant default: null"
            if "additionalProperties" in node:
                assert node["additionalProperties"] is False, f"{tool_name} leaked redundant additionalProperties: true"


@pytest.mark.unit
def test_exported_schemas_preserve_required_properties_constraints_and_open_metadata():
    # create_file: open metadata object, non-body file_type enum, hidden internal content/order
    create_schema = TOOL_SCHEMAS["create_file"]["input_schema"]
    assert create_schema["required"] == ["title", "file_type"]
    create_props = create_schema["properties"]
    assert list(create_props.keys()) == ["title", "file_type", "parent_id", "metadata", "empty", "source_text"]
    assert create_props["title"]["maxLength"] == 255
    assert create_props["file_type"]["enum"] == ["outline", "character", "lore", "snippet", "document", "folder"]
    assert create_props["metadata"] == {"type": "object", "description": "文件元数据（JSON对象）"}
    assert "content" not in create_props and "order" not in create_props

    # write_chapter: chapter minimum=1, file_type enum draft/script, hidden metadata
    wc_schema = TOOL_SCHEMAS["write_chapter"]["input_schema"]
    assert wc_schema["required"] == ["chapter", "title", "file_type"]
    assert wc_schema["properties"]["chapter"]["minimum"] == 1
    assert wc_schema["properties"]["title"]["maxLength"] == 255
    assert wc_schema["properties"]["file_type"]["enum"] == ["draft", "script"]
    assert "metadata" not in wc_schema["properties"]

    # edit_file: edits item schema inlined, continue_on_error default=False, occurrence minimum=1
    edit_schema = TOOL_SCHEMAS["edit_file"]["input_schema"]
    assert edit_schema["required"] == ["id", "edits"]
    assert edit_schema["properties"]["continue_on_error"]["default"] is False
    edit_item = edit_schema["properties"]["edits"]["items"]
    assert edit_item["required"] == ["op"]
    assert edit_item["properties"]["occurrence"]["minimum"] == 1
    assert edit_item["properties"]["replace_all"]["default"] is False
    assert edit_item["properties"]["match_mode"]["default"] == "auto"
    assert edit_item["properties"]["ignore_punct_whitespace"]["default"] is True

    # delete_file & rename_file
    assert TOOL_SCHEMAS["delete_file"]["input_schema"]["required"] == ["id"]
    assert TOOL_SCHEMAS["delete_file"]["input_schema"]["properties"]["recursive"]["default"] is False
    assert TOOL_SCHEMAS["rename_file"]["input_schema"]["required"] == ["id", "title"]
    assert TOOL_SCHEMAS["rename_file"]["input_schema"]["properties"]["title"]["maxLength"] == 255

    # query_files: Batch B ids maxItems=5, >15000 guidance, range constraints, defaults
    qf_schema = TOOL_SCHEMAS["query_files"]["input_schema"]
    assert qf_schema["required"] == []
    qf_props = qf_schema["properties"]
    assert qf_props["ids"]["maxItems"] == 5
    assert qf_props["limit"]["default"] == 50
    assert qf_props["offset"]["default"] == 0
    assert qf_props["content_preview_chars"]["default"] == 200
    assert qf_props["content_preview_chars"]["minimum"] == 0
    assert qf_props["tail_chars"]["minimum"] == 1
    assert qf_props["start_char"]["minimum"] == 0
    assert qf_props["max_chars"]["minimum"] == 1
    assert qf_props["include_content"]["default"] is False
    assert "15000" in qf_props["start_char"]["description"]
    assert "15000" in qf_props["max_chars"]["description"]

    # update_project: tasks.items & unit_spec keep additionalProperties=False, uniqueItems, minItems/maxItems
    up_schema = TOOL_SCHEMAS["update_project"]["input_schema"]
    assert up_schema["required"] == []
    assert up_schema["properties"]["title"]["maxLength"] == 30
    task_item = up_schema["properties"]["tasks"]["items"]
    assert task_item["additionalProperties"] is False
    assert task_item["required"] == ["task", "status"]
    assert task_item["properties"]["artifact_refs"]["uniqueItems"] is True
    assert task_item["properties"]["artifact_refs"]["items"]["minLength"] == 1
    unit_spec = task_item["properties"]["unit_spec"]
    assert unit_spec["additionalProperties"] is False
    assert unit_spec["required"] == ["unit", "numbers"]
    assert unit_spec["properties"]["numbers"]["items"] == {"type": "integer", "minimum": 1}
    assert unit_spec["properties"]["numbers"]["minItems"] == 1
    assert unit_spec["properties"]["numbers"]["maxItems"] == 2001
    assert unit_spec["properties"]["numbers"]["uniqueItems"] is True

    # load_skill & read_skill_resource
    assert TOOL_SCHEMAS["load_skill"]["input_schema"]["required"] == ["name"]
    rsr_schema = TOOL_SCHEMAS["read_skill_resource"]["input_schema"]
    assert rsr_schema["required"] == ["name", "path"]
    assert rsr_schema["properties"]["offset"]["minimum"] == 0


# ---------------------------------------------------------------------------
# 2. Coercion: CoercedBool, strict non-bool int, parameter aliases
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_bool", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("FALSE", False),
        ("0", False),
        ("no", False),
        ("fals", False),
        ("", False),
        (1, True),
        (0, False),
        (None, False),
    ],
)
def test_coerce_bool_across_tool_models(raw_bool, expected):
    delete_in, err = validate_tool_input(DeleteFileInput, {"id": "f-1", "recursive": raw_bool}, tool_name="delete_file")
    assert err is None and delete_in is not None and delete_in.recursive is expected

    edit_in, err = validate_tool_input(
        EditFileInput,
        {"id": "f-1", "edits": [], "continue_on_error": raw_bool},
        tool_name="edit_file",
    )
    assert err is None and edit_in is not None and edit_in.continue_on_error is expected


@pytest.mark.unit
def test_query_files_include_content_preserves_three_valued_semantics():
    omitted, err = validate_tool_input(QueryFilesInput, {}, tool_name="query_files")
    assert err is None and omitted is not None
    assert omitted.include_content is None
    assert "include_content" not in omitted.model_fields_set

    explicit_none, err = validate_tool_input(QueryFilesInput, {"include_content": None}, tool_name="query_files")
    assert err is None and explicit_none is not None
    assert explicit_none.include_content is None
    assert "include_content" in explicit_none.model_fields_set

    str_true, err = validate_tool_input(QueryFilesInput, {"include_content": "true"}, tool_name="query_files")
    assert err is None and str_true is not None and str_true.include_content is True

    str_false, err = validate_tool_input(QueryFilesInput, {"include_content": "false"}, tool_name="query_files")
    assert err is None and str_false is not None and str_false.include_content is False


@pytest.mark.unit
@pytest.mark.parametrize("bad_bool", [True, False])
def test_bool_is_strictly_rejected_for_integer_fields(bad_bool):
    # query_files integer fields
    for field in ("start_char", "max_chars", "tail_chars", "limit", "offset"):
        parsed, err = validate_tool_input(QueryFilesInput, {field: bad_bool}, tool_name="query_files")
        assert parsed is None and err is not None
        assert err["status"] == "error"
        assert err["error_type"] == TOOL_INPUT_VALIDATION_ERROR_TYPE
        assert "布尔值" in err["message"]
        assert err["error"] == err["message"]

    # write_chapter.chapter
    parsed, err = validate_tool_input(
        WriteChapterInput,
        {"chapter": bad_bool, "title": "第一章", "file_type": "draft"},
        tool_name="write_chapter",
        error_type="chapter_invalid",
    )
    assert parsed is None and err is not None
    assert err["error_type"] == "chapter_invalid"

    # read_skill_resource.offset
    parsed, err = validate_tool_input(
        ReadSkillResourceInput,
        {"name": "s", "path": "SKILL.md", "offset": bad_bool},
        tool_name="read_skill_resource",
    )
    assert parsed is None and err is not None and "布尔值" in err["message"]

    # create_file.order
    parsed, err = validate_tool_input(
        CreateFileInput,
        {"title": "大纲", "file_type": "outline", "order": bad_bool},
        tool_name="create_file",
    )
    assert parsed is None and err is not None and "布尔值" in err["message"]

    # EditOperationInput.occurrence & UnitSpecInput.numbers
    parsed, err = validate_tool_input(
        EditOperationInput,
        {"op": "replace", "old": "a", "new": "b", "occurrence": bad_bool},
        tool_name="edit_file",
    )
    assert parsed is None and err is not None and "布尔值" in err["message"]

    parsed, err = validate_tool_input(
        UnitSpecInput,
        {"unit": "章", "numbers": [bad_bool]},
        tool_name="update_project",
    )
    assert parsed is None and err is not None and "布尔值" in err["message"]


@pytest.mark.unit
@pytest.mark.parametrize("id_key", ["id", "file_id", "fileId"])
def test_file_id_aliases_work_across_file_tools(id_key):
    edit_in, err = validate_tool_input(
        EditFileInput,
        {id_key: "  f-42  ", "operations": [{"op": "append", "text": "hi", "replace_all": "false"}]},
        tool_name="edit_file",
    )
    assert err is None and edit_in is not None
    assert edit_in.id == "f-42"
    assert edit_in.edits == [{"op": "append", "text": "hi", "replace_all": False}]

    del_in, err = validate_tool_input(DeleteFileInput, {id_key: "f-42"}, tool_name="delete_file")
    assert err is None and del_in is not None and del_in.id == "f-42"

    ren_in, err = validate_tool_input(RenameFileInput, {id_key: "f-42", "title": "新标题"}, tool_name="rename_file")
    assert err is None and ren_in is not None and ren_in.id == "f-42" and ren_in.title == "新标题"

    qf_in, err = validate_tool_input(QueryFilesInput, {id_key: "f-42"}, tool_name="query_files")
    assert err is None and qf_in is not None and qf_in.id == "f-42"


@pytest.mark.unit
def test_empty_id_falls_back_to_file_id_alias_and_update_project_aliases_work():
    edit_in, err = validate_tool_input(
        EditFileInput,
        {"id": "", "file_id": "f-fallback", "edits": []},
        tool_name="edit_file",
    )
    assert err is None and edit_in is not None and edit_in.id == "f-fallback"

    up_in, err = validate_tool_input(
        UpdateProjectInput,
        {
            "projectSummary": "简介",
            "currentPhase": "第二章",
            "writingStyle": "冷峻克制",
        },
        tool_name="update_project",
    )
    assert err is None and up_in is not None
    assert up_in.summary == "简介"
    assert up_in.current_phase == "第二章"
    assert up_in.writing_style == "冷峻克制"
    assert up_in.model_fields_set == {"summary", "current_phase", "writing_style"}


# ---------------------------------------------------------------------------
# 3. Structured Chinese error contract & ToolFailureBreaker compatibility
# ---------------------------------------------------------------------------


def _mock_req_ctx(mock_executor: MagicMock, *, project_id: str = "proj-1") -> AgentRequestContext:
    req_ctx = AgentRequestContext(session=MagicMock(), project_id=project_id, session_id="sess-1")
    req_ctx.executor = MagicMock(return_value=mock_executor)
    return req_ctx


@pytest.mark.asyncio
@pytest.mark.unit
async def test_direct_mcp_tools_return_structured_chinese_validation_errors():
    mock_executor = MagicMock()
    req_ctx = _mock_req_ctx(mock_executor)

    # edit_file bad id type -> invalid_edit + edits_applied=0 + Chinese error & message
    bad_edit = _parse_mcp(await mcp_tools.edit_file({"id": 123, "edits": []}, request_context=req_ctx))
    assert bad_edit["status"] == "error"
    assert bad_edit["error_type"] == "invalid_edit"
    assert bad_edit["mutation_applied"] is False
    assert bad_edit["edits_applied"] == 0
    assert bad_edit["message"] == bad_edit["error"]
    assert "字符串" in bad_edit["message"]

    # query_files bool passed for start_char -> invalid_tool_input
    bad_query = _parse_mcp(
        await mcp_tools.query_files({"id": "f-1", "start_char": True}, request_context=req_ctx)
    )
    assert bad_query["status"] == "error"
    assert bad_query["error_type"] == TOOL_INPUT_VALIDATION_ERROR_TYPE
    assert bad_query["mutation_applied"] is False
    assert bad_query["message"] == bad_query["error"]
    assert "布尔值" in bad_query["message"]

    # delete_file missing id -> invalid_tool_input
    bad_del = _parse_mcp(await mcp_tools.delete_file({}, request_context=req_ctx))
    assert bad_del["status"] == "error"
    assert bad_del["error_type"] == TOOL_INPUT_VALIDATION_ERROR_TYPE
    assert bad_del["message"] == bad_del["error"]

    # write_chapter bool chapter -> WRITE_CHAPTER_INVALID_ERROR_TYPE
    bad_wc = _parse_mcp(
        await mcp_tools.write_chapter(
            {"chapter": True, "title": "第一章", "file_type": "draft"},
            request_context=req_ctx,
        )
    )
    assert bad_wc["status"] == "error"
    assert bad_wc["error_type"] == mcp_tools.WRITE_CHAPTER_INVALID_ERROR_TYPE
    assert bad_wc["message"] == bad_wc["error"]

    mock_executor.edit_file.assert_not_called()
    mock_executor.query_files.assert_not_called()
    mock_executor.delete_file_with_meta.assert_not_called()


@pytest.mark.unit
def test_validation_error_payload_is_compatible_with_tool_failure_breaker():
    _, err_payload = validate_tool_input(
        QueryFilesInput,
        {"id": "f-1", "start_char": True},
        tool_name="query_files",
    )
    assert err_payload is not None
    raw_output = json.dumps(err_payload, ensure_ascii=False)
    raw_args = json.dumps({"id": "f-1", "start_char": True})

    breaker = ToolFailureBreaker()
    for _ in range(MAX_IDENTICAL_TOOL_FAILURES):
        breaker.observe("query_files", raw_args, raw_output)

    assert breaker.is_open is True
    assert breaker.trip is not None
    assert breaker.trip.reason == TRIP_REASON_IDENTICAL
    assert "布尔值" in breaker.trip.last_error


# ---------------------------------------------------------------------------
# 4. strict_json_schema=False + json_repair + overdue-file cancellation guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sdk_function_tools_keep_strict_json_schema_false_and_clean_params():
    tools = build_agent_function_tools()
    assert [tool.name for tool in tools] == LOOP_TOOL_NAMES
    for tool in tools:
        assert tool.strict_json_schema is False
        assert tool.params_json_schema == export_tool_schema(TOOL_INPUT_MODELS[tool.name])


@pytest.mark.asyncio
@pytest.mark.unit
async def test_invoke_project_tool_repairs_json_before_pydantic_validation():
    mock_executor = MagicMock()
    mock_executor.edit_file.return_value = {"id": "f-1", "edits_applied": 1, "mutation_applied": True}
    req_ctx = _mock_req_ctx(mock_executor)

    # Trailing comma & single quotes (repaired by json_repair) + alias file_id + string bool "false"
    malformed_json = "{'file_id': 'f-1', 'operations': [{'op': 'append', 'text': '你好'}], 'continue_on_error': 'false',}"
    output_text = await invoke_project_tool("edit_file", malformed_json, request_context=req_ctx)

    payload = json.loads(output_text)
    assert payload["status"] == "success"
    mock_executor.edit_file.assert_called_once()
    call_kwargs = mock_executor.edit_file.call_args.kwargs
    assert call_kwargs["id"] == "f-1"
    assert call_kwargs["edits"] == [{"op": "append", "text": "你好"}]
    assert call_kwargs["continue_on_error"] is False


@pytest.mark.unit
def test_cancels_overdue_file_uses_delete_file_input_aliases_and_bool_coercion():
    overdue = {"pending-1"}
    assert _cancels_overdue_file('{"id": "pending-1", "recursive": "false"}', overdue) == "pending-1"
    assert _cancels_overdue_file('{"file_id": "pending-1", "recursive": "0"}', overdue) == "pending-1"
    assert _cancels_overdue_file('{"fileId": "pending-1"}', overdue) == "pending-1"
    assert _cancels_overdue_file('{"id": "pending-1", "recursive": "true"}', overdue) is None
    assert _cancels_overdue_file('{"id": "other-file"}', overdue) is None
    assert _cancels_overdue_file('{"id": 123}', overdue) is None


@pytest.mark.unit
def test_load_skill_and_read_skill_resource_validation():
    parsed, err = validate_tool_input(LoadSkillInput, {"name": "  悬念大师  "}, tool_name="load_skill")
    assert err is None and parsed is not None and parsed.name == "悬念大师"

    parsed, err = validate_tool_input(LoadSkillInput, {"name": "   "}, tool_name="load_skill")
    assert parsed is None and err is not None and err["error_type"] == TOOL_INPUT_VALIDATION_ERROR_TYPE

    parsed, err = validate_tool_input(
        ReadSkillResourceInput,
        {"name": "s1", "path": "SKILL.md", "offset": "120"},
        tool_name="read_skill_resource",
    )
    assert err is None and parsed is not None and parsed.offset == 120

    parsed, err = validate_tool_input(
        ReadSkillResourceInput,
        {"name": "s1", "path": "SKILL.md", "offset": -5},
        tool_name="read_skill_resource",
    )
    assert parsed is None and err is not None and "不小于 0" in err["message"]


# ---------------------------------------------------------------------------
# 5. Verifier regression tests (null-bool, blank file_type, fallback defaults, Chinese errors)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ignore_punct_whitespace_none_preserves_default_true():
    """Passing null for ignore_punct_whitespace must stay True (matching FileEditor default)."""
    op_in, err = validate_tool_input(
        EditOperationInput,
        {"op": "replace", "old": "a", "new": "b", "ignore_punct_whitespace": None},
        tool_name="edit_file",
    )
    assert err is None and op_in is not None and op_in.ignore_punct_whitespace is True

    edit_in, err = validate_tool_input(
        EditFileInput,
        {
            "id": "f-1",
            "edits": [
                {"op": "replace", "old": "a", "new": "b", "ignore_punct_whitespace": None},
                {"op": "replace", "old": "c", "new": "d", "ignore_punct_whitespace": "false"},
                {"op": "replace", "old": "e", "new": "f", "ignore_punct_whitespace": "true"},
            ],
        },
        tool_name="edit_file",
    )
    assert err is None and edit_in is not None
    assert [item["ignore_punct_whitespace"] for item in edit_in.edits] == [True, False, True]


@pytest.mark.unit
def test_query_files_blank_file_type_normalizes_to_none_and_whitespace_case_folded():
    for blank in ("", "   ", None):
        parsed, err = validate_tool_input(QueryFilesInput, {"file_type": blank}, tool_name="query_files")
        assert err is None and parsed is not None and parsed.file_type is None

    parsed, err = validate_tool_input(QueryFilesInput, {"file_type": "  DRAFT  "}, tool_name="query_files")
    assert err is None and parsed is not None and parsed.file_type == "draft"

    parsed, err = validate_tool_input(QueryFilesInput, {"file_type": 123}, tool_name="query_files")
    assert parsed is None and err is not None and "允许值" in err["message"]
    assert " or " not in err["message"]


@pytest.mark.unit
def test_write_chapter_restores_fallback_defaults_for_omitted_file_type_and_title():
    parsed, err = validate_tool_input(WriteChapterInput, {"chapter": 3}, tool_name="write_chapter")
    assert err is None and parsed is not None
    assert parsed.chapter == 3
    assert parsed.file_type == "draft"
    assert parsed.title == ""

    parsed, err = validate_tool_input(
        WriteChapterInput,
        {"chapter": "2", "title": None, "file_type": ""},
        tool_name="write_chapter",
    )
    assert err is None and parsed is not None
    assert parsed.chapter == 2 and parsed.file_type == "draft" and parsed.title == ""

    # Whole float accepted; fractional float / <=0 / non-body file_type rejected
    parsed, err = validate_tool_input(WriteChapterInput, {"chapter": 4.0}, tool_name="write_chapter")
    assert err is None and parsed is not None and parsed.chapter == 4

    for bad_ch in (0, -2, 1.5, "abc", "", []):
        parsed, err = validate_tool_input(WriteChapterInput, {"chapter": bad_ch}, tool_name="write_chapter")
        assert parsed is None and err is not None and "正整数" in err["message"]

    parsed, err = validate_tool_input(
        WriteChapterInput,
        {"chapter": 1, "title": "大纲", "file_type": "outline"},
        tool_name="write_chapter",
    )
    assert parsed is None and err is not None and "draft" in err["message"]


@pytest.mark.unit
def test_chinese_error_formatting_covers_all_pydantic_error_types_without_english_leakage():
    from agent.tools.tool_inputs import UpdateProjectTaskInput, _format_single_error

    # Non-mapping raw_args & None raw_args
    parsed, err = validate_tool_input(QueryFilesInput, ["not-a-dict"], tool_name="query_files")
    assert parsed is None and err is not None and "JSON 对象" in err["message"]
    parsed, err = validate_tool_input(QueryFilesInput, None, tool_name="query_files")
    assert err is None and parsed is not None

    # literal_error: no English " or "
    parsed, err = validate_tool_input(QueryFilesInput, {"file_type": "bogus"}, tool_name="query_files")
    assert parsed is None and err is not None
    assert " 或 " in err["message"] and " or " not in err["message"]

    # extra_forbidden on strict sub-model
    parsed, err = validate_tool_input(
        UpdateProjectTaskInput,
        {"task": "写第一章", "status": "pending", "unknown_field": 1},
        tool_name="update_project",
    )
    assert parsed is None and err is not None and "多余字段" in err["message"]

    # too_short / too_long / string_too_short on UnitSpecInput & UpdateProjectTaskInput
    parsed, err = validate_tool_input(UnitSpecInput, {"unit": "章", "numbers": []}, tool_name="update_project")
    assert parsed is None and err is not None and "不能少于 1 个" in err["message"]

    parsed, err = validate_tool_input(
        UnitSpecInput,
        {"unit": "章", "numbers": list(range(1, 2003))},
        tool_name="update_project",
    )
    assert parsed is None and err is not None and "不能超过 2001 个" in err["message"]

    parsed, err = validate_tool_input(
        UpdateProjectTaskInput,
        {"task": "写第一章", "status": "done", "artifact_refs": [""]},
        tool_name="update_project",
    )
    assert parsed is None and err is not None and "不能少于 1 个字符" in err["message"]

    # dict_type, list_type, string_type, non-int string/empty, non-string ids items, non-list edits
    parsed, err = validate_tool_input(
        CreateFileInput,
        {"title": "大纲", "file_type": "outline", "metadata": "not-a-dict", "order": "bad"},
        tool_name="create_file",
    )
    assert parsed is None and err is not None and "JSON 对象" in err["message"]

    parsed, err = validate_tool_input(
        CreateFileInput,
        {"title": "大纲", "file_type": 123},
        tool_name="create_file",
    )
    assert parsed is None and err is not None and "字符串" in err["message"]

    parsed, err = validate_tool_input(
        CreateFileInput,
        {"title": "大纲", "file_type": "   "},
        tool_name="create_file",
    )
    assert parsed is None and err is not None and "不能为空" in err["message"]

    parsed, err = validate_tool_input(
        CreateFileInput,
        {"title": "大纲", "file_type": "outline", "parent_id": 123, "order": "abc"},
        tool_name="create_file",
    )
    assert parsed is None and err is not None and "字符串" in err["message"]

    parsed, err = validate_tool_input(
        EditFileInput,
        {"id": "f-1", "edits": "not-a-list"},
        tool_name="edit_file",
    )
    assert parsed is None and err is not None and "数组" in err["message"]

    parsed, err = validate_tool_input(QueryFilesInput, {"ids": "not-a-list"}, tool_name="query_files")
    assert parsed is None and err is not None and "ID 列表" in err["message"]

    parsed, err = validate_tool_input(QueryFilesInput, {"ids": [123]}, tool_name="query_files")
    assert parsed is None and err is not None and "字符串文件 ID" in err["message"]

    parsed, err = validate_tool_input(QueryFilesInput, {"start_char": ""}, tool_name="query_files")
    assert err is None and parsed is not None and parsed.start_char is None

    parsed, err = validate_tool_input(ReadSkillResourceInput, {"name": "", "path": ""}, tool_name="read_skill_resource")
    assert parsed is None and err is not None and "name" in err["message"] and "path" in err["message"]

    # Direct unit checks for remaining _format_single_error mappings
    assert "整数" in _format_single_error({"loc": ("x",), "type": "int_type", "msg": ""})[0]
    assert "布尔值" in _format_single_error({"loc": ("x",), "type": "bool_type", "msg": ""})[0]
    assert "字符串" in _format_single_error({"loc": ("x",), "type": "string_type", "msg": ""})[0]
    assert "数组" in _format_single_error({"loc": ("x",), "type": "list_type", "msg": ""})[0]
    assert "不能大于 10" in _format_single_error({"loc": ("x",), "type": "less_than_equal", "ctx": {"le": 10}, "msg": ""})[0]
    assert "超过 30" in _format_single_error({"loc": ("x",), "type": "string_too_long", "ctx": {"max_length": 30}, "msg": ""})[0]
    assert "取值无效" in _format_single_error({"loc": ("x",), "type": "literal_error", "msg": ""})[0]
    assert "无效" in _format_single_error({"loc": ("x",), "type": "custom_unknown", "msg": "Assertion failed, bad"})[0]

