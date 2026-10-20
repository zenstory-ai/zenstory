"""回复语言跟随作者最近一条消息，界面语言只在判断不了时兜底。"""

import pytest

from agent.core.message_manager import MessageManager, detect_author_language
from models import Project, User

pytestmark = pytest.mark.usefixtures("writing_prompt_configs")


def _create_manager(db_session) -> MessageManager:
    owner = User(
        email="message-manager-lang@example.com",
        username="message_manager_lang",
        hashed_password="hashed_password",
        email_verified=True,
        is_active=True,
    )
    db_session.add(owner)
    db_session.commit()
    db_session.refresh(owner)
    project = Project(name="Language Project", owner_id=owner.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return MessageManager(project_id=project.id, user_id=owner.id)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("接着写第2章", "zh"),
        ("好", "zh"),
        ("把 POV 改成第一人称", "zh"),
        ("Write chapter 1 from the outline", "en"),
        ("Write chapter 3 where 苏晚 meets 沈砚", "en"),
        ("B2", None),
        ("https://example.com/a", None),
        ("", None),
    ],
)
def test_detect_author_language(text, expected):
    assert detect_author_language(text) == expected


@pytest.mark.unit
def test_english_ui_with_chinese_message_does_not_force_english(db_session):
    """英文界面、作者这一轮写中文：不再要求「作者写中文也用英文回」，并在本轮上下文写明本轮用中文。"""
    manager = _create_manager(db_session)
    prompt = manager.build_system_prompt(session=db_session, language="en")
    turn_context = manager.build_turn_context(language="en", author_message="接着写第2章")
    assert "Reply in English even if the user writes in Chinese" not in prompt
    assert "本轮作者用中文写消息" in turn_context


@pytest.mark.unit
def test_chinese_ui_with_english_message_states_english_for_every_role(db_session):
    """中文界面、英文作者：本轮上下文写明本轮用英文，审稿等每个角色共用这条。"""
    manager = _create_manager(db_session)
    turn_context = manager.build_turn_context(
        language="zh",
        author_message="Write the opening scene of a short story about a night-shift nurse.",
    )
    assert "wrote this turn's message in English" in turn_context
    assert "including the review summary" in turn_context


@pytest.mark.unit
def test_unclear_message_falls_back_to_ui_language_rule(db_session):
    manager = _create_manager(db_session)
    prompt = manager.build_system_prompt(session=db_session, language="en")
    turn_context = manager.build_turn_context(language="en", author_message="B")
    assert "wrote this turn's message" not in turn_context
    assert "本轮作者用中文写消息" not in turn_context
    assert "fall back to English" in prompt
