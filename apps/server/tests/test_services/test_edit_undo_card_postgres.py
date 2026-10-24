"""PostgreSQL：同一条助手消息里两张修改卡同时撤销成功，两张卡的「已撤销」都要留下。

两次撤销各自读、改、写整份 tool_calls；不在行锁下重读，后提交的一方会把先提交的标记覆盖掉
（独立审查在本地 PostgreSQL 复现：两次都返回 True，落库却是 [True, None]）。合成数据。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel

import database
from models import ChatMessage, ChatSession, Project, User
from services.chat_tool_cards import record_edit_card_undone

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"), reason="explicitly owned PostgreSQL URL required",
)
TABLES = [User.__table__, Project.__table__, ChatSession.__table__, ChatMessage.__table__]
TOKEN = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def undo_pg_engine():
    url = os.environ["ZENSTORY_TEST_POSTGRES_URL"]
    assert url == os.environ["DATABASE_URL"]
    assert database.is_postgres is True
    engine = create_engine(url, connect_args={
        "options": "-c timezone=UTC -c statement_timeout=15000 -c lock_timeout=10000",
    })
    assert engine.dialect.name == "postgresql"
    SQLModel.metadata.create_all(engine, tables=TABLES)
    try:
        yield engine
    finally:
        SQLModel.metadata.drop_all(engine, tables=TABLES)
        engine.dispose()


def _card(call_id: str, file_id: str) -> dict:
    return {
        "id": call_id,
        "name": "edit_file",
        "arguments": {"id": file_id},
        "status": "success",
        "result": {
            "id": file_id,
            "undo": {"before_version_number": 1, "expected_after_updated_at": TOKEN.isoformat()},
        },
    }


def _seed(engine) -> dict[str, str]:
    with Session(engine) as seed:
        user = User(username="undo-pg", email="undo-pg@example.test", hashed_password="unused")
        seed.add(user)
        seed.flush()
        project = Project(name="Undo PG", owner_id=user.id)
        seed.add(project)
        seed.flush()
        chat = ChatSession(user_id=user.id, project_id=project.id)
        seed.add(chat)
        seed.flush()
        message = ChatMessage(
            session_id=chat.id,
            role="assistant",
            content="两处都改好了。",
            tool_calls=json.dumps([_card("call-a", "file-a"), _card("call-b", "file-b")]),
        )
        seed.add(message)
        seed.commit()
        return {"user": user.id, "project": project.id, "message": message.id}


def test_two_concurrent_undos_on_one_message_keep_both_cards_undone(undo_pg_engine):
    ids = _seed(undo_pg_engine)
    # 两边都先读到旧消息、再去改：最容易丢标记的交错。
    barrier = threading.Barrier(2)

    class RacingSession(Session):
        def get(self, entity, ident, **kwargs):
            value = super().get(entity, ident, **kwargs)
            if entity is ChatSession:
                barrier.wait(timeout=5)
            return value

    results: list[bool] = []
    errors: list[BaseException] = []

    def undo(call_id: str, file_id: str) -> None:
        try:
            with RacingSession(undo_pg_engine) as session:
                results.append(record_edit_card_undone(
                    session,
                    user_id=ids["user"],
                    project_id=ids["project"],
                    file_id=file_id,
                    message_id=ids["message"],
                    tool_use_id=call_id,
                    before_version_number=1,
                    expected_after_updated_at=TOKEN,
                ))
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [
        threading.Thread(target=undo, args=("call-a", "file-a")),
        threading.Thread(target=undo, args=("call-b", "file-b")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert errors == []
    assert results == [True, True]
    with Session(undo_pg_engine) as reader:
        stored = json.loads(reader.get(ChatMessage, ids["message"]).tool_calls)
        assert reader.get(ChatMessage, ids["message"]).content == "两处都改好了。"
    assert [card["result"]["undo"].get("undone") for card in stored] == [True, True]
    with undo_pg_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM chat_message")).scalar_one() == 1
