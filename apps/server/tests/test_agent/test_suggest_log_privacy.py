"""建议服务的日志只留固定分类与长度，不记录模型回复原文或异常原文（合成哨兵，不调用模型）。"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.schemas.context import ContextData

SENTINEL = "SENTINEL-作品正文-7731"

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def service():
    from agent.suggest_service import SuggestService

    llm = MagicMock()
    llm.acomplete = AsyncMock()
    with patch("agent.suggest_service.get_llm_client", return_value=llm):
        svc = SuggestService()
    svc.context_assembler = MagicMock()
    svc.context_assembler.assemble.return_value = ContextData(items=[], context="合成上下文", token_estimate=4)
    return svc


def _suggest_log_text(caplog) -> str:
    rendered = []
    for record in caplog.records:
        if record.name != "agent.suggest_service":
            continue
        rendered.append(record.getMessage())
        rendered.append(json.dumps(getattr(record, "custom_fields", {}), ensure_ascii=False, default=str))
        if record.exc_info:
            rendered.append(logging.Formatter().formatException(record.exc_info))
    return "\n".join(rendered)


async def _generate(service, db_session):
    return await service.generate_suggestions(
        session=db_session,
        project_id="p",
        user_id=None,
        recent_messages=[{"role": "user", "content": "合成消息"}],
        count=3,
        language="zh",
    )


async def test_success_logs_length_not_the_model_reply(service, db_session, caplog):
    service.llm.acomplete.return_value = json.dumps(
        {"suggestions": [f"{SENTINEL} 一", f"{SENTINEL} 二", f"{SENTINEL} 三"]}, ensure_ascii=False
    )
    caplog.set_level(logging.DEBUG, logger="agent.suggest_service")

    suggestions = await _generate(service, db_session)

    assert len(suggestions) == 3
    text = _suggest_log_text(caplog)
    assert SENTINEL not in text
    assert "response_length" in text


async def test_parse_failure_logs_a_fixed_category_not_the_raw_reply(service, db_session, caplog):
    service.llm.acomplete.return_value = f"这不是 JSON：{SENTINEL}"
    caplog.set_level(logging.DEBUG, logger="agent.suggest_service")

    suggestions = await _generate(service, db_session)

    assert suggestions  # 照旧退回固定建议
    text = _suggest_log_text(caplog)
    assert SENTINEL not in text
    assert "parse_failed" in text


async def test_exception_logs_its_type_not_its_text(service, db_session, caplog):
    service.llm.acomplete.side_effect = RuntimeError(f"provider echoed {SENTINEL}")
    caplog.set_level(logging.DEBUG, logger="agent.suggest_service")

    suggestions = await _generate(service, db_session)

    assert suggestions
    text = _suggest_log_text(caplog)
    assert SENTINEL not in text
    assert "RuntimeError" in text
