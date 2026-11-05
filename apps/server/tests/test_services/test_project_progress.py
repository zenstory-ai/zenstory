"""Project-card progress remains available after project statistics are retired."""

from uuid import uuid4

from models import File, Project, User
from services.project_progress import get_projects_progress


def test_progress_for_unknown_owner_is_empty(db_session):
    assert get_projects_progress(db_session, "missing-owner") == []


def test_screenplay_progress_counts_scripts_and_refreshes_stale_word_count(db_session):
    suffix = uuid4().hex[:8]
    user = User(
        username=f"progress_{suffix}",
        email=f"progress_{suffix}@example.com",
        hashed_password="unused",
    )
    project = Project(name="晚风知我意", owner_id=user.id, project_type="screenplay")
    db_session.add_all([user, project])
    db_session.commit()

    db_session.add_all(
        [
            File(
                project_id=project.id,
                title="分集大纲（全60集）",
                file_type="outline",
                content="第1集：三周年。第2集：离婚。" * 20,
            ),
            File(
                project_id=project.id,
                title="第1集",
                file_type="script",
                content="【场1】日，内，客厅。林晚把蛋糕放在桌上，手机亮了。" * 5,
                file_metadata='{"word_count": 1}',
            ),
            File(
                project_id=project.id,
                title="第2集",
                file_type="script",
                content="【场1】日，外，民政局。她把离婚协议递了过去。" * 5,
            ),
        ]
    )
    db_session.commit()

    progress = get_projects_progress(db_session, user.id, project.id)

    assert len(progress) == 1
    assert progress[0].written_units == 2
    assert progress[0].word_count > 2
    assert progress[0].framework_ready is False
