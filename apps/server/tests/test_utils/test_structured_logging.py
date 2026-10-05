"""Tests for the JSON log pipeline (utils/logger.py + config/logger_config.py)."""

import io
import json
import logging
import re

import pytest

from config.logger_config import JsonFormatter, RequestContextFilter
from utils.logger import log_with_context
from utils.request_context import bind_request_context, reset_request_context
from utils.sanitize import mask_email, sanitize_query_params


@pytest.fixture
def json_logger():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestContextFilter())
    logger = logging.getLogger("tests.structured_logging")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)

    def read_records() -> list[dict]:
        return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]

    yield logger, read_records
    logger.handlers = []


def _log_from_named_caller(logger):
    log_with_context(logger, logging.INFO, "from caller")


@pytest.mark.unit
def test_log_with_context_reports_caller_location(json_logger):
    logger, read_records = json_logger

    _log_from_named_caller(logger)

    record = read_records()[0]
    assert record["function"] == "_log_from_named_caller"
    assert record["file"].endswith("test_structured_logging.py")


@pytest.mark.unit
def test_error_inside_except_carries_stack(json_logger):
    logger, read_records = json_logger

    try:
        raise RuntimeError("upstream exploded")
    except RuntimeError as exc:
        log_with_context(logger, logging.ERROR, "call failed", error=str(exc))

    record = read_records()[0]
    assert "Traceback" in record["exception"]
    assert "upstream exploded" in record["exception"]


@pytest.mark.unit
def test_warning_inside_except_has_no_stack_unless_requested(json_logger):
    logger, read_records = json_logger

    try:
        raise RuntimeError("expected")
    except RuntimeError:
        log_with_context(logger, logging.WARNING, "degraded")
        log_with_context(logger, logging.WARNING, "degraded with stack", exc_info=True)

    first, second = read_records()
    assert "exception" not in first
    assert "Traceback" in second["exception"]


@pytest.mark.unit
def test_plain_logger_extra_fields_and_request_context_are_kept(json_logger):
    logger, read_records = json_logger
    tokens = bind_request_context(request_id="req12345", trace_id="trace-1")
    try:
        logger.warning("plain call", extra={"project_id": "p1", "error": "boom"})
    finally:
        reset_request_context(tokens)

    record = read_records()[0]
    assert record["project_id"] == "p1"
    assert record["error"] == "boom"
    assert record["request_id"] == "req12345"
    assert record["trace_id"] == "trace-1"


@pytest.mark.unit
def test_timestamp_is_valid_utc_iso8601(json_logger):
    logger, read_records = json_logger

    logger.info("tick")

    timestamp = read_records()[0]["timestamp"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", timestamp)


@pytest.mark.unit
def test_emails_are_masked_and_prompt_previews_dropped(json_logger):
    logger, read_records = json_logger

    log_with_context(
        logger,
        logging.INFO,
        "Verification email sent to alice@example.com",
        email="alice@example.com",
        message_preview="第一章：她推开门，看见",
        nested={"to": ["bob@example.org"]},
    )

    record = read_records()[0]
    raw = json.dumps(record, ensure_ascii=False)
    assert "alice@example.com" not in raw
    assert "bob@example.org" not in raw
    assert record["email"] == "a***@example.com"
    assert record["message"] == "Verification email sent to a***@example.com"
    assert record["nested"] == {"to": ["b***@example.org"]}
    assert "她推开门" not in raw
    assert record["message_preview"] == "[redacted 11 chars]"


@pytest.mark.unit
def test_search_queries_and_output_snippets_are_dropped(json_logger):
    logger, read_records = json_logger

    log_with_context(
        logger,
        logging.INFO,
        "Context assembly started",
        query="我的主角秘密是她其实是公主 alice@example.com",
        original_snippet="<file>第一章：她推开门</file>",
    )

    record = read_records()[0]
    raw = json.dumps(record, ensure_ascii=False)
    assert "主角秘密" not in raw
    assert "她推开门" not in raw
    assert record["query"] == "[redacted 31 chars]"
    assert record["original_snippet"].startswith("[redacted ")


@pytest.mark.unit
def test_mask_email_leaves_plain_text_alone():
    assert mask_email("no address here") == "no address here"


@pytest.mark.unit
def test_sanitize_query_params_redacts_credentials_and_masks_emails():
    rendered = sanitize_query_params(
        [
            ("token", "eyJhbGciOi.payload.sig"),
            ("code", "4/0Abc"),
            ("state", "nonce"),
            ("email", "carol@example.com"),
            ("page", "2"),
        ]
    )

    assert "eyJ" not in rendered
    assert "4/0Abc" not in rendered
    assert "carol@example.com" not in rendered
    assert "token=[redacted]" in rendered
    assert "email=c***@example.com" in rendered
    assert "page=2" in rendered
