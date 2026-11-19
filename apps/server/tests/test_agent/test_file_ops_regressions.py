"""
文件工具回归测试：陈旧读取 / 版本顺序 / replace_all 截断 / query_files 分页

覆盖：
- edit_file / update_file 在共享 session（SQLite 路径）下必须基于数据库最新内容
  做 read-modify-write，而不是身份映射里的陈旧快照
- 并发编辑同一文件时，最高版本号的快照内容必须等于最终 file.content
- fuzzy 路径 replace_all 不得在 20 处被静默截断
- query_files 的 limit/offset 作用于多 file_types 合并后的结果集
"""

import threading
from datetime import datetime

import pytest
from services.file_version import FileVersionService
from sqlmodel import Session, select

from agent.tools.file_ops import FileCRUD, FileEditor
from models import File, Project, User
from models.file_edit_operation import FileEditOperation
from models.file_version import FileVersion
from services.file_edit_operations import undo_edit_operation


@pytest.fixture
def local_history_sideeffects(monkeypatch):
    from agent.tools.file_ops import crud

    monkeypatch.setattr(crud.activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)


def _history_failure(*_args, **_kwargs):
    raise RuntimeError("Optional AI history failure")


def test_ai_create_rolls_back_file_when_required_history_fails(
    db_session, test_user, test_project, local_history_sideeffects, monkeypatch
):
    from agent.tools.write_errors import FileWriteRecoveryError

    crud = FileCRUD(db_session, test_user.id)
    monkeypatch.setattr(FileVersionService, "create_version", _history_failure)
    with pytest.raises(FileWriteRecoveryError):
        crud.create_file(test_project.id, "New chapter", "draft", content="Created body")
    db_session.expire_all()
    assert db_session.exec(select(File).where(File.title == "New chapter")).first() is None


def test_ai_create_with_body_has_exact_empty_anchor_and_can_undo_to_empty(
    db_session, test_user, test_project, local_history_sideeffects
):
    crud = FileCRUD(db_session, test_user.id)
    result = crud.create_file(test_project.id, "New chapter", "draft", content="Created body")
    operation = db_session.get(FileEditOperation, result["undo"]["operation_id"])
    assert operation is not None
    before = db_session.get(FileVersion, operation.before_version_id)
    after = db_session.get(FileVersion, operation.after_version_id)
    assert before is not None and after is not None
    assert FileVersionService().get_content_at_version(
        db_session, result["id"], before.version_number
    ) == ""
    assert FileVersionService().get_content_at_version(
        db_session, result["id"], after.version_number
    ) == "Created body"

    undone = undo_edit_operation(
        db_session,
        operation_id=operation.id,
        file_id=result["id"],
        user_id=test_user.id,
    )
    db_session.expire_all()
    restored = db_session.get(File, result["id"])
    assert undone["status"] == "undone"
    assert restored is not None
    assert restored.content == ""
    assert (restored.title, restored.parent_id, restored.order) == (
        result["title"], result["parent_id"], result["order"]
    )


def test_ai_created_body_merge_undo_preserves_later_author_text(
    db_session, test_user, test_project, local_history_sideeffects
):
    crud = FileCRUD(db_session, test_user.id)
    result = crud.create_file(test_project.id, "New chapter", "draft", content="Created body")
    file = db_session.get(File, result["id"])
    assert file is not None
    file.content = "Created body\nAuthor addition"
    file.updated_at = datetime(2026, 1, 2)
    db_session.add(file)
    db_session.commit()

    preview = undo_edit_operation(
        db_session,
        operation_id=result["undo"]["operation_id"],
        file_id=file.id,
        user_id=test_user.id,
    )
    assert preview["status"] == "needs_merge"
    done = undo_edit_operation(
        db_session,
        operation_id=result["undo"]["operation_id"],
        file_id=file.id,
        user_id=test_user.id,
        expected_updated_at=datetime.fromisoformat(preview["current"]["updated_at"]),
        expected_after_content_sha256=preview["current"]["content_sha256"],
        resolved_content="Author addition",
        metadata_resolution="preserve",
    )
    db_session.refresh(file)
    assert done["status"] == "undone"
    assert file.content == "Author addition"


