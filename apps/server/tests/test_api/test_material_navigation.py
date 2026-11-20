"""Navigation reads preserve labels and access without loading detail payloads."""

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlmodel import Session, SQLModel

from api.materials import router
from database import get_session
from models import User
from models.material_models import (
    Chapter,
    Character,
    CharacterRelationship,
    GoldenFinger,
    Novel,
    Story,
    StoryLine,
    WorldView,
)
from services.core.auth_service import get_current_active_user
from services.quota_service import quota_service

pytestmark = pytest.mark.unit
KINDS = ("characters", "stories", "storylines", "relationships", "worldview", "goldenfingers")
TABLES = {
    "characters": "characters", "stories": "stories", "storylines": "story_lines",
    "relationships": "character_relationships", "worldview": "world_views", "goldenfingers": "golden_fingers",
}


@pytest.fixture
def navigation_client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'navigation.db'}")
    SQLModel.metadata.create_all(engine)
    state = {"entitled": True, "trial_used": False, "columns": []}
    with Session(engine) as session:
        session.add(User(id="owner", username="owner", email="owner@example.invalid", hashed_password="unused"))
        session.add_all([
            Novel(id=1, user_id="owner", title="Owned"),
            Novel(id=2, user_id="other", title="Other"),
            Novel(id=3, user_id="owner", title="Deleted", deleted_at=datetime(2026, 1, 1)),
            Novel(id=4, user_id="owner", title="Empty"),
        ])
        session.flush()
        session.add_all([
            Chapter(id=1, novel_id=1, chapter_number=1, title="First"),
            Chapter(id=2, novel_id=1, chapter_number=2, title="Second"),
            Character(id=11, novel_id=1, name="Later", description="long character " * 2000, aliases='["Alias"]', first_appearance_chapter_id=2),
            Character(id=12, novel_id=1, name="First", first_appearance_chapter_id=1),
            Character(id=13, novel_id=2, name="Other"),
            StoryLine(id=21, novel_id=1, title="Arc", description="long arc " * 2000, main_characters='["First"]', themes='["Hope"]'),
            StoryLine(id=22, novel_id=2, title="Other arc"),
            GoldenFinger(id=41, novel_id=1, name="Ability", description="long ability " * 2000, evolution_history='[{"stage":1}]'),
            WorldView(id=51, novel_id=1, world_structure="  😀世界\n" * 12, power_system="long power " * 2000, special_rules="Hidden rules"),
        ])
        session.flush()
        session.add_all([
            Story(id=31, novel_id=1, title="Direct", synopsis="long story " * 2000),
            Story(id=32, novel_id=None, story_line_id=21, title="Legacy"),
            Story(id=33, novel_id=2, story_line_id=21, title="Other despite link"),
            Story(id=34, novel_id=None, story_line_id=22, title="Other legacy"),
            CharacterRelationship(id=61, novel_id=1, character_a_id=12, character_b_id=11, relationship_type="ally", description="long relationship " * 2000),
        ])
        session.commit()
        user = session.get(User, "owner")

    def capture_columns(_conn, _cursor, _statement, _params, context, _many):
        statement = getattr(context.compiled, "statement", None)
        if getattr(statement, "is_select", False):
            state["columns"].append([
                (getattr(getattr(column, "table", None), "name", None), column.key)
                for column in statement.selected_columns
            ])

    event.listen(engine, "after_cursor_execute", capture_columns)

    def read_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(quota_service, "has_feature_access", lambda *_: state["entitled"])
    monkeypatch.setattr(quota_service, "material_trial_status", lambda *_: {"used": state["trial_used"]})
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = read_session
    app.dependency_overrides[get_current_active_user] = lambda: user
    try:
        with TestClient(app) as client:
            yield client, state
    finally:
        engine.dispose()


