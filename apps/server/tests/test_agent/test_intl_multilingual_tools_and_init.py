"""Comprehensive regression tests for PR 2/3: Multilingual writing tools & language init.

Covers:
1. text_matching.py & edit.py:
   - Single quotes/apostrophes, French guillemets, Polish quotes, dashes, and NFC vs NFD matching
   - Word-boundary protection in find_fuzzy_spans ("he ran" vs "the random")
   - Script-aware min_normalized_len, min_pattern_len, and DUPLICATE_PARAGRAPH_MIN_WORDS
   - Paragraph stats [P01 · 84w] vs [P01 · 84字] and edit.py prefix stripping for both
2. Multilingual title_sequence.py, paragraph_layout.py, and content_range.py:
   - Bab (id), Capítulo (es/pt), Chương (vi), Глава (ru), Chapitre (fr), Rozdział (pl),
     제N장/화 (ko), الفصل/الحلقة (ar)
   - Unicode _WORD_CHAR in paragraph_layout.py
   - Section range reads for multilingual headings
3. mcp_tools.py:
   - _source_copy_content NFC + quote-normalized verbatim match & >=500-char snap preserving author bytes
   - source_text_not_in_author_message error guidance & schema descriptions
   - write_chapter empty title fallback (Chapter N vs 第N章) & non-English titled chapters
   - load_skill & read_skill_resource English reverse-mapping for ALL 14 OFFICIAL_SKILL_EN names
     with strict tenant & enabled-skill scoping
4. file_search_service.py:
   - PostgreSQL ILIKE SQL compilation & case-insensitive search + LIKE wildcard escaping
5. core/i18n.py & API wiring:
   - resolve_user_language precedence (fixing ui=en + empty header race)
   - Localized default session titles (AI Assistant / New Chat vs AI 助手对话 / 新对话)
   - Localized first-work & project creation folders when ui_language='en' and header is omitted
"""

from __future__ import annotations

import json
import unicodedata
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.dialects import postgresql
from sqlmodel import Session, col, select

from agent.tools import mcp_tools
from agent.tools.file_ops.content_range import (
    apply_content_range,
    format_paragraph_stats,
)
from agent.tools.file_ops.edit import (
    DUPLICATE_PARAGRAPH_MIN_CHARS,
    DUPLICATE_PARAGRAPH_MIN_WORDS,
    EDIT_ERROR_DUPLICATED_PARAGRAPH,
    EditFileError,
    FileEditor,
    check_body_edit_guards,
)
from agent.tools.file_ops.paragraph_layout import (
    _is_heading_line,
    _starts_like_paragraph,
)
from agent.tools.file_ops.text_matching import (
    find_approximate_match,
    find_fuzzy_spans,
    locate_exact_or_quote_equivalent,
    locate_nfc_equivalent_spans,
)
from agent.tools.tool_context import AgentRequestContext
from agent.tools.tool_schemas import CREATE_FILE_TOOL
from config.official_skill_translations import OFFICIAL_SKILL_EN
from core.i18n import (
    DEFAULT_CHAT_SESSION_TITLE_EN,
    DEFAULT_CHAT_SESSION_TITLE_ZH,
    NEW_CHAT_SESSION_TITLE_EN,
    NEW_CHAT_SESSION_TITLE_ZH,
    default_chat_session_title,
    new_chat_session_title,
    resolve_user_language,
)
from models import File, Project, PublicSkill, SkillResource, User, UserAddedSkill, UserSkill
from services.core.auth_service import hash_password
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import FileVersionService
from services.features.user_preference_service import set_ui_language
from services.infra.file_search_service import FileSearchService
from utils.title_sequence import (
    chapter_title_has_subtitle,
    extract_chapter_like_sequence_number,
    extract_chapter_sequence_in_text,
    extract_sequence_number,
)


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def _seed_owner_project(
    db_session: Session,
    monkeypatch,
    suffix: str,
    *,
    ui_language: str | None = None,
    project_type: str = "novel",
) -> tuple[User, Project]:
    import database

    user = User(
        username=f"intl_{suffix}",
        email=f"intl_{suffix}@example.test",
        hashed_password=hash_password("password123"),
        name=f"Intl {suffix}",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    if ui_language is not None:
        set_ui_language(db_session, user.id, ui_language)
    project = Project(name=f"Project {suffix}", owner_id=user.id, project_type=project_type)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(project)
    engine = db_session.get_bind()
    monkeypatch.setattr(database, "create_session", lambda: Session(engine))
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_once", lambda *_a, **_kw: None)
    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (True, 0, 10))
    return user, project


