"""AI 写入的净字数按北京自然日计入项目统计（只在读侧从 file_version 计算）。"""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import Session

import api.stats as stats_api
from models import File, Project, User
from models.file_version import FileVersion
from services.core.auth_service import hash_password
from services.features.file_version_service import FileVersionService
from services.features.writing_stats_service import writing_stats_service

TODAY = date(2026, 10, 10)  # Saturday; week starts Monday 10-05, month starts 10-01.


def _project(db_session: Session, owner: User | None = None) -> Project:
    if owner is None:
        owner = User(
            username=f"aiw_{uuid4().hex[:8]}",
            email=f"aiw_{uuid4().hex[:8]}@example.com",
            hashed_password="x",
        )
        db_session.add(owner)
        db_session.commit()
    project = Project(name="AI 字数", owner_id=owner.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    return project


def _file(db_session: Session, project: Project, file_type: str = "draft", title: str = "第1章") -> File:
    file = File(project_id=project.id, title=title, file_type=file_type, content="", order=0)
    db_session.add(file)
    db_session.commit()
    return file


def _version(
    db_session: Session,
    file: File,
    words: int,
    source: str,
    at_utc: datetime,
    change_type: str | None = None,
) -> FileVersion:
    """One saved version of ``words`` CJK characters, stamped at a naive-UTC time."""
    version = FileVersionService().create_version(
        db_session,
        file.id,
        "字" * words,
        change_type=change_type or ("create" if source == "ai" else "edit"),
        change_source=source,
    )
    version.created_at = at_utc
    db_session.add(version)
    db_session.commit()
    return version


def _ai_words(db_session: Session, project: Project, today: date = TODAY) -> dict[str, int]:
    return writing_stats_service.get_ai_words_written(db_session, project.id, today)


def test_ai_create_and_edit_versions_count_positive_deltas(db_session):
    project = _project(db_session)
    chapter = _file(db_session, project)
    _version(db_session, chapter, 500, "ai", datetime(2026, 10, 10, 2, 0))
    _version(db_session, chapter, 800, "ai", datetime(2026, 10, 10, 3, 0))

    assert _ai_words(db_session, project) == {"today": 800, "this_week": 800, "this_month": 800}


def test_author_versions_are_baseline_not_ai_words(db_session):
    project = _project(db_session)
    chapter = _file(db_session, project)
    _version(db_session, chapter, 300, "ai", datetime(2026, 10, 10, 1, 0))
    # The author typed 200 more (counted by the editor's /stats/record, not here)...
    _version(db_session, chapter, 500, "user", datetime(2026, 10, 10, 2, 0))
    # ...then the AI extended the chapter by 100.
    _version(db_session, chapter, 600, "ai", datetime(2026, 10, 10, 3, 0))

    assert _ai_words(db_session, project)["today"] == 400


def test_ai_shrink_is_net_and_a_period_floors_at_zero(db_session):
    project = _project(db_session)
    chapter = _file(db_session, project)
    _version(db_session, chapter, 1000, "ai", datetime(2026, 10, 10, 1, 0))
    _version(db_session, chapter, 400, "ai", datetime(2026, 10, 10, 2, 0))

    # Net, like the author's words: the chapter holds 400 AI words, not 1000.
    assert _ai_words(db_session, project)["today"] == 400

    shrunk = _project(db_session)
    earlier = _file(db_session, shrunk)
    _version(db_session, earlier, 1000, "ai", datetime(2026, 10, 1, 4, 0))
    _version(db_session, earlier, 400, "ai", datetime(2026, 10, 10, 2, 0))

    # A day with only a shortening shows 0, never a negative count.
    assert _ai_words(db_session, shrunk) == {"today": 0, "this_week": 0, "this_month": 400}


def test_accepted_ai_review_counts_as_ai_words(db_session):
    """Editor 「应用更改」 saves AI review text as ai_edit with source 'user'."""
    project = _project(db_session)
    chapter = _file(db_session, project)
    _version(db_session, chapter, 500, "ai", datetime(2026, 10, 10, 1, 0))
    _version(db_session, chapter, 524, "user", datetime(2026, 10, 10, 2, 0), change_type="ai_edit")

    assert _ai_words(db_session, project)["today"] == 524


def test_ai_edit_review_only_file_counts(db_session):
    """A file whose only AI version today is an accepted review still contributes."""
    project = _project(db_session)
    chapter = _file(db_session, project)
    _version(db_session, chapter, 300, "user", datetime(2026, 10, 10, 1, 0))
    _version(db_session, chapter, 320, "user", datetime(2026, 10, 10, 2, 0), change_type="ai_edit")

    assert _ai_words(db_session, project)["today"] == 20


def test_ai_words_of_a_new_project_match_its_manuscript(db_session):
    """Audit r4short: AI create, extend, accepted review, shorten, extend; plus a second file."""
    project = _project(db_session)
    chapter = _file(db_session, project, title="正文")
    _version(db_session, chapter, 6422, "ai", datetime(2026, 10, 10, 1, 0))
    _version(db_session, chapter, 8243, "ai", datetime(2026, 10, 10, 2, 0), change_type="edit")
    _version(db_session, chapter, 8267, "user", datetime(2026, 10, 10, 3, 0), change_type="ai_edit")
    _version(db_session, chapter, 8259, "ai", datetime(2026, 10, 10, 4, 0), change_type="edit")
    _version(db_session, chapter, 8338, "ai", datetime(2026, 10, 10, 5, 0), change_type="edit")
    extra = _file(db_session, project, title="番外")
    _version(db_session, extra, 3030, "ai", datetime(2026, 10, 10, 6, 0))

    # Equal to the two files' word counts: 今日字数 == 总字数 for a same-day project.
    assert _ai_words(db_session, project)["today"] == 8338 + 3030


def test_prev_version_before_period_is_the_baseline(db_session):
    project = _project(db_session)
    chapter = _file(db_session, project)
    # Beijing 10-09 (yesterday): AI wrote 1000; today it extended to 1500.
    _version(db_session, chapter, 1000, "ai", datetime(2026, 10, 9, 4, 0))
    _version(db_session, chapter, 1500, "ai", datetime(2026, 10, 10, 4, 0))

    assert _ai_words(db_session, project) == {"today": 500, "this_week": 1500, "this_month": 1500}


def test_beijing_day_boundary(db_session):
    project = _project(db_session)
    late = _file(db_session, project, title="第1章")
    early = _file(db_session, project, title="第2章")
    # 2026-10-09T16:30Z is 10-10 00:30 in Beijing; 15:59Z is still 10-09 23:59.
    _version(db_session, late, 70, "ai", datetime(2026, 10, 9, 16, 30))
    _version(db_session, early, 30, "ai", datetime(2026, 10, 9, 15, 59))

    assert _ai_words(db_session, project)["today"] == 70
    assert _ai_words(db_session, project, date(2026, 10, 9))["today"] == 30


def test_outlines_deleted_files_and_other_projects_are_excluded(db_session):
    project = _project(db_session)
    outline = _file(db_session, project, file_type="outline", title="总纲")
    _version(db_session, outline, 900, "ai", datetime(2026, 10, 10, 1, 0))
    deleted = _file(db_session, project, title="第9章")
    _version(db_session, deleted, 400, "ai", datetime(2026, 10, 10, 1, 0))
    deleted.is_deleted = True
    db_session.add(deleted)
    db_session.commit()
    script = _file(db_session, project, file_type="script", title="第1集")
    _version(db_session, script, 50, "ai", datetime(2026, 10, 10, 1, 0))
    other = _project(db_session)
    _version(db_session, _file(db_session, other), 999, "ai", datetime(2026, 10, 10, 1, 0))

    assert _ai_words(db_session, project)["today"] == 50


def test_week_and_month_windows(db_session):
    project = _project(db_session)
    chapter = _file(db_session, project)
    _version(db_session, chapter, 100, "ai", datetime(2026, 9, 30, 4, 0))  # last month
    _version(db_session, chapter, 300, "ai", datetime(2026, 10, 2, 4, 0))  # this month, last week
    _version(db_session, chapter, 600, "ai", datetime(2026, 10, 6, 4, 0))  # this week
    _version(db_session, chapter, 700, "ai", datetime(2026, 10, 11, 4, 0))  # after today: ignored

    assert _ai_words(db_session, project) == {"today": 0, "this_week": 300, "this_month": 500}


async def _login(client: AsyncClient, db_session: Session) -> tuple[str, User]:
    suffix = uuid4().hex[:8]
    user = User(
        username=f"aiw_{suffix}",
        email=f"aiw_{suffix}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    login = await client.post("/api/auth/login", data={"username": user.username, "password": "password123"})
    return login.json()["access_token"], user


@pytest.mark.integration
async def test_stats_returns_ai_words_and_defaults_to_beijing_date(client: AsyncClient, db_session, monkeypatch):
    token, user = await _login(client, db_session)
    project = _project(db_session, owner=user)
    chapter = _file(db_session, project)
    # Beijing 10-10 01:00; still 10-09 in UTC.
    _version(db_session, chapter, 1200, "ai", datetime(2026, 10, 9, 17, 0))
    monkeypatch.setattr(stats_api, "utcnow", lambda: datetime(2026, 10, 9, 20, 0, tzinfo=UTC))
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.get(f"/api/v1/projects/{project.id}/stats", headers=headers)
    recorded = await client.post(
        f"/api/v1/projects/{project.id}/stats/record",
        json={"word_count": 1230, "words_added": 30},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["words_today"] == 0
    assert body["ai_words_today"] == 1200
    assert body["ai_words_this_week"] == 1200
    assert body["ai_words_this_month"] == 1200
    assert recorded.status_code == 201
    assert recorded.json()["stats_date"] == "2026-10-10"
