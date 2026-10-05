"""
Tests for standard skill package endpoints.

- POST /api/v1/skills/import
- GET /api/v1/skills/{id}/export
- GET/PUT/DELETE /api/v1/skills/{id}/resources, GET /api/v1/skills/{id}/resources/content
- resource copy on share, resource cleanup on delete
"""

import io
import json
import zipfile

import pytest
from httpx import AsyncClient
from sqlmodel import select

from agent.skills.package import read_skill_zip
from models import PublicSkill, SkillResource, UsageQuota, User, UserAddedSkill, UserSkill

SKILL_MD = """---
name: suspense-master
description: 增强钩子和悬念
license: MIT
allowed-tools: query_files
metadata:
  zenstory:
    display_name: 悬念大师
    triggers: [悬念, 钩子]
---
先强化钩子，再收紧悬念。
"""


def _zip(entries: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buffer.getvalue()


async def _login(client: AsyncClient, db_session, username: str) -> tuple[User, dict[str, str]]:
    from services.core.auth_service import hash_password

    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    response = await client.post("/api/auth/login", data={"username": username, "password": "password123"})
    assert response.status_code == 200
    return user, {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _import(client: AsyncClient, headers: dict[str, str], filename: str, data: bytes):
    return await client.post(
        "/api/v1/skills/import",
        files={"file": (filename, data, "application/octet-stream")},
        headers=headers,
    )


# ==================== Import ====================


@pytest.mark.integration
async def test_import_zip_creates_skill_resources_and_reports_dropped_files(client: AsyncClient, db_session):
    user, headers = await _login(client, db_session, "pkg_import")
    data = _zip({
        "suspense/SKILL.md": SKILL_MD,
        "suspense/references/hooks.md": "钩子清单",
        "suspense/assets/template.json": "{}",
        "suspense/scripts/check.py": "print('never executed')",
        "suspense/assets/cover.png": b"\x89PNG",
    })

    response = await _import(client, headers, "suspense.zip", data)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["skill"]["name"] == "悬念大师"
    assert body["skill"]["description"] == "增强钩子和悬念"
    assert body["skill"]["triggers"] == ["悬念", "钩子"]
    assert body["skill"]["instructions"] == "先强化钩子，再收紧悬念。"
    assert body["skill"]["source"] == "user"
    assert body["skill"]["resource_count"] == 2
    assert any("scripts/check.py" in warning for warning in body["warnings"])
    assert any("assets/cover.png" in warning for warning in body["warnings"])

    db_skill = db_session.get(UserSkill, body["skill"]["id"])
    assert db_skill.user_id == user.id
    assert json.loads(db_skill.skill_metadata) == {"license": "MIT", "allowed_tools": ["query_files"]}
    paths = sorted(r.path for r in db_session.exec(
        select(SkillResource).where(SkillResource.user_skill_id == db_skill.id)
    ).all())
    assert paths == ["assets/template.json", "references/hooks.md"]

    listed = await client.get("/api/v1/skills", headers=headers)
    assert listed.json()["skills"][0]["resource_count"] == 2
    my_skills = await client.get("/api/v1/skills/my-skills", headers=headers)
    assert my_skills.json()["user_skills"][0]["resource_count"] == 2


@pytest.mark.integration
async def test_import_single_markdown(client: AsyncClient, db_session):
    _user, headers = await _login(client, db_session, "pkg_import_md")

    response = await _import(
        client, headers, "SKILL.md",
        "# 旧格式技能\n\n描述。\n\n## Triggers\n- 甲\n\n## Instructions\n旧指令。".encode(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["skill"]["name"] == "旧格式技能"
    assert body["skill"]["triggers"] == ["甲"]
    assert body["skill"]["resource_count"] == 0
    assert body["warnings"] == []


@pytest.mark.integration
@pytest.mark.parametrize(
    ("filename", "data", "status", "code"),
    [
        ("bad.zip", b"not a zip", 400, "ERR_SKILL_PACKAGE_INVALID"),
        ("evil.zip", _zip({"s/SKILL.md": SKILL_MD, "../evil.md": "x"}), 400, "ERR_SKILL_PACKAGE_INVALID"),
        ("nothing.zip", _zip({"s/references/a.md": "a"}), 400, "ERR_SKILL_PACKAGE_INVALID"),
        ("big.zip", b"PK\x03\x04" + b"\x00" * (1024 * 1024 + 10), 413, "ERR_SKILL_PACKAGE_TOO_LARGE"),
        ("skill.txt", b"hello", 400, "ERR_SKILL_PACKAGE_INVALID"),
    ],
    ids=["not-zip", "traversal", "no-skill-md", "oversize", "unsupported-ext"],
)
async def test_import_rejects_invalid_packages(client: AsyncClient, db_session, filename, data, status, code):
    _user, headers = await _login(client, db_session, f"pkg_bad_{filename.replace('.', '_')}")

    response = await _import(client, headers, filename, data)

    assert response.status_code == status
    body = response.json()
    assert body["error_code"] == code
    assert body["error_detail"]
    assert db_session.exec(select(UserSkill)).all() == []


@pytest.mark.integration
async def test_import_counts_owned_skills_against_custom_skill_limit(client: AsyncClient, db_session):
    user, headers = await _login(client, db_session, "pkg_quota")

    ok = await _import(client, headers, "SKILL.md", SKILL_MD.encode())
    assert ok.status_code == 200
    quota = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == user.id)).first()
    assert quota is not None and quota.skill_creates_used == 1  # activity metric only

    # 免费档 custom_skills = 3：再拥有两个后到达上限
    db_session.add_all(
        [UserSkill(user_id=user.id, name=f"owned {i}", instructions="x") for i in range(2)]
    )
    db_session.commit()

    blocked = await _import(client, headers, "SKILL.md", SKILL_MD.encode())
    assert blocked.status_code == 402
    assert len(db_session.exec(select(UserSkill).where(UserSkill.user_id == user.id)).all()) == 3

    # Deleting a skill frees its slot; the monthly counter does not matter.
    removed = await client.delete(f"/api/v1/skills/{ok.json()['skill']['id']}", headers=headers)
    assert removed.status_code == 200
    again = await _import(client, headers, "SKILL.md", SKILL_MD.encode())
    assert again.status_code == 200


# ==================== Export ====================


@pytest.mark.integration
async def test_export_import_round_trip_through_api(client: AsyncClient, db_session):
    _user, headers = await _login(client, db_session, "pkg_roundtrip")
    imported = await _import(client, headers, "s.zip", _zip({
        "s/SKILL.md": SKILL_MD,
        "s/references/hooks.md": "钩子清单",
    }))
    skill_id = imported.json()["skill"]["id"]

    exported = await client.get(f"/api/v1/skills/{skill_id}/export", headers=headers)

    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"
    assert "attachment" in exported.headers["content-disposition"]
    assert ".zip" in exported.headers["content-disposition"]
    skill, resources, warnings = read_skill_zip(exported.content)
    assert (skill.name, skill.description, skill.instructions) == (
        "悬念大师", "增强钩子和悬念", "先强化钩子，再收紧悬念。",
    )
    assert skill.triggers == ["悬念", "钩子"]
    assert skill.license == "MIT"
    assert skill.allowed_tools == ["query_files"]
    assert resources == [("references/hooks.md", "钩子清单")]
    assert warnings == []

    reimported = await _import(client, headers, "exported.zip", exported.content)
    assert reimported.status_code == 200
    assert reimported.json()["skill"]["name"] == "悬念大师"
    assert reimported.json()["skill"]["resource_count"] == 1


@pytest.mark.integration
async def test_export_added_public_skill_uses_custom_name_and_public_resources(client: AsyncClient, db_session):
    user, headers = await _login(client, db_session, "pkg_export_added")
    public = PublicSkill(
        name="氛围渲染器", description="强化氛围", instructions="写感官细节。",
        category="style", tags=json.dumps(["氛围"]), status="approved",
    )
    db_session.add(public)
    db_session.commit()
    added = UserAddedSkill(user_id=user.id, public_skill_id=public.id, custom_name="阴影编织者")
    db_session.add(added)
    db_session.add(SkillResource(public_skill_id=public.id, path="references/senses.md", content="五感", size=6))
    db_session.commit()

    response = await client.get(f"/api/v1/skills/{added.id}/export", headers=headers)

    assert response.status_code == 200
    skill, resources, _warnings = read_skill_zip(response.content)
    assert skill.name == "阴影编织者"
    assert skill.triggers == ["氛围"]
    assert skill.category == "style"
    assert resources == [("references/senses.md", "五感")]


# ==================== Resources CRUD ====================


@pytest.mark.integration
async def test_resource_crud_on_own_skill(client: AsyncClient, db_session):
    _user, headers = await _login(client, db_session, "pkg_resources")
    created = await _import(client, headers, "SKILL.md", SKILL_MD.encode())
    skill_id = created.json()["skill"]["id"]
    base = f"/api/v1/skills/{skill_id}/resources"

    put = await client.put(base, json={"path": "references/style.md", "content": "文风"}, headers=headers)
    assert put.status_code == 200, put.text
    assert put.json()["path"] == "references/style.md"
    assert put.json()["size"] == len("文风".encode())

    replaced = await client.put(base, json={"path": "references/style.md", "content": "新文风"}, headers=headers)
    assert replaced.status_code == 200
    assert replaced.json()["size"] == len("新文风".encode())

    listing = await client.get(base, headers=headers)
    assert [item["path"] for item in listing.json()["resources"]] == ["references/style.md"]

    content = await client.get(f"{base}/content", params={"path": "references/style.md"}, headers=headers)
    assert content.json() == {"path": "references/style.md", "content": "新文风"}

    deleted = await client.delete(base, params={"path": "references/style.md"}, headers=headers)
    assert deleted.status_code == 200
    assert (await client.get(base, headers=headers)).json()["resources"] == []
    missing = await client.get(f"{base}/content", params={"path": "references/style.md"}, headers=headers)
    assert missing.status_code == 404


@pytest.mark.integration
@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        ({"path": "scripts/run.md", "content": "x"}, 400, "ERR_SKILL_PACKAGE_INVALID"),
        ({"path": "references/../x.md", "content": "x"}, 400, "ERR_SKILL_PACKAGE_INVALID"),
        ({"path": "references/run.py", "content": "x"}, 400, "ERR_SKILL_PACKAGE_INVALID"),
        ({"path": "references/big.md", "content": "a" * (64 * 1024 + 1)}, 413, "ERR_SKILL_PACKAGE_TOO_LARGE"),
    ],
    ids=["scripts", "traversal", "non-text", "oversize"],
)
async def test_resource_upsert_is_validated_like_import(client: AsyncClient, db_session, payload, status, code):
    _user, headers = await _login(client, db_session, f"pkg_res_bad_{status}_{len(payload['path'])}")
    created = await _import(client, headers, "SKILL.md", SKILL_MD.encode())
    skill_id = created.json()["skill"]["id"]

    response = await client.put(f"/api/v1/skills/{skill_id}/resources", json=payload, headers=headers)

    assert response.status_code == status
    assert response.json()["error_code"] == code