# ===========================================================================
# 1. text_matching.py & edit.py multilingual hardening
# ===========================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    ("content", "pattern"),
    [
        ("She said, “I don’t know why he left.”", 'She said, "I don\'t know why he left."'),
        ("L’homme ouvrit la porte et murmura « Bonsoir ».", "L'homme ouvrit la porte et murmura \" Bonsoir \"."),
        ("Wait—don’t go yet, she whispered.", "Wait–don't go yet, she whispered."),
        ("Polish dialogue: „Nie wrócę już tutaj,” rzekł.", 'Polish dialogue: "Nie wrócę już tutaj," rzekł.'),
    ],
)
def test_quote_apostrophe_guillemet_dash_folding_matches_exactly(content: str, pattern: str):
    located = locate_exact_or_quote_equivalent(content, pattern)
    assert located is not None
    mode, haystack, needle = located
    assert mode == "quote_equivalent"
    idx = haystack.find(needle)
    assert idx == 0
    assert len(needle) == len(content)


@pytest.mark.unit
@pytest.mark.parametrize(
    "nfc_text",
    [
        "Żółć i gęśla jaźń nad rzeką.",
        "Người lữ hành bước qua sương sớm.",
        "한글 문서의 첫 번째 문단입니다.",
    ],
)
def test_nfc_vs_nfd_spans_map_to_exact_original_offsets(nfc_text: str):
    nfd_text = unicodedata.normalize("NFD", nfc_text)
    assert nfd_text != nfc_text
    prefix = "PREFIX: "
    suffix = " :SUFFIX"
    content = prefix + nfd_text + suffix

    spans = locate_nfc_equivalent_spans(content, nfc_text)
    assert len(spans) == 1
    start, end = spans[0]
    assert content[start:end] == nfd_text


@pytest.mark.unit
def test_find_fuzzy_spans_word_boundary_protection():
    # "he ran" must match when bounded by word edges, but NEVER inside "the random".
    assert find_fuzzy_spans("He ran quickly down the alley.", "he ran") == [(0, 6)]
    stats: dict[str, int] = {}
    assert find_fuzzy_spans("Look at the random numbers.", "he ran", stats=stats) == []
    assert stats["word_boundary_rejected"] >= 1

    # CJK substring fuzzy matching remains unaffected (no spaces in Chinese prose).
    cjk_spans = find_fuzzy_spans("他缓缓转身离开了那个昏暗的房间。", "转身离开了那个")
    assert len(cjk_spans) == 1


@pytest.mark.unit
def test_script_aware_fuzzy_and_approximate_thresholds():
    # Single short English word should not fuzzy-match or approximate-match.
    assert find_fuzzy_spans("She was walking slowly down the street.", "walking") == []
    assert find_approximate_match("Suddenly the lights went out.", "Suddenlx", min_pattern_len=8) is None

    # Multi-word English passage matches cleanly.
    hit = find_approximate_match(
        "The detective examined the faded photograph on the wooden desk.",
        "The detective examined the faded photograf on the wooden desk.",
        max_error_rate=0.25,
        min_pattern_len=8,
    )
    assert hit is not None


