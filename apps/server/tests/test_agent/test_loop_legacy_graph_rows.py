"""图（已删除的多 agent 工作流）写下的旧 assistant 行在单循环历史回放里逐字不变。

旧会话里还留着图写下的行：路由（routing）、交接（handoff_to_agent）、parallel_execute、补问卡、
轮数耗尽卡与 display_events。图的执行代码删除后，这些行只经 ``loop/history.py`` 的旧行折叠
（``SessionLoader._format_chat_message_for_history``）读取；期望值是删除前的代码生成的。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlmodel import Session

from agent.loop.history import load_loop_history
from models import ChatMessage, ChatSession, File, Project, User


def _seed(db_session: Session) -> tuple[str, str, str, dict[str, str]]:
    suffix = uuid4().hex[:8]
    user = User(
        email=f"legacy-{suffix}@example.com",
        username=f"legacy_{suffix}",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    project = Project(name=f"Legacy {suffix}", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    files = {}
    for key, title, file_type in (
        ("outline", "卷纲", "outline"),
        ("chapter", "第一章", "draft"),
        ("character", "林舟", "character"),
    ):
        file = File(id=f"legacy-{key}", project_id=project.id, title=title, file_type=file_type, content=f"{title}正文")
        db_session.add(file)
        files[key] = file.id
    chat_session = ChatSession(user_id=user.id, project_id=project.id, title="legacy", is_active=True)
    db_session.add(chat_session)
    db_session.commit()

    base = datetime(2026, 11, 1, 8, 0, 0)

    def add(role: str, content: str, minutes: int, **fields) -> None:
        db_session.add(
            ChatMessage(
                id=f"legacy-row-{minutes:02d}",
                session_id=chat_session.id,
                role=role,
                content=content,
                created_at=base + timedelta(minutes=minutes),
                **fields,
            )
        )

    add("user", "帮我规划第一卷", 0)
    add(
        "assistant",
        "卷纲写好了，交给内容创作者写第一章。",
        1,
        reasoning_content="先规划再写",
        tool_calls=json.dumps(
            [
                {
                    "id": "c1",
                    "name": "create_file",
                    "arguments": {"title": "卷纲", "file_type": "outline"},
                    "status": "success",
                    "result": {"status": "success", "data": {"id": files["outline"], "title": "卷纲"}},
                },
                {
                    "id": "c2",
                    "name": "handoff_to_agent",
                    "arguments": {"target_agent": "writer", "reason": "写第一章", "context": "按卷纲写"},
                    "status": "success",
                    "result": {"status": "handoff", "target_agent": "writer", "reason": "写第一章"},
                },
            ],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps(
            {
                "routing": {"agent_type": "planner", "workflow_plan": "standard", "write_content": True},
                "display_events": [
                    {"type": "agent_selected", "data": {"agent_type": "planner"}},
                    {"type": "tool_call", "tool_call_index": 0},
                    {"type": "tool_call", "tool_call_index": 1},
                ],
            },
            ensure_ascii=False,
        ),
    )
    add("user", "改第一章，再看看人设", 2)
    add(
        "assistant",
        "",
        3,
        tool_calls=json.dumps(
            [
                {
                    "id": "c3",
                    "name": "parallel_execute",
                    "arguments": {
                        "tasks": [
                            {"type": "query_files", "description": "读角色", "params": {"id": files["character"]}},
                            {"type": "edit_file", "description": "改章", "params": {"id": files["chapter"], "edits": []}},
                        ]
                    },
                    "status": "success",
                    "result": {
                        "tasks": [
                            {"index": 0, "params": {"id": files["character"]}, "type": "query_files", "status": "completed"},
                            {"index": 1, "params": {"id": files["chapter"]}, "type": "edit_file", "status": "completed"},
                        ]
                    },
                },
                {
                    "id": "c4",
                    "name": "handoff_to_agent",
                    "arguments": {"target_agent": "writer", "reason": "节奏偏慢", "context": "第三段压缩"},
                    "status": "success",
                    "result": {
                        "status": "handoff",
                        "target_agent": "writer",
                        "reason": "节奏偏慢",
                        "context": "第三段压缩",
                        "todo": ["压缩第三段"],
                    },
                },
            ],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps(
            {
                "stop_reason": "wall_clock",
                "routing": {"agent_type": "writer", "workflow_plan": "standard"},
                "status_cards": [
                    {
                        "type": "iteration_exhausted",
                        "layer": "collaboration",
                        "iterationsUsed": 12,
                        "maxIterations": 12,
                        "reason": "协作轮数用完了",
                        "lastAgent": "quality_reviewer",
                    }
                ],
                "display_events": [
                    {"type": "agent_selected", "data": {"agent_type": "writer"}},
                    {"type": "tool_call", "tool_call_index": 0},
                    {"type": "agent_selected", "data": {"agent_type": "quality_reviewer"}},
                    {"type": "tool_call", "tool_call_index": 1},
                ],
            },
            ensure_ascii=False,
        ),
    )
    add("user", "主角叫什么好", 4)
    add(
        "assistant",
        "",
        5,
        tool_calls=json.dumps(
            [
                {
                    "id": "c5",
                    "name": "request_clarification",
                    "arguments": {"question": "主角想要古风还是现代的名字？", "options": ["古风", "现代"]},
                    "status": "success",
                    "result": {"status": "clarification_needed"},
                }
            ],
            ensure_ascii=False,
        ),
        message_metadata=json.dumps(
            {
                "routing": {"agent_type": "planner", "clarify_first": True},
                "status_cards": [
                    {
                        "type": "workflow_stopped",
                        "reason": "clarification_needed",
                        "question": "主角想要古风还是现代的名字？",
                        "context": "取名前先确认风格",
                        "details": ["古风", "现代"],
                        "lastAgent": "planner",
                    }
                ],
            },
            ensure_ascii=False,
        ),
    )
    db_session.commit()
    return project.id, user.id, chat_session.id, files


@pytest.mark.integration
def test_graph_rows_replay_unchanged_in_loop_history(db_session: Session):
    project_id, user_id, chat_session_id, _files = _seed(db_session)

    history = load_loop_history(db_session, project_id=project_id, user_id=user_id, chat_session_id=chat_session_id)

    assert history.items == EXPECTED_ITEMS
    assert history.author_messages == ["帮我规划第一卷", "改第一章，再看看人设", "主角叫什么好"]
    assert history.last_message_id == "legacy-row-05"


EXPECTED_ITEMS = [
    {"role": "user", "content": "帮我规划第一卷"},
    {"role": "assistant", "content": "卷纲写好了，交给内容创作者写第一章。\n[此前的工具操作]\n- 已创建文件 《卷纲》"},
    {"role": "user", "content": "改第一章，再看看人设"},
    {"role": "assistant", "content": "[iteration_exhausted]\nlayer: collaboration\niterations: 12/12\nreason: 协作轮数用完了\nlast_agent: quality_reviewer\n\n[此前的工具操作]\n- 已读取文件 （未命名） (id=legacy-character)\n- 已编辑文件 （未命名） (id=legacy-chapter)"},
    {"role": "user", "content": "主角叫什么好"},
    {"role": "assistant", "content": "[workflow_stopped]\nreason: clarification_needed\nquestion: 主角想要古风还是现代的名字？\ncontext: 取名前先确认风格\ndetails: 古风; 现代"},
]


# ----------------------------------------- SessionLoader：图写下的路由信号与 parallel_execute 面包屑


def _history_row(content: str, metadata: dict | None = None, tool_calls=None):
    return SimpleNamespace(
        id="m1",
        role="assistant",
        content=content,
        message_metadata=json.dumps(metadata) if metadata is not None else None,
        reasoning_content=None,
        tool_calls=json.dumps(tool_calls) if tool_calls is not None else None,
    )



@pytest.mark.unit
def test_history_dict_exposes_routing_last_agent_and_clarification():
    from agent.core.session_loader import SessionLoader

    loader = SessionLoader("p", "u")
    routing = {"initial_agent": "quality_reviewer", "read_only": True}
    exhausted = loader._format_chat_message_for_history(
        _history_row(
            "阶段总结",
            {
                "stop_reason": "max_turns_exceeded",
                "routing": routing,
                "status_cards": [{"type": "iteration_exhausted", "layer": "tool_call", "lastAgent": "quality_reviewer"}],
            },
        )
    )
    assert exhausted["routing"] == routing
    assert exhausted["last_agent"] == "quality_reviewer"
    assert exhausted["clarification_pending"] is False

    clarification = loader._format_chat_message_for_history(
        _history_row(
            "",
            {"status_cards": [{"type": "workflow_stopped", "reason": "clarification_needed", "question": "主角叫什么？"}]},
        )
    )
    assert clarification["clarification_pending"] is True
    assert "routing" not in clarification



@pytest.mark.unit
def test_guard_rewritten_parallel_batch_records_only_executed_tasks():
    """守卫拦下第 1 个读取后实际只执行 2 个子任务：只按结果里回显的目标记面包屑。"""
    from agent.core.session_loader import SessionLoader

    tool_call = {
        "name": "parallel_execute",
        "status": "success",
        "arguments": {
            "tasks": [
                {"type": "query_files", "description": "重读第一章", "params": {"id": "c1"}},
                {"type": "query_files", "description": "读第二章", "params": {"id": "c2"}},
                {"type": "edit_file", "description": "改第三章", "params": {"id": "c3"}},
            ]
        },
        "result": {
            "tasks": [
                {
                    "index": 0,
                    "params": {"id": "c2"},
                    "type": "query_files",
                    "status": "completed",
                    "result": {"status": "success", "data": [{"id": "c2", "title": "第二章"}]},
                },
                {
                    "index": 1,
                    "params": {"id": "c3"},
                    "type": "edit_file",
                    "status": "failed",
                    "error": "找不到原文",
                    "result": None,
                },
            ]
        },
    }

    actions = SessionLoader("p", "u")._extract_parallel_file_actions(tool_call)

    assert actions == [{"name": "query_files", "file_id": "c2", "title": "第二章"}]



def _deletes_truncated_after_five():
    tasks = [{"type": "delete_file", "description": f"删第{i}稿", "params": {"id": f"f{i}"}} for i in range(1, 8)]
    results = [
        {
            "index": i - 1,
            "params": {"id": f"f{i}"},
            "type": "delete_file",
            "status": "completed",
            "result": {"status": "success", "mutation_applied": True, "data": True},
        }
        for i in range(1, 6)
    ]
    return {
        "name": "parallel_execute",
        "status": "success",
        "arguments": {"tasks": tasks},
        "result": {
            "tasks": results,
            "dropped": 2,
            "dropped_tasks": [{"index": 5, "type": "delete_file"}, {"index": 6, "type": "delete_file"}],
            "all_completed": False,
        },
    }



@pytest.mark.unit
def test_truncated_tail_tasks_are_not_recorded_as_done():
    """超过 5 个的尾部任务没有执行：不能出现在「已删除」面包屑里。"""
    from agent.core.session_loader import SessionLoader

    loader = SessionLoader("p", "u")
    actions = loader._extract_file_actions([_deletes_truncated_after_five()])

    assert [action["file_id"] for action in actions] == ["f1", "f2", "f3", "f4", "f5"]
    breadcrumb = loader._build_tool_calls_history_content([_deletes_truncated_after_five()])
    assert "f6" not in breadcrumb and "f7" not in breadcrumb



@pytest.mark.unit
def test_pending_subtask_results_and_failed_tasks_are_not_recorded():
    from agent.core.session_loader import SessionLoader

    tool_call = {
        "name": "parallel_execute",
        "status": "success",
        "arguments": {"tasks": [{"type": "edit_file", "params": {"id": "a"}}, {"type": "edit_file", "params": {"id": "b"}}]},
        "result": {
            "tasks": [
                {"index": 0, "params": {"id": "a"}, "type": "edit_file", "status": "pending", "result": None},
                {"index": 1, "params": {"id": "b"}, "type": "edit_file", "status": "partial", "result": {"data": {"id": "b"}}},
            ]
        },
    }

    actions = SessionLoader("p", "u")._extract_parallel_file_actions(tool_call)

    # partial 的改动已经落地，照常记编辑；pending 没有结果，不记。
    assert [(action["name"], action["file_id"]) for action in actions] == [("edit_file", "b")]



@pytest.mark.unit
def test_legacy_parallel_result_without_echo_pairs_only_when_every_task_ran():
    """旧格式结果（没有回显 params）：任务一个不少、类型逐个对上时才按下标配对。"""
    from agent.core.session_loader import SessionLoader

    loader = SessionLoader("p", "u")
    tasks = [{"type": "delete_file", "params": {"id": "d1"}}, {"type": "query_files", "params": {"id": "q1"}}]
    complete = {
        "name": "parallel_execute",
        "status": "success",
        "arguments": {"tasks": tasks},
        "result": {
            "tasks": [
                {"type": "delete_file", "status": "completed", "result": {"data": True}},
                {"type": "query_files", "status": "completed", "result": {"data": [{"id": "q1", "title": "卷纲"}]}},
            ]
        },
    }
    assert [(a["name"], a["file_id"]) for a in loader._extract_parallel_file_actions(complete)] == [
        ("delete_file", "d1"),
        ("query_files", "q1"),
    ]

    shortened = {**complete, "result": {"tasks": complete["result"]["tasks"][1:]}}
    assert loader._extract_parallel_file_actions(shortened) == []



@pytest.mark.unit
def test_pending_top_level_call_is_recorded_as_outcome_unknown():
    """没有落下结果的工具调用（status=pending）：面包屑写「结果未知」，不写成已完成。"""
    from agent.core.session_loader import SessionLoader

    loader = SessionLoader("p", "u")
    breadcrumb = loader._build_tool_calls_history_content(
        [{"name": "delete_file", "status": "pending", "arguments": {"id": "g1"}}]
    )

    assert "g1" in breadcrumb
    assert "已删除文件" not in breadcrumb
    assert "结果未知" in breadcrumb

