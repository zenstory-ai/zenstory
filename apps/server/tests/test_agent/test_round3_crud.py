"""第三轮 review 回归测试：文件 CRUD（agent/tools/file_ops/crud.py）。

覆盖三条确认缺陷：
- #15：agent 侧 create_file/update_file 不校验「parent 必须是 folder」，
       导致文件挂到普通文件下、在三端侧栏文件树中彻底不可见。
- #3（crud 侧契约 C1）：剧本分集复用分支把 content 谎报成 "" 当协议信号。
- #10（crud 侧契约 C4）：`if recursive:` 把字符串 "false" 判真，
       「删一个文件」变成「递归软删整棵子树」。

另含 _delete_recursive 的环保护（历史脏数据里的自引用不得打成 RecursionError）。
"""

import pytest
from sqlmodel import Session, select

from agent.tools.file_ops.crud import (
    FileCRUD,
    find_nearest_folder_ancestor,
    is_descendant_of,
    validate_parent_assignment,
)
from models import File, Project, User


@pytest.fixture
def test_user(db_session: Session) -> User:
    user = User(
        email="round3_crud@example.com",
        username="round3_crud",
        hashed_password="hashed_password",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def novel_project(db_session: Session, test_user: User) -> Project:
    project = Project(name="Round3 Novel", owner_id=test_user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    draft_folder = File(
        id=f"{project.id}-draft-folder",
        project_id=project.id,
        title="正文",
        file_type="folder",
        order=0,
        parent_id=None,
    )
    db_session.add(draft_folder)
    db_session.commit()
    return project


@pytest.fixture
def screenplay_project(db_session: Session, test_user: User) -> Project:
    project = Project(
        name="Round3 Screenplay", owner_id=test_user.id, project_type="screenplay"
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    script_folder = File(
        id=f"{project.id}-script-folder",
        project_id=project.id,
        title="剧本",
        file_type="folder",
        order=0,
        parent_id=None,
    )
    db_session.add(script_folder)
    db_session.commit()
    return project


def _visible_titles(session: Session, project_id: str) -> list[str]:
    """按前端侧栏的渲染规则收集可见文件标题。

    三端实现（FileTreePane / useVirtualizedTree / MobileFileTree）都只在
    `file_type === 'folder'` 时才递归子节点，因此挂在普通文件下的节点
    永远渲染不出来。这里复刻同一条规则。
    """
    rows = list(
        session.exec(
            select(File).where(
                File.project_id == project_id,
                File.is_deleted.is_(False),
            )
        ).all()
    )
    children_by_parent: dict[str | None, list[File]] = {}
    for row in rows:
        children_by_parent.setdefault(row.parent_id, []).append(row)

    visible: list[str] = []

    def walk(parent_id: str | None) -> None:
        for node in children_by_parent.get(parent_id, []):
            visible.append(node.title)
            if node.file_type == "folder":
                walk(node.id)

    walk(None)
    return visible


# ========== #15: parent 必须是 folder ==========


@pytest.mark.unit
def test_create_file_under_non_folder_parent_stays_visible_in_tree(
    db_session: Session, test_user: User, novel_project: Project
):
    """模型把「上一章的 id」当 parent 传进来时，新章节不得从文件树里消失。"""
    crud = FileCRUD(db_session, user_id=test_user.id)

    ch1 = crud.create_file(
        project_id=novel_project.id,
        title="第1章 起点",
        file_type="draft",
        content="正文1",
        parent_id=f"{novel_project.id}-draft-folder",
    )

    # 缺陷路径：parent_id 指向一个 draft 文件而不是 folder
    ch2 = crud.create_file(
        project_id=novel_project.id,
        title="第2章 续",
        file_type="draft",
        content="正文2",
        parent_id=ch1["id"],
    )

    # 必须被就近上挂到最近的 folder 祖先，绝不能挂在 draft 文件下
    assert ch2["parent_id"] == f"{novel_project.id}-draft-folder"

    stored = db_session.get(File, ch2["id"])
    assert stored is not None
    assert stored.parent_id == f"{novel_project.id}-draft-folder"

    # 前端侧栏能看见它
    assert "第2章 续" in _visible_titles(db_session, novel_project.id)


@pytest.mark.unit
def test_create_file_falls_back_to_root_when_no_folder_ancestor(
    db_session: Session, test_user: User, novel_project: Project
):
    """父链上一个 folder 都没有时，挂到根层（仍然可见），而不是挂在普通文件下。"""
    orphan = File(
        project_id=novel_project.id,
        title="根层散落大纲",
        file_type="outline",
        parent_id=None,
        order=0,
    )
    db_session.add(orphan)
    db_session.commit()
    db_session.refresh(orphan)

    crud = FileCRUD(db_session, user_id=test_user.id)
    created = crud.create_file(
        project_id=novel_project.id,
        title="挂错地方的角色",
        file_type="character",
        parent_id=orphan.id,
    )

    assert created["parent_id"] is None
    assert "挂错地方的角色" in _visible_titles(db_session, novel_project.id)


@pytest.mark.unit
def test_update_file_rejects_self_parent_for_folder_and_no_recursion_error(
    db_session: Session, test_user: User, novel_project: Project
):
    """folder 自引用必须被拒绝；即便库里已有脏数据，递归删除也不能爆 RecursionError。"""
    crud = FileCRUD(db_session, user_id=test_user.id)

    sub_folder = File(
        project_id=novel_project.id,
        title="卷一",
        file_type="folder",
        parent_id=f"{novel_project.id}-draft-folder",
        order=0,
    )
    db_session.add(sub_folder)
    db_session.commit()
    db_session.refresh(sub_folder)

    with pytest.raises(ValueError, match="不能把文件移动到它自己"):
        crud.update_file(id=sub_folder.id, parent_id=sub_folder.id)

    db_session.refresh(sub_folder)
    assert sub_folder.parent_id == f"{novel_project.id}-draft-folder"

    # 历史脏数据：绕过工具层直接写出自引用，递归删除必须能收敛
    sub_folder.parent_id = sub_folder.id
    db_session.add(sub_folder)
    db_session.commit()

    assert crud.delete_file(id=sub_folder.id, recursive=True) is True
    db_session.expire_all()
    assert db_session.get(File, sub_folder.id).is_deleted is True


@pytest.mark.unit
def test_update_file_non_folder_parent_is_re_anchored(
    db_session: Session, test_user: User, novel_project: Project
):
    """update_file 与 create_file 走同一套不变量：非 folder 父节点就近归一。"""
    crud = FileCRUD(db_session, user_id=test_user.id)

    ch1 = crud.create_file(
        project_id=novel_project.id,
        title="第1章",
        file_type="draft",
        content="正文1",
        parent_id=f"{novel_project.id}-draft-folder",
    )
    ch2 = crud.create_file(
        project_id=novel_project.id,
        title="第2章",
        file_type="draft",
        content="正文2",
        parent_id=f"{novel_project.id}-draft-folder",
    )

    updated = crud.update_file(id=ch2["id"], parent_id=ch1["id"])

    assert updated["parent_id"] == f"{novel_project.id}-draft-folder"
    assert "第2章" in _visible_titles(db_session, novel_project.id)


@pytest.mark.unit
def test_validate_parent_assignment_enforces_all_three_invariants(
    db_session: Session, test_user: User, novel_project: Project
):
    """公共校验函数本身覆盖三条不变量（供 REST 层复用同一份实现）。"""
    draft = File(
        project_id=novel_project.id,
        title="普通草稿",
        file_type="draft",
        parent_id=f"{novel_project.id}-draft-folder",
        order=0,
    )
    db_session.add(draft)
    db_session.commit()
    db_session.refresh(draft)

    # 合法：folder
    assert (
        validate_parent_assignment(
            db_session, novel_project.id, f"{novel_project.id}-draft-folder"
        )
        == f"{novel_project.id}-draft-folder"
    )
    # None 直接放行（挂到根层）
    assert validate_parent_assignment(db_session, novel_project.id, None) is None

    # 不存在
    with pytest.raises(ValueError, match="not found"):
        validate_parent_assignment(db_session, novel_project.id, "no-such-file")

    # 不是 folder
    with pytest.raises(ValueError, match="not a folder"):
        validate_parent_assignment(db_session, novel_project.id, draft.id)

    # 成环
    with pytest.raises(ValueError, match="不能把文件移动到它自己"):
        validate_parent_assignment(
            db_session,
            novel_project.id,
            f"{novel_project.id}-draft-folder",
            moving_file_id=f"{novel_project.id}-draft-folder",
        )


@pytest.mark.unit
def test_cycle_helpers_terminate_on_dirty_data(
    db_session: Session, test_user: User, novel_project: Project
):
    """两个链式遍历工具在自引用脏数据上必须收敛，而不是死循环/爆栈。"""
    looped = File(
        project_id=novel_project.id,
        title="自引用节点",
        file_type="draft",
        order=0,
    )
    db_session.add(looped)
    db_session.commit()
    db_session.refresh(looped)
    looped.parent_id = looped.id
    db_session.add(looped)
    db_session.commit()

    assert is_descendant_of(db_session, "some-other-id", looped.id) is False
    assert find_nearest_folder_ancestor(db_session, novel_project.id, looped.id) is None


# ========== #3: 复用分支返回契约（C1） ==========


@pytest.mark.unit
def test_reused_episode_returns_real_content_and_explicit_flags(
    db_session: Session, test_user: User, screenplay_project: Project
):
    """复用已存在分集时必须返回真实 content + reused_existing/original_content_length。"""
    crud = FileCRUD(db_session, user_id=test_user.id)

    first = crud.create_file(
        project_id=screenplay_project.id,
        title="第7集：旧稿",
        file_type="draft",
        parent_id=f"{screenplay_project.id}-script-folder",
    )
    assert first.get("reused_existing") is None  # 首次创建不带复用标记

    crud.update_file(id=first["id"], content="已经写好的整集正文" * 20)
    expected = "已经写好的整集正文" * 20

    second = crud.create_file(
        project_id=screenplay_project.id,
        title="第7集：旧稿",
        file_type="draft",
        parent_id=f"{screenplay_project.id}-script-folder",
        content="",  # 流式模式
    )

    assert second["id"] == first["id"]
    assert second["content"] == expected
    assert second["reused_existing"] is True
    assert second["original_content_length"] == len(expected)


@pytest.mark.unit
def test_reused_empty_episode_reports_zero_original_length(
    db_session: Session, test_user: User, screenplay_project: Project
):
    """复用一个仍为空的分集：content 为空但复用标记仍然显式为 True。"""
    crud = FileCRUD(db_session, user_id=test_user.id)

    first = crud.create_file(
        project_id=screenplay_project.id,
        title="第8集：空稿",
        file_type="draft",
        parent_id=f"{screenplay_project.id}-script-folder",
    )
    second = crud.create_file(
        project_id=screenplay_project.id,
        title="第8集：空稿",
        file_type="draft",
        parent_id=f"{screenplay_project.id}-script-folder",
        content="",
    )

    assert second["id"] == first["id"]
    assert second["content"] == ""
    assert second["reused_existing"] is True
    assert second["original_content_length"] == 0


# ========== #10: recursive 的布尔强制（C4） ==========


def _build_folder_with_children(
    db_session: Session, project: Project, n: int = 3
) -> tuple[str, list[str]]:
    folder = File(
        project_id=project.id, title="旧草稿文件夹", file_type="folder", order=0
    )
    db_session.add(folder)
    db_session.commit()
    db_session.refresh(folder)

    child_ids: list[str] = []
    for i in range(n):
        child = File(
            project_id=project.id,
            title=f"第{i + 1}章",
            file_type="draft",
            parent_id=folder.id,
            content=f"章节 {i + 1} 正文",
            order=i,
        )
        db_session.add(child)
        db_session.commit()
        db_session.refresh(child)
        child_ids.append(child.id)
    return folder.id, child_ids


def _deleted_flags(db_session: Session, ids: list[str]) -> list[bool]:
    db_session.expire_all()
    return [bool(db_session.get(File, fid).is_deleted) for fid in ids]


@pytest.mark.parametrize("falsy", ["false", "False", " false ", "0", "", "null", "no"])
@pytest.mark.unit
def test_delete_file_string_falsy_recursive_does_not_cascade(
    db_session: Session, test_user: User, novel_project: Project, falsy: str
):
    """字符串形态的假值绝不能触发级联软删除。"""
    crud = FileCRUD(db_session, user_id=test_user.id)
    folder_id, child_ids = _build_folder_with_children(db_session, novel_project)

    crud.delete_file(id=folder_id, recursive=falsy)

    assert _deleted_flags(db_session, [folder_id]) == [True]
    assert _deleted_flags(db_session, child_ids) == [False] * len(child_ids)


@pytest.mark.parametrize("truthy", [True, "true", "True", "1", "yes"])
@pytest.mark.unit
def test_delete_file_truthy_recursive_still_cascades(
    db_session: Session, test_user: User, novel_project: Project, truthy
):
    """真值语义不能被误伤：显式要求递归时仍然级联。"""
    crud = FileCRUD(db_session, user_id=test_user.id)
    folder_id, child_ids = _build_folder_with_children(db_session, novel_project)

    crud.delete_file(id=folder_id, recursive=truthy)

    assert _deleted_flags(db_session, [folder_id]) == [True]
    assert _deleted_flags(db_session, child_ids) == [True] * len(child_ids)


@pytest.mark.unit
def test_delete_file_unknown_recursive_value_is_conservative(
    db_session: Session, test_user: User, novel_project: Project
):
    """无法判定的值（如 json_repair 产出的 "fals"）按保守语义处理，不级联。"""
    crud = FileCRUD(db_session, user_id=test_user.id)
    folder_id, child_ids = _build_folder_with_children(db_session, novel_project)

    crud.delete_file(id=folder_id, recursive="fals")

    assert _deleted_flags(db_session, [folder_id]) == [True]
    assert _deleted_flags(db_session, child_ids) == [False] * len(child_ids)