def test_update_structural_gate_is_shared_for_metadata_and_exclusive_for_title(
    db_session, test_user, test_project, local_history_sideeffects, monkeypatch
):
    from agent.tools.file_ops import crud as crud_module

    file = File(
        project_id=test_project.id,
        title="Structure",
        content="",
        file_type="draft",
    )
    db_session.add(file)
    db_session.commit()
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        crud_module,
        "lock_project_for_files",
        lambda _session, project_id, *, exclusive=False: calls.append(
            (project_id, exclusive)
        ),
    )
    crud = FileCRUD(db_session, test_user.id)

    crud.update_file(file.id, metadata={"status": "draft"})
    crud.update_file(file.id, title="Renamed")

    assert calls == [(test_project.id, False), (test_project.id, True)]


def test_ai_empty_create_gets_first_history_when_content_is_filled(db_session, test_user, test_project, local_history_sideeffects):
    crud = FileCRUD(db_session, test_user.id)
    result = crud.create_file(test_project.id, "Empty chapter", "draft", content="")
    assert db_session.exec(select(FileVersion).where(FileVersion.file_id == result["id"])).all() == []
    crud.update_file(result["id"], content="Streamed body")
    db_session.expire_all()
    assert db_session.get(File, result["id"]).content == "Streamed body"
    versions = db_session.exec(
        select(FileVersion)
        .where(FileVersion.file_id == result["id"])
        .order_by(FileVersion.version_number)
    ).all()
    assert [(version.version_number, version.change_source) for version in versions] == [
        (1, "system"),
        (2, "ai"),
    ]
    assert FileVersionService().get_content_at_version(db_session, result["id"], 1) == ""
    assert FileVersionService().get_content_at_version(db_session, result["id"], 2) == "Streamed body"


def test_ai_update_rolls_back_when_required_after_history_fails(db_session, test_user, test_project, local_history_sideeffects, monkeypatch):
    from agent.tools.write_errors import FileWriteRecoveryError

    crud = FileCRUD(db_session, test_user.id)
    result = crud.create_file(test_project.id, "Existing chapter", "draft", content="Original body")
    original_create = FileVersionService.create_version

    def fail_ai_after(self, *args, **kwargs):
        if kwargs.get("change_source") == "ai":
            raise RuntimeError("required AI history failure")
        return original_create(self, *args, **kwargs)

    monkeypatch.setattr(FileVersionService, "create_version", fail_ai_after)
    with pytest.raises(FileWriteRecoveryError):
        crud.update_file(result["id"], content="Changed body")
    db_session.expire_all()
    assert db_session.get(File, result["id"]).content == "Original body"
    versions = db_session.exec(
        select(FileVersion)
        .where(FileVersion.file_id == result["id"])
        .order_by(FileVersion.version_number)
    ).all()
    assert [version.version_number for version in versions] == [1, 2]
    assert FileVersionService().get_content_at_version(db_session, result["id"], 2) == "Original body"

# ========== Fixtures ==========