@pytest.mark.parametrize("kind,selected,expected", [
    ("characters", {"id", "name"}, [{"id": 12, "name": "First"}, {"id": 11, "name": "Later"}]),
    ("stories", {"id", "title"}, [{"id": 31, "title": "Direct"}, {"id": 32, "title": "Legacy"}]),
    ("storylines", {"id", "title"}, [{"id": 21, "title": "Arc"}]),
    ("goldenfingers", {"id", "name"}, [{"id": 41, "name": "Ability"}]),
    ("relationships", {"id", "character_a_name", "character_b_name"}, [{"id": 61, "character_a_name": "First", "character_b_name": "Later"}]),
    ("worldview", {"id", "world_structure"}, {"id": 51, "world_structure": "  😀世界\n" * 12}),
])
def test_navigation_selects_only_label_columns(navigation_client, kind, selected, expected):
    client, state = navigation_client
    response = client.get(f"/api/v1/materials/1/{kind}", params={"navigation": "true"})
    assert response.status_code == 200
    payload = response.json()
    if kind == "characters":
        assert payload == expected
    else:
        assert payload == expected
    entity_reads = [columns for columns in state["columns"] if any(table == TABLES[kind] for table, _ in columns)]
    assert len(entity_reads) == 1
    assert {key for _, key in entity_reads[0]} == selected
    if kind == "characters":
        assert not any(table == "chapters" for columns in state["columns"] for table, _ in columns)


@pytest.mark.parametrize("kind,field,value", [
    ("characters", "description", "long character " * 2000),
    ("stories", "synopsis", "long story " * 2000),
    ("storylines", "description", "long arc " * 2000),
    ("goldenfingers", "description", "long ability " * 2000),
    ("relationships", "description", "long relationship " * 2000),
    ("worldview", "power_system", "long power " * 2000),
])
def test_default_and_explicit_false_keep_full_detail(navigation_client, kind, field, value):
    client, _state = navigation_client
    for params in ({}, {"navigation": "false"}):
        response = client.get(f"/api/v1/materials/1/{kind}", params=params)
        assert response.status_code == 200
        payload = response.json()
        row = payload if kind == "worldview" else next(row for row in payload if row.get(field))
        assert row[field] == value
        if kind == "characters":
            assert row["aliases"] == ["Alias"]
            assert row["first_appearance_chapter"] == 2
        if kind == "storylines":
            assert row["stories_count"] == 1


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("novel_id", [2, 3, 999])
def test_navigation_preserves_ownership_and_soft_delete(navigation_client, kind, novel_id):
    client, state = navigation_client
    response = client.get(f"/api/v1/materials/{novel_id}/{kind}", params={"navigation": "true"})
    assert response.status_code == 403
    assert not any(table == TABLES[kind] for columns in state["columns"] for table, _ in columns)


@pytest.mark.parametrize("kind", KINDS)
def test_navigation_preserves_entitlement_and_trial_read_access(navigation_client, kind):
    client, state = navigation_client
    state["entitled"] = False
    response = client.get(f"/api/v1/materials/1/{kind}", params={"navigation": "true"})
    assert response.status_code == 402
    state["trial_used"] = True
    assert client.get(f"/api/v1/materials/1/{kind}", params={"navigation": "true"}).status_code == 200


@pytest.mark.parametrize("kind", KINDS)
def test_empty_navigation_is_successful(navigation_client, kind):
    client, _state = navigation_client
    response = client.get(f"/api/v1/materials/4/{kind}", params={"navigation": "true"})
    assert response.status_code == 200
    assert response.json() == (None if kind == "worldview" else [])


def test_navigation_flag_is_validated(navigation_client):
    client, _state = navigation_client
    assert client.get("/api/v1/materials/1/characters", params={"navigation": "maybe"}).status_code == 422


def test_character_openapi_documents_full_and_navigation_shapes(navigation_client):
    client, _state = navigation_client
    spec = client.get("/openapi.json").json()
    schema = spec["paths"]["/api/v1/materials/{novel_id}/characters"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert {item["items"]["$ref"].rsplit("/", 1)[-1] for item in schema["anyOf"]} == {
        "CharacterListItem", "CharacterNavigationItem",
    }