@pytest.mark.integration
async def test_resource_count_limit(client: AsyncClient, db_session):
    user, headers = await _login(client, db_session, "pkg_res_count")
    skill = UserSkill(user_id=user.id, name="满额技能", instructions="x")
    db_session.add(skill)
    db_session.commit()
    for index in range(20):
        db_session.add(SkillResource(user_skill_id=skill.id, path=f"references/{index}.md", content="x", size=1))
    db_session.commit()

    new = await client.put(
        f"/api/v1/skills/{skill.id}/resources", json={"path": "references/new.md", "content": "x"}, headers=headers,
    )
    replace = await client.put(
        f"/api/v1/skills/{skill.id}/resources", json={"path": "references/0.md", "content": "y"}, headers=headers,
    )

    assert new.status_code == 413
    assert replace.status_code == 200


@pytest.mark.integration
async def test_added_skill_resources_are_read_only(client: AsyncClient, db_session):
    user, headers = await _login(client, db_session, "pkg_res_added")
    public = PublicSkill(name="公共技能", description="d", instructions="i", status="approved")
    db_session.add(public)
    db_session.commit()
    added = UserAddedSkill(user_id=user.id, public_skill_id=public.id)
    db_session.add(added)
    db_session.add(SkillResource(public_skill_id=public.id, path="references/a.md", content="A", size=1))
    db_session.commit()
    base = f"/api/v1/skills/{added.id}/resources"

    listing = await client.get(base, headers=headers)
    content = await client.get(f"{base}/content", params={"path": "references/a.md"}, headers=headers)
    put = await client.put(base, json={"path": "references/b.md", "content": "B"}, headers=headers)
    delete = await client.delete(base, params={"path": "references/a.md"}, headers=headers)

    assert [item["path"] for item in listing.json()["resources"]] == ["references/a.md"]
    assert content.json()["content"] == "A"
    assert put.status_code == 403
    assert delete.status_code == 403
    listed = await client.get("/api/v1/skills", headers=headers)
    assert listed.json()["skills"][0]["resource_count"] == 1


