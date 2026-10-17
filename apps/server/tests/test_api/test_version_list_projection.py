"""Version reads must hydrate only bodies required by their response."""

from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, inspect
from sqlmodel import Session, SQLModel, create_engine, select

from api.agent_api import _load_accessible_file
from api.versions import verify_file_ownership
from core.error_codes import ErrorCode
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


@pytest.mark.parametrize(
    ("route", "number"),
    [("metadata", 1), ("metadata", 12), ("content", 1), ("content", 12),
     ("compare", 12), ("latest", 12), ("agent", 1), ("agent", 12)],
)
async def test_single_version_reads_defer_only_the_ownership_body(version_projection_fixture, route, number):
    engine, user_id, file_id, key, _, metadata, live_content = version_projection_fixture
    file_selects, selects, sessions = [], [], []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        sql = " ".join(statement.split())
        if sql.startswith("SELECT"):
            selects.append(sql)
        if sql.startswith("SELECT") and "FROM file " in sql:
            file_selects.append(sql)

    def request_session():
        with Session(engine) as session:
            state = {"cold": len(session.identity_map) == 0, "closed": False}
            sessions.append(state)
            try:
                yield session
            finally:
                session.close()
                state["closed"] = True

    urls = {
        "metadata": f"/api/v1/versions/version-{number}",
        "content": f"/api/v1/files/{file_id}/versions/{number}/content",
        "compare": f"/api/v1/files/{file_id}/versions/compare?v1=1&v2={number}",
        "latest": f"/api/v1/files/{file_id}/versions/latest",
        "agent": f"/api/v1/agent/files/{file_id}/versions/{number}",
    }
    headers = {"X-Agent-API-Key": key} if route == "agent" else {
        "Authorization": "Bearer " + create_access_token({"sub": user_id})
    }
    expected = metadata[number - 1].copy()
    requested = f"\u3000\u3000第一行。\n第二行第{number}版🙂\n"
    if route == "content":
        expected = {field: expected[field] for field in ("file_id", "version_number", "word_count", "char_count", "created_at")}
        expected["content"] = requested
    elif route == "agent":
        expected["content"] = requested
    elif route == "compare":
        with Session(engine) as check:
            expected = FileVersionService().compare_versions(check, file_id, 1, number)
    event.listen(engine, "before_cursor_execute", capture)
    app.dependency_overrides[get_session] = request_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(urls[route], headers=headers)
    finally:
        app.dependency_overrides.pop(get_session, None)
        event.remove(engine, "before_cursor_execute", capture)

    assert response.status_code == 200, response.text
    assert response.json() == expected
    assert response.headers["content-type"] == "application/json"
    assert response.headers["content-length"] == str(len(response.content))
    assert sessions and all(state == {"cold": True, "closed": True} for state in sessions)
    with Session(engine) as check:
        assert check.get(File, file_id).content == live_content
        assert FileVersionService().get_content_at_version(check, file_id, number) == requested
    # Snapshot/delta contents and complete responses pass before the old code
    # fails this column contract. Exactly one file read also rules out lazy N+1.
    expected_selects = {"metadata": 4, "content": 5 if number == 1 else 7, "compare": 7,
                        "latest": 4, "agent": 7 if number == 1 else 9}
    assert len(selects) == expected_selects[route]
    assert len(file_selects) == 1
    assert "file.content" not in file_selects[0].split("FROM", 1)[0]


@pytest.mark.parametrize("route", ["metadata", "content", "compare", "latest", "agent"])
@pytest.mark.parametrize("denial", ["foreign_owner", "deleted_file", "deleted_project", "missing_version"])
async def test_single_version_reads_preserve_access_and_missing_errors(version_projection_fixture, route, denial):
    engine, user_id, file_id, key, _, _, _ = version_projection_fixture
    with Session(engine) as session:
        file = session.get(File, file_id)
        project = session.get(Project, file.project_id)
        if denial == "foreign_owner":
            session.add(User(id="other-owner", username="other-owner", email="other@example.test", hashed_password="unused"))
            project.owner_id = "other-owner"
            session.add(project)
        elif denial == "deleted_file":
            file.is_deleted = True
            session.add(file)
        elif denial == "deleted_project":
            project.is_deleted = True
            session.add(project)
        elif route == "latest":
            for version in session.exec(select(FileVersion).where(FileVersion.file_id == file_id)).all():
                session.delete(version)
        session.commit()

    number = 999 if denial == "missing_version" else 12
    urls = {
        "metadata": f"/api/v1/versions/version-{number}",
        "content": f"/api/v1/files/{file_id}/versions/{number}/content",
        "compare": f"/api/v1/files/{file_id}/versions/compare?v1=1&v2={number}",
        "latest": f"/api/v1/files/{file_id}/versions/latest",
        "agent": f"/api/v1/agent/files/{file_id}/versions/{number}",
    }
    headers = {"X-Agent-API-Key": key} if route == "agent" else {
        "Authorization": "Bearer " + create_access_token({"sub": user_id})
    }

    def request_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = request_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(urls[route], headers=headers)
    finally:
        app.dependency_overrides.pop(get_session, None)

    expected_status = 403 if denial == "foreign_owner" and route != "agent" else 404
    expected_code = ErrorCode.FILE_NOT_FOUND
    if denial == "missing_version":
        expected_code = ErrorCode.VERSION_NO_VERSIONS_FOUND if route == "latest" else ErrorCode.VERSION_NOT_FOUND
    elif denial == "foreign_owner" and route != "agent":
        expected_code = ErrorCode.NOT_AUTHORIZED
    elif denial == "deleted_project" and route != "agent":
        expected_code = ErrorCode.PROJECT_NOT_FOUND
    assert response.status_code == expected_status, response.text
    assert response.json()["error_code"] == expected_code


@pytest.mark.parametrize("restriction", ["write_only", "other_project"])
async def test_agent_version_detail_keeps_scope_and_allowlist_checks(version_projection_fixture, restriction):
    engine, user_id, file_id, key, _, _, _ = version_projection_fixture
    with Session(engine) as session:
        api_key = session.exec(select(AgentApiKey).where(AgentApiKey.user_id == user_id)).one()
        if restriction == "write_only":
            api_key.scopes = ["write"]
        else:
            api_key.project_ids = ["another-project"]
        session.add(api_key)
        session.commit()

    def request_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = request_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/agent/files/{file_id}/versions/12", headers={"X-Agent-API-Key": key}
            )
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert response.status_code == 403, response.text
