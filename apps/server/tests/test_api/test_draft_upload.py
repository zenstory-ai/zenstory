"""
Tests for Draft Upload API - POST /api/v1/projects/{project_id}/files/upload-drafts

Tests the bulk draft upload endpoint with chapter splitting:
- Single/multiple file uploads (.txt, .md)
- Auto chapter splitting by headings (Chinese chapter, Chapter X, numbered)
- Validation: file type, size, char count, file count limits
- Encoding: UTF-8, GBK/GB18030, UTF-8 BOM
- Edge cases: empty files, heading-only content, single heading, partial success
- Auth: unauthenticated, wrong-user access
- Metadata: word_count, file_type correctness, ordering
"""

import json

import pytest
from httpx import AsyncClient

from api.files import (
    DRAFT_MAX_BYTES,
    DRAFT_MAX_CHARS,
    DRAFT_MAX_FILES,
)
from core.error_codes import ErrorCode
from models import File, Project, User

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_user_project(client: AsyncClient, db_session, username: str):
    """Create a user, log in, create a project, and return (token, project)."""
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

    login_resp = await client.post(
        "/api/auth/login",
        data={"username": username, "password": "password123"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    project = Project(name=f"Project-{username}", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    return token, project, user


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ==================== Success Cases ====================


@pytest.mark.integration
async def test_upload_single_txt_creates_single_draft(client: AsyncClient, db_session):
    """Upload a single .txt file creates one draft file."""
    token, project, _ = await _create_user_project(client, db_session, "du_user1")

    content = "This is a simple draft content."
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("my_draft.txt", content.encode("utf-8"), "text/plain")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["files"]) == 1
    assert data["errors"] == []

    created = data["files"][0]
    assert created["file_type"] == "draft"
    assert created["title"] == "my_draft"
    assert created["content"] == content


@pytest.mark.integration
async def test_upload_single_md_creates_single_draft(client: AsyncClient, db_session):
    """Upload a single .md file creates one draft file."""
    token, project, _ = await _create_user_project(client, db_session, "du_user2")

    content = "# Chapter 1\n\nSome markdown content."
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("notes.md", content.encode("utf-8"), "text/markdown")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["files"][0]["file_type"] == "draft"
    assert data["files"][0]["title"] == "notes"


@pytest.mark.integration
async def test_upload_splits_by_chinese_chapter_headings(client: AsyncClient, db_session):
    """Content with multiple 第X章 headings splits into separate drafts."""
    token, project, _ = await _create_user_project(client, db_session, "du_user3")

    content = (
        "第一章 开始\n"
        "这是第一章的内容。\n\n"
        "第二章 发展\n"
        "这是第二章的内容。\n\n"
        "第三章 结局\n"
        "这是第三章的内容。"
    )
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("novel.txt", content.encode("utf-8"), "text/plain")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["files"]) == 3

    titles = [f["title"] for f in data["files"]]
    assert "novel - 第一章 开始" in titles
    assert "novel - 第二章 发展" in titles
    assert "novel - 第三章 结局" in titles


@pytest.mark.integration
async def test_upload_splits_by_chapter_english_headings(client: AsyncClient, db_session):
    """Content with 'Chapter X' headings splits correctly."""
    token, project, _ = await _create_user_project(client, db_session, "du_user4")

    content = (
        "Chapter 1 The Beginning\n"
        "Content of chapter one.\n\n"
        "Chapter 2 The Middle\n"
        "Content of chapter two.\n\n"
        "Chapter 3 The End\n"
        "Content of chapter three."
    )
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("story.txt", content.encode("utf-8"), "text/plain")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3

    titles = [f["title"] for f in data["files"]]
    assert "story - Chapter 1 The Beginning" in titles
    assert "story - Chapter 3 The End" in titles


@pytest.mark.integration
async def test_upload_splits_by_numbered_headings(client: AsyncClient, db_session):
    """Content with '1. xxx' / '2. xxx' numbered headings splits correctly."""
    token, project, _ = await _create_user_project(client, db_session, "du_user5")

    content = (
        "1. First section\n"
        "Content for first.\n\n"
        "2. Second section\n"
        "Content for second.\n\n"
        "3. Third section\n"
        "Content for third."
    )
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("sections.txt", content.encode("utf-8"), "text/plain")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3

    titles = [f["title"] for f in data["files"]]
    assert "sections - 1. First section" in titles


@pytest.mark.integration
async def test_upload_no_chapter_headings_creates_single_draft(client: AsyncClient, db_session):
    """Content without any chapter headings creates a single draft."""
    token, project, _ = await _create_user_project(client, db_session, "du_user6")

    content = "Just some plain text.\nNo chapter headings here.\nMore text."
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("plain.txt", content.encode("utf-8"), "text/plain")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["files"][0]["title"] == "plain"
    assert data["files"][0]["content"] == content


@pytest.mark.integration
async def test_upload_multiple_files_at_once(client: AsyncClient, db_session):
    """Uploading multiple files creates separate drafts for each."""
    token, project, _ = await _create_user_project(client, db_session, "du_user7")

    file1_content = "Content of file one."
    file2_content = "Content of file two."
    file3_content = "Content of file three."

    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files=[
            ("files", ("file_a.txt", file1_content.encode("utf-8"), "text/plain")),
            ("files", ("file_b.md", file2_content.encode("utf-8"), "text/markdown")),
            ("files", ("file_c.txt", file3_content.encode("utf-8"), "text/plain")),
        ],
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["errors"]) == 0

    titles = [f["title"] for f in data["files"]]
    assert "file_a" in titles
    assert "file_b" in titles
    assert "file_c" in titles


