from __future__ import annotations

import json

from sqlmodel import Session

from models.material_models import Chapter, Novel
from services.material.character_mentions_service import CharacterMentionsService


def _create_novel_with_chapter(db_session: Session) -> tuple[Novel, Chapter]:
    novel = Novel(user_id="user-1", title="Novel", author="Author")
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)

    chapter = Chapter(novel_id=novel.id, chapter_number=1, title="Chapter 1")
    db_session.add(chapter)
    db_session.commit()
    db_session.refresh(chapter)
    return novel, chapter


def test_character_mentions_upsert_and_exact_alias_matching(db_session: Session):
    novel, chapter = _create_novel_with_chapter(db_session)
    service = CharacterMentionsService()

    created = service.upsert_mention(
        db_session,
        novel.id,
        chapter.id,
        "Annie",
        {
            "aliases": json.dumps(["小安", "安妮"]),
            "chapter_description": "安妮出场",
        },
    )
    service.upsert_mention(
        db_session,
        novel.id,
        chapter.id,
        "Bob",
        {
            "aliases": json.dumps(["老鲍"]),
            "chapter_description": "Bob出场",
        },
    )

    by_novel = service.get_by_novel(db_session, novel.id)
    by_exact_alias = service.get_by_character_name_or_alias(db_session, novel.id, "安妮")
    by_partial_alias = service.get_by_character_name_or_alias(db_session, novel.id, "安")

    assert created.id is not None
    assert {mention.character_name for mention in by_novel} == {"Annie", "Bob"}
    assert [mention.character_name for mention in by_exact_alias] == ["Annie"]
    assert by_partial_alias == []


def test_upsert_updates_existing_mention(db_session: Session):
    novel, chapter = _create_novel_with_chapter(db_session)
    service = CharacterMentionsService()

    created = service.upsert_mention(
        db_session,
        novel.id,
        chapter.id,
        "Alice",
        {"chapter_description": "第一次出现", "aliases": json.dumps(["阿璃"])},
    )
    updated = service.upsert_mention(
        db_session,
        novel.id,
        chapter.id,
        "Alice",
        {"chapter_description": "第二次出现", "aliases": json.dumps(["阿离"])},
    )
    db_session.commit()

    assert created.id == updated.id
    assert updated.chapter_description == "第二次出现"
