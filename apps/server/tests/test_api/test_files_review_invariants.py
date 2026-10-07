from io import BytesIO

import pytest
from fastapi import BackgroundTasks, UploadFile

from api.files import ReorderFilesRequest, reorder_files, upload_drafts
from core.error_handler import APIException
from models import File, Project, User


@pytest.fixture
def file_tree(db_session):
    user = User(username="review_files", email="review-files@example.test", hashed_password="unused", email_verified=True)
    project = Project(name="Review files", owner_id=user.id)
    folder = File(project_id=project.id, title="Folder", file_type="folder")
    root = File(project_id=project.id, title="Root", file_type="draft", order=5)
    nested = File(project_id=project.id, title="Nested", file_type="draft", parent_id=folder.id, order=7)
    db_session.add_all([user, project, folder, root, nested])
    db_session.commit()
    return user, project, folder, root, nested


@pytest.mark.parametrize("order", ["root-first", "nested-first", "duplicate"])
def test_reorder_rejects_mixed_parents_and_duplicate_ids(db_session, file_tree, order):
    user, project, _, root, nested = file_tree
    ids = {"root-first": [root.id, nested.id], "nested-first": [nested.id, root.id], "duplicate": [root.id, root.id]}[order]
    with pytest.raises(APIException) as exc:
        reorder_files(project.id, ReorderFilesRequest(ordered_ids=ids), current_user=user, session=db_session)
    assert exc.value.status_code == 400
    db_session.refresh(root)
    db_session.refresh(nested)
    assert (root.order, nested.order) == (5, 7)


def test_reorder_accepts_root_siblings(db_session, file_tree):
    user, project, _, root, _ = file_tree
    other = File(project_id=project.id, title="Other", file_type="draft", order=8)
    db_session.add(other)
    db_session.commit()
    result = reorder_files(project.id, ReorderFilesRequest(ordered_ids=[other.id, root.id]), current_user=user, session=db_session)
    assert result["count"] == 2
    assert (other.order, root.order) == (0, 1)


async def test_draft_upload_rejects_deleted_destination_folder(db_session, file_tree):
    user, project, folder, _, _ = file_tree
    folder.is_deleted = True
    db_session.add(folder)
    db_session.commit()
    with pytest.raises(APIException) as exc:
        upload_drafts(project.id, files=[UploadFile(filename="review.txt", file=BytesIO(b"Draft content"))], parent_id=folder.id, current_user=user, session=db_session, background_tasks=BackgroundTasks())
    assert exc.value.status_code == 400


async def test_draft_upload_indexes_under_authenticated_owner(db_session, file_tree):
    user, project, folder, _, _ = file_tree
    background = BackgroundTasks()
    upload_drafts(project.id, files=[UploadFile(filename="review.txt", file=BytesIO(b"Draft content"))], parent_id=folder.id, current_user=user, session=db_session, background_tasks=background)
    index_tasks = [task for task in background.tasks if task.func.__name__ == "schedule_index_upsert"]
    assert index_tasks
    assert all(task.kwargs.get("user_id") == user.id for task in index_tasks)
