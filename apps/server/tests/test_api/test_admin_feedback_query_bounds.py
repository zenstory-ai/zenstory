"""Cold authenticated pagination must materialize only the requested feedback page."""

import json
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event
from sqlmodel import Session, SQLModel

from database import get_session
from main import app
from models import User, UserFeedback
from services.core.auth_service import create_access_token


@pytest.mark.asyncio
@pytest.mark.parametrize("row_count", [32, 256])
async def test_feedback_page_bounds_real_selected_rows(tmp_path, monkeypatch, record_property, row_count):
    path = tmp_path / "feedback-bounds.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    user_id = uuid4().hex
    feedback_ids = [uuid4().hex for _ in range(row_count)]
    loaded, sql, observations = [], [], []

    def request_session():
        with Session(engine) as session:
            assert not session.identity_map and session.expire_on_commit
            yield session

    def on_load(session, instance):
        if session.get_bind() is engine and isinstance(instance, UserFeedback):
            loaded.append(instance.id)

    def before_sql(_connection, _cursor, statement, _parameters, _context, _many):
        sql.append(statement)

    try:
        SQLModel.metadata.create_all(engine)
        with Session(engine) as seed:
            seed.add(User(id=user_id, username="feedback-" + user_id,
                          email=user_id + "@example.test", hashed_password="unused",
                          email_verified=True, is_active=True, is_superuser=True))
            seed.flush()
            seed.add_all([UserFeedback(
                id=feedback_ids[i], user_id=user_id, issue_text=f"issue-{i}",
                source_page="editor" if i % 2 else "dashboard",
                status="resolved" if i % 3 == 0 else "open",
                created_at=datetime(2026, 1, 1) + timedelta(seconds=i),
            ) for i in range(row_count)])
            seed.commit()
        event.listen(Session, "loaded_as_persistent", on_load)
        event.listen(engine, "before_cursor_execute", before_sql)
        with monkeypatch.context() as settings:
            settings.setitem(app.dependency_overrides, get_session, request_session)
            settings.setenv("FEEDBACK_UPLOAD_DIR", str(tmp_path / "screenshots"))
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                headers = {"Authorization": "Bearer " + create_access_token({"sub": user_id})}
                for skip in (0, 7, row_count - 3, row_count + 10):
                    loaded.clear()
                    sql.clear()
                    response = await client.get("/api/admin/feedback", headers=headers,
                                                params={"skip": skip, "limit": 5})
                    assert response.status_code == 200
                    body = response.json()
                    expected = list(reversed(feedback_ids))[skip:skip + 5]
                    assert body["total"] == row_count
                    assert [item["id"] for item in body["items"]] == expected
                    assert all(item["username"] == "feedback-" + user_id
                               and item["has_screenshot"] is False
                               and item["screenshot_download_url"] is None for item in body["items"])
                    observations.append({"skip": skip, "returned": len(expected),
                                         "selected_feedback_rows": len(loaded), "sql": list(sql)})
                loaded.clear()
                response = await client.get("/api/admin/feedback", headers=headers,
                                            params={"status": "resolved", "source_page": "dashboard",
                                                    "search": "issue-", "skip": 1, "limit": 5})
                assert response.status_code == 200
                expected = [feedback_ids[i] for i in reversed(range(row_count)) if i % 6 == 0]
                assert response.json()["total"] == len(expected)
                assert [item["id"] for item in response.json()["items"]] == expected[1:6]
                observations.append({"filters": "resolved/dashboard/issue-", "selected_feedback_rows": len(loaded),
                                     "returned": len(expected[1:6])})
        record_property("selected_row_measurement", json.dumps({"seed_rows": row_count,
                                                               "observations": observations}))
        # DTO/pagination/filter controls above pass even in the unbounded baseline.
        assert all(item["selected_feedback_rows"] == item["returned"] for item in observations)
    finally:
        if event.contains(Session, "loaded_as_persistent", on_load):
            event.remove(Session, "loaded_as_persistent", on_load)
        if event.contains(engine, "before_cursor_execute", before_sql):
            event.remove(engine, "before_cursor_execute", before_sql)
        engine.dispose()
        path.unlink(missing_ok=True)
        assert not path.exists()
