"""Real authenticated review and audit SQL must commit or roll back together."""

import json
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event
from sqlmodel import Session, SQLModel, select

from database import get_session
from main import app
from models import Inspiration, User
from models.subscription import AdminAuditLog
from services.core.auth_service import create_access_token
from services.inspiration_service import review_inspiration


@pytest.mark.asyncio
@pytest.mark.parametrize("approve", [True, False])
async def test_review_audit_failure_rolls_back_and_allows_retry(tmp_path, monkeypatch,
                                                             record_property, approve):
    await _review(tmp_path, monkeypatch, record_property, approve, audit_failure=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("approve", [True, False])
async def test_review_success_keeps_one_audit_and_status_commit(tmp_path, monkeypatch,
                                                              record_property, approve):
    await _review(tmp_path, monkeypatch, record_property, approve)


@pytest.mark.asyncio
async def test_non_admin_review_never_writes_status_or_audit(tmp_path, monkeypatch, record_property):
    await _review(tmp_path, monkeypatch, record_property, True, admin=False)


async def _review(tmp_path, monkeypatch, record_property, approve, *, audit_failure=False, admin=True):
    path = tmp_path / "review-audit.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    user_id, inspiration_id = uuid4().hex, uuid4().hex
    commits, sql, lifetimes = [], [], []
    injected = []

    def request_session():
        session = Session(engine)
        lifetime = {"cold": not session.identity_map, "expiring": session.expire_on_commit,
                    "closed": False}
        lifetimes.append(lifetime)
        try:
            yield session
        finally:
            session.close()
            lifetime["closed"] = True

    def after_commit(session):
        if session.get_bind() is engine:
            commits.append("request-commit")

    def before_sql(_connection, _cursor, statement, _parameters, _context, _many):
        sql.append(statement)
        if (audit_failure and not injected
                and statement.lstrip().lower().startswith("insert into admin_audit_log")):
            injected.append("real-audit-insert")
            raise RuntimeError("controlled audit INSERT failure")

    def read_state():
        with Session(engine) as reader:
            row = reader.get(Inspiration, inspiration_id)
            return {"status": row.status, "reviewed_by": row.reviewed_by,
                    "reviewed_at": row.reviewed_at, "reason": row.rejection_reason}, list(
                reader.exec(select(AdminAuditLog).where(AdminAuditLog.resource_id == inspiration_id)).all()
            )

    try:
        SQLModel.metadata.create_all(engine)
        with Session(engine) as seed:
            seed.add(User(id=user_id, username="review-" + user_id,
                          email=user_id + "@example.test", hashed_password="unused",
                          email_verified=True, is_active=True, is_superuser=admin))
            seed.flush()
            seed.add(Inspiration(id=inspiration_id, name="Pending atomic review",
                                 project_type="novel", status="pending", source="community",
                                 author_id=user_id, snapshot_data='{"files": []}', tags="[]"))
            seed.commit()
        event.listen(Session, "after_commit", after_commit)
        event.listen(engine, "before_cursor_execute", before_sql)
        with monkeypatch.context() as settings:
            settings.setenv("INSPIRATIONS_ENABLED", "true")
            settings.setitem(app.dependency_overrides, get_session, request_session)
            async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                                   base_url="http://test") as client:
                url = f"/api/admin/inspirations/{inspiration_id}/review"
                headers = {"Authorization": "Bearer " + create_access_token({"sub": user_id})}
                payload = {"approve": approve, "rejection_reason": "  revise the draft  "}
                response = await client.post(url, headers=headers, json=payload)
                after_first, audits = read_state()
                first_commits = len(commits)
                retry = None
                if audit_failure:
                    assert injected == ["real-audit-insert"]
                    assert response.status_code == 500
                    # Fault fires once; the next request uses the real healthy audit SQL.
                    retry = await client.post(url, headers=headers, json=payload)
                    record_property("review_atomicity", json.dumps({
                        "approve": approve, "first_status": response.status_code,
                        "durable_after_failure": {k: str(v) for k, v in after_first.items()},
                        "audits_after_failure": len(audits), "commits_after_failure": first_commits,
                        "retry_status": retry.status_code, "sql": sql,
                    }, ensure_ascii=False))
                    assert after_first == {"status": "pending", "reviewed_by": None,
                                           "reviewed_at": None, "reason": None}
                    assert audits == []
                    assert first_commits == 0
                    assert retry.status_code == 200
                elif not admin:
                    assert response.status_code == 403
                    assert after_first["status"] == "pending"
                    assert audits == [] and commits == []
                    return
                else:
                    assert response.status_code == 200
                final, audits = read_state()
                assert final["status"] == ("approved" if approve else "rejected")
                assert final["reviewed_by"] == user_id and final["reviewed_at"] is not None
                assert final["reason"] == (None if approve else "revise the draft")
                assert len(audits) == 1
                assert audits[0].action == ("approve_inspiration" if approve else "reject_inspiration")
                assert audits[0].old_value["status"] == "pending"
                assert audits[0].new_value["status"] == final["status"]
                # A healthy request commits the status and audit exactly once.
                assert len(commits) == 1
        assert lifetimes and all(item == {"cold": True, "expiring": True, "closed": True}
                                 for item in lifetimes)
    finally:
        if event.contains(Session, "after_commit", after_commit):
            event.remove(Session, "after_commit", after_commit)
        if event.contains(engine, "before_cursor_execute", before_sql):
            event.remove(engine, "before_cursor_execute", before_sql)
        engine.dispose()
        path.unlink(missing_ok=True)
        assert not path.exists()
        record_property("owned_cleanup", "listener/override restored, case SQLite absent, engine disposed")