@pytest.mark.integration
async def test_upload_with_explicit_parent_id(client: AsyncClient, db_session):
    """Uploading with explicit parent_id places drafts under that folder."""
    token, project, _ = await _create_user_project(client, db_session, "du_user8")

    folder = File(project_id=project.id, title="My Drafts", file_type="folder")
    db_session.add(folder)
    db_session.commit()

    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("draft.txt", b"hello world", "text/plain")},
        data={"parent_id": folder.id},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["files"][0]["parent_id"] == folder.id


@pytest.mark.integration
async def test_upload_without_parent_id_auto_creates_draft_folder(client: AsyncClient, db_session):
    """Uploading without parent_id auto-creates the draft folder."""
    token, project, _ = await _create_user_project(client, db_session, "du_user9")

    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("auto_draft.txt", b"auto content", "text/plain")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1

    parent_id = data["files"][0]["parent_id"]
    assert parent_id is not None

    draft_folder = db_session.get(File, parent_id)
    assert draft_folder is not None
    assert draft_folder.file_type == "folder"


@pytest.mark.integration
async def test_upload_creates_correct_file_type_draft(client: AsyncClient, db_session):
    """All uploaded files should have file_type='draft'."""
    token, project, _ = await _create_user_project(client, db_session, "du_user10")

    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files=[
            ("files", ("a.txt", b"content a", "text/plain")),
            ("files", ("b.md", b"content b", "text/markdown")),
        ],
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    for f in response.json()["files"]:
        assert f["file_type"] == "draft"


@pytest.mark.integration
async def test_upload_sets_word_count_in_metadata(client: AsyncClient, db_session):
    """Uploaded draft files should include word_count in metadata."""
    token, project, _ = await _create_user_project(client, db_session, "du_user11")

    content = "Hello world"
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("words.txt", content.encode("utf-8"), "text/plain")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1

    metadata = json.loads(data["files"][0]["file_metadata"])
    assert "word_count" in metadata
    assert metadata["word_count"] > 0
    assert metadata["source"] == "upload"
    assert metadata["original_filename"] == "words.txt"
    assert metadata["char_count"] == len(content)


@pytest.mark.integration
async def test_upload_chapter_ordering_numeric_sort(client: AsyncClient, db_session):
    """Chapters with 第9章/第10章 should be ordered by numeric value, not lexicographic."""
    token, project, _ = await _create_user_project(client, db_session, "du_user12")

    content = (
        "第九章 Nine\n"
        "Content nine.\n\n"
        "第十章 Ten\n"
        "Content ten.\n\n"
        "第十一章 Eleven\n"
        "Content eleven."
    )
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("ordered.txt", content.encode("utf-8"), "text/plain")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3

    titles = [f["title"] for f in data["files"]]
    assert titles.index("ordered - 第九章 Nine") < titles.index("ordered - 第十章 Ten")
    assert titles.index("ordered - 第十章 Ten") < titles.index("ordered - 第十一章 Eleven")


# ==================== Validation / Error Cases ====================


@pytest.mark.integration
async def test_upload_rejects_pdf_file(client: AsyncClient, db_session):
    """Uploading a .pdf file should add an error for that file."""
    token, project, _ = await _create_user_project(client, db_session, "du_user13")

    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("doc.pdf", b"%PDF-1.4 fake", "application/pdf")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert len(data["errors"]) == 1
    assert ErrorCode.FILE_TYPE_INVALID in data["errors"][0]


@pytest.mark.integration
async def test_upload_rejects_oversized_file(client: AsyncClient, db_session):
    """Uploading a file larger than DRAFT_MAX_BYTES should add an error."""
    token, project, _ = await _create_user_project(client, db_session, "du_user14")

    oversized = b"x" * (DRAFT_MAX_BYTES + 1)
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("big.txt", oversized, "text/plain")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert len(data["errors"]) == 1
    assert ErrorCode.FILE_TOO_LARGE in data["errors"][0]


