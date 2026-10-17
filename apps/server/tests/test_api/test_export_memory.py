"""Whole-project TXT export must not retain redundant whole-text copies."""

import gc
import tracemalloc
from types import SimpleNamespace
from urllib.parse import quote

import pytest

import api.export as export_api
from models import File, Project, User
from services.features import export_service


@pytest.mark.parametrize(("character", "peak_per_response_byte"), [("汉", 2.0), ("a", 5.5), ("🙂", 2.5)])
def test_export_releases_pre_bom_text_before_encoding(monkeypatch, character, peak_per_response_byte):
    owner = User(username="memory-test", email="memory@example.test", hashed_password="unused")
    project = Project(name="分配回归", owner_id=owner.id)
    session = SimpleNamespace(get=lambda *args: project)
    # Construct inside the measured request, with no fixture reference retaining
    # the service's returned text after the route replaces it with BOM text.
    monkeypatch.setattr(
        export_api, "_export_drafts_to_txt_with_outline_state", lambda *args, **kwargs: (character * 500_000, False)
    )
    monkeypatch.setattr(export_api.quota_service, "get_user_plan", lambda *args: None)
    gc.collect()
    tracemalloc.start()
    try:
        response = export_api.export_project_drafts(
            project.id, include_outline=False, current_user=owner, session=session
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert response.body == b"\xef\xbb\xbf" + (character * 500_000).encode("utf-8")
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''" + quote("分配回归_正文.txt")
    # Broad CPython bound: catches a second full Unicode text live at encoding.
    # ASCII widens for BOM and the UTF-8 codec reserves extra capacity: measured
    # baseline ~6x response bytes vs released-original ~5x (hence 5.5x bound).
    assert peak < peak_per_response_byte * len(response.body)


@pytest.mark.parametrize("include_outline", [False, True])
def test_export_service_avoids_per_chapter_and_whole_body_copies(monkeypatch, include_outline):
    body = "\u3000\u3000" + "汉" * 16_384 + "🙂"
    drafts = [File(title=f"第{i + 1}章", file_type="draft", content=body) for i in range(32)]
    outlines = [File(title="核心大纲", file_type="outline", content="\u3000\u3000大纲内容")]
    project = Project(name="测量作品", project_type="screenplay")
    session = SimpleNamespace(get=lambda *args: project)
    monkeypatch.setattr(export_service, "get_sorted_drafts", lambda *args: drafts)
    monkeypatch.setattr(export_service, "get_sorted_outlines", lambda *args: outlines)
    chapters = export_service.CHAPTER_SEPARATOR.join(f"{f.title}\n\n{body}" for f in drafts)
    expected = "测量作品\n\n" + (
        "【大纲】\n\n核心大纲\n\n\u3000\u3000大纲内容" + export_service.CHAPTER_SEPARATOR + "【剧本】\n\n"
        if include_outline else ""
    ) + chapters
    gc.collect()
    tracemalloc.start()
    try:
        content = export_service.export_drafts_to_txt(
            session, project.id, title=project.name, include_outline=include_outline
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert content == expected
    # Existing inputs are outside tracing; output has astral chars (4-byte
    # Unicode). Allow 50% overhead, but not a second whole chapter/body copy.
    assert peak < 1.5 * (len(content) * 4)
