"""Actual suggest route, auth, context and provider prompt genre contract."""

import json
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from agent import suggest_service
from database import get_session
from main import app
from models import Project, User
from services.core.auth_service import create_access_token


@pytest.mark.asyncio
@pytest.mark.parametrize("project_type,header", [
    ("novel", "你是小说写作助手的建议生成器。"),
    ("short", "你是短篇小说写作助手的建议生成器。"),
    ("screenplay", "你是短剧剧本写作助手的建议生成器。"),
])
async def test_suggest_uses_authorized_project_genre(tmp_path, monkeypatch, record_property,
                                                   project_type, header):
    await _exercise(tmp_path, monkeypatch, record_property, project_type, header)


@pytest.mark.asyncio
async def test_suggest_foreign_project_never_calls_provider(tmp_path, monkeypatch, record_property):
    await _exercise(tmp_path, monkeypatch, record_property, "screenplay", None, foreign=True)


@pytest.mark.asyncio
async def test_suggest_unavailable_provider_preserves_fallback(tmp_path, monkeypatch, record_property):
    await _exercise(tmp_path, monkeypatch, record_property, "short", None, unavailable=True)


async def _exercise(tmp_path, monkeypatch, record_property, project_type, header,
                    *, foreign=False, unavailable=False):
    path = tmp_path / "suggest-genre.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    calls = []
    lifecycles = []
    user_id = uuid4().hex
    project_id = uuid4().hex

    class Provider:
        async def acomplete(self, **kwargs):
            calls.append(kwargs)
            return '{"suggestions":["推进当前剧情","完善人物动机","细化场景冲突"]}'

    def local_provider():
        if unavailable:
            raise ValueError("locally unavailable provider")
        return Provider()

    def request_session():
        session = Session(engine)
        lifecycle = {"cold": len(session.identity_map) == 0,
                     "expire_on_commit": session.expire_on_commit, "closed": False}
        lifecycles.append(lifecycle)
        try:
            yield session
        finally:
            session.close()
            lifecycle["closed"] = True

    try:
        SQLModel.metadata.create_all(engine)
        with Session(engine) as seed:
            user = User(id=user_id, username="genre-" + user_id,
                        email=user_id + "@example.test", hashed_password="unused-local-hash",
                        is_active=True, email_verified=True)
            seed.add(user)
            if foreign:
                owner_id = uuid4().hex
                seed.add(User(id=owner_id, username="owner-" + owner_id,
                              email=owner_id + "@example.test", hashed_password="unused-local-hash",
                              is_active=True, email_verified=True))
            else:
                owner_id = user_id
            seed.add(Project(id=project_id, owner_id=owner_id, name="Owned genre fixture",
                             project_type=project_type))
            seed.commit()
            before = seed.get(Project, project_id).model_dump(mode="json")
        with monkeypatch.context() as overrides:
            overrides.setitem(app.dependency_overrides, get_session, request_session)
            overrides.setattr(suggest_service, "get_llm_client", local_provider)
            overrides.setattr(suggest_service, "_service", None)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/v1/agent/suggest", json={
                    "project_id": project_id,
                    "recent_messages": [{"role": "user", "content": "继续规划"}], "count": 3,
                }, headers={"Authorization": "Bearer " + create_access_token({"sub": user_id}),
                            "Accept-Language": "zh-CN"})
        facts = {"project_type": project_type, "status": response.status_code,
                 "body": response.json(), "calls": calls, "sessions": lifecycles}
        record_property("suggest_genre", json.dumps(facts, ensure_ascii=False, sort_keys=True))
        assert lifecycles and all(row == {"cold": True, "expire_on_commit": True,
                                          "closed": True} for row in lifecycles)
        with Session(engine) as reader:
            assert reader.get(Project, project_id).model_dump(mode="json") == before
        if foreign:
            assert response.status_code == 403
            assert calls == []
        elif unavailable:
            assert response.status_code == 200
            assert calls == []
            assert response.json()["suggestions"] == [
                "写下一章的情节发展", "完善角色的人物动机", "设计一个情节转折点",
            ]
        else:
            assert response.status_code == 200
            assert response.json() == {"suggestions": ["推进当前剧情", "完善人物动机", "细化场景冲突"]}
            assert len(calls) == 1
            assert calls[0]["messages"][0]["content"].startswith(header), facts
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)
        assert not path.exists()
        record_property("owned_cleanup", "override restored; provider singleton restored; engine disposed; SQLite absent")
