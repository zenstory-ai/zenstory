"""
Project operations for agent tools.

This module provides operations related to project management:
- update_project_status: Update project status information for AI context
"""

import json
import re
from collections.abc import Callable
from typing import Any

from sqlmodel import Session, desc, select

from agent.tools.permissions import check_project_ownership
from config.datetime_utils import utcnow
from config.project_notes import without_legacy_proposals
from config.project_status import (
    PROJECT_TITLE_AUTHOR_TOOL,
    PROJECT_TITLE_LEDGER_ACTION,
    PROJECT_TITLE_SOURCE_AI,
    PROJECT_TITLE_SOURCE_AUTHOR,
    normalize_project_status_payload,
)
from config.project_templates import PROJECT_TEMPLATES_BY_LANG
from models import AgentArtifactLedger, Project
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

PROJECT_TITLE_MAX_CHARS = 30

# 项目还叫这些名字时，说明作者没自己起过名：AI 给作品起了名就可以跟着改。
# 来源是各类型、各语言模板的 default_project_name，加上前端的兜底名。
DEFAULT_PROJECT_NAMES = frozenset(
    {
        str(template.get("default_project_name") or "").strip()
        for templates in PROJECT_TEMPLATES_BY_LANG.values()
        for template in templates.values()
    }
    | {"我的项目", "未命名项目", "Untitled"}
) - {""}
# The dashboard numbers a default name the author already uses (「我的小说 2」); still a default.
# Only the numbers it makes (2–99): a year or other number after the name is the author's.
_NUMBERED_DEFAULT_NAME = re.compile(r"^(?P<base>.+) (?P<number>[2-9]|[1-9][0-9])$")


def is_default_project_name(name: str) -> bool:
    """A template's default name, or one the dashboard numbered so the work list has no twins."""
    if name in DEFAULT_PROJECT_NAMES:
        return True
    numbered = _NUMBERED_DEFAULT_NAME.match(name)
    return bool(numbered and numbered.group("base") in DEFAULT_PROJECT_NAMES)

TITLE_SKIPPED_AUTHOR_NAMED = "author_named"
TITLE_SKIPPED_INVALID = "invalid_title"
TITLE_SKIPPED_UNCHANGED = "unchanged"
TITLE_SOURCE_UNAVAILABLE = "provenance_unavailable"

# 每次 update_project 改了项目名，都在 agent_artifact_ledger 记一行（同一次提交）。
# 项目表没有元数据字段，「现在这个名字是不是 AI 自己起的」只能靠这条记录判断：
# 最近一条记录是 AI 自动命名、且名字和项目现在的名字一样，才算 AI 起的名。

# 拒绝改名时写进工具结果的说明。必须给出界面上真实存在的入口，模型才不会自己编一个。
# 这里不提 author_requested：每次拒绝都会带这段话，写了就等于教模型「再带上标记重试」。
AUTHOR_NAMED_TITLE_NOTE = (
    "项目名「{current}」是作者自己起的，这次没改，不要再换参数重试。"
    "需要提到改名时告诉作者：点顶部的项目名打开项目切换器，"
    "再点项目名旁的铅笔（「编辑项目名称」）就能自己改。"
)

_TITLE_STRIP_CHARS = re.compile(r"[《》“”]")
_WHITESPACE_RUN = re.compile(r"\s+")


def normalize_project_title(raw: Any) -> str | None:
    """去掉书名号、弯引号和首尾空白（中间连续空白并成一个），长度 1–30 才有效。"""
    if not isinstance(raw, str):
        return None
    title = _WHITESPACE_RUN.sub(" ", _TITLE_STRIP_CHARS.sub("", raw)).strip()
    if not title or len(title) > PROJECT_TITLE_MAX_CHARS:
        return None
    return title


