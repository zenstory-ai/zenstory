"""TXT 导出保留首段的全角空格缩进，只去掉开头空行（新用户审计 #33）。"""

import pytest
from sqlmodel import Session

from models import File, Project, User
from services.features.export_service import export_drafts_to_txt


@pytest.mark.unit
def test_export_keeps_first_paragraph_indent_and_drops_leading_blank_lines(db_session: Session):
    user = User(username="exp_indent", email="exp_indent@example.com", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.commit()
    project = Project(name="导出", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.add_all([
        File(
            project_id=project.id, title="第一章 雨夜", file_type="draft", order=1,
            content="\n  \n　　雨下了一夜。\n　　他没睡。\n\n\n",
        ),
        File(
            project_id=project.id, title="第二章 天亮", file_type="draft", order=2,
            content="　　天亮了。",
        ),
    ])
    db_session.commit()

    exported = export_drafts_to_txt(db_session, project.id)

    assert exported == (
        "第一章 雨夜\n\n　　雨下了一夜。\n　　他没睡。"
        "\n\n---\n\n"
        "第二章 天亮\n\n　　天亮了。"
    )