@pytest.mark.integration
async def test_upload_rejects_over_char_limit(client: AsyncClient, db_session):
    """Uploading a file with more than DRAFT_MAX_CHARS characters should add an error."""
    token, project, _ = await _create_user_project(client, db_session, "du_user15")

    # Create content that is within byte limit but exceeds char limit
    # Each char = 1 byte for ASCII, so char count = byte count
    oversized_chars = "a" * (DRAFT_MAX_CHARS + 1)
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("long.txt", oversized_chars.encode("utf-8"), "text/plain")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert len(data["errors"]) == 1
    assert ErrorCode.FILE_CONTENT_TOO_LONG in data["errors"][0]


@pytest.mark.integration
async def test_upload_rejects_too_many_files(client: AsyncClient, db_session):
    """Uploading more than DRAFT_MAX_FILES files should return 400."""
    token, project, _ = await _create_user_project(client, db_session, "du_user16")

    files = [
        ("files", (f"file_{i}.txt", f"content {i}".encode(), "text/plain"))
        for i in range(DRAFT_MAX_FILES + 1)
    ]
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files=files,
        headers=_auth_headers(token),
    )
    assert response.status_code == 400


@pytest.mark.integration
async def test_upload_file_with_no_extension_adds_error(client: AsyncClient, db_session):
    """Uploading a file with no extension should add an error (no valid extension)."""
    token, project, _ = await _create_user_project(client, db_session, "du_user17")

    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("noext", b"some content", "text/plain")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert len(data["errors"]) == 1
    assert ErrorCode.FILE_TYPE_INVALID in data["errors"][0]


@pytest.mark.integration
async def test_upload_with_invalid_parent_id(client: AsyncClient, db_session):
    """Uploading with an invalid parent_id should return 400."""
    token, project, _ = await _create_user_project(client, db_session, "du_user18")

    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("draft.txt", b"content", "text/plain")},
        data={"parent_id": "00000000-0000-0000-0000-000000000000"},
        headers=_auth_headers(token),
    )
    assert response.status_code == 400


@pytest.mark.integration
async def test_upload_unauthenticated(client: AsyncClient, db_session):
    """Uploading without authentication returns 401."""
    response = await client.post(
        "/api/v1/projects/fake-project/files/upload-drafts",
        files={"files": ("test.txt", b"content", "text/plain")},
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_upload_other_users_project(client: AsyncClient, db_session):
    """Uploading to another user's project returns 403."""
    token1, _, _ = await _create_user_project(client, db_session, "du_user19a")
    _, project2, _ = await _create_user_project(client, db_session, "du_user19b")

    response = await client.post(
        f"/api/v1/projects/{project2.id}/files/upload-drafts",
        files={"files": ("test.txt", b"content", "text/plain")},
        headers=_auth_headers(token1),
    )
    assert response.status_code == 403


# ==================== Encoding Cases ====================


@pytest.mark.integration
async def test_upload_utf8_content(client: AsyncClient, db_session):
    """UTF-8 encoded file content is decoded correctly."""
    token, project, _ = await _create_user_project(client, db_session, "du_user20")

    content = "你好世界\n这是中文内容。"
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("chinese.txt", content.encode("utf-8"), "text/plain")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["files"][0]["content"] == content


@pytest.mark.integration
async def test_upload_gbk_encoded_content(client: AsyncClient, db_session):
    """GBK encoded file content is decoded via gb18030 fallback."""
    token, project, _ = await _create_user_project(client, db_session, "du_user21")

    content = "你好世界\nGBK编码的内容。"
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("gbk.txt", content.encode("gb18030"), "text/plain")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    # The content should be decoded back to the same text
    assert "你好世界" in data["files"][0]["content"]


@pytest.mark.integration
async def test_upload_utf8_bom_content(client: AsyncClient, db_session):
    """UTF-8 with BOM file content is decoded correctly."""
    token, project, _ = await _create_user_project(client, db_session, "du_user22")

    content = "UTF-8 BOM content here."
    bom_content = b"\xef\xbb\xbf" + content.encode("utf-8")
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("bom.txt", bom_content, "text/plain")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    # UTF-8 BOM is decoded as valid UTF-8 (the BOM char may appear as a prefix)
    assert content in data["files"][0]["content"]


# ==================== Edge Cases ====================


@pytest.mark.integration
async def test_upload_empty_file_creates_draft_with_empty_content(client: AsyncClient, db_session):
    """An empty .txt file should still create a draft with empty content."""
    token, project, _ = await _create_user_project(client, db_session, "du_user23")

    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("empty.txt", b"", "text/plain")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["files"][0]["content"] == ""
    assert data["files"][0]["file_type"] == "draft"


@pytest.mark.integration
async def test_upload_file_with_only_chapter_headings(client: AsyncClient, db_session):
    """File with only chapter headings and no body content creates drafts."""
    token, project, _ = await _create_user_project(client, db_session, "du_user24")

    content = "第一章 标题一\n\n第二章 标题二\n\n第三章 标题三"
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("headings_only.txt", content.encode("utf-8"), "text/plain")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    # With at least 2 headings, it should split
    assert data["total"] >= 2


@pytest.mark.integration
async def test_upload_single_chapter_heading_no_split(client: AsyncClient, db_session):
    """File with exactly 1 chapter heading should NOT split (treated as no-chapter)."""
    token, project, _ = await _create_user_project(client, db_session, "du_user25")

    content = "第一章 唯一的一章\n这是内容。"
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("single_chapter.txt", content.encode("utf-8"), "text/plain")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    # Only 1 titled heading -> titled_count < 2 -> no split
    assert data["total"] == 1
    assert data["files"][0]["title"] == "single_chapter"


@pytest.mark.integration
async def test_upload_mixed_valid_and_invalid_files(client: AsyncClient, db_session):
    """Uploading a mix of valid and invalid files yields partial success."""
    token, project, _ = await _create_user_project(client, db_session, "du_user26")

    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files=[
            ("files", ("valid.txt", b"valid content", "text/plain")),
            ("files", ("invalid.pdf", b"%PDF-1.4 fake", "application/pdf")),
            ("files", ("also_valid.md", b"markdown content", "text/markdown")),
        ],
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["errors"]) == 1
    assert ErrorCode.FILE_TYPE_INVALID in data["errors"][0]

    # Verify only valid files were created
    titles = [f["title"] for f in data["files"]]
    assert "valid" in titles
    assert "also_valid" in titles


