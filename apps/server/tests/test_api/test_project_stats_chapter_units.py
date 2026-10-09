"""项目统计的章节进度：整本大纲不算章，正文/剧本都算，字数与作品卡片同一口径。"""

from uuid import uuid4

import pytest
from httpx import AsyncClient

from models import File, Project, User
from services.core.auth_service import hash_password
from services.features.writing_stats_service import writing_stats_service


async def _login(client: AsyncClient, db_session) -> tuple[str, User]:
    suffix = uuid4().hex[:8]
    user = User(
        username=f"units_{suffix}",
        email=f"units_{suffix}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    login = await client.post("/api/auth/login", data={"username": user.username, "password": "password123"})
    return login.json()["access_token"], user


def _add(db_session, project: Project, title: str, file_type: str, content: str, order: int) -> File:
    file = File(project_id=project.id, title=title, file_type=file_type, content=content, order=order)
    db_session.add(file)
    db_session.commit()
    return file


@pytest.mark.integration
async def test_drama_stats_count_episodes_not_book_outlines(client: AsyncClient, db_session):
    token, user = await _login(client, db_session)
    project = Project(name="晚风知我意", owner_id=user.id, project_type="screenplay")
    db_session.add(project)
    db_session.commit()
    # Same shape the audit saw: two whole-book outlines, then three episode scripts.
    _add(db_session, project, "核心大纲", "outline", "女主三周年纪念日撞破真相。" * 40, 0)
    _add(db_session, project, "分集大纲（全60集）", "outline", "第1集：三周年。第2集：离婚。" * 200, 1)
    episode_text = "【场1】日，内，客厅。林晚把蛋糕放在桌上，手机亮了。" * 20
    episodes = [
        _add(db_session, project, f"第{n}集", "script", episode_text, n - 1) for n in (1, 2, 3)
    ]
    # A stale cache from before AI writes refreshed it must not leak into the stats.
    episodes[0].set_metadata({"word_count": 1})
    db_session.add(episodes[0])
    db_session.commit()

    response = await client.get(
        f"/api/v1/projects/{project.id}/stats",
        headers={"Authorization": f"Bearer {token}"},
    )
    progress = await client.get(
        "/api/v1/projects/progress",
        params={"project_id": project.id},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["project_type"] == "screenplay"
    completion = body["chapter_completion"]
    assert completion["total_chapters"] == 3
    assert completion["completed_chapters"] == 3
    assert completion["completion_percentage"] == 100
    titles = [row["title"] for row in completion["chapter_details"]]
    assert titles == ["第1集", "第2集", "第3集"]
    assert all(row["draft_id"] for row in completion["chapter_details"])
    # Per-episode and total word counts agree with the project card.
    card = progress.json()[0]
    assert sum(row["word_count"] for row in completion["chapter_details"]) == card["word_count"]
    assert body["total_word_count"] == card["word_count"]
    assert card["written_units"] == completion["total_chapters"]


def test_numbered_outline_plans_a_chapter_and_unmatched_drafts_still_count(db_session):
    owner = User(
        username=f"units_{uuid4().hex[:8]}",
        email=f"units_{uuid4().hex[:8]}@example.com",
        hashed_password="x",
    )
    db_session.add(owner)
    db_session.commit()
    project = Project(name="长篇", owner_id=owner.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    _add(db_session, project, "故事大纲", "outline", "主线：外卖员看见倒计时。", 0)
    _add(db_session, project, "第1章 细纲", "outline", "开场。", 1)
    _add(db_session, project, "第2章 细纲", "outline", "冲突。", 2)
    _add(db_session, project, "第1章 倒计时", "draft", "他抬头看见数字在跳。" * 10, 1)
    # Written without an outline; previously ignored whenever outlines existed.
    _add(db_session, project, "第3章 追逐", "draft", "楼道里的脚步声越来越近。" * 10, 3)

    stats = writing_stats_service.get_chapter_completion_stats(db_session, project.id)

    rows = {row["title"]: row for row in stats["chapter_details"]}
    assert set(rows) == {"第1章 细纲", "第2章 细纲", "第3章 追逐"}
    assert rows["第1章 细纲"]["status"] == "complete"
    assert rows["第2章 细纲"]["draft_id"] is None
    assert rows["第2章 细纲"]["status"] == "not_started"
    assert rows["第3章 追逐"]["status"] == "complete"
    assert stats["total_chapters"] == 3
    assert stats["completed_chapters"] == 2


def _project(db_session, project_type: str) -> Project:
    owner = User(
        username=f"units_{uuid4().hex[:8]}",
        email=f"units_{uuid4().hex[:8]}@example.com",
        hashed_password="x",
    )
    db_session.add(owner)
    db_session.commit()
    project = Project(name="编号", owner_id=owner.id, project_type=project_type)
    db_session.add(project)
    db_session.commit()
    return project


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("第12章 第一节课", 12),
        ("第3章 第一回合", 3),
        ("第十二章 第1节", 12),
        ("第 7 集 重逢", 7),
        ("第一节课", 1),
        ("60集分集大纲", None),
        ("100章 总纲", None),
        ("60集故事梗概", None),
        ("12 章 大纲", None),
        ("8集全剧大纲", None),
        ("1集大纲", 1),
        ("3集大纲 重逢", 3),
        ("3 开端", 3),
        ("Episode 4", 4),
    ],
)
def test_chapter_number_takes_the_leading_ordinal_and_ignores_book_lengths(title, expected):
    assert writing_stats_service._extract_chapter_number(title) == expected


def test_chapter_title_with_inner_section_pairs_with_its_own_outline(db_session):
    project = _project(db_session, "novel")
    _add(db_session, project, "第1章 细纲", "outline", "开学。", 0)
    _add(db_session, project, "第12章 细纲", "outline", "第一节课。", 1)
    # Order deliberately crossed so only the chapter number can pair them right.
    chapter_12 = _add(db_session, project, "第12章 第一节课", "draft", "铃声响了。" * 10, 0)
    chapter_1 = _add(db_session, project, "第1章 开学", "draft", "他背着书包。" * 10, 1)

    stats = writing_stats_service.get_chapter_completion_stats(db_session, project.id)

    rows = {row["title"]: row for row in stats["chapter_details"]}
    assert rows["第1章 细纲"]["draft_id"] == chapter_1.id
    assert rows["第12章 细纲"]["draft_id"] == chapter_12.id
    assert stats["total_chapters"] == 2


def test_book_outline_titled_with_episode_count_is_not_an_episode(db_session):
    project = _project(db_session, "screenplay")
    _add(db_session, project, "60集分集大纲", "outline", "第1集：三周年。" * 50, 0)
    _add(db_session, project, "第1集", "script", "【场1】客厅。" * 10, 0)
    _add(db_session, project, "第2集", "script", "【场1】民政局。" * 10, 1)

    stats = writing_stats_service.get_chapter_completion_stats(db_session, project.id)

    assert [row["title"] for row in stats["chapter_details"]] == ["第1集", "第2集"]
    assert stats["total_chapters"] == 2
    assert all(row["draft_id"] for row in stats["chapter_details"])