class ProjectOperations:
    """
    Project-related operations for AI agent tools.

    This class provides operations for managing project status,
    with permission checking and logging.
    """

    def __init__(
        self,
        session: Session,
        user_id: str | None = None,
        *,
        request_context: Any | None = None,
    ):
        """
        Initialize project operations.

        Args:
            session: Database session
            user_id: Current user ID (UUID string, for permission checks)
            request_context: Optional request-scoped ``AgentRequestContext``
        """
        self.session = session
        self.user_id = user_id
        self.request_context = request_context

    def update_project_status(
        self,
        project_id: str,
        summary: str | None = None,
        current_phase: str | None = None,
        writing_style: str | None = None,
        notes: str | None = None,
        title: str | None = None,
        author_requested: bool = False,
        *,
        notes_transform: Callable[[str | None], str | None] | None = None,
    ) -> dict[str, Any]:
        """Update metadata using fresh locked state, including any source-aware note merge."""
        from services.project_service import project_metadata_write_lock

        with project_metadata_write_lock(project_id):
            return self._update_project_status_locked(
                project_id, summary, current_phase, writing_style, notes, title,
                author_requested, notes_transform=notes_transform,
            )

    def _update_project_status_locked(
        self,
        project_id: str,
        summary: str | None = None,
        current_phase: str | None = None,
        writing_style: str | None = None,
        notes: str | None = None,
        title: str | None = None,
        author_requested: bool = False,
        *,
        notes_transform: Callable[[str | None], str | None] | None = None,
    ) -> dict[str, Any]:
        """
        Update project status information for AI context awareness.

        Args:
            project_id: Project ID
            summary: Project summary/background
            current_phase: Current writing phase description
            writing_style: Writing style guidelines
            notes: Additional notes for AI assistant
            title: Work title (≤30 chars, no 《》). Renames the project while
                it still has a default name or the name AI auto-naming gave it;
                a name the author chose is always kept.
            author_requested: Legacy parameter, ignored; model claims never grant authority.

        Returns:
            Updated project status fields; with ``title``, also
            ``project_name_updated`` and, when not renamed, ``title_skipped``
            (author_named / invalid_title / unchanged / provenance_unavailable)

        Raises:
            PermissionError: If user doesn't have permission
            ValueError: If project not found
        """
        # Serialize with the author's explicit rename, and refresh stale ORM state
        # before deciding whether the current title is still AI-owned.
        self.session.exec(
            select(Project).where(Project.id == project_id).with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        project = check_project_ownership(self.session, project_id, self.user_id)

        if notes_transform is not None:
            notes = notes_transform(project.notes)

        # Track what was updated
        updated_fields = []

        # Normalize and validate status fields.
        # Empty string is preserved so callers can explicitly clear a field.
        raw_updates = {
            "summary": summary,
            "current_phase": current_phase,
            "writing_style": writing_style,
            "notes": notes,
        }
        normalized_updates = normalize_project_status_payload(raw_updates)

        for field_name, raw_value in raw_updates.items():
            if raw_value is not None:
                setattr(project, field_name, normalized_updates.get(field_name, ""))
                updated_fields.append(field_name)

        title_result: dict[str, Any] = {}
        if title is not None:
            title_result = self._apply_ai_title(
                project, title, author_requested=author_requested
            )

        has_status_fields = any(value is not None for value in raw_updates.values())
        if has_status_fields or title_result.get("project_name_updated"):
            # Update timestamp
            project.updated_at = utcnow()
            self.session.commit()
            self.session.refresh(project)
        else:
            # Nothing to write, but the FOR UPDATE above must not outlive this call: the request
            # session can stay open for the rest of the run, and a later write that needs this row
            # (in 2026-10-20 the tool's artifact ledger insert) would deadlock the request.
            self.session.commit()

        result: dict[str, Any] = {
            "project_id": project.id,
            "updated_fields": updated_fields,
            "current_status": {
                "summary": project.summary,
                "current_phase": project.current_phase,
                "writing_style": project.writing_style,
                "notes": without_legacy_proposals(project.notes),
            },
        }
        result.update(title_result)
        return result

    def _apply_ai_title(
        self, project: Project, raw_title: Any, *, author_requested: bool = False
    ) -> dict[str, Any]:
        """改项目名。

        - 默认名、或 AI 自动命名留下的名字：直接改成新作品名。
        - 作者自己起的名字：不能由 Agent 改，作者使用项目设置中的明确改名操作。
        """
        del author_requested  # Model/tool booleans do not grant author-title authority.
        title = normalize_project_title(raw_title)
        if title is None:
            return {
                "project_name_updated": False,
                "title_skipped": TITLE_SKIPPED_INVALID,
                "title_error": (
                    f"作品名去掉书名号后需为 1–{PROJECT_TITLE_MAX_CHARS} 字，本次未改项目名"
                ),
            }
        current_name = (project.name or "").strip()
        if current_name == title:
            return {"project_name_updated": False, "title_skipped": TITLE_SKIPPED_UNCHANGED}
        title_source = self._current_title_source(project, current_name)
        if title_source == TITLE_SOURCE_UNAVAILABLE:
            return {
                "project_name_updated": False,
                "title_skipped": TITLE_SOURCE_UNAVAILABLE,
                "title_note": "无法确认项目名的命名来源，本次未改名；作者可以通过项目设置直接改名。",
            }
        if title_source == PROJECT_TITLE_SOURCE_AUTHOR or (
            not is_default_project_name(current_name) and title_source != PROJECT_TITLE_SOURCE_AI
        ):
            return {
                "project_name_updated": False,
                "title_skipped": TITLE_SKIPPED_AUTHOR_NAMED,
                "title_note": AUTHOR_NAMED_TITLE_NOTE.format(current=current_name),
            }
        project.name = title
        self.session.add(
            AgentArtifactLedger(
                project_id=project.id,
                user_id=self.user_id,
                action=PROJECT_TITLE_LEDGER_ACTION,
                tool_name="update_project",
                artifact_ref=f"project:{project.id}",
                payload=json.dumps(
                    {
                        "project_name": title,
                        "source": PROJECT_TITLE_SOURCE_AI,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        return {"project_name_updated": True, "project_name": title}

    def _current_title_source(self, project: Project, current_name: str) -> str | None:
        """Explicit human title events override older AI naming, even after A→B→A."""
        try:
            # Human ownership is permanent in this tool's contract. Do not let
            # timestamp ties (or clock skew) resurrect an older AI naming grant.
            with self.session.begin_nested():
                author_event = self.session.exec(
                    select(AgentArtifactLedger.id).where(
                        AgentArtifactLedger.project_id == project.id,
                        AgentArtifactLedger.action == PROJECT_TITLE_LEDGER_ACTION,
                        AgentArtifactLedger.tool_name == PROJECT_TITLE_AUTHOR_TOOL,
                    ).limit(1)
                ).first()
                if author_event is not None:
                    return PROJECT_TITLE_SOURCE_AUTHOR
                payload = self.session.exec(
                    select(AgentArtifactLedger.payload)
                    .where(
                        AgentArtifactLedger.project_id == project.id,
                        AgentArtifactLedger.action == PROJECT_TITLE_LEDGER_ACTION,
                    )
                    .order_by(desc(AgentArtifactLedger.created_at))
                    .limit(1)
                ).first()
        except Exception as exc:  # 读不到记录时按作者起的名字处理，宁可不改
            log_with_context(
                logger,
                30,  # WARNING
                "Failed to read project title ledger",
                project_id=project.id,
                error=str(exc),
            )
            return TITLE_SOURCE_UNAVAILABLE
        if payload is None:
            return None
        if not isinstance(payload, str):
            return TITLE_SOURCE_UNAVAILABLE
        try:
            record = json.loads(payload)
        except (TypeError, ValueError):
            return TITLE_SOURCE_UNAVAILABLE
        if not isinstance(record, dict):
            return TITLE_SOURCE_UNAVAILABLE
        # Source is durable authority; name equality only checks legacy AI-state consistency.
        if record.get("source") == PROJECT_TITLE_SOURCE_AUTHOR:
            return PROJECT_TITLE_SOURCE_AUTHOR
        if record.get("source") == PROJECT_TITLE_SOURCE_AI:
            if str(record.get("project_name") or "").strip() == current_name:
                return PROJECT_TITLE_SOURCE_AI
            # Legacy/direct writes did not record a source event. A name newer
            # than the last AI event is protected, never granted AI authority.
            return PROJECT_TITLE_SOURCE_AUTHOR
        return TITLE_SOURCE_UNAVAILABLE


__all__ = [
    "ProjectOperations",
]
