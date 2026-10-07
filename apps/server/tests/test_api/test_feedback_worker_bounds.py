"""Actual feedback writes belong in FastAPI's sync worker, with bounded screenshot reads."""

import builtins
import json
from tempfile import SpooledTemporaryFile
from threading import get_ident
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event
from sqlmodel import Session, SQLModel, select

import api.feedback as feedback_api
from database import get_session
from main import app
from models import User, UserFeedback
from services.core.auth_service import create_access_token


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["text", "near-limit", "oversized", "sql-fault"])
async def test_actual_feedback_worker_and_read_budget(tmp_path, monkeypatch, record_property, kind):
    loop_thread = get_ident()
    path = tmp_path / "feedback-worker.db"
    upload_root = tmp_path / "uploads"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    user_id = uuid4().hex
    writes, reads, inserts = [], [], []
    original_read = SpooledTemporaryFile.read

    def request_session():
        with Session(engine) as session:
            assert not session.identity_map and session.expire_on_commit
            yield session

    def observed_open(filename, mode="r", *args, **kwargs):
        if mode == "xb":
            writes.append({"thread": get_ident(), "path": str(filename)})
        return builtins.open(filename, mode, *args, **kwargs)

    def observed_read(file, size=-1):
        result = original_read(file, size)
        reads.append({"requested": size, "returned": len(result), "thread": get_ident()})
        return result

    def before_sql(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().lower().startswith("insert into user_feedback"):
            inserts.append({"thread": get_ident(), "statement": statement})
            if kind == "sql-fault":
                raise RuntimeError("controlled real feedback INSERT failure")

    try:
        SQLModel.metadata.create_all(engine)
        with Session(engine) as seed:
            seed.add(User(id=user_id, username="worker-" + user_id,
                          email=user_id + "@example.test", hashed_password="unused",
                          email_verified=True, is_active=True))
            seed.commit()
        event.listen(engine, "before_cursor_execute", before_sql)
        with monkeypatch.context() as settings:
            settings.setitem(app.dependency_overrides, get_session, request_session)
            settings.setenv("FEEDBACK_UPLOAD_DIR", str(upload_root))
            settings.setattr(feedback_api, "open", observed_open, raising=False)
            settings.setattr(SpooledTemporaryFile, "read", observed_read)
            files = None
            if kind != "text":
                size = (feedback_api.MAX_SCREENSHOT_BYTES if kind == "near-limit" else
                        feedback_api.MAX_SCREENSHOT_BYTES + 1024 if kind == "oversized" else 128)
                data = b"\x89PNG\r\n\x1a\n" + b"x" * (size - 8)
                files = {"screenshot": ("screen.png", data, "image/png")}
            async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                                   base_url="http://test") as client:
                response = await client.post("/api/v1/feedback", files=files,
                    data={"issue_text": "  actual feedback  ", "source_page": "editor"},
                    headers={"Authorization": "Bearer " + create_access_token({"sub": user_id})})
            with Session(engine) as reader:
                rows = list(reader.exec(select(UserFeedback)).all())
            stored_files = list(upload_root.glob("*")) if upload_root.exists() else []
            expected_status = 400 if kind == "oversized" else 500 if kind == "sql-fault" else 200
            assert response.status_code == expected_status
            if expected_status == 200:
                assert len(rows) == 1 and rows[0].issue_text == "actual feedback"
                assert rows[0].source_page == "editor" and rows[0].user_id == user_id
                assert response.json()["id"] == rows[0].id
                assert len(stored_files) == int(kind != "text")
                if stored_files:
                    assert stored_files[0].read_bytes() == data
                    assert rows[0].screenshot_size_bytes == len(data)
            else:
                assert rows == [] and stored_files == []
        record_property("actual_worker_read_observations", json.dumps({"kind": kind,
            "loop_thread": loop_thread, "inserts": inserts, "writes": writes, "reads": reads}))
        # All response/content/rollback controls pass before these performance-budget assertions.
        assert all(item["thread"] != loop_thread for item in inserts + writes)
        assert all(0 <= item["requested"] <= feedback_api.MAX_SCREENSHOT_BYTES + 1
                   and item["returned"] <= feedback_api.MAX_SCREENSHOT_BYTES + 1 for item in reads)
    finally:
        if event.contains(engine, "before_cursor_execute", before_sql):
            event.remove(engine, "before_cursor_execute", before_sql)
        engine.dispose()
        path.unlink(missing_ok=True)
        for stored_file in upload_root.glob("*"):
            stored_file.unlink()
        if upload_root.exists():
            upload_root.rmdir()
        assert not path.exists() and not upload_root.exists()