@pytest.mark.integration
async def test_cannot_access_other_users_skill_resources(client: AsyncClient, db_session):
    owner, _owner_headers = await _login(client, db_session, "pkg_owner")
    _intruder, headers = await _login(client, db_session, "pkg_intruder")
    skill = UserSkill(user_id=owner.id, name="私有技能", instructions="x")
    db_session.add(skill)
    db_session.commit()
    db_session.add(SkillResource(user_skill_id=skill.id, path="references/secret.md", content="机密", size=6))
    public = PublicSkill(name="别人加的", description="d", instructions="i", status="approved")
    db_session.add(public)
    db_session.commit()
    others_added = UserAddedSkill(user_id=owner.id, public_skill_id=public.id)
    db_session.add(others_added)
    db_session.commit()

    for skill_id in (skill.id, others_added.id, public.id):
        base = f"/api/v1/skills/{skill_id}/resources"
        responses = [
            await client.get(base, headers=headers),
            await client.get(f"{base}/content", params={"path": "references/secret.md"}, headers=headers),
            await client.put(base, json={"path": "references/x.md", "content": "x"}, headers=headers),
            await client.delete(base, params={"path": "references/secret.md"}, headers=headers),
            await client.get(f"/api/v1/skills/{skill_id}/export", headers=headers),
        ]
        assert [r.status_code for r in responses] == [404] * 5
        assert all("机密" not in r.text for r in responses)

    assert db_session.exec(select(SkillResource).where(SkillResource.user_skill_id == skill.id)).one().content == "机密"


