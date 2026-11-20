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
from agent.core.run_meter import AgentRunMeter
from agent.core.session_loader import SessionLoader
from agent.loop.history import load_loop_history
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
def test_load_loop_history_replays_status_only_turn_with_reasoning(
    db_session: Session,
    round3_user: User,
):
    """端到端：落库 → load_loop_history，澄清问题必须仍在历史里。"""
    from agent.loop.history import load_loop_history

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

    loaded = load_loop_history(
        db_session, project_id=project.id, user_id=round3_user.id, chat_session_id=chat_session.id
    )
    assert [m["role"] for m in loaded.items] == ["user", "assistant"]
    assert "主角视角用第一人称还是第三人称？" in loaded.items[-1]["content"]


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


# ---------------------------------------------------------------------------
# A-1 & A-2: 历史回放工具名清洗与墙钟时限软/硬/旧记录附注
# ---------------------------------------------------------------------------


def test_a1_history_replay_sanitizes_malformed_tool_name_and_preserves_valid_unknown_and_call_id(
    db_session: Session,
    round3_user: User,
):
    project = _make_project(db_session, round3_user.id, "novel")
    chat_session = ChatSession(user_id=round3_user.id, project_id=project.id, title="A1Hist", is_active=True)
    db_session.add(chat_session)
    db_session.commit()

    leaked = "历史旧行里的长正文不能重放回模型输入。" * 10
    tool_calls = json.dumps(
        [
            {
                "name": f"<file>\n{leaked}",
                "arguments": {},
                "status": "error",
                "error": f"未知工具 '<file>\n{leaked}'。请只调用当前已声明的工具。",
            },
            {
                "name": "9-tool",
                "arguments": {"q": 1},
                "status": "error",
                "error": "未知工具 '9-tool'。请只调用当前已声明的工具。",
            },
        ],
        ensure_ascii=False,
    )
    user_msg = ChatMessage(session_id=chat_session.id, role="user", content="继续写")
    assistant_msg = ChatMessage(
        session_id=chat_session.id,
        role="assistant",
        content="好的。",
        tool_calls=tool_calls,
        message_metadata=json.dumps({"engine": "loop"}),
    )
    db_session.add_all([user_msg, assistant_msg])
    db_session.commit()
    db_session.refresh(assistant_msg)

    loaded = load_loop_history(
        db_session, project_id=project.id, user_id=round3_user.id, chat_session_id=chat_session.id
    )
    serialized = json.dumps(loaded.items, ensure_ascii=False)
    assert "历史旧行里的长正文不能重放回模型输入" not in serialized

    expected_ids = [f"h{assistant_msg.id}_0", f"h{assistant_msg.id}_1"]
    fn_calls = [item for item in loaded.items if item.get("type") == "function_call"]
    fn_outputs = [item for item in loaded.items if item.get("type") == "function_call_output"]
    assert [c["call_id"] for c in fn_calls] == expected_ids
    assert [o["call_id"] for o in fn_outputs] == expected_ids
    assert fn_calls[0]["name"].startswith("invalid_tool_name_")
    assert len(fn_calls[0]["name"]) == len("invalid_tool_name_") + 12
    assert fn_calls[1]["name"] == "9-tool"
    out0 = json.loads(fn_outputs[0]["output"])
    out1 = json.loads(fn_outputs[1]["output"])
    assert out0["error"] == "工具调用名称格式无效，调用未执行"
    assert out1["error"] == "未知工具 '9-tool'。请只调用当前已声明的工具。"


@pytest.mark.parametrize(
    ("metadata", "expected_substring", "forbidden_substrings"),
    [
        # 显式软着陆且无未完成文件 -> 客观说明按提醒收尾结束，不推断已完成或没做完
        (
            {"engine": "loop", "stop_reason": "run_deadline_exceeded", "wall_clock_landing": "soft"},
            "（这一轮因接近单次处理时长上限而按提醒收尾结束；是否还有未完成部分以作者原要求与已保存结果为准。）",
            ["这一轮没做完：时间用完了", "已完成内容"],
        ),
        # 显式硬截断 -> 保持未做完提示
        (
            {"engine": "loop", "stop_reason": "run_deadline_exceeded", "wall_clock_landing": "hard"},
            "（这一轮没做完：时间用完了。）",
            ["按提醒收尾结束"],
        ),
        # 即使带 soft 标记，但卡片显式记录了 unfinished_files -> 仍按没做完处理
        (
            {
                "engine": "loop",
                "stop_reason": "run_deadline_exceeded",
                "wall_clock_landing": "soft",
                "status_cards": [
                    {
                        "type": "iteration_exhausted",
                        "limit": "wall_clock",
                        "unfinished_files": [{"id": "f1", "title": "第二章"}],
                    }
                ],
            },
            "（这一轮没做完：时间用完了。）",
            ["按提醒收尾结束"],
        ),
        # 没有任何软/硬标记的历史旧记录（即便带 wall_clock 卡且无 unfinished_files） -> 只能中性描述，不推断已完成或没做完
        (
            {
                "engine": "loop",
                "stop_reason": "run_deadline_exceeded",
                "status_cards": [{"type": "iteration_exhausted", "limit": "wall_clock", "reason": "单次时长接近上限"}],
            },
            "（这一轮因时间限制结束；是否还有未完成部分以作者原要求与已保存结果为准。）",
            ["这一轮没做完：时间用完了", "按提醒收尾结束"],
        ),
    ],
)
def test_a2_deadline_round_replay_note_distinguishes_soft_hard_and_unmarked_legacy(
    db_session: Session,
    round3_user: User,
    metadata: dict,
    expected_substring: str,
    forbidden_substrings: list[str],
):
    project = _make_project(db_session, round3_user.id, "novel")
    chat_session = ChatSession(user_id=round3_user.id, project_id=project.id, title="A2Hist", is_active=True)
    db_session.add(chat_session)
    db_session.commit()

    db_session.add_all(
        [
            ChatMessage(session_id=chat_session.id, role="user", content="写前两章"),
            ChatMessage(
                session_id=chat_session.id,
                role="assistant",
                content="前两章已写好。",
                message_metadata=json.dumps(metadata, ensure_ascii=False),
            ),
        ]
    )
    db_session.commit()

    loaded = load_loop_history(
        db_session, project_id=project.id, user_id=round3_user.id, chat_session_id=chat_session.id
    )
    content = loaded.items[-1]["content"]
    assert expected_substring in content
    for forbidden in forbidden_substrings:
        assert forbidden not in content