@pytest.mark.unit
def test_duplicate_paragraph_guard_is_script_aware():
    # ~84 chars (16 words) English dialogue repeated twice must NOT trip duplicated_paragraph.
    short_en_line = '"I told you never to open that cellar door after midnight," Mara whispered again.'
    assert len(short_en_line) >= DUPLICATE_PARAGRAPH_MIN_CHARS
    check_body_edit_guards(
        old_content=short_en_line,
        new_content=f"{short_en_line}\n\n{short_en_line}",
        edits=[{"op": "append", "text": f"\n\n{short_en_line}"}],
    )

    # A full 40-word English narrative paragraph duplicated SHOULD trip duplicated_paragraph.
    long_en_para = " ".join(["narrative"] * (DUPLICATE_PARAGRAPH_MIN_WORDS + 5)) + "."
    with pytest.raises(EditFileError) as exc_info:
        check_body_edit_guards(
            old_content=long_en_para,
            new_content=f"{long_en_para}\n\n{long_en_para}",
            edits=[{"op": "append", "text": f"\n\n{long_en_para}"}],
        )
    assert exc_info.value.error_type == EDIT_ERROR_DUPLICATED_PARAGRAPH

    # Chinese 80+ char paragraph still trips duplicated_paragraph.
    long_zh_para = "他站在雨夜的石阶前久久没有开口，直到远处的钟声响过第三遍才慢慢转身。" * 3
    with pytest.raises(EditFileError) as zh_exc:
        check_body_edit_guards(
            old_content=long_zh_para,
            new_content=f"{long_zh_para}\n\n{long_zh_para}",
            edits=[{"op": "append", "text": f"\n\n{long_zh_para}"}],
        )
    assert zh_exc.value.error_type == EDIT_ERROR_DUPLICATED_PARAGRAPH


@pytest.mark.integration
def test_edit_file_nfd_apostrophe_and_w_paragraph_prefix_stripping(db_session, monkeypatch):
    user, project = _seed_owner_project(db_session, monkeypatch, "edit-intl")
    nfd_polish = unicodedata.normalize("NFD", "Żółć i l’homme — don’t leave.")
    doc = File(
        project_id=project.id,
        title="Chapter 1",
        file_type="draft",
        content=f"{nfd_polish}\n\nSecond paragraph stays untouched.",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    editor = FileEditor(db_session, user.id)
    # 1) Edit using NFC + straight apostrophe + ASCII hyphen even in exact mode!
    res = editor.edit_file(
        id=doc.id,
        edits=[
            {
                "op": "replace",
                "old": "Żółć i l'homme - don't leave.",
                "new": "Żółć i l'homme — stay here.",
                "match_mode": "exact",
            }
        ],
    )
    assert res["mutation_applied"] is True
    db_session.expire_all()
    assert db_session.get(File, doc.id).content.startswith("Żółć i l'homme — stay here.")

    # 2) Paragraph stats uses `w` for non-CJK and `字` for CJK, and edit_file strips `[P01 · Nw] ` cleanly.
    annotated_en, _ = format_paragraph_stats("Second paragraph stays untouched.")
    assert annotated_en == "[P01 · 4w] Second paragraph stays untouched."
    annotated_zh, _ = format_paragraph_stats("第一段正文。")
    assert annotated_zh == "[P01 · 5字] 第一段正文。"

    res_strip = editor.edit_file(
        id=doc.id,
        edits=[
            {
                "op": "replace",
                "old": "[P02 · 4w] Second paragraph stays untouched.",
                "new": "[P02 · 4w] Second paragraph updated cleanly.",
            }
        ],
    )
    assert res_strip["mutation_applied"] is True
    db_session.expire_all()
    assert "Second paragraph updated cleanly." in db_session.get(File, doc.id).content
    assert "[P02" not in db_session.get(File, doc.id).content


# ===========================================================================
# 2. Multilingual title_sequence, paragraph_layout & content_range
# ===========================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    ("title", "unit", "expected_num", "has_subtitle"),
    [
        ("Bab 1", "章", 1, False),
        ("Bab 3: Hujan di Pelabuhan", "章", 3, True),
        ("Capítulo 2 - El regreso", "章", 2, True),
        ("Capitulo 4", "章", 4, False),
        ("Chương 5: Mùa đông", "章", 5, True),
        (unicodedata.normalize("NFD", "Chương 6: Đêm mưa"), "章", 6, True),
        ("Глава 7. Тень", "章", 7, True),
        ("Глава 8", "章", 8, False),
        ("Chapitre 9: L'ombre", "章", 9, True),
        ("Rozdział 10 — Cisza", "章", 10, True),
        ("제11장 첫 만남", "章", 11, True),
        ("제12장", "章", 12, False),
        ("الفصل 13: البداية", "章", 13, True),
        ("الفصل ١٤", "章", 14, False),
        ("제3화 추격", "集", 3, True),
        ("Episodio 4: La trampa", "集", 4, True),
        ("Tập 5", "集", 5, False),
        ("Эпизод 6", "集", 6, False),
        ("Odcinek 7: Powrót", "集", 7, True),
        ("الحلقة 8", "集", 8, False),
    ],
)
def test_multilingual_title_sequence_detection(
    title: str, unit: str, expected_num: int, has_subtitle: bool
):
    assert extract_sequence_number(title, unit=unit) == expected_num
    assert extract_chapter_like_sequence_number(title) == expected_num
    assert extract_chapter_sequence_in_text(f"Progress: {title}") == expected_num
    assert chapter_title_has_subtitle(title, unit) is has_subtitle
    other_unit = "集" if unit == "章" else "章"
    assert extract_sequence_number(title, unit=other_unit) is None