# ==================== Share / delete ====================


@pytest.mark.integration
async def test_share_copies_resources_and_metadata(client: AsyncClient, db_session):
    _user, headers = await _login(client, db_session, "pkg_share")
    created = await _import(client, headers, "s.zip", _zip({
        "s/SKILL.md": SKILL_MD,
        "s/references/hooks.md": "钩子清单",
    }))
    skill_id = created.json()["skill"]["id"]

    response = await client.post(f"/api/v1/skills/{skill_id}/share", json={"category": "plot"}, headers=headers)

    assert response.status_code == 200
    public = db_session.get(PublicSkill, response.json()["public_skill_id"])
    assert json.loads(public.skill_metadata) == {"license": "MIT", "allowed_tools": ["query_files"]}
    copied = db_session.exec(select(SkillResource).where(SkillResource.public_skill_id == public.id)).all()
    assert [(r.path, r.content) for r in copied] == [("references/hooks.md", "钩子清单")]
    # 原技能的资源保持不变
    assert len(db_session.exec(select(SkillResource).where(SkillResource.user_skill_id == skill_id)).all()) == 1


@pytest.mark.integration
async def test_delete_skill_removes_its_resources(client: AsyncClient, db_session):
    _user, headers = await _login(client, db_session, "pkg_delete")
    first = (await _import(client, headers, "a.zip", _zip({"a/SKILL.md": SKILL_MD, "a/references/a.md": "a"}))).json()
    second = (await _import(client, headers, "b.zip", _zip({"b/SKILL.md": SKILL_MD, "b/references/b.md": "b"}))).json()

    single = await client.delete(f"/api/v1/skills/{first['skill']['id']}", headers=headers)
    batch = await client.post(
        "/api/v1/skills/batch-update",
        json={"skill_ids": [second["skill"]["id"]], "action": "delete"},
        headers=headers,
    )

    assert single.status_code == 200
    assert batch.status_code == 200
    assert db_session.exec(select(SkillResource)).all() == []


