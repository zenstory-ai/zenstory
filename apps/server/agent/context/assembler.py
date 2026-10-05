"""
Context assembler for gathering and formatting project context.

Gathers relevant context from the project using the unified File model
and assembles it into a formatted prompt with priority-based selection.
"""

# Standard library
import contextlib
import hashlib
import json
import os
import re
import time
from typing import Any

# Third-party
from sqlmodel import Session, select

# Local
from utils.logger import get_logger, log_with_context

from ..constants import CONTENT_FILE_TYPES, INVENTORY_FILE_TYPES
from ..schemas.context import ContextData, ContextItem, ContextPriority
from ..tools.permissions import ForbiddenError, NotFoundError, check_project_ownership
from ..utils.aho_corasick import AhoCorasickMatcher, select_longest_non_ambiguous_matches
from ..utils.token_utils import estimate_text_tokens
from .budget import TokenBudget, truncate_text_to_tokens
from .prioritizer import ContextPrioritizer

logger = get_logger(__name__)

# Floor for the item-selection token budget after reserving header/framing
# overhead, so a large project-status + inventory header can never starve the
# priority-selected items down to nothing.
MIN_ITEM_TOKEN_BUDGET = 512

# 条目档（焦点文件 / 用户附加文件 / 用户引用文本所在的 CRITICAL 档）的保底比例。
#
# 历史缺陷：header（项目状态 + 文件清单）完全不受 max_tokens 约束，先渲染再用
# `max_tokens - header_tokens` 给条目分预算，长篇项目下这个差值恒为负，
# 条目预算永远触底到 MIN_ITEM_TOKEN_BUDGET，于是用户手动附加的文件和手动选中的
# 引用文本被静默丢弃——最不重要的静态清单挤掉了最重要的用户意图。
# 现在反过来：先给条目档留出这个比例，再把剩下的额度分给可裁剪的 header。
ITEM_TOKEN_RESERVE_RATIO = 0.35

# 项目状态段（简介 / 备注等自由文本）允许占用的最大比例，超出即裁剪。
PROJECT_STATUS_TOKEN_RATIO = 0.25

# 项目状态里单个长字段（summary / notes）无论如何都要保留的最小 token 数，
# 免得极端预算下把项目简介裁成空字符串。
MIN_PROJECT_STATUS_FIELD_TOKENS = 120


DEFAULT_RETRIEVAL_SEMANTIC_TIMEOUT_S = 5.0


def _retrieval_semantic_timeout_s() -> float:
    """上下文组装时语义检索的外层时限（AGENT_RETRIEVAL_SEMANTIC_TIMEOUT_S，秒）。"""
    raw = (os.getenv("AGENT_RETRIEVAL_SEMANTIC_TIMEOUT_S") or "").strip()
    if not raw:
        return DEFAULT_RETRIEVAL_SEMANTIC_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_RETRIEVAL_SEMANTIC_TIMEOUT_S
    return value if value > 0 else DEFAULT_RETRIEVAL_SEMANTIC_TIMEOUT_S