@pytest.mark.asyncio
async def test_a2_runner_soft_landing_persists_wall_clock_landing_marker_through_stream_adapter(
    db_session: Session,
    round3_user: User,
    monkeypatch,
):
    """A-2 端到端接线：runner 软着陆 -> StreamAdapter.get_last_message_metadata() -> MessageManager 落库 -> 历史回放识别 soft。"""
    from agent.core.events import EventType
    from agent.openai_agents import runner as runner_mod
    from agent.openai_agents.model import reset_sdk_model_cache
    from agent.stream_adapter import create_stream_adapter
    from agent.tools.mcp_tools import ToolContext
    from services.usage.llm_usage_service import drain_pending_usage_records
    from tests.test_agent.test_round3_runner import _ChatCompletionsStub, _point_sdk_at_stub, _text_turn, _tool_turn

    project = _make_project(db_session, round3_user.id, "novel")
    chat_session = ChatSession(user_id=round3_user.id, project_id=project.id, title="SoftWire", is_active=True)
    db_session.add(chat_session)
    db_session.commit()

    # 将请求级 wrap_up_after_s 设为 0s，使工具调用后的下一步进入真实 time_landed 软着陆分支
    run_meter = AgentRunMeter(wrap_up_after_s=0.0)
    expected_reply = "已整理并保存当前进度。"
    stub = _ChatCompletionsStub(
        turns=[
            _tool_turn("call-q1", "query_files", {}),
            _text_turn(expected_reply),
        ],
        fallback=_text_turn("EXTRA"),
    )
    adapter = create_stream_adapter(project_id=project.id, user_id=round3_user.id)
    state = {"user_message": "整理设定", "messages": [], "system_prompt": "base", "run_meter": run_meter}
    ToolContext.set_context(
        session=db_session,
        user_id=round3_user.id,
        project_id=project.id,
        session_id=chat_session.id,
    )
    try:
        with stub:
            _point_sdk_at_stub(monkeypatch, stub.port)
            raw_stream = runner_mod.run_openai_agents_streaming_agent(
                state=state, agent_type="loop", system_prompt="你是测试助手"
            )
            frames = [frame async for frame in adapter.process_workflow_events(raw_stream)]
    finally:
        ToolContext.clear_context()
        reset_sdk_model_cache()
        await drain_pending_usage_records()

    extra_meta = {"engine": "loop", **adapter.get_last_message_metadata()}
    assert extra_meta["stop_reason"] == "run_deadline_exceeded"
    assert extra_meta["wall_clock_landing"] == "soft"

    collected_text = "".join(
        frame.data["text"] for frame in frames if frame.type == EventType.CONTENT
    )
    assert collected_text == expected_reply

    mm = MessageManager(project_id=project.id, user_id=round3_user.id)
    saved_id = await mm.save_messages(
        session=db_session,
        session_id=chat_session.id,
        user_message="整理设定",
        assistant_message=collected_text,
        assistant_stop_reason=extra_meta["stop_reason"],
        assistant_extra_metadata={"engine": "loop", "wall_clock_landing": extra_meta["wall_clock_landing"]},
    )
    assert saved_id is not None

    loaded = load_loop_history(
        db_session, project_id=project.id, user_id=round3_user.id, chat_session_id=chat_session.id
    )
    assert loaded.items[-2]["content"] == expected_reply
    assert (
        loaded.items[-1]["content"]
        == "（这一轮因接近单次处理时长上限而按提醒收尾结束；是否还有未完成部分以作者原要求与已保存结果为准。）"
    )
