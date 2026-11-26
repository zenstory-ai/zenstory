"""回复语言由模型按作者最近一条消息自主判断，界面语言只在判断不了时兜底，不再用正则猜测注入。"""

import pytest

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