@pytest.mark.unit
def test_paragraph_layout_and_content_range_multilingual():
    # Unicode letters in Cyrillic, Arabic, Korean, accented Latin start prose paragraphs.
    for line in (
        "Вечерний ветер шумел за окном.",
        "كانت المدينة هادئة في تلك الليلة.",
        "그녀는 천천히 창밖을 바라보았다.",
        "Żółć spłynęła po kamiennych schodach.",
        "« Bonsoir », murmura-t-il.",
    ):
        assert _starts_like_paragraph(line) is True

    # Multilingual headings recognized by paragraph_layout.
    for heading in (
        "Bab 1: Permulaan",
        "Capítulo 2",
        "Chương 3",
        "Глава 4",
        "Chapitre 5",
        "Rozdział 6",
        "제7장",
        "الفصل 8",
    ):
        assert _is_heading_line(heading) is True

    # content_range section reads for multilingual headings.
    outline = (
        "Bab 1: Awal\nIsi bab pertama.\n\n"
        "Bab 2: Konflik\nIsi bab kedua.\n\n"
        "Bab 3: Akhir\nIsi bab ketiga."
    )
    sliced = apply_content_range({"content": outline}, {"section": "Bab 2"})
    assert "Isi bab kedua." in sliced["content"]
    assert "Isi bab pertama." not in sliced["content"]
    assert "Isi bab ketiga." not in sliced["content"]


# ===========================================================================
# 3. mcp_tools.py: source_text, write_chapter, load_skill
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_source_copy_nfc_and_quote_normalization_preserves_original_author_bytes():
    # Author pastes NFD Polish text with Polish low-9/curly quotes; model sends NFC + straight quotes.
    author_verbatim = unicodedata.normalize(
        "NFD",
        "Karta postaci: Żółć\nOpis: „Nie ufam nikomu,” powiedział l’homme.\nMotywacja: odnaleźć zaginiony klucz.",
    )
    model_source_text = (
        "Karta postaci: Żółć\n"
        'Opis: "Nie ufam nikomu," powiedział l\'homme.\n'
        "Motywacja: odnaleźć zaginiony klucz."
    )
    assert model_source_text not in author_verbatim

    req_ctx = AgentRequestContext(
        session=MagicMock(),
        user_id="author-pl",
        project_id="proj-pl",
        session_id="sess-pl",
        author_message=f"Zapisz proszę te notatki:\n\n{author_verbatim}\n\nDziękuję.",
    )
    executor = MagicMock()
    executor.create_file.return_value = {
        "id": "char-pl-1",
        "title": "Żółć",
        "file_type": "character",
        "content": author_verbatim,
        "mutation_applied": True,
    }
    with patch.object(req_ctx, "executor", return_value=executor):
        res = _payload(
            await mcp_tools.create_file(
                {"title": "Żółć", "file_type": "character", "source_text": model_source_text},
                request_context=req_ctx,
            )
        )
    assert res["status"] == "success"
    # Saved bytes must be the author's verbatim original slice, NOT the model's rewritten quotes!
    assert executor.create_file.call_args.kwargs["content"] == author_verbatim


