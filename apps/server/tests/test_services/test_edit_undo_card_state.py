"""撤销一张 AI 修改卡后，这张卡的当前状态随撤销真实结果更新（刷新后仍一致）。

撤销本身仍是既有的条件回滚（修改前版本 + 修改后版本 token）；这里只核对：成功后同一条助手
消息里那一张卡被标成已撤销，失败 / 冲突 / 作者后来改过 / 指错卡时不标、不覆盖新稿。合成数据。
"""

import json

import pytest
from sqlmodel import select

from api import files as files_api
from models import ChatMessage, ChatSession, File, FileVersion, Project, User
from tests.test_services.test_edit_undo import _edit, undo_file  # noqa: F401 - pytest fixture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

TOOL_USE_ID = "tool-edit-1"
REPLY = "第三场结尾的钩子已收紧，改动都在最后一段。"


def _card_message(db_session, user, project, file_id, undo, *, tool_use_id=TOOL_USE_ID, owner=None):
    chat = ChatSession(user_id=(owner or user).id, project_id=project.id)
    db_session.add(chat)
    db_session.commit()
    message = ChatMessage(
        session_id=chat.id,
        role="assistant",
        content=REPLY,
        tool_calls=json.dumps(
            [{
                "id": tool_use_id,
                "name": "edit_file",
                "arguments": {"id": file_id},
                "status": "success",
                "result": {"id": file_id, "title": "Draft", "details": [], "undo": undo},
                "error": None,
            }],
            ensure_ascii=False,
        ),
    )
    db_session.add(message)
    db_session.commit()
    return message.id


def _card_undo(db_session, message_id):
    db_session.expire_all()
    stored = db_session.get(ChatMessage, message_id)
    return stored.content, json.loads(stored.tool_calls)[0]["result"]["undo"]


async def _undo(client, file_id, undo, **ids):
    return await client.post(
        f"/api/v1/files/{file_id}/versions/{undo['before_version_number']}/rollback",
        json={"expected_updated_at": undo["expected_after_updated_at"], **ids},
    )


def _versions(db_session, file_id):
    return len(db_session.exec(select(FileVersion).where(FileVersion.file_id == file_id)).all())


async def test_successful_undo_marks_only_its_own_card_and_a_repeat_changes_nothing(client, db_session, undo_file):  # noqa: F811
    user, project, file, *_ = undo_file
    undo = _edit(db_session, user, file)["undo"]
    message_id = _card_message(db_session, user, project, file.id, undo)

    first = await _undo(client, file.id, undo, chat_message_id=message_id, tool_use_id=TOOL_USE_ID)

    assert first.status_code == 200 and first.json()["undo_recorded"] is True
    db_session.refresh(file)
    assert file.content == "Before\n"
    content, card = _card_undo(db_session, message_id)
    assert content == REPLY, "不改写助手原话"
    assert card["undone"] is True
    versions = _versions(db_session, file.id)

    again = await _undo(client, file.id, undo, chat_message_id=message_id, tool_use_id=TOOL_USE_ID)

    assert again.status_code == 409
    db_session.refresh(file)
    assert file.content == "Before\n" and _versions(db_session, file.id) == versions
    assert _card_undo(db_session, message_id)[1]["undone"] is True


async def test_author_edit_after_the_ai_edit_conflicts_and_the_card_is_not_marked(client, db_session, undo_file):  # noqa: F811
    user, project, file, *_ = undo_file
    undo = _edit(db_session, user, file)["undo"]
    message_id = _card_message(db_session, user, project, file.id, undo)
    files_api.update_file(
        file.id, files_api.FileUpdate(content="Before\nAI\n作者后来补的一句\n"), current_user=user, session=db_session
    )

    response = await _undo(client, file.id, undo, chat_message_id=message_id, tool_use_id=TOOL_USE_ID)

    assert response.status_code == 409
    db_session.refresh(file)
    assert file.content == "Before\nAI\n作者后来补的一句\n", "不覆盖作者的新稿"
    assert "undone" not in _card_undo(db_session, message_id)[1]


@pytest.mark.parametrize("case", ["other_tool_call", "other_users_message", "other_edit_token", "no_ids"])
async def test_undo_never_marks_a_card_it_cannot_prove_it_came_from(client, db_session, undo_file, case):  # noqa: F811
    user, project, file, *_ = undo_file
    undo = _edit(db_session, user, file)["undo"]
    stranger = User(username="undo-stranger", email="undo-stranger@example.test", hashed_password="unused")
    db_session.add(stranger)
    db_session.commit()
    card_undo = dict(undo)
    if case == "other_edit_token":
        card_undo["expected_after_updated_at"] = "2035-01-02T00:00:00+00:00"
    message_id = _card_message(
        db_session, user, project, file.id, card_undo, owner=stranger if case == "other_users_message" else None
    )
    ids = {} if case == "no_ids" else {
        "chat_message_id": message_id,
        "tool_use_id": "another-call" if case == "other_tool_call" else TOOL_USE_ID,
    }

    response = await _undo(client, file.id, undo, **ids)

    # 撤销本身是作者对自己文件的既有操作，照常生效；只是不能借这次撤销去标别的卡。
    assert response.status_code == 200 and response.json()["undo_recorded"] is False
    db_session.refresh(file)
    assert file.content == "Before\n"
    assert "undone" not in _card_undo(db_session, message_id)[1]


async def test_card_in_another_project_is_not_marked(client, db_session, undo_file):  # noqa: F811
    user, _project, file, *_ = undo_file
    undo = _edit(db_session, user, file)["undo"]
    other = Project(name="Other work", owner_id=user.id)
    db_session.add(other)
    db_session.commit()
    message_id = _card_message(db_session, user, other, file.id, undo)

    response = await _undo(client, file.id, undo, chat_message_id=message_id, tool_use_id=TOOL_USE_ID)

    assert response.status_code == 200 and response.json()["undo_recorded"] is False
    assert "undone" not in _card_undo(db_session, message_id)[1]
    assert db_session.get(File, file.id) is not None