@pytest.fixture
def test_user(db_session):
    """创建测试用户"""
    from services.core.auth_service import hash_password

    user = User(
        email="file_ops_regressions@example.com",
        username="file_ops_regressions",
        hashed_password=hash_password("password123"),
        name="Regression User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def test_project(db_session, test_user):
    """创建测试项目"""
    project = Project(
        name="File Ops Regression Project",
        description="Regression project for file ops",
        owner_id=test_user.id,
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.fixture
def version_engine(db_session, monkeypatch):
    """让独立版本 session（database.create_session）落到测试库"""
    import database

    engine = db_session.get_bind()
    monkeypatch.setattr(database, "create_session", lambda: Session(engine))
    return engine


def _create_file(db_session, project_id: str, title: str, content: str, **kwargs) -> File:
    file = File(
        project_id=project_id,
        title=title,
        file_type=kwargs.pop("file_type", "draft"),
        content=content,
        **kwargs,
    )
    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)
    return file


# ========== Bug: 共享 session 身份映射陈旧读取 ==========


def test_edit_file_reads_latest_content_past_identity_map(
    db_session, test_user, test_project, version_engine
):
    """共享 session 已缓存旧 File；edit_file 必须基于其它 session 提交的最新内容。"""
    file = _create_file(db_session, test_project.id, "草稿", "旧内容第一版。")

    # 模拟上下文组装阶段：共享 session 把 File 加载进身份映射
    cached = db_session.get(File, file.id)
    assert cached.content == "旧内容第一版。"

    # 用户在 LLM 往返期间通过独立 session 自动保存了新内容
    with Session(version_engine) as other:
        concurrent = other.get(File, file.id)
        concurrent.content = "用户自动保存的新内容。"
        other.add(concurrent)
        other.commit()

    editor = FileEditor(db_session, user_id=test_user.id)
    editor.edit_file(file.id, [{"op": "append", "text": "AI追加的段落。"}])

    with Session(version_engine) as check:
        latest = check.get(File, file.id)
        assert latest.content == "用户自动保存的新内容。AI追加的段落。"


def test_update_file_content_changed_uses_fresh_content(
    db_session, test_user, test_project, version_engine
):
    """update_file 的 content_changed 判定必须基于数据库最新内容而非缓存值。"""
    file = _create_file(db_session, test_project.id, "设定", "v1")

    cached = db_session.get(File, file.id)
    assert cached.content == "v1"

    with Session(version_engine) as other:
        concurrent = other.get(File, file.id)
        concurrent.content = "v2"
        other.add(concurrent)
        other.commit()

    crud = FileCRUD(db_session, user_id=test_user.id)
    crud.update_file(file.id, content="v1")

    with Session(version_engine) as check:
        latest = check.get(File, file.id)
        assert latest.content == "v1"
        versions = list(
            check.exec(
                select(FileVersion)
                .where(FileVersion.file_id == file.id)
                .order_by(FileVersion.version_number)
            ).all()
        )
        # 并发写入的 v2 不在历史里：AI 覆盖前先备份成 system 版本，再记 AI 版本。
        assert [(v.change_source, v.change_summary) for v in versions] == [
            ("system", "Before AI edit"),
            ("ai", "AI 更新文件内容"),
        ]
        assert FileVersionService().get_content_at_version(check, file.id, 1) == "v2"


# ========== Bug: 并发编辑时版本链头与正文逆序 ==========


def _assert_head_version_matches_content(engine, file_id: str) -> None:
    with Session(engine) as check:
        latest_file = check.get(File, file_id)
        service = FileVersionService()
        versions = service.get_versions(check, file_id, include_auto_save=True)
        assert versions, "应当已生成版本快照"
        head = versions[0]  # newest first
        head_content = service.get_content_at_version(check, file_id, head.version_number)
        assert head_content == latest_file.content
        return latest_file.content


def test_concurrent_edit_file_head_version_matches_content(
    db_session, test_user, test_project, version_engine, monkeypatch
):
    """两个并发 edit_file：最高版本号的快照内容必须等于最终 file.content。"""
    file = _create_file(db_session, test_project.id, "并发草稿", "基础内容。")
    file_id = file.id

    a_in_version = threading.Event()
    b_finished = threading.Event()
    from services import file_edit_operations

    orig_stage = file_edit_operations.stage_recoverable_file_mutation

    def paced_stage(*args, **kwargs):
        if threading.current_thread().name == "editor-a":
            a_in_version.set()
            # 给并发编辑留出插队窗口：若该点位于内容 commit 之后且无互斥，
            # 另一个编辑会先提交内容并先写版本，导致版本链头指向旧内容
            b_finished.wait(timeout=2.0)
        return orig_stage(*args, **kwargs)

    monkeypatch.setattr(file_edit_operations, "stage_recoverable_file_mutation", paced_stage)

    errors: list[Exception] = []

    def run_edit(text: str):
        s = Session(version_engine)
        try:
            FileEditor(s, user_id=test_user.id).edit_file(
                file_id, [{"op": "append", "text": text}]
            )
        except Exception as e:  # pragma: no cover - 失败时直接暴露
            errors.append(e)
        finally:
            s.close()

    def run_b():
        a_in_version.wait(timeout=5.0)
        run_edit("B段。")
        b_finished.set()

    ta = threading.Thread(target=run_edit, args=("A段。",), name="editor-a")
    tb = threading.Thread(target=run_b, name="editor-b")
    ta.start()
    tb.start()
    ta.join(timeout=15)
    tb.join(timeout=15)
    assert not errors

    final_content = _assert_head_version_matches_content(version_engine, file_id)
    assert "A段。" in final_content
    assert "B段。" in final_content


def test_concurrent_update_file_head_version_matches_content(
    db_session, test_user, test_project, version_engine, monkeypatch
):
    """两个并发 update_file：最高版本号的快照内容必须等于最终 file.content。"""
    file = _create_file(db_session, test_project.id, "并发设定", "基础版本。")
    file_id = file.id

    a_in_version = threading.Event()
    b_finished = threading.Event()
    orig_create = FileCRUD._create_version

    def paced_version(self, fid, content, **kwargs):
        if threading.current_thread().name == "updater-a":
            a_in_version.set()
            b_finished.wait(timeout=2.0)
        orig_create(self, fid, content, **kwargs)

    monkeypatch.setattr(FileCRUD, "_create_version", paced_version)

    errors: list[Exception] = []

    def run_update(text: str):
        s = Session(version_engine)
        try:
            FileCRUD(s, user_id=test_user.id).update_file(file_id, content=text)
        except Exception as e:  # pragma: no cover - 失败时直接暴露
            errors.append(e)
        finally:
            s.close()

    def run_b():
        a_in_version.wait(timeout=5.0)
        run_update("第二版全文。")
        b_finished.set()

    ta = threading.Thread(target=run_update, args=("第一版全文。",), name="updater-a")
    tb = threading.Thread(target=run_b, name="updater-b")
    ta.start()
    tb.start()
    ta.join(timeout=15)
    tb.join(timeout=15)
    assert not errors

    _assert_head_version_matches_content(version_engine, file_id)


# ========== Bug: fuzzy 路径 replace_all 截断 ==========


def _build_repeated_content(occurrences: int) -> str:
    return "\n".join(
        f"第{i}段：神秘的宝藏，就在这里！其他内容{i}。" for i in range(occurrences)
    )


def test_fuzzy_replace_all_replaces_beyond_20(
    db_session, test_user, test_project, version_engine
):
    """标点差异走 fuzzy 路径时，replace_all 必须替换全部出现而非前 20 处。"""
    occurrences = 25
    file = _create_file(
        db_session, test_project.id, "重复片段", _build_repeated_content(occurrences)
    )

    editor = FileEditor(db_session, user_id=test_user.id)
    result = editor.edit_file(
        file.id,
        [{
            "op": "replace",
            "old": "神秘的宝藏就在这里",  # 无标点 → 精确匹配失败，落入 fuzzy
            "new": "宝藏已经易主",
            "replace_all": True,
        }],
    )

    detail = result["details"][0]
    assert detail["match_mode"] == "fuzzy"
    assert detail["count"] == occurrences

    with Session(version_engine) as check:
        final = check.get(File, file.id).content
    assert final.count("宝藏已经易主") == occurrences
    assert "神秘的宝藏" not in final


def test_fuzzy_replace_all_warns_when_cap_hit(
    db_session, test_user, test_project, version_engine, monkeypatch
):
    """达到 replace_all 上限时必须显式告警并标记 truncated。"""
    import agent.tools.file_ops.edit as edit_module

    monkeypatch.setattr(edit_module, "REPLACE_ALL_MAX_FUZZY_MATCHES", 5)

    occurrences = 8
    file = _create_file(
        db_session, test_project.id, "超限片段", _build_repeated_content(occurrences)
    )

    editor = FileEditor(db_session, user_id=test_user.id)
    result = editor.edit_file(
        file.id,
        [{
            "op": "replace",
            "old": "神秘的宝藏就在这里",
            "new": "宝藏已经易主",
            "replace_all": True,
        }],
    )

    detail = result["details"][0]
    assert detail["count"] == 5
    assert detail["truncated"] is True
    assert any("上限" in w for w in result["warnings"])


# ========== Bug: NFKC 展开边界（edit_file 层） ==========


def test_edit_replace_rejects_mid_expansion_fuzzy_match(
    db_session, test_user, test_project, version_engine
):
    """pattern 结束于展开字符（Ⅻ→xii）中途时必须拒绝，而非吞掉整个 Ⅻ。"""
    content = "他们说序章完毕。第Ⅻ章开始了……"
    file = _create_file(db_session, test_project.id, "罗马数字章节", content)

    editor = FileEditor(db_session, user_id=test_user.id)
    with pytest.raises(ValueError):
        editor.edit_file(
            file.id,
            [{"op": "replace", "old": "序章完毕第x", "new": "新的开头"}],
        )

    with Session(version_engine) as check:
        assert check.get(File, file.id).content == content

    # continue_on_error 模式下应在 warnings 中说明拒绝原因
    result = editor.edit_file(
        file.id,
        [{"op": "replace", "old": "序章完毕第x", "new": "新的开头"}],
        continue_on_error=True,
    )
    assert result["edits_applied"] == 0
    assert result["failed_edits"]
    assert any("边界" in w for w in result["warnings"])


# ========== Bug: query_files 逐类型分页 ==========


@pytest.fixture
def mixed_type_files(db_session, test_project):
    """3 个角色 + 3 个设定，order 交错：角色=1/3/5，设定=2/4/6"""
    for i in range(3):
        _create_file(
            db_session, test_project.id, f"角色{i}", f"角色内容{i}",
            file_type="character", order=2 * i + 1,
        )
        _create_file(
            db_session, test_project.id, f"设定{i}", f"设定内容{i}",
            file_type="lore", order=2 * i + 2,
        )
    return test_project


def test_query_files_limit_applies_to_merged_types(db_session, test_user, mixed_type_files):
    crud = FileCRUD(db_session, user_id=test_user.id)
    results = crud.query_files(
        mixed_type_files.id, file_types=["character", "lore"], limit=4
    )
    assert len(results) == 4
    assert [r["title"] for r in results] == ["角色0", "设定0", "角色1", "设定1"]


def test_query_files_offset_applies_to_merged_types(db_session, test_user, mixed_type_files):
    crud = FileCRUD(db_session, user_id=test_user.id)
    page = crud.query_files(
        mixed_type_files.id, file_types=["character", "lore"], limit=2, offset=2
    )
    assert [r["title"] for r in page] == ["角色1", "设定1"]


def test_query_files_keyword_treats_like_wildcards_literally(db_session, test_user, test_project):
    """F-24：关键字里的 `_` / `%` 是字面字符，不能当 LIKE 通配符。"""
    _create_file(db_session, test_project.id, "a_b 线索", "正文")
    _create_file(db_session, test_project.id, "axb 线索", "正文")
    _create_file(db_session, test_project.id, "折扣", "打了 50% 的折")
    _create_file(db_session, test_project.id, "比例", "大约 500 人")
    crud = FileCRUD(db_session, user_id=test_user.id)

    assert [r["title"] for r in crud.query_files(test_project.id, query="a_b")] == ["a_b 线索"]
    assert [r["title"] for r in crud.query_files(test_project.id, query="50%")] == ["折扣"]
