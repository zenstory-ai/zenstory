"""Permission policy locked before sync extraction; no access/session mocks."""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session

from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import Project, User
from utils import permission


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["async", "sync"])
@pytest.mark.parametrize("policy,expected", [
    ("owner", None), ("superuser", None), ("foreign", 403), ("missing", 403), ("deleted", 404),
])
async def test_permission_policy_compatibility(tmp_path, policy, expected, mode):
    path = tmp_path / "permission.db"
    engine = create_engine(f"sqlite:///{path}")
    try:
        User.__table__.create(engine)
        Project.__table__.create(engine)
        ids = [uuid4().hex for _ in range(3)]
        with Session(engine) as seed:
            users = [User(id=id_, username="permission-" + id_, email=id_ + "@example.test",
                          hashed_password="unused-local-hash", is_active=True,
                          is_superuser=(index == 1 and policy == "superuser"))
                     for index, id_ in enumerate(ids[:2])]
            seed.add_all(users)
            seed.add(Project(id=ids[2], owner_id=ids[0], name="Permission fixture",
                             is_deleted=policy == "deleted"))
            seed.commit()
        with Session(engine) as session:
            user = session.get(User, ids[0] if policy in ("owner", "deleted", "missing") else ids[1])
            project_id = "absent-owned-fixture" if policy == "missing" else ids[2]
            async def verify():
                if mode == "sync":
                    return permission.verify_project_access_sync(project_id, session, user)
                return await permission.verify_project_access(project_id, session, user)

            if expected is None:
                result = await verify()
                assert result.id == ids[2]
                assert result is session.get(Project, ids[2])
            else:
                with pytest.raises(APIException) as error:
                    await verify()
                assert error.value.status_code == expected
                assert error.value.error_code == (ErrorCode.PROJECT_NOT_FOUND if expected == 404
                                                  else ErrorCode.NOT_AUTHORIZED)
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)
        assert not path.exists()
