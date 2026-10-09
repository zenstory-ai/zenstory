"""resolve_prose_word_counts 在 GET 时回填字数缓存，不能盖掉同时落地的保存。"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import load_only
from sqlmodel import Session, select

from models import File, Project, User
from models.file_model import FILE_TYPE_DRAFT, cached_word_count
from services.features import writing_stats_service
from services.features.writing_stats_service import resolve_prose_word_counts
from tests.conftest import TestSessionLocal


def _draft(db_session: Session, content: str, metadata: dict | None) -> File:
    user = User(username="wc_backfill", email="wc_backfill@example.com", hashed_password="pass")
    db_session.add(user)
    db_session.commit()
    project = Project(name="回填", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    draft = File(project_id=project.id, title="第一章", content=content, file_type=FILE_TYPE_DRAFT)
    if metadata is not None:
        draft.set_metadata(metadata)
    db_session.add(draft)
    db_session.commit()
    return draft


def _light(db_session: Session, file_id: str) -> list[File]:
    db_session.expunge_all()
    return list(
        db_session.exec(select(File).options(load_only(File.id, File.file_metadata)).where(File.id == file_id)).all()
    )


def _stored(file_id: str) -> File:
    with TestSessionLocal() as fresh:
        stored = fresh.get(File, file_id)
        assert stored is not None
        return stored


@pytest.mark.unit
def test_backfill_stamps_an_untouched_stale_cache(db_session: Session):
    draft = _draft(db_session, "山风吹过断崖", {"word_count": 999, "chapter_number": 1})

    counts = resolve_prose_word_counts(db_session, _light(db_session, draft.id))

    assert counts == {draft.id: 6}
    stored = _stored(draft.id)
    assert cached_word_count(stored.file_metadata) == 6
    assert stored.get_metadata_field("chapter_number") == 1


@pytest.mark.unit
def test_backfill_does_not_overwrite_a_save_that_lands_between_read_and_write_back(
    db_session: Session, monkeypatch
):
    draft = _draft(db_session, "山风", {"word_count": 999})
    real_count_words = writing_stats_service.count_words
    saved = {"done": False}

    def count_then_concurrent_save(content):
        # Another request (editor save / AI edit) commits new content and metadata
        # after the GET read the row but before it writes the backfill back.
        if not saved["done"]:
            saved["done"] = True
            with TestSessionLocal() as other:
                row = other.get(File, draft.id)
                assert row is not None
                row.content = "山风吹过断崖，他回头看了一眼 village。"
                metadata = row.get_metadata()
                metadata["word_count_target"] = 3000
                row.set_metadata(metadata)
                other.add(row)
                other.commit()
        return real_count_words(content)

    monkeypatch.setattr(writing_stats_service, "count_words", count_then_concurrent_save)

    resolve_prose_word_counts(db_session, _light(db_session, draft.id))

    stored = _stored(draft.id)
    assert stored.content == "山风吹过断崖，他回头看了一眼 village。"
    # The concurrent save's own stamp and its other metadata survive.
    assert cached_word_count(stored.file_metadata) == 14
    assert stored.get_metadata_field("word_count_target") == 3000
