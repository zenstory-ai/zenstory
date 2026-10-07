"""TXT assembly must not execute synchronous DB work on the event loop."""

import inspect
from threading import get_ident

from api.export import get_current_active_user
from main import app
from models import File, Project, User


async def test_export_handler_uses_fastapi_worker(client, db_session, monkeypatch):
    owner = User(username="export-worker", email="export-worker@example.test", hashed_password="unused", email_verified=True)
    project = Project(name="导出测试", owner_id=owner.id)
    file = File(project_id=project.id, title="第一章", content="正文", file_type="draft")
    db_session.add_all([owner, project, file])
    db_session.commit()
    monkeypatch.setitem(app.dependency_overrides, get_current_active_user, lambda: owner)
    route = next(route for route in app.routes if getattr(route, "path", None) == "/api/v1/projects/{project_id}/export/drafts")
    original = route.dependant.call
    loop_thread = get_ident()
    observed = []
    if inspect.iscoroutinefunction(original):
        async def observe(*args, **kwargs):
            observed.append(get_ident())
            return await original(*args, **kwargs)
    else:
        def observe(*args, **kwargs):
            observed.append(get_ident())
            return original(*args, **kwargs)
    monkeypatch.setattr(route.dependant, "call", observe)

    response = await client.get(f"/api/v1/projects/{project.id}/export/drafts")
    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert "正文" in response.content.decode("utf-8-sig")
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    assert len(observed) == 1
    assert observed[0] != loop_thread