@pytest.mark.asyncio
@pytest.mark.unit
async def test_long_paste_verbatim_snap_with_nfc_and_quotes_preserves_author_bytes():
    lines = [
        unicodedata.normalize("NFD", f"Linia {i:02d}: Żółć powiedział „słowo {i}” w ciemności.")
        for i in range(1, 22)
    ]
    author_paste = "\n".join(lines)
    assert len(author_paste) >= 500
    # Model reproduces with NFC + straight quotes + 1 minor typo on line 10 (still >= 98% lines match? 20/21 = 95.2% -> use 55 lines so 54/55 = 98.2%).
    lines = [
        unicodedata.normalize("NFD", f"Linia {i:02d}: Żółć powiedział „słowo {i}” w ciemności.")
        for i in range(1, 56)
    ]
    author_paste = "\n".join(lines)
    model_lines = [
        f'Linia {i:02d}: Żółć powiedział "słowo {i}" w ciemności.'
        for i in range(1, 56)
    ]
    model_lines[10] = model_lines[10].replace("ciemności", "ciemno\ufffdci")
    model_paste = "\n".join(model_lines)

    req_ctx = AgentRequestContext(
        session=MagicMock(),
        user_id="author-pl2",
        project_id="proj-pl2",
        session_id="sess-pl2",
        author_message=f"Oto materiał:\n\n{author_paste}",
    )
    executor = MagicMock()
    executor.create_file.return_value = {
        "id": "lore-pl-1",
        "title": "Materiał",
        "file_type": "lore",
        "content": author_paste,
        "mutation_applied": True,
    }
    with patch.object(req_ctx, "executor", return_value=executor):
        res = _payload(
            await mcp_tools.create_file(
                {"title": "Materiał", "file_type": "lore", "source_text": model_paste},
                request_context=req_ctx,
            )
        )
    assert res["status"] == "success"
    assert executor.create_file.call_args.kwargs["content"] == author_paste


@pytest.mark.asyncio
@pytest.mark.unit
async def test_source_text_error_and_schema_clarify_single_file_vs_streaming():
    req_ctx = AgentRequestContext(
        session=MagicMock(),
        user_id="author-1",
        project_id="proj-1",
        session_id="sess-1",
        author_message="Here is a long outline to split into character cards.",
    )
    res = _payload(
        await mcp_tools.create_file(
            {"title": "Elena", "file_type": "character", "source_text": "Reorganized summary of Elena."},
            request_context=req_ctx,
        )
    )
    assert res["status"] == "error"
    assert res["error_type"] == "source_text_not_in_author_message"
    assert "拆分" in res["error"] and "<file>" in res["error"] and "并行" in res["error"]

    schema_desc = CREATE_FILE_TOOL["description"]
    source_desc = CREATE_FILE_TOOL["input_schema"]["properties"]["source_text"]["description"]
    assert "source_text" in schema_desc and "<file>" in schema_desc
    assert "拆分" in source_desc and "并行" in source_desc


