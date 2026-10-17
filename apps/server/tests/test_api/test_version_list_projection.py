"""Metadata-only version lists must not hydrate every stored body/diff."""

from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, inspect
from sqlmodel import Session, SQLModel, create_engine, select

from api.agent_api import _load_accessible_file
from api.versions import verify_file_ownership
from database import get_session
from main import app
from models import AgentApiKey, File, FileVersion, Project, User
from services.agent_auth_service import generate_api_key, hash_api_key
from services.core.auth_service import create_access_token
from services.features.file_version_service import FileVersionService


@pytest.fixture
def version_projection_fixture(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'versions.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    service = FileVersionService()
    user_id, project_id, file_id = "projection-owner", "projection-project", "projection-file"
    plain_key = generate_api_key()
    stored, metadata, previous = {}, [], ""
    fixed = datetime(2026, 1, 1)
    with Session(engine) as session:
        session.add(User(id=user_id, username=user_id, email="projection@example.test", hashed_password="unused", email_verified=True))
        session.add(Project(id=project_id, name="版本投影", owner_id=user_id))
        for number in range(1, 13):
            text = f"\u3000\u3000第一行。\n第二行第{number}版🙂\n"
            is_base = number in (1, 10)
            stored[number] = text if is_base else service._create_diff(previous, text)
            row = FileVersion(
                id=f"version-{number}", file_id=file_id, project_id=project_id,
                version_number=number, content=stored[number], is_base_version=is_base,
                word_count=number, char_count=len(text),
                change_type="auto_save" if number == 6 else "edit",
                change_source="user", change_summary=f"修改{number}🙂",
                lines_added=number, lines_removed=number - 1,
                created_at=fixed + timedelta(minutes=number),
            )
            metadata.append(row.model_dump(mode="json", exclude={"content", "snapshot_id"}))
            session.add(row)
            previous = text
        session.add(File(id=file_id, project_id=project_id, title="正文🙂", file_type="draft", content=previous))
        session.add(AgentApiKey(user_id=user_id, key_prefix="eg_", key_hash=hash_api_key(plain_key), name="Local test", scopes=["read"], project_ids=[project_id]))
        session.commit()
    try:
        yield engine, user_id, file_id, plain_key, stored, metadata, previous
    finally:
        engine.dispose()


@pytest.mark.parametrize("route", ["web", "agent"])
@pytest.mark.parametrize("offset", [0, 2])
@pytest.mark.parametrize("include_auto_save", [False, True])
async def test_metadata_version_lists_omit_payload_without_n_plus_one(
    version_projection_fixture, route, offset, include_auto_save
):
    engine, user_id, file_id, key, _, metadata, live_content = version_projection_fixture
    selects, file_selects, sessions = [], [], []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        normal = " ".join(statement.split())
        if normal.startswith("SELECT") and "FROM file_version " in normal:
            selects.append(normal)
        elif normal.startswith("SELECT") and "FROM file " in normal:
            file_selects.append(normal)

    def request_session():
        with Session(engine) as session:
            state = {"cold": len(session.identity_map) == 0, "closed": False}
            sessions.append(state)
            try:
                yield session
            finally:
                session.close()
                state["closed"] = True

    url = f"/api/v1/{'agent/' if route == 'agent' else ''}files/{file_id}/versions"
    headers = {"X-Agent-API-Key": key} if route == "agent" else {
        "Authorization": "Bearer " + create_access_token({"sub": user_id})
    }
    event.listen(engine, "before_cursor_execute", capture)
    app.dependency_overrides[get_session] = request_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(url, headers=headers, params={
                "limit": 5, "offset": offset, "include_auto_save": str(include_auto_save).lower(),
            })
    finally:
        app.dependency_overrides.pop(get_session, None)
        event.remove(engine, "before_cursor_execute", capture)

    assert response.status_code == 200, response.text
    visible = [row for row in reversed(metadata) if include_auto_save or row["change_type"] != "auto_save"]
    expected = {"versions": visible[offset:offset + 5], "total": len(visible), "file_id": file_id, "file_title": "正文🙂"}
    if route == "web":
        expected["current_version_number"] = 12 if offset == 0 else None
    else:
        expected.update(limit=5, offset=offset)
    assert response.json() == expected
    assert sessions and all(state == {"cold": True, "closed": True} for state in sessions)

    listed = [sql for sql in selects if "ORDER BY file_version.version_number DESC" in sql and "LIMIT" in sql]
    assert len(listed) == 1
    assert "file_version.content" not in listed[0].split("FROM", 1)[0]
    # list + count. Web page 0 also reconstructs latest delta from its target,
    # nearest base and chain; no deferred-body SELECT per listed row.
    assert len(selects) == (5 if route == "web" and offset == 0 else 2)
    # Only the web's first page compares the current body with the latest version.
    # Metadata-only ownership reads must not load it, including via a lazy SELECT.
    assert len(file_selects) == 1
    assert ("file.content" in file_selects[0].split("FROM", 1)[0]) == (
        route == "web" and offset == 0
    )
    with Session(engine) as check:
        assert check.get(File, file_id).content == live_content
        assert FileVersionService().get_content_at_version(check, file_id, 12) == live_content


def test_default_service_keeps_full_content_available_after_session_close(version_projection_fixture):
    engine, _, file_id, _, stored, _, _ = version_projection_fixture
    with Session(engine) as session:
        versions = FileVersionService().get_versions(session, file_id, include_auto_save=True)
        assert all("content" not in inspect(row).unloaded for row in versions)
    assert {row.version_number: row.content for row in versions} == stored


@pytest.mark.parametrize("route", ["web", "agent", "agent_write"])
def test_default_ownership_helpers_keep_detached_body(version_projection_fixture, route):
    engine, user_id, file_id, _, _, _, live_content = version_projection_fixture
    with Session(engine) as session:
        if route == "web":
            file = verify_file_ownership(session, file_id, session.get(User, user_id))
        else:
            key = session.exec(select(AgentApiKey).where(AgentApiKey.user_id == user_id)).one()
            file = _load_accessible_file(
                session, user_id, key, file_id, for_write=route == "agent_write"
            )
        assert "content" not in inspect(file).unloaded
    assert file.content == live_content


@pytest.mark.parametrize("route", ["web", "agent"])
def test_metadata_option_reuses_already_loaded_file(version_projection_fixture, route):
    engine, user_id, file_id, _, _, _, live_content = version_projection_fixture
    file_selects = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        if "FROM file " in " ".join(statement.split()):
            file_selects.append(statement)

    with Session(engine) as session:
        loaded = session.get(File, file_id)
        event.listen(engine, "before_cursor_execute", capture)
        try:
            if route == "web":
                file = verify_file_ownership(
                    session, file_id, session.get(User, user_id), include_content=False
                )
            else:
                key = session.exec(select(AgentApiKey).where(AgentApiKey.user_id == user_id)).one()
                file = _load_accessible_file(session, user_id, key, file_id, include_content=False)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert file is loaded
        assert "content" not in inspect(file).unloaded
        assert not file_selects
    assert file.content == live_content
