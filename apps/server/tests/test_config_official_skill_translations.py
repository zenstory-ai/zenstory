"""English text for official skills: unknown official skills stay readable and are reported once."""

import pytest

from config import official_skill_translations as translations
from config.official_skill_translations import localized_skill_text

pytestmark = pytest.mark.unit


def test_unknown_official_skill_keeps_its_text_and_is_reported_once(monkeypatch):
    warnings: list[dict] = []
    monkeypatch.setattr(translations, "_reported_missing", set())
    monkeypatch.setattr(
        translations, "log_with_context", lambda _logger, _level, _message, **context: warnings.append(context)
    )

    for _ in range(2):
        assert localized_skill_text(
            source="official", name="新上架技能", description="说明", language="en"
        ) == ("新上架技能", "说明")

    assert warnings == [{"skill_name": "新上架技能"}]
    # Chinese readers and community skills are never translated, so they are never reported.
    localized_skill_text(source="official", name="另一个新技能", description=None, language="zh")
    localized_skill_text(source="community", name="社区技能", description=None, language="en")
    assert len(warnings) == 1