@pytest.mark.asyncio
@pytest.mark.integration
async def test_write_chapter_empty_title_uses_project_language_and_preserves_multilingual_heading(
    db_session, monkeypatch
):
    user, project = _seed_owner_project(db_session, monkeypatch, "write-chap-en", ui_language="en")
    en_folder = File(
        id=f"{project.id}-draft-folder",
        project_id=project.id,
        title="Drafts",
        file_type="folder",
    )
    db_session.add(en_folder)
    db_session.commit()

    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-en",
        current_agent="loop",
    )
    res_empty = _payload(
        await mcp_tools.write_chapter({"chapter": 1, "title": "", "empty": True}, request_context=req_ctx)
    )
    assert res_empty["status"] == "success"
    assert res_empty["data"]["title"] == "Chapter 1"

    # Non-English titled chapter ("Bab 2: Konflik") must NOT get "Chapter 2: " prepended!
    res_id = _payload(
        await mcp_tools.write_chapter(
            {"chapter": 2, "title": "Bab 2: Konflik", "empty": True},
            request_context=req_ctx,
        )
    )
    assert res_id["status"] == "success"
    assert res_id["data"]["title"] == "Bab 2: Konflik"

    # Chinese project still falls back to 第1章.
    zh_user, zh_project = _seed_owner_project(db_session, monkeypatch, "write-chap-zh", ui_language="zh")
    zh_folder = File(
        id=f"{zh_project.id}-draft-folder",
        project_id=zh_project.id,
        title="正文",
        file_type="folder",
    )
    db_session.add(zh_folder)
    db_session.commit()
    zh_ctx = AgentRequestContext(
        session=db_session,
        user_id=zh_user.id,
        project_id=zh_project.id,
        session_id="sess-zh",
        current_agent="loop",
    )
    res_zh = _payload(
        await mcp_tools.write_chapter({"chapter": 1, "title": "", "empty": True}, request_context=zh_ctx)
    )
    assert res_zh["status"] == "success"
    assert res_zh["data"]["title"] == "第1章"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_load_skill_and_read_resource_reverse_map_all_14_official_english_names(
    db_session, monkeypatch
):
    assert len(OFFICIAL_SKILL_EN) == 14
    owner, project = _seed_owner_project(db_session, monkeypatch, "skills-en")
    other, _ = _seed_owner_project(db_session, monkeypatch, "skills-other")

    # Seed all 14 official skills enabled for owner.
    for idx, (zh_name, (en_name, _en_desc)) in enumerate(OFFICIAL_SKILL_EN.items()):
        pub = PublicSkill(
            name=zh_name,
            description=f"{zh_name} desc",
            instructions=f"Instructions for {zh_name} ({en_name})",
            source="official",
            status="approved",
        )
        db_session.add(pub)
        db_session.flush()
        db_session.add(
            UserAddedSkill(user_id=owner.id, public_skill_id=pub.id, is_active=True)
        )
        if idx == 0:
            db_session.add(
                SkillResource(
                    public_skill_id=pub.id,
                    path="references/guide.md",
                    content="Official English-mapped guide",
                    size=29,
                )
            )
    db_session.commit()

    # Every single one of the 14 English names must resolve cleanly via load_skill!
    for zh_name, (en_name, _en_desc) in OFFICIAL_SKILL_EN.items():
        req_ctx = AgentRequestContext(
            session=db_session,
            user_id=owner.id,
            project_id=project.id,
            session_id="sess-skill",
        )
        res = _payload(await mcp_tools.load_skill({"name": en_name.lower()}, request_context=req_ctx))
        assert res["status"] == "success", f"Failed to resolve {en_name!r} -> {zh_name!r}: {res}"
        assert res["data"]["skill_name"] == zh_name
        assert zh_name in res["data"]["instructions"]

    # read_skill_resource also works with English official skill name.
    first_zh, (first_en, _) = next(iter(OFFICIAL_SKILL_EN.items()))
    res_ctx = AgentRequestContext(
        session=db_session,
        user_id=owner.id,
        project_id=project.id,
        session_id="sess-res",
    )
    res_file = _payload(
        await mcp_tools.read_skill_resource(
            {"name": first_en, "path": "references/guide.md"},
            request_context=res_ctx,
        )
    )
    assert res_file["status"] == "success"
    assert res_file["data"]["content"] == "Official English-mapped guide"

    # Tenant isolation: another user who did NOT enable the skill cannot load it!
    other_ctx = AgentRequestContext(
        session=db_session,
        user_id=other.id,
        project_id=project.id,
        session_id="sess-other",
    )
    denied = _payload(
        await mcp_tools.load_skill({"name": "Create an Outline"}, request_context=other_ctx)
    )
    assert denied["status"] == "error"
    assert denied["error_type"] == "skill_not_found"

    # Community skill with the same Chinese name must NOT be resolved via official English mapping.
    comm_pub = PublicSkill(
        name="创建大纲",
        description="community clone",
        instructions="Community instructions",
        source="community",
        status="approved",
    )
    db_session.add(comm_pub)
    db_session.flush()
    db_session.add(UserAddedSkill(user_id=other.id, public_skill_id=comm_pub.id, is_active=True))
    db_session.commit()
    comm_denied = _payload(
        await mcp_tools.load_skill({"name": "Create an Outline"}, request_context=other_ctx)
    )
    assert comm_denied["status"] == "error"
    assert comm_denied["error_type"] == "skill_not_found"

    # User's own custom skill literally named "Create an Outline" still takes precedence.
    custom = UserSkill(user_id=other.id, name="Create an Outline", instructions="My custom outline")
    db_session.add(custom)
    db_session.commit()
    custom_ok = _payload(
        await mcp_tools.load_skill({"name": "Create an Outline"}, request_context=other_ctx)
    )
    assert custom_ok["status"] == "success"
    assert custom_ok["data"]["instructions"] == "My custom outline"
    assert first_zh  # keep flake/lint clean


