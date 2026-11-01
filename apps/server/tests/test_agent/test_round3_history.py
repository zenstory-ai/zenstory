"""第三轮深度 review 回归测试：会话历史与提示装配（组 key: history）。

覆盖两条确认缺陷：

- #17 assistant 消息带 reasoning_content 时，status_cards 的历史回填被短路，
  导致 request_clarification / iteration_exhausted 那一轮在回放给模型的历史里
  彻底消失（AI 忘了自己问过什么）。
- #22 `_get_folder_ids` 的「按子文件类型计数」启发式被一个放错位置的文件永久劫持，
  AI 持续把新文件建到错误的根目录，且该偏差自我强化。
"""

import json
from uuid import uuid4

import pytest
from sqlmodel import Session

from agent.core.message_manager import MessageManager
from agent.core.session_loader import SessionLoader
from agent.openai_agents.runner import (
    extract_text_from_message_content,
    normalize_messages_for_openai_agents,
)
from models import ChatMessage, ChatSession, File, Project, User

# ---------------------------------------------------------------------------
# 公共夹具
# ---------------------------------------------------------------------------


@pytest.fixture
def round3_user(db_session: Session) -> User:
    suffix = uuid4().hex[:8]
    user = User(
        email=f"round3-history-{suffix}@example.com",
        username=f"round3_history_{suffix}",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_project(db_session: Session, owner_id: str, project_type: str) -> Project:
    project = Project(
        name=f"Round3 {project_type} {uuid4().hex[:6]}",
        owner_id=owner_id,
        project_type=project_type,
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


# ---------------------------------------------------------------------------
# #17 status_cards 与 reasoning_content 共存
# ---------------------------------------------------------------------------

CLARIFICATION_CARD = {
    "type": "workflow_stopped",
    "reason": "clarification_needed",
    "question": "主角视角用第一人称还是第三人称？",
    "details": ["叙事视角", "时态"],
}

ITERATION_CARD = {
    "type": "iteration_exhausted",
    "layer": "workflow",
    "iterationsUsed": 8,
    "maxIterations": 8,
    "reason": "max_iterations_reached",
    "lastAgent": "writer",
}


def _assistant_message(
    *,
    content: str,
    reasoning_content: str | None,
    status_cards: list[dict] | None,
) -> ChatMessage:
    """构造一条未落库的 assistant ChatMessage（仅用于格式化逻辑单测）。"""
    metadata = json.dumps({"status_cards": status_cards}) if status_cards else None
    return ChatMessage(
        session_id="round3-fake-session",
        role="assistant",
        content=content,
        reasoning_content=reasoning_content,
        message_metadata=metadata,
    )


def _loader() -> SessionLoader:
    return SessionLoader(project_id="round3-project", user_id="round3-user")


@pytest.mark.unit
def test_status_cards_survive_reasoning_content():
    """有 reasoning_content 时，状态卡摘要仍必须进入会被回放的 text 块。"""
    msg = _assistant_message(
        content="",
        reasoning_content="模型的一大段思考",
        status_cards=[CLARIFICATION_CARD],
    )

    msg_data = _loader()._format_chat_message_for_history(msg)  # noqa: SLF001

    content = msg_data["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "thinking", "thinking": "模型的一大段思考"}

    # 回放侧只取 text 块，因此状态卡摘要必须以 text 块形式存在
    replayed = extract_text_from_message_content(content)
    assert "clarification_needed" in replayed
    assert "主角视角用第一人称还是第三人称？" in replayed


@pytest.mark.unit
def test_status_only_turn_with_reasoning_is_not_dropped_from_replay():
    """整条 assistant 消息不得在 normalize 阶段被剔除（缺陷的最终危害）。"""
    msg = _assistant_message(
        content="",
        reasoning_content="思考",
        status_cards=[CLARIFICATION_CARD],
    )
    msg_data = _loader()._format_chat_message_for_history(msg)  # noqa: SLF001

    normalized = normalize_messages_for_openai_agents(
        [{"role": "user", "content": "继续"}, msg_data]
    )

    assert [m["role"] for m in normalized] == ["user", "assistant"]
    assert "clarification_needed" in normalized[-1]["content"]


@pytest.mark.unit
def test_iteration_exhausted_card_survives_reasoning_content():
    """iteration_exhausted 状态卡同样不能被 reasoning 分支吃掉。"""
    msg = _assistant_message(
        content="",
        reasoning_content="思考",
        status_cards=[ITERATION_CARD],
    )
    msg_data = _loader()._format_chat_message_for_history(msg)  # noqa: SLF001

    replayed = extract_text_from_message_content(msg_data["content"])
    assert "iteration_exhausted" in replayed
    assert "max_iterations_reached" in replayed


@pytest.mark.unit
def test_status_cards_without_reasoning_still_plain_string():
    """对照组：没有 reasoning 时保持字符串形态（原有行为不回退）。"""
    msg = _assistant_message(
        content="",
        reasoning_content=None,
        status_cards=[CLARIFICATION_CARD],
    )
    msg_data = _loader()._format_chat_message_for_history(msg)  # noqa: SLF001

    assert isinstance(msg_data["content"], str)
    assert "clarification_needed" in msg_data["content"]


@pytest.mark.unit
def test_only_clarification_cards_are_appended_when_turn_has_real_text():
    """有正文的轮次不追加其他状态卡摘要，但补问卡的问题和选项仍要进回放（只追加一次）。"""
    msg = _assistant_message(
        content="已经写完第一章。",
        reasoning_content="思考",
        status_cards=[ITERATION_CARD, CLARIFICATION_CARD],
    )
    msg_data = _loader()._format_chat_message_for_history(msg)  # noqa: SLF001

    replayed = extract_text_from_message_content(msg_data["content"])
    assert replayed.startswith("已经写完第一章。")
    assert "iteration_exhausted" not in replayed
    assert replayed.count("主角视角用第一人称还是第三人称？") == 1
    assert "叙事视角; 时态" in replayed


@pytest.mark.unit
def test_load_chat_session_replays_status_only_turn_with_reasoning(
    db_session: Session,
    round3_user: User,
    monkeypatch,
):
    """端到端：落库 → load_chat_session → normalize，澄清问题必须仍在历史里。"""
    monkeypatch.setattr("agent.core.session_loader.AGENT_CHAT_HISTORY_TOKEN_BUDGET", 4000)

    project = _make_project(db_session, round3_user.id, "novel")
    chat_session = ChatSession(
        user_id=round3_user.id,
        project_id=project.id,
        title="Round3 clarification",
        is_active=True,
        message_count=0,
    )
    db_session.add(chat_session)
    db_session.commit()
    db_session.refresh(chat_session)

    db_session.add(
        ChatMessage(session_id=chat_session.id, role="user", content="帮我写第一章")
    )
    db_session.add(
        ChatMessage(
            session_id=chat_session.id,
            role="assistant",
            content="",
            # 生产配置下模型是 reasoning 模型，正常轮次 reasoning_content 都非空
            reasoning_content="用户没说视角，需要先问清楚。",
            message_metadata=json.dumps({"status_cards": [CLARIFICATION_CARD]}),
        )
    )
    db_session.commit()

    loader = SessionLoader(project_id=project.id, user_id=round3_user.id)
    session_data = loader.load_chat_session(db_session)

    normalized = normalize_messages_for_openai_agents(session_data.history_messages)
    assert [m["role"] for m in normalized] == ["user", "assistant"]
    assert "主角视角用第一人称还是第三人称？" in normalized[-1]["content"]


@pytest.mark.unit
def test_extract_content_text_ignores_thinking_blocks():
    """判空辅助函数必须与回放侧规则一致：thinking 块不算正文。"""
    loader = _loader()

    assert loader._extract_content_text("hello") == "hello"  # noqa: SLF001
    assert loader._extract_content_text(None) == ""  # noqa: SLF001
    assert (
        loader._extract_content_text(  # noqa: SLF001
            [{"type": "thinking", "thinking": "一大段推理"}]
        )
        == ""
    )
    assert (
        loader._extract_content_text(  # noqa: SLF001
            [
                {"type": "thinking", "thinking": "推理"},
                {"type": "text", "text": "正文"},
            ]
        )
        == "正文"
    )


# ---------------------------------------------------------------------------
# #22 根目录选取启发式
# ---------------------------------------------------------------------------


def _add_novel_root_folders(db_session: Session, project_id: str) -> None:
    """按 novel 模板创建 5 个空的根目录（确定性 id + 规范标题）。"""
    specs = [
        ("lore-folder", "设定"),
        ("character-folder", "角色"),
        ("material-folder", "素材"),
        ("outline-folder", "大纲"),
        ("draft-folder", "正文"),
    ]
    for order, (suffix, title) in enumerate(specs):
        db_session.add(
            File(
                id=f"{project_id}-{suffix}",
                project_id=project_id,
                title=title,
                file_type="folder",
                parent_id=None,
                order=order,
            )
        )
    db_session.commit()


def test_folder_ids_not_hijacked_by_single_misplaced_file(
    db_session: Session,
    round3_user: User,
):
    """路径 A：一个错放在「设定」下的角色卡不得劫持 character 占位符。"""
    project = _make_project(db_session, round3_user.id, "novel")
    _add_novel_root_folders(db_session, project.id)

    db_session.add(
        File(
            id=f"{project.id}-stray-character",
            project_id=project.id,
            title="被放错位置的角色卡",
            file_type="character",
            parent_id=f"{project.id}-lore-folder",
            order=0,
        )
    )
    db_session.commit()

    manager = MessageManager(project_id=project.id, user_id=round3_user.id)
    folder_ids = manager._get_folder_ids(  # noqa: SLF001
        session=db_session,
        project_type="novel",
    )

    assert folder_ids["character"] == f"{project.id}-character-folder"
    assert folder_ids["lore"] == f"{project.id}-lore-folder"


def test_folder_ids_survive_nested_drafts_plus_stray_draft(
    db_session: Session,
    round3_user: User,
):
    """路径 B：正文按卷分子目录（根级 draft 计数为 0）+ 大纲下一个散落草稿。"""
    project = _make_project(db_session, round3_user.id, "novel")
    _add_novel_root_folders(db_session, project.id)

    db_session.add(
        File(
            id=f"{project.id}-volume-1",
            project_id=project.id,
            title="第一卷",
            file_type="folder",
            parent_id=f"{project.id}-draft-folder",
            order=0,
        )
    )
    db_session.commit()

    for index in range(5):
        db_session.add(
            File(
                id=f"{project.id}-chapter-{index}",
                project_id=project.id,
                title=f"第{index + 1}章",
                file_type="draft",
                parent_id=f"{project.id}-volume-1",
                order=index,
            )
        )
    db_session.add(
        File(
            id=f"{project.id}-stray-draft",
            project_id=project.id,
            title="散落的草稿",
            file_type="draft",
            parent_id=f"{project.id}-outline-folder",
            order=9,
        )
    )
    db_session.commit()

    manager = MessageManager(project_id=project.id, user_id=round3_user.id)
    folder_ids = manager._get_folder_ids(  # noqa: SLF001
        session=db_session,
        project_type="novel",
    )

    assert folder_ids["draft"] == f"{project.id}-draft-folder"
    assert folder_ids["outline"] == f"{project.id}-outline-folder"


def test_folder_ids_material_folder_not_hijacked_by_snippet_elsewhere(
    db_session: Session,
    round3_user: User,
):
    """material 期望的子类型是 snippet，素材目录里通常是 document，同样会被劫持。"""
    project = _make_project(db_session, round3_user.id, "novel")
    _add_novel_root_folders(db_session, project.id)

    db_session.add(
        File(
            id=f"{project.id}-material-doc",
            project_id=project.id,
            title="参考资料",
            file_type="document",
            parent_id=f"{project.id}-material-folder",
            order=0,
        )
    )
    db_session.add(
        File(
            id=f"{project.id}-stray-snippet",
            project_id=project.id,
            title="错放的片段",
            file_type="snippet",
            parent_id=f"{project.id}-outline-folder",
            order=1,
        )
    )
    db_session.commit()

    manager = MessageManager(project_id=project.id, user_id=round3_user.id)
    folder_ids = manager._get_folder_ids(  # noqa: SLF001
        session=db_session,
        project_type="novel",
    )

    assert folder_ids["material"] == f"{project.id}-material-folder"


def test_folder_ids_still_recover_unrecognizable_legacy_folder(
    db_session: Session,
    round3_user: User,
):
    """兜底路径保留：标题无法识别、也没有规范目录时，仍认「装着该类型文件」的目录。"""
    project = _make_project(db_session, round3_user.id, "novel")
    legacy_id = "legacy-unnamed-root"
    db_session.add(
        File(
            id=legacy_id,
            project_id=project.id,
            title="我的小本本",
            file_type="folder",
            parent_id=None,
            order=0,
        )
    )
    db_session.commit()

    for index in range(3):
        db_session.add(
            File(
                id=f"legacy-character-{index}",
                project_id=project.id,
                title=f"角色{index}",
                file_type="character",
                parent_id=legacy_id,
                order=index,
            )
        )
    db_session.commit()

    manager = MessageManager(project_id=project.id, user_id=round3_user.id)
    folder_ids = manager._get_folder_ids(  # noqa: SLF001
        session=db_session,
        project_type="novel",
    )

    assert folder_ids["character"] == legacy_id
    # 没有任何信号的类型回退到确定性 id
    assert folder_ids["draft"] == f"{project.id}-draft-folder"


def test_folder_ids_prefer_actively_used_folder_when_both_are_canonical(
    db_session: Session,
    round3_user: User,
):
    """两个目录都规范（确定性 id vs 同名旧目录）时，仍由子文件计数裁决。"""
    project = _make_project(db_session, round3_user.id, "novel")
    _add_novel_root_folders(db_session, project.id)

    legacy_id = "legacy-character-root"
    db_session.add(
        File(
            id=legacy_id,
            project_id=project.id,
            title="角色",
            file_type="folder",
            parent_id=None,
            order=9,
        )
    )
    db_session.commit()

    db_session.add(
        File(
            id="legacy-character-child",
            project_id=project.id,
            title="李妍",
            file_type="character",
            parent_id=legacy_id,
            order=0,
        )
    )
    db_session.commit()

    manager = MessageManager(project_id=project.id, user_id=round3_user.id)
    folder_ids = manager._get_folder_ids(  # noqa: SLF001
        session=db_session,
        project_type="novel",
    )

    assert folder_ids["character"] == legacy_id
