"""Registered lists preserve their primary ordering and use IDs to resolve ties."""

import json
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from database import get_session
from main import app
from models import User, UserFeedback
from services.core.auth_service import create_access_token


@pytest.mark.asyncio
@pytest.mark.parametrize("tied", [True, False], ids=["equal-keys", "ordinary-order"])
@pytest.mark.parametrize(
    "resource", ["feedback", "feedback-with-image", "feedback-without-image"]
)
async def test_feedback_pages_are_total_and_repeatable(tmp_path, monkeypatch, record_property, resource, tied):
    path = tmp_path / "pagination.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    tag = uuid4().hex[:12]
    owner_id, other_id = tag + "-owner", tag + "-other"
    ids = [tag + "-" + suffix for suffix in ("a", "f", "b", "e", "c", "d")]
    stamp = datetime(2026, 6, 1, 12)
    statements = []

    def observe(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    def request_session():
        with Session(engine) as session:
            yield session

    event.listen(engine, "before_cursor_execute", observe)
    try:
        SQLModel.metadata.create_all(engine)
        with Session(engine) as seed:
            for key in [owner_id, other_id]:
                seed.add(User(id=key, username=key, email=key + "@example.test", hashed_password="unused", is_active=True,
                              is_superuser=key == owner_id, email_verified=True))
            seed.commit()
            screenshot = tmp_path / "screen.png"
            screenshot.write_bytes(b"ordinary-local-image")
            monkeypatch.setenv("FEEDBACK_UPLOAD_DIR", str(tmp_path))
            for index, key in enumerate(ids):
                created_at = stamp if tied else stamp + timedelta(minutes=index)
                seed.add(UserFeedback(id=key, user_id=owner_id, source_page="dashboard", status="open",
                                      issue_text="Parity " + key, trace_id="trace-" + key, created_at=created_at,
                                      screenshot_path=str(screenshot) if resource == "feedback-with-image" else None))
            seed.add(UserFeedback(id=tag + "-excluded", user_id=other_id, source_page="editor", status="resolved",
                                  issue_text="Other", created_at=stamp + timedelta(days=1)))
            seed.commit()
        with monkeypatch.context() as context:
            context.setitem(app.dependency_overrides, get_session, request_session)
            headers = {"Authorization": "Bearer " + create_access_token({"sub": owner_id})}
            route, field = "/api/admin/feedback", "items"
            filters = {"status": "open", "source_page": "dashboard", "search": "Parity"}
            if resource != "feedback":
                filters["has_screenshot"] = "true" if resource == "feedback-with-image" else "false"
            expected = sorted(ids, reverse=True) if tied else list(reversed(ids))
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                denied = await client.get(route, headers={"Authorization": "Bearer " + create_access_token({"sub": other_id})},
                                          params=filters)
                assert denied.status_code == 403
                observed = []
                for page in range(1, 4):
                    params = {**filters, "skip": (page - 1) * 2, "limit": 2}
                    first = await client.get(route, headers=headers, params=params)
                    repeat = await client.get(route, headers=headers, params=params)
                    assert first.status_code == repeat.status_code == 200
                    assert first.json() == repeat.json()
                    payload = first.json()
                    items = payload[field]
                    assert payload["total"] == 6
                    for item in items:
                        assert item["issue_text"] == "Parity " + item["id"]
                        assert item["trace_id"] == "trace-" + item["id"]
                        assert item["has_screenshot"] is (resource == "feedback-with-image")
                    observed.extend(item["id"] for item in items)
                record_property("actual_list_sql", json.dumps(statements))
                assert observed == expected
                assert len(set(observed)) == 6
                invalid = await client.get(route, headers=headers, params={"limit": 0, "page_size": 0})
                assert invalid.status_code == 422
    finally:
        event.remove(engine, "before_cursor_execute", observe)
        engine.dispose()
        path.unlink(missing_ok=True)
        assert not path.exists()