@pytest.mark.integration
async def test_upload_rejects_docx_extension(client: AsyncClient, db_session):
    """Uploading a .docx file should be rejected with FILE_TYPE_INVALID."""
    token, project, _ = await _create_user_project(client, db_session, "du_user27")

    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("document.docx", b"PK\x03\x04 fake docx", "application/octet-stream")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert len(data["errors"]) == 1
    assert ErrorCode.FILE_TYPE_INVALID in data["errors"][0]


# ==================== Chapter Title Stripping ====================


@pytest.mark.integration
async def test_upload_strips_chapter_heading_from_content(client: AsyncClient, db_session):
    """Chapter headings should be stripped from content body (they're already the title)."""
    token, project, _ = await _create_user_project(client, db_session, "du_strip1")

    content = (
        "第一章 开始\n"
        "  这是第一章的内容。\n\n"
        "第二章 发展\n"
        "  这是第二章的内容。"
    )
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("novel.txt", content.encode("utf-8"), "text/plain")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2

    # Content should NOT contain the chapter heading line as its own line
    for f in data["files"]:
        heading_lines = [line.strip() for line in f["content"].splitlines()]
        assert "第一章 开始" not in heading_lines
        assert "第二章 发展" not in heading_lines
        # But should contain the actual paragraph text
        assert "这是" in f["content"]


@pytest.mark.integration
async def test_upload_preserves_paragraph_indentation(client: AsyncClient, db_session):
    """Paragraph indentation (leading spaces) should be preserved after title stripping."""
    token, project, _ = await _create_user_project(client, db_session, "du_strip2")

    content = (
        "第一章 缩进测试\n"
        "　　这是第一段，前面有两个全角空格缩进。\n"
        "　　这是第二段，同样有缩进。"
    )
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("indent.txt", content.encode("utf-8"), "text/plain")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1

    body = data["files"][0]["content"]
    # The heading line should be removed
    assert "第一章 缩进测试" not in body
    # The first paragraph should still have its indentation
    assert body.startswith("　　这是第一段")


@pytest.mark.integration
async def test_upload_english_chapter_heading_stripped_from_content(client: AsyncClient, db_session):
    """English 'Chapter X' headings should also be stripped from content."""
    token, project, _ = await _create_user_project(client, db_session, "du_strip3")

    content = (
        "Chapter 1 The Beginning\n"
        "Content of chapter one.\n\n"
        "Chapter 2 The End\n"
        "Content of chapter two."
    )
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload-drafts",
        files={"files": ("en_novel.txt", content.encode("utf-8"), "text/plain")},
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2

    for f in data["files"]:
        heading_lines = [line.strip() for line in f["content"].splitlines()]
        assert "Chapter 1 The Beginning" not in heading_lines
        assert "Chapter 2 The End" not in heading_lines
        assert "Content of chapter" in f["content"]
