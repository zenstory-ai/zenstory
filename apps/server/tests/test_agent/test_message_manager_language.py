"""回复语言由模型按作者最近一条消息自主判断，界面语言只在判断不了时兜底，不再用正则猜测注入。"""

import pytest

from agent.core import message_manager
from agent.core.message_manager import MessageManager
from agent.loop.turn_context import build_loop_turn_context
from models import Project, User

pytestmark = pytest.mark.usefixtures("writing_prompt_configs")


def _create_project_and_manager(db_session) -> tuple[Project, MessageManager]:
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
    return project, MessageManager(project_id=project.id, user_id=owner.id)


@pytest.mark.unit
def test_regex_author_language_heuristic_removed():
    """Author reply language must be decided by the model directly, never by regex/character counting."""
    assert not hasattr(message_manager, "detect_author_language")
    assert not hasattr(MessageManager, "_build_author_language_line")


@pytest.mark.unit
def test_english_ui_system_prompt_supports_any_author_language_without_forcing_english(db_session):
    _, manager = _create_project_and_manager(db_session)
    prompt = manager.build_system_prompt(session=db_session, language="en")
    assert "Reply in English even if the user writes in Chinese" not in prompt
    assert "exact language of the author's latest message" in prompt
    assert "any other language" in prompt
    assert "fall back to English" in prompt


@pytest.mark.unit
def test_chinese_ui_system_prompt_supports_any_author_language(db_session):
    _, manager = _create_project_and_manager(db_session)
    prompt = manager.build_system_prompt(session=db_session, language="zh")
    assert "作者最近一条消息的语言" in prompt
    assert "其他任何语言" in prompt
    assert "就用中文（作者的界面语言）" in prompt


@pytest.mark.unit
@pytest.mark.parametrize(
    "author_message",
    [
        "Hãy viết tiếp chương 2 với không khí hồi hộp và tiết tấu chậm hơn.",
        "Écris le début du chapitre 2 dans une ambiance pluvieuse.",
        "Write the opening scene of a short story about a night-shift nurse.",
        "接着写第2章",
        "B",
    ],
)
def test_turn_context_does_not_inject_heuristic_language_override(db_session, author_message):
    """Vietnamese, French, English, Chinese, and ambiguous turns must never get a regex-guessed
    language directive in <turn_context>."""
    project, _ = _create_project_and_manager(db_session)
    turn_ctx = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=project.owner_id,
        metadata={},
        language="zh",
        author_message=author_message,
    )
    assert "wrote this turn's message in English" not in turn_ctx.text
    assert "本轮作者用中文写消息" not in turn_ctx.text
    assert "Reply language for this turn" not in turn_ctx.text
    assert "本轮回复语言" not in turn_ctx.text