# ==================== 安全加固回归（代码评审发现） ====================


def _skill_md_with_metadata(metadata_yaml: str) -> bytes:
    return f"---\nname: x\ndescription: d\nmetadata:\n{metadata_yaml}---\n正文\n".encode()


@pytest.mark.integration
async def test_import_rejects_yaml_alias_bomb(client: AsyncClient, db_session):
    _user, headers = await _login(client, db_session, "pkg_alias_bomb")
    bomb = _skill_md_with_metadata(
        "  a: &a [x, x, x, x, x, x, x, x, x, x]\n"
        "  b: &b [*a, *a, *a, *a, *a, *a, *a, *a, *a, *a]\n"
        "  c: &c [*b, *b, *b, *b, *b, *b, *b, *b, *b, *b]\n"
        "  d: [*c, *c, *c, *c, *c, *c, *c, *c, *c, *c]\n"
    )

    response = await _import(client, headers, "SKILL.md", bomb)

    assert response.status_code == 400
    assert response.json()["error_code"] == "ERR_SKILL_PACKAGE_INVALID"
    assert db_session.exec(select(UserSkill)).all() == []


@pytest.mark.integration
async def test_import_stores_yaml_dates_as_iso_and_rejects_sets(client: AsyncClient, db_session):
    _user, headers = await _login(client, db_session, "pkg_yaml_types")

    ok = await _import(client, headers, "SKILL.md", _skill_md_with_metadata("  released: 2024-01-02\n"))
    bad = await _import(client, headers, "SKILL.md", _skill_md_with_metadata("  tags: !!set {a: null}\n"))

    assert ok.status_code == 200, ok.text
    stored = db_session.exec(select(UserSkill)).one()
    assert json.loads(stored.skill_metadata) == {"metadata": {"released": "2024-01-02"}}
    assert bad.status_code == 400
    assert bad.json()["error_code"] == "ERR_SKILL_PACKAGE_INVALID"


@pytest.mark.integration
async def test_import_rejects_duplicate_zip_entries(client: AsyncClient, db_session):
    import warnings

    _user, headers = await _login(client, db_session, "pkg_dup_entries")
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("s/SKILL.md", SKILL_MD)
            zf.writestr("s/references/a.md", "one")
            zf.writestr("s/references/a.md", "two")

    response = await _import(client, headers, "dup.zip", buffer.getvalue())

    assert response.status_code == 400
    assert response.json()["error_code"] == "ERR_SKILL_PACKAGE_INVALID"
    assert db_session.exec(select(SkillResource)).all() == []


@pytest.mark.integration
async def test_import_rejects_oversized_upload_by_content_length(client: AsyncClient, db_session, monkeypatch):
    """Content-Length 超限时在解析 multipart/调用端点前就拒收。"""
    from services import skill_package_service

    _user, headers = await _login(client, db_session, "pkg_content_length")
    called: list[str] = []
    monkeypatch.setattr(
        skill_package_service, "parse_skill_upload", lambda *args: called.append("parse"),
    )

    response = await _import(client, headers, "big.zip", b"\x00" * (2 * 1024 * 1024))

    assert response.status_code == 413
    assert response.json()["error_code"] == "ERR_SKILL_PACKAGE_TOO_LARGE"
    assert called == []


@pytest.mark.integration
async def test_import_parses_package_in_worker_thread(client: AsyncClient, db_session, monkeypatch):
    import api.skills as skills_api
    from services import skill_package_service

    _user, headers = await _login(client, db_session, "pkg_to_thread")
    offloaded: list[object] = []
    real_to_thread = skills_api.asyncio.to_thread

    async def spy_to_thread(func, /, *args, **kwargs):
        offloaded.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(skills_api.asyncio, "to_thread", spy_to_thread)

    response = await _import(client, headers, "SKILL.md", SKILL_MD.encode())

    assert response.status_code == 200, response.text
    assert skill_package_service.parse_skill_upload in offloaded