# ===========================================================================
# 4. PostgreSQL case-insensitive file search
# ===========================================================================


@pytest.mark.integration
def test_file_search_case_insensitive_and_escaped_ilike(db_session, monkeypatch):
    _user, project = _seed_owner_project(db_session, monkeypatch, "search-ilike")
    f_elena = File(
        project_id=project.id,
        title="Elena Vance",
        file_type="character",
        content="Elena stepped into the sunlit courtyard.",
    )
    f_percent = File(
        project_id=project.id,
        title="Budget 100% confirmed",
        file_type="lore",
        content="Risk is 100% mitigated; code_name is alpha_1.",
    )
    f_other = File(
        project_id=project.id,
        title="Budget 1000 gold",
        file_type="lore",
        content="CodeXname is alphaX1.",
    )
    db_session.add_all([f_elena, f_percent, f_other])
    db_session.commit()

    service = FileSearchService()
    hits = service.search(project_id=project.id, query="elena", session=db_session)
    assert [r.entity_id for r in hits] == [f_elena.id]

    hits_upper = service.search(project_id=project.id, query="ELENA", session=db_session)
    assert [r.entity_id for r in hits_upper] == [f_elena.id]

    # `%` and `_` in query must be treated literally, not as SQL wildcards.
    hits_pct = service.search(project_id=project.id, query="100%", session=db_session)
    assert [r.entity_id for r in hits_pct] == [f_percent.id]

    hits_underscore = service.search(project_id=project.id, query="alpha_1", session=db_session)
    assert [r.entity_id for r in hits_underscore] == [f_percent.id]

    # Verify PostgreSQL dialect compiles ILIKE with ESCAPE '\'.
    expr = col(File.title).ilike("%elena%", escape="\\")
    compiled = str(expr.compile(dialect=postgresql.dialect()))
    assert "ILIKE" in compiled
    assert "ESCAPE" in compiled


# ===========================================================================
# 5. Unified language resolution + localized default session titles & folders
# ===========================================================================


