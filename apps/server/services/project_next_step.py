"""作品的「下一步」：框架已经有了、正文还一个字没有时，提议写第一章。

只看文件，不调模型：大纲 / 角色 / 设定里至少有一份非空，且正文类文件
（draft / script）全部为空或不存在。复盘里拿到框架就走的作者占比最高，
这一步在聊天输入框上方以按钮出现，点击直接把对应请求发给 AI。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, func, select

from models import File, Project

PLANNING_FILE_TYPES = ("outline", "character", "lore")
PROSE_FILE_TYPES = ("draft", "script")

WRITE_FIRST_CHAPTER = "write_first_chapter"

# (按钮文字, 点击后发给 AI 的话)；按项目类型说成作者自己的请求。
_COPY: dict[str, dict[str, tuple[str, str]]] = {
    "zh": {
        "novel": ("写第一章正文", "按大纲写第一章正文"),
        "short": ("开始写正文", "按大纲开始写正文"),
        "screenplay": ("写第 1 集剧本", "按分集大纲写第 1 集剧本"),
    },
    "en": {
        "novel": ("Write chapter 1", "Write chapter 1 from the outline"),
        "short": ("Start the story", "Start writing the story from the outline"),
        "screenplay": ("Write episode 1", "Write the episode 1 script from the episode outline"),
    },
}


@dataclass(frozen=True)
class NextStep:
    kind: str
    label: str
    message: str


def _non_empty_count(session: Session, project_id: str, file_types: tuple[str, ...]) -> int:
    statement = select(func.count()).select_from(File).where(
        File.project_id == project_id,
        File.is_deleted == False,  # noqa: E712 - SQL comparison
        File.file_type.in_(file_types),
        func.length(func.trim(func.coalesce(File.content, ""))) > 0,
    )
    return int(session.exec(statement).one())


def compute_next_step(session: Session, project: Project, language: str) -> NextStep | None:
    """框架已就绪且还没有正文时返回「写第一章」，否则 None。"""
    if _non_empty_count(session, project.id, PROSE_FILE_TYPES) > 0:
        return None
    if _non_empty_count(session, project.id, PLANNING_FILE_TYPES) == 0:
        return None
    copy = _COPY["en" if language == "en" else "zh"]
    label, message = copy.get(project.project_type or "novel", copy["novel"])
    return NextStep(kind=WRITE_FIRST_CHAPTER, label=label, message=message)