class ContextAssembler:
    """
    Assembles context from project data for AI prompts.

    Features:
    - Gathers files by type (outline, draft, character, lore, snippet)
    - Priority-based selection
    - Token budget management
    - Formatted output for prompts
    """

    def __init__(self):
        """
        Initialize assembler with prioritizer.
        """
        self.prioritizer = ContextPrioritizer()

    def assemble(
        self,
        session: Session,
        project_id: str,
        user_id: str | None = None,
        query: str | None = None,
        focus_file_id: str | None = None,
        attached_file_ids: list[str] | None = None,
        attached_library_materials: list[dict[str, int]] | None = None,
        text_quotes: list[dict[str, str]] | None = None,
        max_tokens: int = 6000,
        include_characters: bool = True,
        include_lores: bool = True,
    ) -> ContextData:
        """
        Assemble context for a project.

        Args:
            session: Database session
            project_id: Project ID
            user_id: User ID for ownership verification (None to skip verification)
            query: Optional query for retrieval
            focus_file_id: Focus file ID
            attached_file_ids: List of manually attached file IDs (e.g., materials)
            attached_library_materials: List of library material references
            text_quotes: List of user-selected text quotes
            max_tokens: Maximum tokens for context
            include_characters: Whether to include characters
            include_lores: Whether to include lore

        Returns:
            ContextData with formatted context
        """
        log_with_context(
            logger,
            20,  # INFO
            "Context assembly started",
            project_id=project_id,
            user_id=user_id,
            query=query[:100] if query else None,
            focus_file_id=focus_file_id,
            attached_file_count=len(attached_file_ids) if attached_file_ids else 0,
            attached_library_count=len(attached_library_materials) if attached_library_materials else 0,
            text_quote_count=len(text_quotes) if text_quotes else 0,
            max_tokens=max_tokens,
            include_characters=include_characters,
            include_lores=include_lores,
        )

        # Verify user ownership of the project
        if user_id is not None:
            try:
                check_project_ownership(session, project_id, user_id)
            except (ForbiddenError, NotFoundError) as e:
                log_with_context(
                    logger,
                    40,  # ERROR
                    "Context assembly blocked: permission denied",
                    project_id=project_id,
                    user_id=user_id,
                    error=str(e),
                )
                # Return empty context rather than raising exception
                return ContextData(
                    context="",
                    items=[],
                    refs=[],
                    token_estimate=0,
                    original_item_count=0,
                    trimmed_item_count=0,
                    budget_used={},
                )

        from models import File

        items: list[ContextItem] = []

        # 0. Get project status info (for AI context awareness)
        project_status = self._get_project_status(session, project_id)

        # 0.1 Get project file inventory (outlines and drafts list)
        # This helps the agent know what chapters/outlines already exist
        file_inventory = self._get_file_inventory(session, project_id)

        # 0.2 Get attached files (manually selected by user, CRITICAL priority)
        if attached_file_ids:
            items.extend(self._get_attached_files(session, project_id, attached_file_ids))

        # 0.25 Get attached library materials (manually selected by user, CRITICAL priority)
        if attached_library_materials:
            items.extend(self._get_attached_library_materials(session, user_id, attached_library_materials))

        # 0.3 Get text quotes (user-selected text, CRITICAL priority)
        if text_quotes:
            items.extend(self._get_text_quotes(text_quotes))

        # 1. Get focus file (outline or draft)
        if focus_file_id:
            focus_file = session.get(File, focus_file_id)
            if focus_file and not focus_file.is_deleted and focus_file.project_id == project_id:
                items.append(self._file_to_context_item(focus_file, is_focus=True))

                # Get related outlines/drafts
                items.extend(self._get_related_files(
                    session, project_id, focus_file
                ))

        # 2 & 3. Get characters and lores in a single batch query
        # Optimization: Combine two separate queries into one
        if include_characters or include_lores:
            file_types_to_fetch = []
            if include_characters:
                file_types_to_fetch.append("character")
            if include_lores:
                file_types_to_fetch.append("lore")

            if file_types_to_fetch:
                items.extend(self._get_files_by_types(session, project_id, file_types_to_fetch))

        # 4. Query-time retrieval snippets (hybrid search, best-effort).
        # Inject compact snippet payloads with source/line/score metadata.
        existing_file_ids = {
            item.id for item in items
            if isinstance(item.id, str) and item.id.strip()
        }
        items.extend(
            self._get_retrieved_snippets(
                project_id=project_id,
                query=query,
                exclude_entity_ids=existing_file_ids,
            )
        )

        # 5. Deduplicate items by ID (prefer higher priority / is_focus)
        items = self._deduplicate_items(items)

        # 6. Query-aware recall ranking
        items = self._apply_query_recall_ranking(items, query)

        # 7. Prioritize and select within budget.
        #
        # 分配顺序是**反的**：先给条目档留出保底额度，再把剩余额度分给 header
        # （项目状态 + 文件清单），而不是先把 header 渲染满再拿剩下的给条目。
        # 否则长篇项目的 header 会吃掉全部 max_tokens，条目预算触底到
        # MIN_ITEM_TOKEN_BUDGET，用户显式附加的文件与引用文本被静默丢弃。
        #
        # header 的两段都是可裁剪的：项目状态按 PROJECT_STATUS_TOKEN_RATIO 裁剪
        # 长字段，文件清单按剩余额度逐条填充并显式提示「已省略 N 个」。
        reserved_for_items = max(
            MIN_ITEM_TOKEN_BUDGET, int(max_tokens * ITEM_TOKEN_RESERVE_RATIO)
        )
        project_status = self._bound_project_status(
            project_status,
            max_tokens=int(max_tokens * PROJECT_STATUS_TOKEN_RATIO),
        )
        status_tokens = estimate_text_tokens(
            self._format_context([], None, project_status)
        )
        inventory_token_budget = max(
            0, max_tokens - reserved_for_items - status_tokens
        )

        header_tokens = estimate_text_tokens(
            self._format_context(
                [],
                file_inventory,
                project_status,
                inventory_token_budget=inventory_token_budget,
            )
        )
        item_token_budget = max(MIN_ITEM_TOKEN_BUDGET, max_tokens - header_tokens)
        budget = TokenBudget(max_tokens=item_token_budget)
        prioritized = self.prioritizer.prioritize(items)
        groups = self.prioritizer.group_by_priority(prioritized)
        selected, budget_used = budget.select_items(prioritized, groups)

        # 8. Format context with project status
        formatted = self._format_context(
            selected,
            file_inventory,
            project_status,
            inventory_token_budget=inventory_token_budget,
        )

        # 9. Collect referenced item IDs
        refs = [item.id for item in selected]

        return ContextData(
            context=formatted,
            items=[item.to_dict() for item in selected],
            refs=refs,
            token_estimate=budget.estimate_tokens(formatted),
            original_item_count=len(items),
            trimmed_item_count=len(selected),
            budget_used={p.value: v for p, v in budget_used.items()},
        )

    def _batch_get_files(
        self,
        session: Session,
        file_ids: list[str],
    ) -> dict[str, Any]:
        """
        Batch fetch files by IDs to avoid N+1 queries.

        Args:
            session: Database session
            file_ids: List of file IDs to fetch

        Returns:
            Dict mapping file_id -> File object
        """
        from models import File

        if not file_ids:
            return {}

        files = session.exec(
            select(File).where(File.id.in_(file_ids))
        ).all()

        return {file.id: file for file in files}

    def _file_to_context_item(
        self,
        file: Any,
        is_focus: bool = False,
        relation: str = "",
    ) -> ContextItem:
        """Convert a File to ContextItem based on its type.

        重要不变量：无论文件是什么类型，``is_focus`` / ``relation`` / ``file_type``
        都必须落到 item.metadata 上。历史缺陷是只有 outline/draft 分支传了
        is_focus，character/lore/snippet 三个分支把形参直接丢弃，导致
        ContextItem.is_focus（读 metadata["is_focus"] 的属性）恒为 False，
        下游整条焦点保护链（prioritizer 的 CRITICAL 提升与组内排序、
        TokenBudget 的 CRITICAL 池化预算、渲染时的「← 当前焦点」标记）全部失效，
        用户打开角色卡/设定条目再提问时焦点文件甚至会被检索片段挤出上下文。
        因此这里统一走 `_finalize_context_item` 后处理，新增类型分支不会再漏。
        """
        file_type = file.file_type

        # Parse metadata
        metadata = {}
        if file.file_metadata:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                metadata = json.loads(file.file_metadata)

        if file_type == "character":
            # Build character profile from content and metadata
            profile_parts = []
            if file.content:
                profile_parts.append(file.content)
            if metadata.get("role"):
                profile_parts.append(f"角色: {metadata['role']}")
            if metadata.get("age"):
                profile_parts.append(f"年龄: {metadata['age']}")
            if metadata.get("gender"):
                profile_parts.append(f"性别: {metadata['gender']}")
            if metadata.get("personality"):
                profile_parts.append(f"性格: {metadata['personality']}")

            item = ContextItem.from_character(
                id=file.id,
                name=file.title,
                profile="\n".join(profile_parts) if profile_parts else "",
            )

        elif file_type == "lore":
            item = ContextItem.from_lore(
                id=file.id,
                title=file.title,
                content=file.content or "",
                category=metadata.get("category", ""),
                importance=metadata.get("importance", "medium"),
            )

        elif file_type == "snippet":
            item = ContextItem.from_snippet(
                id=file.id,
                title=file.title,
                content=file.content or "",
                relevance_score=0.0,
                source=metadata.get("source", ""),
            )

        else:  # outline, draft, script, document, or other
            item = ContextItem.from_outline(
                id=file.id,
                title=file.title,
                content=file.content or "",
                is_focus=is_focus,
                relation=relation,
            )

        return self._finalize_context_item(
            item,
            file_type=file_type,
            is_focus=is_focus,
            relation=relation,
        )

    @staticmethod
    def _finalize_context_item(
        item: ContextItem,
        *,
        file_type: str,
        is_focus: bool,
        relation: str,
    ) -> ContextItem:
        """统一补齐所有类型分支都必须携带的元数据 / 优先级。

        放在一处而不是各分支各写一遍，是为了让「新增一种 file_type」不可能
        再漏掉焦点标记（见 _file_to_context_item 的 docstring）。
        """
        item.metadata["file_type"] = file_type
        item.metadata["is_focus"] = bool(is_focus)
        # from_outline 已经写过 relation（可能是 None），这里只补未写过的分支，
        # 避免把 outline 分支显式写入的 None 覆盖成 ""。
        if "relation" not in item.metadata:
            item.metadata["relation"] = relation or None

        if is_focus:
            # 与 from_outline(is_focus=True) 的行为保持一致：焦点文件必须进
            # CRITICAL 档并拿到最高相关度，否则组内排序会把它排到检索片段之后。
            item.priority = ContextPriority.CRITICAL
            item.relevance_score = max(item.relevance_score or 0.0, 1.0)

        return item

    def _get_related_files(
        self,
        session: Session,
        project_id: str,
        focus: Any,
    ) -> list[ContextItem]:
        """Get related files (parent outline, previous chapter, siblings).

        Performance note:
        Avoid loading *all* sibling files (and their potentially large content)
        into memory just to pick a few. We fetch only the parent + at most
        1 previous chapter + 3 most-recently-updated siblings.
        """
        from models import File

        items: list[ContextItem] = []

        if not focus.parent_id:
            return items

        # 1. 获取父级大纲（提供故事背景）
        # Note: session.get() uses primary key lookup which is O(1), so this is already optimal
        parent = session.get(File, focus.parent_id)
        if parent and not parent.is_deleted and parent.project_id == project_id and parent.file_type == "outline":
            items.append(self._file_to_context_item(parent, relation="parent"))

        # 2. 查找前一章节（order 小于当前 focus.order 的最大值）
        prev_chapter: Any | None = None
        if focus.order is not None:
            prev_stmt = (
                select(File)
                .where(
                    File.project_id == project_id,
                    File.parent_id == focus.parent_id,
                    File.id != focus.id,
                    File.file_type != "folder",
                    File.is_deleted.is_(False),
                    # 同族修复：只写死 "draft" 会让短剧项目（正文是 script）永远找不到
                    # "前一集"，续写时丢失上一集剧情。统一走 CONTENT_FILE_TYPES。
                    File.file_type.in_([*CONTENT_FILE_TYPES, "outline"]),
                    File.order.is_not(None),
                    File.order < focus.order,
                )
                .order_by(
                    File.order.desc(),
                    File.updated_at.desc(),
                    File.id.desc(),
                )
                .limit(1)
            )
            prev_chapter = session.exec(prev_stmt).first()

        if prev_chapter:
            items.append(self._file_to_context_item(prev_chapter, relation="previous"))

        # 3. 获取其他同级文件：按最近修改排序，取前 3 个（同时间戳时按 id 稳定排序）
        exclude_ids = {str(focus.id)}
        if prev_chapter is not None and getattr(prev_chapter, "id", None):
            exclude_ids.add(str(prev_chapter.id))

        siblings_stmt = (
            select(File)
            .where(
                File.project_id == project_id,
                File.parent_id == focus.parent_id,
                File.file_type != "folder",
                File.is_deleted.is_(False),
                File.id.notin_(list(exclude_ids)),
            )
            .order_by(
                File.updated_at.desc(),
                File.id,
            )
            .limit(3)
        )
        other_siblings = session.exec(siblings_stmt).all()

        for sibling in other_siblings:
            items.append(self._file_to_context_item(sibling, relation="sibling"))

        return items

    def _get_attached_files(
        self,
        session: Session,
        project_id: str,
        file_ids: list[str],
    ) -> list[ContextItem]:
        """
        Get manually attached files (e.g., materials added by user to chat).

        These files are given CRITICAL priority as the user explicitly selected them.

        Args:
            session: Database session
            project_id: Project ID
            file_ids: List of file IDs to include

        Returns:
            List of ContextItem with CRITICAL priority
        """
        if not file_ids:
            return []

        # Batch fetch all files to avoid N+1 queries
        files_map = self._batch_get_files(session, file_ids)

        items = []
        for file_id in file_ids:
            file = files_map.get(file_id)
            if file and not file.is_deleted and file.project_id == project_id:
                item = self._file_to_context_item(file, relation="attached")
                # Override priority to CRITICAL for attached files
                item.priority = ContextPriority.CRITICAL
                item.metadata["attached"] = True
                items.append(item)

        return items

    def _get_attached_library_materials(
        self,
        session: Session,
        user_id: str | None,
        library_materials: list[dict[str, int]],
    ) -> list[ContextItem]:
        """
        Get manually attached library materials (from material library).

        These materials are given CRITICAL priority as the user explicitly selected them.

        Args:
            session: Database session
            user_id: User ID for ownership verification
            library_materials: List of dicts with novel_id, entity_type, entity_id

        Returns:
            List of ContextItem with CRITICAL priority
        """
        from api.material_utils import (
            format_character_to_markdown,
            format_goldenfinger_to_markdown,
            format_relationship_to_markdown,
            format_story_to_markdown,
            format_storyline_to_markdown,
            format_worldview_to_markdown,
        )
        from models.material_models import (
            Character,
            GoldenFinger,
            Novel,
            Story,
            StoryLine,
            WorldView,
        )

        if not library_materials:
            return []

        items = []

        # Optimization: Batch fetch all novels first
        novel_ids = {m.get("novel_id") for m in library_materials if m.get("novel_id")}
        novels_map: dict[int, Novel] = {}
        if novel_ids:
            novels = session.exec(
                select(Novel).where(Novel.id.in_(novel_ids))
            ).all()
            novels_map = {n.id: n for n in novels}

        # Optimization: Batch fetch all characters
        character_ids = {
            (m.get("novel_id"), m.get("entity_id"))
            for m in library_materials
            if m.get("entity_type") == "characters" and m.get("entity_id") and m.get("novel_id")
        }
        characters_map: dict[int, Character] = {}
        if character_ids:
            char_entity_ids = [eid for _, eid in character_ids]
            characters = session.exec(
                select(Character).where(Character.id.in_(char_entity_ids))
            ).all()
            characters_map = {c.id: c for c in characters}

        # Optimization: Batch fetch all goldenfingers
        gf_ids = {
            (m.get("novel_id"), m.get("entity_id"))
            for m in library_materials
            if m.get("entity_type") == "goldenfingers" and m.get("entity_id") and m.get("novel_id")
        }
        goldenfingers_map: dict[int, GoldenFinger] = {}
        if gf_ids:
            gf_entity_ids = [eid for _, eid in gf_ids]
            goldenfingers = session.exec(
                select(GoldenFinger).where(GoldenFinger.id.in_(gf_entity_ids))
            ).all()
            goldenfingers_map = {gf.id: gf for gf in goldenfingers}

        # Optimization: Batch fetch all storylines
        sl_ids = {
            (m.get("novel_id"), m.get("entity_id"))
            for m in library_materials
            if m.get("entity_type") == "storylines" and m.get("entity_id") and m.get("novel_id")
        }
        storylines_map: dict[int, StoryLine] = {}
        if sl_ids:
            sl_entity_ids = [eid for _, eid in sl_ids]
            storylines = session.exec(
                select(StoryLine).where(StoryLine.id.in_(sl_entity_ids))
            ).all()
            storylines_map = {sl.id: sl for sl in storylines}

        # Optimization: Batch fetch all stories
        story_ids = {
            (m.get("novel_id"), m.get("entity_id"))
            for m in library_materials
            if m.get("entity_type") == "stories" and m.get("entity_id") and m.get("novel_id")
        }
        stories_map: dict[int, Story] = {}
        if story_ids:
            story_entity_ids = [eid for _, eid in story_ids]
            stories = session.exec(
                select(Story).where(Story.id.in_(story_entity_ids))
            ).all()
            stories_map = {story.id: story for story in stories}

        # Optimization: Batch fetch worldviews (one per novel_id)
        worldview_novel_ids = {
            m.get("novel_id")
            for m in library_materials
            if m.get("entity_type") == "worldview" and m.get("novel_id")
        }
        worldviews_map: dict[int, WorldView] = {}
        if worldview_novel_ids:
            worldviews = session.exec(
                select(WorldView).where(WorldView.novel_id.in_(worldview_novel_ids))
            ).all()
            worldviews_map = {wv.novel_id: wv for wv in worldviews}

        # Now process each material using pre-fetched data
        for material in library_materials:
            novel_id = material.get("novel_id")
            entity_type = material.get("entity_type")
            entity_id = material.get("entity_id")

            if not all([novel_id, entity_type, entity_id]):
                continue

            # Get novel from pre-fetched map
            novel = novels_map.get(novel_id)
            if not novel or novel.deleted_at is not None or (user_id and novel.user_id != user_id):
                continue

            markdown = ""
            title = ""

            try:
                if entity_type == "characters":
                    character = characters_map.get(entity_id)
                    if not character or character.novel_id != novel_id:
                        continue

                    title, markdown = format_character_to_markdown(character, novel.title)

                elif entity_type == "worldview":
                    world_view = worldviews_map.get(novel_id)
                    if not world_view:
                        continue

                    title, markdown = format_worldview_to_markdown(world_view, novel.title)

                elif entity_type == "goldenfingers":
                    golden_finger = goldenfingers_map.get(entity_id)
                    if not golden_finger or golden_finger.novel_id != novel_id:
                        continue

                    title, markdown = format_goldenfinger_to_markdown(golden_finger, novel.title)

                elif entity_type == "storylines":
                    story_line = storylines_map.get(entity_id)
                    if not story_line or story_line.novel_id != novel_id:
                        continue

                    title, markdown = format_storyline_to_markdown(story_line, session, novel.title)

                elif entity_type == "stories":
                    story = stories_map.get(entity_id)
                    if not story:
                        continue

                    story_novel_id = story.novel_id
                    if story_novel_id is None and story.story_line_id:
                        # 历史行没有 novel_id，经故事线归属
                        story_line = session.get(StoryLine, story.story_line_id)
                        story_novel_id = story_line.novel_id if story_line else None
                    if story_novel_id != novel_id:
                        continue

                    title, markdown = format_story_to_markdown(story, novel.title)

                elif entity_type == "relationships":
                    title, markdown = format_relationship_to_markdown(
                        novel_id,
                        session,
                        novel.title,
                        relationship_id=entity_id,
                    )

                else:
                    continue

                if markdown and title:
                    item = ContextItem.from_snippet(
                        id=f"lib_{novel_id}_{entity_type}_{entity_id}",
                        title=title,
                        content=markdown,
                        relevance_score=1.0,
                        source=f"library:{novel.title}",
                    )
                    # Override priority to CRITICAL for attached library materials
                    item.priority = ContextPriority.CRITICAL
                    item.metadata["attached"] = True
                    item.metadata["library_material"] = True
                    items.append(item)

            except Exception as e:
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Failed to load library material",
                    novel_id=novel_id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    error=str(e),
                )
                continue

        return items

    def _get_text_quotes(
        self,
        text_quotes: list[dict[str, str]],
    ) -> list[ContextItem]:
        """
        Convert user-selected text quotes to ContextItems.

        Args:
            text_quotes: List of quote dicts with text, fileId, fileTitle

        Returns:
            List of ContextItem with CRITICAL priority
        """
        import uuid

        items = []
        for quote in text_quotes:
            text = quote.get("text", "")
            file_title = quote.get("fileTitle", "未知文件")
            if text:
                item = ContextItem.from_quote(
                    id=str(uuid.uuid4()),
                    text=text,
                    file_title=file_title,
                )
                items.append(item)

        return items

    def _get_retrieved_snippets(
        self,
        project_id: str,
        query: str | None,
        exclude_entity_ids: set[str] | None = None,
        top_k: int = 6,
    ) -> list[ContextItem]:
        """
        Retrieve query-related snippet items via hybrid search.

        Best-effort:
        - Fails open (returns []) when vector service is unavailable.
        - Never blocks normal context assembly.
        """
        enabled_raw = os.getenv("AGENT_ENABLE_RETRIEVAL_SNIPPETS")
        if enabled_raw is not None and enabled_raw.strip().lower() in {"0", "false", "no", "off"}:
            return []

        top_k_env = (os.getenv("AGENT_RETRIEVAL_SNIPPETS_TOP_K") or "").strip()
        if top_k_env:
            with contextlib.suppress(ValueError):
                top_k = int(top_k_env)
        if top_k <= 0:
            return []

        normalized_query = (query or "").strip()
        if not normalized_query:
            return []

        excluded_ids = {eid for eid in (exclude_entity_ids or set()) if isinstance(eid, str) and eid.strip()}
        seen_entity_ids: set[str] = set()

        started = time.perf_counter()
        error_reason: str | None = None
        search_results: list[Any] = []
        try:
            from services.llama_index import get_llama_index_service

            svc = get_llama_index_service()
            search_results = svc.hybrid_search(
                project_id=project_id,
                query=normalized_query,
                top_k=max(1, int(top_k or 6)),
                entity_types=None,
                # 每轮 /stream 组装上下文都会走这里：embedding 服务变慢时语义
                # 分支到时即放弃，只用词法结果，不让整轮对话卡在「组装上下文」。
                semantic_timeout_s=_retrieval_semantic_timeout_s(),
            )
        except Exception as e:
            error_reason = str(e)
            return []
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            log_threshold_ms_raw = (os.getenv("AGENT_RETRIEVAL_SNIPPETS_LOG_THRESHOLD_MS") or "").strip()
            try:
                log_threshold_ms = int(log_threshold_ms_raw) if log_threshold_ms_raw else 150
            except ValueError:
                log_threshold_ms = 150

            if error_reason or duration_ms >= log_threshold_ms:
                query_hash = None
                try:
                    query_hash = hashlib.sha256(normalized_query.encode("utf-8")).hexdigest()[:12]
                except Exception:
                    query_hash = None
                log_with_context(
                    logger,
                    20,  # INFO
                    "Hybrid snippet retrieval finished",
                    project_id=project_id,
                    query_length=len(normalized_query),
                    query_hash=query_hash,
                    top_k=top_k,
                    result_count=len(search_results),
                    status="error" if error_reason else "success",
                    reason=error_reason,
                    duration_ms=duration_ms,
                    log_threshold_ms=log_threshold_ms,
                )

        total = max(1, len(search_results))
        snippet_items: list[ContextItem] = []

        for index, result in enumerate(search_results, start=1):
            entity_id = str(getattr(result, "entity_id", "") or "").strip()
            if not entity_id or entity_id in excluded_ids or entity_id in seen_entity_ids:
                continue

            snippet = str(getattr(result, "snippet", "") or "").strip()
            if not snippet:
                snippet = str(getattr(result, "content", "") or "").strip()
            if not snippet:
                continue

            entity_type = str(getattr(result, "entity_type", "") or "").strip().lower() or "unknown"
            title = str(getattr(result, "title", "") or "").strip() or f"{entity_type}:{entity_id}"
            fused_score_raw = getattr(result, "fused_score", None)
            score_raw = getattr(result, "score", None)
            line_start_raw = getattr(result, "line_start", None)
            sources_raw = getattr(result, "sources", None)
            sources = (
                [str(s).strip() for s in sources_raw if str(s).strip()]
                if isinstance(sources_raw, list)
                else []
            )
            fused_score = (
                float(fused_score_raw)
                if isinstance(fused_score_raw, int | float)
                else (float(score_raw) if isinstance(score_raw, int | float) else None)
            )
            line_start = int(line_start_raw) if isinstance(line_start_raw, int) and line_start_raw > 0 else None

            # rank-based relevance normalization keeps snippets in RELEVANT bucket.
            rank_relevance = 1.0 - ((index - 1) / (total + 1))
            relevance_score = max(0.55, min(rank_relevance, 1.0))

            source_label_parts = [entity_type]
            if sources:
                source_label_parts.append("+".join(sources))
            source_label = "retrieval:" + "/".join(source_label_parts)

            item = ContextItem.from_snippet(
                id=f"retrieval::{entity_id}",
                title=title,
                content=snippet,
                relevance_score=relevance_score,
                source=source_label,
            )
            item.priority = ContextPriority.RELEVANT
            item.metadata.update({
                "retrieved": True,
                "entity_id": entity_id,
                "entity_type": entity_type,
                "line_start": line_start,
                "fused_score": fused_score,
                "score": float(score_raw) if isinstance(score_raw, int | float) else None,
                "sources": sources,
            })
            snippet_items.append(item)
            seen_entity_ids.add(entity_id)

        return snippet_items

    def _get_files_by_types(
        self,
        session: Session,
        project_id: str,
        file_types: list[str],
        limit_per_type: int = 10,
    ) -> list[ContextItem]:
        """
        Get files of multiple types in a single batch query.

        Optimization: Reduces N queries to 1 query when fetching multiple file types.

        Args:
            session: Database session
            project_id: Project ID
            file_types: List of file types to fetch (e.g., ["character", "lore"])
            limit_per_type: Maximum files per type

        Returns:
            List of ContextItem for all requested types
        """
        from models import File

        if not file_types:
            return []

        # 每种类型各下推一条带 LIMIT 的查询。
        #
        # 之前是一条不带 .limit() 的 select(File)（含 content 正文列），
        # 把项目里全部角色/设定的正文一次性读进内存，再在 Python 侧只留每类前
        # limit_per_type 条——大项目下每次对话都做一次全表规模的正文读取，
        # PostgreSQL 上还会触发大量 TOAST 解压，正是 _get_file_inventory 注释里
        # 明确要避免的成本。类型数固定（character/lore），2 条小查询远优于全表。
        files_by_type: dict[str, list[File]] = {ft: [] for ft in file_types}
        if limit_per_type > 0:
            for file_type in file_types:
                files_by_type[file_type] = list(
                    session.exec(
                        select(File).where(
                            File.project_id == project_id,
                            File.file_type == file_type,
                            File.is_deleted.is_(False),
                        )
                        .order_by(File.updated_at.desc(), File.id.asc())
                        .limit(limit_per_type)
                    ).all()
                )

        # Convert to ContextItems
        items = []
        for file_type in file_types:
            for file in files_by_type[file_type]:
                items.append(self._file_to_context_item(file))

        # Sort lores by importance if applicable
        if "lore" in file_types:
            importance_order = {"high": 0, "medium": 1, "low": 2}
            lore_items = [item for item in items if item.metadata.get("file_type") == "lore" or item.type == "lore"]
            lore_items.sort(
                key=lambda x: (
                    importance_order.get(x.metadata.get("importance", "medium"), 3),
                    str(x.id),
                )
            )
            # Rebuild items list with sorted lores
            non_lore_items = [item for item in items if item not in lore_items]
            items = non_lore_items + lore_items

        return items

    def _get_files_by_type(
        self,
        session: Session,
        project_id: str,
        file_type: str,
        limit: int = 10,
    ) -> list[ContextItem]:
        """Get files of a specific type."""
        from models import File

        # 对于 lore 类型，保持原有逻辑（按 order 排序，后续按 importance 排序）
        # 对于 character 类型，按最近修改时间排序
        if file_type == "lore":
            files = session.exec(
                select(File).where(
                    File.project_id == project_id,
                    File.file_type == file_type,
                    File.is_deleted.is_(False),
                ).order_by(File.order.asc(), File.id.asc())
                .limit(limit)
            ).all()
        else:
            files = session.exec(
                select(File).where(
                    File.project_id == project_id,
                    File.file_type == file_type,
                    File.is_deleted.is_(False),
                ).order_by(File.updated_at.desc(), File.id.asc())  # 按最近修改时间排序
                .limit(limit)
            ).all()

        items = []
        for file in files:
            items.append(self._file_to_context_item(file))

        # Sort lores by importance if applicable
        if file_type == "lore":
            importance_order = {"high": 0, "medium": 1, "low": 2}
            items.sort(
                key=lambda x: (
                    importance_order.get(x.metadata.get("importance", "medium"), 3),
                    str(x.id),
                )
            )

        return items

    def _get_project_status(
        self,
        session: Session,
        project_id: str,
    ) -> dict[str, Any]:
        """
        Get project status information for AI context awareness.

        Args:
            session: Database session
            project_id: Project ID

        Returns:
            Dict with project status fields
        """
        from models import Project

        project = session.get(Project, project_id)
        if not project:
            return {}

        # Skip soft-deleted projects
        if project.is_deleted:
            return {}

        return {
            "name": project.name,
            "description": project.description,
            "summary": project.summary,
            "current_phase": project.current_phase,
            "writing_style": project.writing_style,
            "notes": project.notes,
        }

    def _get_file_inventory(
        self,
        session: Session,
        project_id: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Get a lightweight inventory of all project files.

        This provides the agent with awareness of what files already exist
        (especially outlines and drafts) without loading full content.

        Args:
            session: Database session
            project_id: Project ID

        Returns:
            Dict with file_type -> list of {id, title, word_count?} mappings
        """
        from models import File

        # 桶来自共享常量 INVENTORY_FILE_TYPES（folder 之外的全部实体类型）。
        #
        # 历史缺陷：这里把桶写死成 outline/draft/character/lore/snippet 五种，
        # 再用 `if file_type not in inventory: continue` 丢掉其余类型，于是
        # script（短剧分集正文）与 document（create_file 的默认类型）整体消失，
        # _format_context 还在「请勿重复创建已存在的文件」标题下输出肯定断言
        # 「正文: (暂无)」，直接驱动 AI 重复创建已经写好的分集。
        inventory: dict[str, list[dict[str, Any]]] = {
            ft: [] for ft in INVENTORY_FILE_TYPES
        }

        # Query only required columns to avoid loading full file content.
        #
        # IMPORTANT: Do NOT compute func.length(File.content) here.
        # On PostgreSQL large content is stored in TOAST; length(content) can
        # force fetching large values for *every* row and become a hot-path
        # latency source on each agent request.
        file_rows = session.exec(
            select(
                File.id,
                File.title,
                File.file_type,
                File.order,
                File.created_at,
            ).where(
                File.project_id == project_id,
                File.file_type != "folder",
                File.is_deleted.is_(False),
            )
        ).all()

        from utils.title_sequence import build_sequence_sort_key

        grouped: dict[str, list[tuple[tuple[int, int, Any, str], dict[str, Any]]]] = {
            key: [] for key in inventory
        }

        for file_id, title, file_type, file_order, created_at in file_rows:
            # 未知/新增的类型也建桶，绝不静默丢弃——「看不见的文件」正是本缺陷
            # 的根因，宁可多渲染一节「其他文件」也不能让文件凭空消失。
            bucket = grouped.setdefault(file_type, [])

            word_count = None

            # Keep inventory ordering aligned with file-tree/export behavior:
            # chapter-like writing files follow title sequence, while still
            # recovering from legacy order=0 data and obvious typos like
            # 580 for 第58章.
            effective_order, seq_num = build_sequence_sort_key(
                file_order,
                title=title,
                file_type=file_type,
            )

            sort_key = (effective_order, seq_num, created_at, file_id)

            bucket.append(
                (
                    sort_key,
                    {
                        "id": file_id,
                        "title": title,
                        "file_type": file_type,
                        "word_count": word_count,
                    },
                )
            )

        for file_type, rows in grouped.items():
            if rows:
                inventory[file_type] = [item for _, item in sorted(rows, key=lambda x: x[0])]

        return inventory

    def _deduplicate_items(self, items: list[ContextItem]) -> list[ContextItem]:
        """
        Remove duplicate items by ID, keeping the one with higher priority or is_focus.

        Args:
            items: List of context items (may contain duplicates)

        Returns:
            Deduplicated list
        """
        seen: dict[str, ContextItem] = {}
        priority_order = {
            ContextPriority.CRITICAL: 0,
            ContextPriority.CONSTRAINT: 1,
            ContextPriority.RELEVANT: 2,
            ContextPriority.INSPIRATION: 3,
        }

        for item in items:
            if item.id not in seen:
                seen[item.id] = item
            else:
                existing = seen[item.id]
                # Prefer is_focus
                if item.is_focus and not existing.is_focus or (priority_order.get(item.priority, 4) <
                      priority_order.get(existing.priority, 4)) or ((item.relevance_score or 0) >
                      (existing.relevance_score or 0)):
                    seen[item.id] = item

        return list(seen.values())

    def _apply_query_recall_ranking(
        self,
        items: list[ContextItem],
        query: str | None,
    ) -> list[ContextItem]:
        """
        Boost item relevance when query terms match title/content/tags.

        Query为空时保持原行为不变。
        """
        query_text = (query or "").strip()
        if not query_text:
            return items

        normalized_query = query_text.lower()
        query_terms = self._extract_query_terms(normalized_query)
        if not query_terms:
            return items
        query_matcher = AhoCorasickMatcher(query_terms)
        phrase_matcher = AhoCorasickMatcher([normalized_query]) if normalized_query else AhoCorasickMatcher([])
        if query_matcher.is_empty and phrase_matcher.is_empty:
            return items

        for item in items:
            boost = self._calculate_query_match_boost(
                item=item,
                query_matcher=query_matcher,
                phrase_matcher=phrase_matcher,
            )
            if boost <= 0:
                continue
            item.relevance_score = (item.relevance_score or 0.0) + boost

        return items

    def _extract_query_terms(self, query: str) -> list[str]:
        """Extract normalized query terms for match scoring."""
        matches = re.findall(r"[a-z0-9\u4e00-\u9fff]+", query.lower())

        # Keep stable order while removing duplicates
        seen: set[str] = set()
        terms: list[str] = []
        for term in matches:
            if term and term not in seen:
                seen.add(term)
                terms.append(term)
        return terms

    def _calculate_query_match_boost(
        self,
        item: ContextItem,
        query_matcher: AhoCorasickMatcher,
        phrase_matcher: AhoCorasickMatcher,
    ) -> float:
        """
        Calculate explainable boost from title/content/tag matches.

        Score design (capped to avoid over-amplification):
        - Phrase match in title/content/tags gives stronger boost
        - Token match gives incremental boost
        """
        title = (item.title or "").lower()
        content = (item.content or "").lower()
        tags = [tag.lower() for tag in self._extract_item_tags(item)]
        merged_tags = "\n".join(tags)

        title_term_hits = self._count_relevance_hits(title, query_matcher)
        content_term_hits = self._count_relevance_hits(content, query_matcher)
        tag_term_hits = self._count_relevance_hits(merged_tags, query_matcher)
        title_phrase_hits = self._count_relevance_hits(title, phrase_matcher)
        content_phrase_hits = self._count_relevance_hits(content, phrase_matcher)
        tag_phrase_hits = self._count_relevance_hits(merged_tags, phrase_matcher)

        score = 0.0

        # Phrase-level match
        if title_phrase_hits > 0:
            score += 0.45
        if content_phrase_hits > 0:
            score += 0.20
        if tag_phrase_hits > 0:
            score += 0.30

        # Term-level incremental match
        score += min(0.30, title_term_hits * 0.12)
        score += min(0.20, content_term_hits * 0.04)
        score += min(0.20, tag_term_hits * 0.10)

        return min(score, 0.90)

    def _count_relevance_hits(self, text: str, matcher: AhoCorasickMatcher) -> int:
        """
        Count unambiguous longest term hits for relevance gating.

        Uses Aho-Corasick for candidate matching, then applies:
        - ambiguity disable (same overlap group, same max length -> drop)
        - longest match selection in each overlap group
        """
        if not text or matcher.is_empty:
            return 0

        raw_matches = matcher.find_matches(text)
        if not raw_matches:
            return 0

        filtered = select_longest_non_ambiguous_matches(raw_matches)
        if not filtered:
            return 0

        return len({match.term for match in filtered})

    def _extract_item_tags(self, item: ContextItem) -> list[str]:
        """Collect searchable tag-like fields from item metadata."""
        tags: list[str] = []
        metadata = item.metadata or {}

        def append_value(value: Any) -> None:
            if value is None:
                return
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    tags.append(cleaned)
                return
            if isinstance(value, list | tuple | set):
                for entry in value:
                    append_value(entry)
                return
            cleaned = str(value).strip()
            if cleaned:
                tags.append(cleaned)

        # explicit tag fields + commonly used metadata labels
        for key in ("tags", "tag", "keywords", "category"):
            append_value(metadata.get(key))

        return tags

    def _bound_project_status(
        self,
        project_status: dict[str, Any] | None,
        max_tokens: int,
    ) -> dict[str, Any] | None:
        """把项目状态段裁剪到 max_tokens 以内。

        summary / notes 是用户可写入数千字符的自由文本（各 4000 字符上限），
        满值时仅这一段就能吃掉几千 token。它属于 header 的一部分，
        必须和文件清单一样受 max_tokens 约束，否则条目档的保底额度形同虚设。

        只裁剪 summary / notes 两个长字段；name / current_phase / writing_style
        都很短且是结构化信息，原样保留。
        """
        if not project_status:
            return project_status

        max_tokens = max(0, int(max_tokens or 0))
        if estimate_text_tokens(self._format_context([], None, project_status)) <= max_tokens:
            return project_status

        bounded = dict(project_status)
        long_fields = [
            field
            for field in ("summary", "notes")
            if isinstance(bounded.get(field), str) and bounded[field].strip()
        ]
        if not long_fields:
            return bounded

        # 先扣掉框架与短字段的固定开销，剩下的额度在长字段之间平分
        fixed_tokens = estimate_text_tokens(
            self._format_context(
                [],
                None,
                {**bounded, **dict.fromkeys(long_fields, "")},
            )
        )
        remaining = max(0, max_tokens - fixed_tokens)
        per_field = max(
            MIN_PROJECT_STATUS_FIELD_TOKENS, remaining // len(long_fields)
        )

        for field in long_fields:
            bounded[field] = truncate_text_to_tokens(bounded[field], per_field)

        return bounded

    @staticmethod
    def _inventory_env_int(name: str, default: int) -> int:
        """读取清单相关的整数型环境变量（非法值回落默认值）。"""
        raw = os.getenv(name)
        if raw is None:
            return default
        raw = raw.strip()
        if not raw:
            return default
        try:
            return max(0, int(raw))
        except ValueError:
            return default

    def _build_inventory_sections(
        self,
        file_inventory: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """把 inventory 各桶整理成待渲染的小节（已按条数上限截断）。

        分桶规则：
        - 大纲：outline
        - 正文：CONTENT_FILE_TYPES（draft + script）——短剧项目的分集正文是
          script，必须和 draft 一起进「正文」小节，否则清单会在
          「请勿重复创建已存在的文件」标题下输出「正文: (暂无)」这种肯定断言。
        - 角色 / 设定：character / lore
        - 其他文件：snippet、document 以及任何未预期的新类型，兜底展示，
          保证没有任何文件会从清单里凭空消失。
        """
        max_outline = self._inventory_env_int("AGENT_FILE_INVENTORY_MAX_OUTLINE", 80)
        max_draft = self._inventory_env_int("AGENT_FILE_INVENTORY_MAX_DRAFT", 80)
        max_script = self._inventory_env_int("AGENT_FILE_INVENTORY_MAX_SCRIPT", 80)
        max_character = self._inventory_env_int("AGENT_FILE_INVENTORY_MAX_CHARACTER", 40)
        max_lore = self._inventory_env_int("AGENT_FILE_INVENTORY_MAX_LORE", 40)
        max_other = self._inventory_env_int("AGENT_FILE_INVENTORY_MAX_OTHER", 40)

        def _entries(file_type: str) -> list[dict[str, Any]]:
            rows = file_inventory.get(file_type) or []
            return [{**row, "file_type": row.get("file_type") or file_type} for row in rows]

        content_limits = {"draft": max_draft, "script": max_script}
        content_entries: list[dict[str, Any]] = []
        content_total = 0
        for file_type in CONTENT_FILE_TYPES:
            rows = _entries(file_type)
            content_total += len(rows)
            limit = content_limits.get(file_type, max_draft)
            content_entries.extend(rows[:limit] if limit > 0 else [])

        other_entries: list[dict[str, Any]] = []
        other_total = 0
        for file_type in file_inventory:
            if file_type in CONTENT_FILE_TYPES or file_type in ("outline", "character", "lore"):
                continue
            typed = _entries(file_type)
            other_total += len(typed)
            other_entries.extend(typed)
        other_entries = other_entries[:max_other] if max_other > 0 else []

        outlines = _entries("outline")
        characters = _entries("character")
        lores = _entries("lore")

        return [
            {
                "key": "outline",
                "leftover_rank": 1,
                "label": "大纲",
                "layout": "list",
                "empty_text": "大纲: (暂无)",
                "total": len(outlines),
                "entries": outlines[:max_outline] if max_outline > 0 else [],
                "show_type": False,
            },
            {
                "key": "content",
                "leftover_rank": 0,
                "label": "正文",
                "layout": "list",
                "empty_text": "\n正文: (暂无)",
                "total": content_total,
                "entries": content_entries,
                # 同时存在 draft 与 script 时标出类型，方便模型选对 file_type
                "show_type": len({e["file_type"] for e in content_entries}) > 1,
            },
            {
                "key": "character",
                "leftover_rank": 2,
                "label": "角色",
                "layout": "inline",
                "empty_text": "",
                "total": len(characters),
                "entries": characters[:max_character] if max_character > 0 else [],
                "show_type": False,
            },
            {
                "key": "lore",
                "leftover_rank": 3,
                "label": "设定",
                "layout": "inline",
                "empty_text": "",
                "total": len(lores),
                "entries": lores[:max_lore] if max_lore > 0 else [],
                "show_type": False,
            },
            {
                "key": "other",
                "leftover_rank": 4,
                "label": "其他文件",
                "layout": "inline",
                "empty_text": "",
                "total": other_total,
                "entries": other_entries,
                "show_type": True,
            },
        ]

    @staticmethod
    def _render_inventory_entry(
        entry: dict[str, Any],
        *,
        layout: str,
        with_id: bool,
        show_type: bool,
    ) -> str:
        """渲染单条清单条目。"""
        meta: list[str] = []
        if with_id:
            meta.append(f"id={entry['id']}")
        if show_type:
            meta.append(f"类型={entry['file_type']}")
        if entry.get("word_count"):
            meta.append(f"{entry['word_count']}字")

        suffix = f" ({', '.join(meta)})" if meta else ""
        title = entry.get("title") or "(未命名)"
        return f"  - {title}{suffix}" if layout == "list" else f"{title}{suffix}"

    # 「…（中间省略 N 个）」这一行的粗略 token 成本
    _INVENTORY_ELLIPSIS_COST = 24

    @staticmethod
    def _inventory_head_count(count: int) -> int:
        """展示 count 条时，其中多少条取自开头（其余取自结尾）。"""
        return max(1, count // 4)

    @classmethod
    def _inventory_selection_cost(cls, costs: list[int], count: int) -> int:
        """展示 count 条时该小节条目行的 token 成本。"""
        total = len(costs)
        if count <= 0:
            return 0
        if count >= total:
            return sum(costs)
        head = cls._inventory_head_count(count)
        tail = count - head
        cost = sum(costs[:head]) + cls._INVENTORY_ELLIPSIS_COST
        if tail > 0:
            cost += sum(costs[total - tail:])
        return cost

    @classmethod
    def _pick_inventory_display_count(
        cls,
        costs: list[int],
        allowance: int | None,
    ) -> int:
        """在 token 额度内决定某小节能展示几条。

        展示策略是「头 + 尾」而不是单纯取前 N 条：头部让模型知道第 1 章确实存在，
        尾部（最新章节）才是它接下来要续写、最容易重复创建的部分。
        """
        total = len(costs)
        if allowance is None:
            return total
        if allowance <= 0 or total == 0:
            return 0

        for count in range(total, 0, -1):
            if cls._inventory_selection_cost(costs, count) <= allowance:
                return count

        return 0

    def _render_file_inventory(
        self,
        file_inventory: dict[str, list[dict[str, Any]]],
        token_budget: int | None = None,
    ) -> list[str]:
        """渲染「项目文件清单」段，整体受 token_budget 约束。

        为什么必须受约束：这段以前完全不受 max_tokens 限制，长篇项目下仅章节
        清单就能到 5000+ token，把条目预算压到 MIN_ITEM_TOKEN_BUDGET，
        用户手动附加的文件与手动选中的引用文本被静默丢弃。现在按剩余额度逐条
        填充，装不下就显式写明「已省略 N 个」，让模型知道清单不完整而不是
        误以为文件不存在。

        Args:
            file_inventory: _get_file_inventory 的产物
            token_budget: 本段允许占用的 token 上限，None 表示不限

        Returns:
            要拼进 parts 的文本行
        """
        separator = "=" * 60
        id_tail = self._inventory_env_int("AGENT_FILE_INVENTORY_ID_TAIL", 20)

        header = [
            separator,
            "项目文件清单 [请勿重复创建已存在的文件]",
            separator,
            "",
            "注：清单可能会被截断；当需要确认是否存在某文件时，请先调用 query_files 搜索。",
            "未标注 id 的条目需要 id 时，同样通过 query_files 获取。",
            "",
        ]

        sections = self._build_inventory_sections(file_inventory)

        # 预先算好每小节的行与成本；小节标题行属于固定开销（它承载「共 N 个」
        # 这个关键信号，再紧张也要保留），条目行才参与额度竞争。
        rendered: list[dict[str, Any]] = []
        fixed_cost = estimate_text_tokens("\n".join(header)) if token_budget is not None else 0

        for section in sections:
            entries = section["entries"]
            count = len(entries)
            lines = [
                self._render_inventory_entry(
                    entry,
                    layout=section["layout"],
                    with_id=(count - index) <= id_tail,
                    show_type=section["show_type"],
                )
                for index, entry in enumerate(entries)
            ]
            costs = (
                [estimate_text_tokens(line) + 1 for line in lines]
                if token_budget is not None
                else [0] * count
            )
            rendered.append({**section, "lines": lines, "costs": costs})

        remaining = None
        if token_budget is not None:
            remaining = max(0, token_budget - fixed_cost)

        # 两轮分配：先给每个非空小节公平份额，再把剩余额度按顺序补给还没展示完的
        # 小节。避免 80 条大纲把角色/设定挤没，也避免额度闲置。
        non_empty = [s for s in rendered if s["lines"]]
        allowances: dict[str, int | None] = {s["key"]: None for s in rendered}
        if remaining is not None and non_empty:
            fair = remaining // len(non_empty)
            spent = 0
            first_pass: dict[str, int] = {}
            for section in non_empty:
                allowances[section["key"]] = fair
                count = self._pick_inventory_display_count(section["costs"], fair)
                first_pass[section["key"]] = count
                spent += self._inventory_selection_cost(section["costs"], count)

            # 剩余额度按重要性顺序补给：正文最关键（决定会不会重复创建已存在的
            # 章节），其次是大纲，最后才是角色/设定/其他。
            leftover = max(0, remaining - spent)
            for section in sorted(non_empty, key=lambda s: s["leftover_rank"]):
                if leftover <= 0:
                    break
                key = section["key"]
                if first_pass[key] >= len(section["lines"]):
                    continue
                base = self._inventory_selection_cost(section["costs"], first_pass[key])
                # 只增不减：第二轮额度必须不低于第一轮，否则会把已展示的条目又砍掉
                allowance = max(fair, base + leftover)
                allowances[key] = allowance
                count = self._pick_inventory_display_count(section["costs"], allowance)
                leftover -= max(
                    0, self._inventory_selection_cost(section["costs"], count) - base
                )

        parts = list(header)
        for section in rendered:
            parts.extend(
                self._render_inventory_section_lines(
                    section, allowances.get(section["key"])
                )
            )

        parts.extend(self._render_chapter_gap_hints(file_inventory))
        parts.append("")
        return parts

    def _render_inventory_section_lines(
        self,
        section: dict[str, Any],
        allowance: int | None,
    ) -> list[str]:
        """按额度渲染单个清单小节（含「已省略 N 个」提示）。"""
        lines: list[str] = section["lines"]
        total = section["total"]

        if not lines:
            return [section["empty_text"]] if section["empty_text"] else []

        shown = self._pick_inventory_display_count(section["costs"], allowance)
        head = self._inventory_head_count(shown)
        if shown >= len(lines):
            display = lines
        elif shown <= 0:
            display = []
        else:
            tail = shown - head
            display = lines[:head] + (lines[-tail:] if tail > 0 else [])

        omitted = total - len(display)
        prefix = "\n" if section["empty_text"].startswith("\n") else ""

        if section["layout"] == "list":
            if omitted > 0:
                out = [f"{prefix}{section['label']} ({total} 个，展示 {len(display)} 个):"]
            else:
                out = [f"{prefix}{section['label']} ({total} 个):"]
            if not display:
                out.append(f"  ...（{total} 个全部省略；请调用 query_files 查询）")
                return out
            if omitted > 0 and len(display) > 1:
                out.extend(display[:head])
                out.append(f"  ...（中间省略 {omitted} 个；需要时请先调用 query_files 搜索）")
                out.extend(display[head:])
            else:
                out.extend(display)
                if omitted > 0:
                    out.append(f"  ...（已省略 {omitted} 个；需要时请先调用 query_files 搜索）")
            return out

        # inline layout：角色 / 设定 / 其他文件
        suffix = f" ...（省略 {omitted} 个，可用 query_files 查询）" if omitted > 0 else ""
        if not display:
            return [f"\n{section['label']} ({total} 个): （全部省略，可用 query_files 查询）"]
        return [f"\n{section['label']} ({total} 个): {', '.join(display)}{suffix}"]

    def _render_chapter_gap_hints(
        self,
        file_inventory: dict[str, list[dict[str, Any]]],
    ) -> list[str]:
        """大纲 vs 正文的章节缺口提醒。

        比对的正文集合是 draft ∪ script（CONTENT_FILE_TYPES）——只比 draft 时，
        短剧项目的提醒恒为空。

        This is important for batch generation flows where users request
        "生成 1-5 章" but earlier turns might have created/deleted files.
        The LLM can mistakenly assume chapter 1 exists and start from 2.
        We compute missing chapters *within the already-started range*
        (<= max existing content chapter) to avoid noisy warnings for future
        outlines that naturally have no body yet.
        """
        try:
            from utils.title_sequence import extract_sequence_number

            outlines = file_inventory.get("outline") or []
            contents: list[dict[str, Any]] = []
            for file_type in CONTENT_FILE_TYPES:
                contents.extend(file_inventory.get(file_type) or [])

            outline_nums = {
                n
                for f in outlines
                if (n := extract_sequence_number(f.get("title"))) is not None
            }
            content_nums = {
                n
                for f in contents
                if (n := extract_sequence_number(f.get("title"))) is not None
            }

            if outline_nums and content_nums:
                max_content_seq = max(content_nums)
                missing_nums = sorted(
                    n for n in outline_nums if n <= max_content_seq and n not in content_nums
                )
            else:
                missing_nums = []
        except Exception as exc:
            log_with_context(
                logger,
                10,  # DEBUG
                "Failed to compute outline/draft gap hints",
                error=str(exc),
            )
            missing_nums = []

        if not missing_nums:
            return []

        shown = "、".join(str(n) for n in missing_nums[:10])
        suffix = "..." if len(missing_nums) > 10 else ""
        hints = [f"\n⚠️ 章节一致性提醒：以下章节已有大纲但缺少正文：{shown}{suffix}"]
        if 1 in missing_nums:
            hints.append("⚠️ 注意：第1章正文缺失。批量生成正文时请优先补齐第1章，避免从第2章开始造成断档。")
        return hints

    def _format_context(
        self,
        items: list[ContextItem],
        file_inventory: dict[str, list[dict[str, Any]]] | None = None,
        project_status: dict[str, Any] | None = None,
        inventory_token_budget: int | None = None,
    ) -> str:
        """
        Format context items into a structured prompt with clear sections.

        Args:
            items: List of context items to format
            file_inventory: Optional file inventory for project awareness
            project_status: Optional project status for AI context
            inventory_token_budget: 文件清单段允许占用的 token 上限；
                None 表示不限（仅受各小节条数上限约束）

        Returns:
            Formatted context string
        """
        parts = []
        separator = "=" * 60

        # Project status section
        has_project_status = project_status and any([
            project_status.get("summary"),
            project_status.get("current_phase"),
            project_status.get("writing_style"),
            project_status.get("notes"),
        ])

        if has_project_status:
            # Type narrowing: project_status is not None here
            assert project_status is not None
            parts.append(separator)
            parts.append("项目状态 [核心信息]")
            parts.append(separator)
            parts.append("")

            if project_status.get("summary"):
                parts.append("【项目简介】")
                parts.append(project_status["summary"])
                parts.append("")

            if project_status.get("current_phase"):
                parts.append("【当前阶段】")
                parts.append(project_status["current_phase"])
                parts.append("")

            if project_status.get("writing_style"):
                parts.append("【写作风格】")
                parts.append(project_status["writing_style"])
                parts.append("")

            if project_status.get("notes"):
                parts.append("【备注】")
                parts.append(project_status["notes"])
                parts.append("")
        else:
            # Remind AI to collect project info when status is empty
            parts.append(separator)
            parts.append("项目状态 [待收集]")
            parts.append(separator)
            parts.append("")
            parts.append("⚠️ 项目信息尚未记录！当用户提到以下内容时，请立即调用 update_project 记录：")
            parts.append("- 小说类型/题材/背景 → summary")
            parts.append("- 写作风格/语言偏好 → writing_style")
            parts.append("- 特殊要求/注意事项 → notes")
            parts.append("")

        # File inventory section —— 受 inventory_token_budget 约束，见
        # _render_file_inventory 的说明
        if file_inventory:
            parts.extend(
                self._render_file_inventory(file_inventory, inventory_token_budget)
            )

        # Group items by type
        sections: dict[str, list[ContextItem]] = {
            "outline": [],
            "draft": [],
            "snippet": [],
            "character": [],
            "lore": [],
            "quote": [],
        }

        for item in items:
            item_type = item.type
            # 正文类文件（draft / script）都以 ContextItem.type == "outline" 承载，
            # 靠 metadata["file_type"] 区分。这里必须用 CONTENT_FILE_TYPES 判断，
            # 只写死 "draft" 会让短剧分集（script）被渲染进【大纲详情】。
            if item_type == "outline" and item.metadata.get("file_type") in CONTENT_FILE_TYPES:
                sections["draft"].append(item)
            elif item_type in sections:
                sections[item_type].append(item)

        # Sort items within each section by title for deterministic ordering (cache-friendly)
        for section_items in sections.values():
            section_items.sort(key=lambda x: x.title or "")

        # Only add detailed sections if we have items
        has_detailed_content = any([
            sections["outline"],
            sections["draft"],
            sections["snippet"],
            sections["character"],
            sections["lore"],
            sections["quote"],
        ])

        if has_detailed_content:
            parts.append(separator)
            parts.append("相关内容详情")
            parts.append(separator)
            parts.append("")

        # Outlines (detailed content)
        if sections["outline"]:
            parts.append("【大纲详情】")
            for item in sections["outline"]:
                relation = item.metadata.get("relation", "")
                prefix = f"[{relation}] " if relation else ""
                focus_mark = " ← 当前焦点" if item.is_focus else ""
                parts.append(f"{prefix}{item.title}{focus_mark}")
                parts.append(item.content)
                parts.append("")

        # Drafts (detailed content)
        if sections["draft"]:
            parts.append("【正文详情】")
            for item in sections["draft"]:
                relation = item.metadata.get("relation", "")
                prefix = f"[{relation}] " if relation else ""
                focus_mark = " ← 当前焦点" if item.is_focus else ""
                parts.append(f"{prefix}{item.title}{focus_mark}")
                parts.append(item.content)
                parts.append("")

        # Snippets
        if sections["snippet"]:
            parts.append("【参考素材】")
            for item in sections["snippet"]:
                source = item.metadata.get("source", "")
                line_start = item.metadata.get("line_start")
                fused_score = item.metadata.get("fused_score")
                sources = item.metadata.get("sources")

                meta_parts: list[str] = []
                if source:
                    meta_parts.append(str(source))
                if isinstance(line_start, int) and line_start > 0:
                    meta_parts.append(f"line_start={line_start}")
                if isinstance(fused_score, int | float):
                    meta_parts.append(f"fused_score={float(fused_score):.4f}")
                if isinstance(sources, list):
                    cleaned_sources = [str(s).strip() for s in sources if str(s).strip()]
                    if cleaned_sources:
                        meta_parts.append("sources=" + "+".join(cleaned_sources))

                prefix = f"[{' | '.join(meta_parts)}] " if meta_parts else ""
                # 参考素材区块同样可能承载焦点文件（snippet 类型），必须带焦点标记，
                # 否则模型无法从同区块的多条素材里分辨用户当前指的是哪一条。
                focus_mark = " ← 当前焦点" if item.is_focus else ""
                parts.append(f"{prefix}{item.title}{focus_mark}")
                parts.append(item.content)
                parts.append("")

        # Characters
        if sections["character"]:
            parts.append("【角色信息】")
            for item in sections["character"]:
                focus_mark = " ← 当前焦点" if item.is_focus else ""
                parts.append(f"{item.title}{focus_mark}")
                parts.append(item.content)
                parts.append("")

        # Lores
        if sections["lore"]:
            parts.append("【世界设定】")
            for item in sections["lore"]:
                category = item.metadata.get("category", "")
                prefix = f"[{category}] " if category else ""
                focus_mark = " ← 当前焦点" if item.is_focus else ""
                parts.append(f"{prefix}{item.title}{focus_mark}")
                parts.append(item.content)
                parts.append("")

        # Quotes (user-selected text)
        if sections["quote"]:
            parts.append("【用户引用文本】")
            parts.append("以下是用户选中并引用的文本片段，请特别关注：")
            for item in sections["quote"]:
                parts.append(f"{item.title}")
                parts.append(item.content)
                parts.append("")

        return "\n".join(parts)


# Singleton
_assembler: ContextAssembler | None = None


def get_context_assembler() -> ContextAssembler:
    """Get or create singleton context assembler."""
    global _assembler
    if _assembler is None:
        _assembler = ContextAssembler()
    return _assembler