@pytest.mark.integration
def test_resolve_user_language_precedence_and_title_helpers(db_session, monkeypatch):
    en_user, _ = _seed_owner_project(db_session, monkeypatch, "lang-en", ui_language="en")
    unset_user, _ = _seed_owner_project(db_session, monkeypatch, "lang-unset", ui_language=None)

    # Empty/missing/wildcard header falls back to stored ui_language (fixes U_f4eb8c2b race).
    assert resolve_user_language(db_session, en_user.id, None) == "en"
    assert resolve_user_language(db_session, en_user.id, "") == "en"
    assert resolve_user_language(db_session, en_user.id, "   ") == "en"
    assert resolve_user_language(db_session, en_user.id, "*") == "en"

    # Explicit header always takes precedence over stored ui_language.
    assert resolve_user_language(db_session, en_user.id, "zh-CN,zh;q=0.9") == "zh"
    assert resolve_user_language(db_session, en_user.id, "pl-PL,pl;q=0.9") == "en"

    # Unset ui_language + missing header stays backward-compatible ('zh').
    assert resolve_user_language(db_session, unset_user.id, None) == "zh"
    assert resolve_user_language(db_session, unset_user.id, "en-US") == "en"

    assert default_chat_session_title("en") == DEFAULT_CHAT_SESSION_TITLE_EN == "AI Assistant"
    assert default_chat_session_title("zh") == DEFAULT_CHAT_SESSION_TITLE_ZH == "AI 助手对话"
    assert new_chat_session_title("en") == NEW_CHAT_SESSION_TITLE_EN == "New Chat"
    assert new_chat_session_title("zh") == NEW_CHAT_SESSION_TITLE_ZH == "新对话"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_api_localized_first_work_project_and_chat_sessions_with_empty_header(
    client: AsyncClient, db_session: Session, monkeypatch
):
    from models import UserPersonaProfile
    from services.first_work_service import enroll_new_user

    monkeypatch.setenv("FIRST_WORK_ONBOARDING_ROLLOUT_PERCENT", "100")
    user = User(
        username="en_race_user",
        email="en_race_user@example.com",
        hashed_password=hash_password("password123"),
        name="EN Race User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    enroll_new_user(db_session, user)
    db_session.add(UserPersonaProfile(user_id=user.id, skipped=True))
    set_ui_language(db_session, user.id, "en")
    db_session.commit()

    login = await client.post(
        "/api/auth/login",
        data={"username": "en_race_user", "password": "password123"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    # Deliberately omit Accept-Language header (simulating U_f4eb8c2b hydration race).
    headers = {"Authorization": f"Bearer {token}"}

    # 1) First-work initialization should create English folders, NOT Chinese 正文/大纲.
    fw_resp = await client.post(
        "/api/v1/workspace/first-work",
        json={"preferred_type": "novel"},
        headers=headers,
    )
    assert fw_resp.status_code == 200
    fw_project_id = fw_resp.json()["project_id"]
    fw_folders = db_session.exec(
        select(File.title).where(File.project_id == fw_project_id, File.file_type == "folder")
    ).all()
    assert fw_folders
    assert all(t not in {"正文", "大纲", "角色", "设定"} for t in fw_folders)

    # 2) Project creation with empty Accept-Language should also honor ui_language='en'.
    proj_resp = await client.post(
        "/api/v1/projects",
        json={"project_type": "novel"},
        headers=headers,
    )
    assert proj_resp.status_code == 200
    proj_data = proj_resp.json()
    proj_folders = db_session.exec(
        select(File.title).where(File.project_id == proj_data["id"], File.file_type == "folder")
    ).all()
    assert proj_folders
    assert all(t not in {"正文", "大纲", "角色", "设定"} for t in proj_folders)

    # 3) Chat session creation should use localized "AI Assistant" and "New Chat".
    sess_resp = await client.get(
        f"/api/v1/chat/session/{proj_data['id']}",
        headers=headers,
    )
    assert sess_resp.status_code == 200
    assert sess_resp.json()["title"] == "AI Assistant"

    new_sess_resp = await client.post(
        f"/api/v1/chat/session/{proj_data['id']}/new",
        headers=headers,
    )
    assert new_sess_resp.status_code == 200
    assert new_sess_resp.json()["title"] == "New Chat"

    # Explicit zh header overrides ui_language='en'.
    zh_new_resp = await client.post(
        f"/api/v1/chat/session/{proj_data['id']}/new",
        headers={**headers, "Accept-Language": "zh-CN"},
    )
    assert zh_new_resp.status_code == 200
    assert zh_new_resp.json()["title"] == "新对话"