@pytest.mark.parametrize("commit", [True, False])
def test_review_helper_preserves_default_commit_and_caller_rollback(tmp_path, caplog, commit):
    path = tmp_path / "review-helper.db"
    engine = create_engine(f"sqlite:///{path}")
    user_id, inspiration_id = uuid4().hex, uuid4().hex
    commits = []

    def after_commit(session):
        if session.get_bind() is engine:
            commits.append("commit")

    try:
        SQLModel.metadata.create_all(engine)
        with Session(engine) as seed:
            seed.add(User(id=user_id, username="helper-" + user_id,
                          email=user_id + "@example.test", hashed_password="unused",
                          email_verified=True, is_active=True, is_superuser=True))
            seed.flush()
            seed.add(Inspiration(id=inspiration_id, name="Helper review", project_type="novel",
                                 status="pending", source="community", author_id=user_id,
                                 snapshot_data='{"files": []}', tags="[]"))
            seed.commit()
        event.listen(Session, "after_commit", after_commit)
        with Session(engine) as session:
            row = session.get(Inspiration, inspiration_id)
            reviewer = session.get(User, user_id)
            with caplog.at_level("INFO", logger="services.inspiration_service"):
                if commit:
                    reviewed = review_inspiration(session, row, reviewer, True)
                else:
                    reviewed = review_inspiration(session, row, reviewer, True, commit=False)
            assert reviewed is row and row.status == "approved"
            assert row.reviewed_by == user_id and row.reviewed_at is not None
            assert len(commits) == int(commit)
            success_logs = [record.message for record in caplog.records
                            if "approved inspiration" in record.message]
            assert bool(success_logs) is commit
            session.rollback()
        with Session(engine) as reader:
            durable = reader.get(Inspiration, inspiration_id)
            assert durable.status == ("approved" if commit else "pending")
            assert (durable.reviewed_by is not None) is commit
            assert (durable.reviewed_at is not None) is commit
    finally:
        if event.contains(Session, "after_commit", after_commit):
            event.remove(Session, "after_commit", after_commit)
        engine.dispose()
        path.unlink(missing_ok=True)
        assert not path.exists()