@pytest.mark.integration
@pytest.mark.parametrize(
    "payload",
    [
        {"name": "n" * 101, "triggers": [], "instructions": "i"},
        {"name": "n", "description": "d" * 1025, "triggers": [], "instructions": "i"},
        {"name": "n", "triggers": [], "instructions": "i" * 50_001},
        {"name": "n", "triggers": ["t"] * 51, "instructions": "i"},
        {"name": "n", "triggers": ["t" * 101], "instructions": "i"},
        {"name": "", "triggers": [], "instructions": "i"},
        {"name": "n", "triggers": [], "instructions": ""},
    ],
    ids=["name", "description", "instructions", "trigger-count", "trigger-length", "empty-name", "empty-instructions"],
)
async def test_create_and_update_skill_enforce_import_length_limits(client: AsyncClient, db_session, payload):
    user, headers = await _login(client, db_session, f"pkg_limits_{len(json.dumps(payload))}")
    skill = UserSkill(user_id=user.id, name="已有技能", instructions="方法")
    db_session.add(skill)
    db_session.commit()

    created = await client.post("/api/v1/skills", json=payload, headers=headers)
    updated = await client.put(f"/api/v1/skills/{skill.id}", json=payload, headers=headers)

    assert created.status_code == 422
    assert updated.status_code == 422
    assert len(db_session.exec(select(UserSkill).where(UserSkill.user_id == user.id)).all()) == 1


@pytest.mark.integration
async def test_unapproved_added_skill_is_not_resolvable(client: AsyncClient, db_session):
    user, headers = await _login(client, db_session, "pkg_unapproved")
    public = PublicSkill(name="下架技能", description="d", instructions="i", status="rejected")
    db_session.add(public)
    db_session.commit()
    added = UserAddedSkill(user_id=user.id, public_skill_id=public.id)
    db_session.add(added)
    db_session.add(SkillResource(public_skill_id=public.id, path="references/a.md", content="A", size=1))
    db_session.commit()

    export = await client.get(f"/api/v1/skills/{added.id}/export", headers=headers)
    listing = await client.get(f"/api/v1/skills/{added.id}/resources", headers=headers)

    assert export.status_code == 404
    assert listing.status_code == 404


@pytest.mark.integration
async def test_resource_paths_are_nfc_normalized(client: AsyncClient, db_session):
    user, headers = await _login(client, db_session, "pkg_res_nfc")
    skill = UserSkill(user_id=user.id, name="NFC 技能", instructions="x")
    db_session.add(skill)
    db_session.commit()
    base = f"/api/v1/skills/{skill.id}/resources"
    nfd, nfc = "references/café.md", "references/café.md"

    first = await client.put(base, json={"path": nfd, "content": "one"}, headers=headers)
    second = await client.put(base, json={"path": nfc, "content": "two"}, headers=headers)
    content = await client.get(f"{base}/content", params={"path": nfd}, headers=headers)

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["path"] == nfc
    assert [item["path"] for item in (await client.get(base, headers=headers)).json()["resources"]] == [nfc]
    assert content.json() == {"path": nfc, "content": "two"}


@pytest.mark.integration
async def test_resource_upsert_unique_race_returns_409(db_session, monkeypatch):
    """并发插入同一路径：本请求没看到对方的行，提交时撞唯一约束 → 409 而不是 500。"""
    from core.error_handler import APIException
    from services import skill_package_service

    user = User(username="pkg_race", email="pkg_race@example.com", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.commit()
    skill = UserSkill(user_id=user.id, name="竞争技能", instructions="x")
    db_session.add(skill)
    db_session.commit()
    db_session.add(SkillResource(user_skill_id=skill.id, path="references/a.md", content="old", size=3))
    db_session.commit()
    # 模拟并发：读现有资源时还看不到另一个请求刚插入的同名行
    monkeypatch.setattr(skill_package_service, "list_skill_resources", lambda *args, **kwargs: [])

    with pytest.raises(APIException) as exc_info:
        skill_package_service.upsert_resource(db_session, user.id, skill.id, "references/a.md", "new")

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "ERR_RESOURCE_CONFLICT"
    remaining = db_session.exec(select(SkillResource).where(SkillResource.user_skill_id == skill.id)).all()
    assert [(item.path, item.content) for item in remaining] == [("references/a.md", "old")]
