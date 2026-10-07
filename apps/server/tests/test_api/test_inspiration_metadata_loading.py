"""Metadata routes should not materialize template snapshot bodies they never return."""

import json
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event
from sqlmodel import Session, SQLModel

from database import get_session
from main import app
from models import Inspiration, User
from services.core.auth_service import create_access_token
from services.inspiration_service import get_featured_inspirations, list_inspirations


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["list", "featured", "mine", "admin"])
async def test_metadata_route_omits_body_loading_preserving_detail(tmp_path, monkeypatch, record_property, route):
    path = tmp_path / "inspiration-metadata.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    user_id = uuid4().hex
    ids = [uuid4().hex for _ in range(8)]
    snapshot = json.dumps({"files": [{"title": "Unicode chapter", "file_type": "draft",
                                     "content": "界" * 32768}]}, ensure_ascii=False)
    loaded = []

    def request_session():
        with Session(engine) as session:
            assert not session.identity_map and session.expire_on_commit
            yield session

    def on_load(session, row):
        if session.get_bind() is engine and isinstance(row, Inspiration):
            body = row.__dict__.get("snapshot_data")
            loaded.append({"id": row.id, "body_loaded": body is not None,
                           "body_utf8_bytes": len(body.encode()) if body is not None else 0})

    try:
        SQLModel.metadata.create_all(engine)
        with Session(engine) as seed:
            seed.add(User(id=user_id, username="metadata-" + user_id,
                          email=user_id + "@example.test", hashed_password="unused",
                          email_verified=True, is_active=True, is_superuser=True))
            seed.flush()
            seed.add_all([Inspiration(id=ids[i], name=f"template-{i}", description="Description",
                          project_type="novel", source="community", author_id=user_id,
                          tags='["tag"]', snapshot_data=snapshot, status="approved" if i < 6 else "pending",
                          is_featured=True, sort_order=i, copy_count=i,
                          created_at=datetime(2026, 1, 1) + timedelta(seconds=i)) for i in range(8)])
            seed.commit()
        event.listen(Session, "loaded_as_persistent", on_load)
        with monkeypatch.context() as settings:
            settings.setitem(app.dependency_overrides, get_session, request_session)
            settings.setenv("INSPIRATIONS_ENABLED", "true")
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                headers = {"Authorization": "Bearer " + create_access_token({"sub": user_id})}
                if route == "list":
                    url, params, key, expected = "/api/v1/inspirations", {"page_size": 5}, "inspirations", list(reversed(ids[:6]))[:5]
                elif route == "featured":
                    url, params, key, expected = "/api/v1/inspirations/featured", {"limit": 5}, None, ids[:5]
                elif route == "mine":
                    url, params, key, expected = "/api/v1/inspirations/my-submissions", {"page_size": 5}, "items", list(reversed(ids))[:5]
                else:
                    url, params, key, expected = "/api/admin/inspirations", {"limit": 5}, "items", list(reversed(ids))[:5]
                response = await client.get(url, params=params, headers=headers)
                assert response.status_code == 200
                data = response.json()
                items = data if key is None else data[key]
                assert [item["id"] for item in items] == expected
                assert all(item["tags"] == ["tag"] and "snapshot_data" not in item for item in items)
                if key is not None:
                    assert data["total"] == (6 if route == "list" else 8)
                if route in ("list", "featured"):
                    assert all(item["author_id"] is None and item["original_project_id"] is None for item in items)
                metadata_loads = list(loaded)
                loaded.clear()
                detail = await client.get(f"/api/v1/inspirations/{ids[0]}")
                assert detail.status_code == 200
                assert detail.json()["file_preview"] == [{"title": "Unicode chapter", "file_type": "draft", "has_content": True}]
                assert len(loaded) == 1 and loaded[0]["body_utf8_bytes"] == len(snapshot.encode())
                detail_loads = list(loaded)
                if route in ("list", "featured"):
                    # Default callers retain full ORM loading, opt-in callers can still lazy-read the body.
                    with Session(engine) as direct:
                        rows = (list_inspirations(direct, page_size=5)[0] if route == "list" else
                                get_featured_inspirations(direct, limit=5))
                        assert len(rows) == 5 and all(row.__dict__["snapshot_data"] == snapshot for row in rows)
                    with Session(engine) as direct:
                        rows = (list_inspirations(direct, page_size=5, metadata_only=True)[0] if route == "list" else
                                get_featured_inspirations(direct, limit=5, metadata_only=True))
                        assert len(rows) == 5 and all("snapshot_data" not in row.__dict__ for row in rows)
                        assert rows[0].snapshot_data == snapshot
        record_property("body_loading", json.dumps({"route": route, "snapshot_bytes": len(snapshot.encode()),
                                                     "metadata_loads": metadata_loads, "detail_loads": detail_loads}))
        assert len(metadata_loads) == 5
        assert sum(item["body_utf8_bytes"] for item in metadata_loads) == 0
    finally:
        if event.contains(Session, "loaded_as_persistent", on_load):
            event.remove(Session, "loaded_as_persistent", on_load)
        engine.dispose()
        path.unlink(missing_ok=True)
        assert not path.exists()
