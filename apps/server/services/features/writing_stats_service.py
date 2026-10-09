"""
Writing Stats Service - Core service for writing statistics and streak tracking.

Provides methods for:
- Daily word count tracking per project
- Word count trend aggregation (daily/weekly/monthly)
- Chapter completion percentage calculation
- Writing streak tracking with recovery options
- AI usage metrics aggregation per project
"""
import json as json_module
import os
import re
from datetime import date, datetime, timedelta
from sqlite3 import Connection as SQLiteConnection
from typing import Any, cast

from sqlalchemy import and_, case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import QueryableAttribute, SessionTransaction, load_only
from sqlmodel import Session, col, select, update

from agent.constants import CONTENT_FILE_TYPES
from config.datetime_utils import utcnow
from models.entities import ChatMessage, ChatSession
from models.file_model import FILE_TYPE_OUTLINE, File, cached_word_count, stamp_word_count
from models.writing_stats import WritingStats, WritingStreak
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

# Configuration from environment
STREAK_GRACE_PERIOD_DAYS = int(os.getenv("STREAK_GRACE_PERIOD_DAYS", "1"))
STREAK_MIN_WORDS_FOR_DAY = int(os.getenv("STREAK_MIN_WORDS_FOR_DAY", "10"))
STREAK_FREEZE_MAX_DAYS = int(os.getenv("STREAK_FREEZE_MAX_DAYS", "3"))


def _get_non_negative_float_env(name: str, default: float = 0.0) -> float:
    """Parse a non-negative float from environment."""
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    try:
        parsed = float(raw_value)
    except ValueError:
        return default

    return parsed if parsed >= 0 else default


AI_USAGE_INPUT_COST_PER_1M_USD = _get_non_negative_float_env("AI_USAGE_INPUT_COST_PER_1M_USD", 0.0)
AI_USAGE_OUTPUT_COST_PER_1M_USD = _get_non_negative_float_env("AI_USAGE_OUTPUT_COST_PER_1M_USD", 0.0)
AI_USAGE_CACHE_READ_COST_PER_1M_USD = _get_non_negative_float_env("AI_USAGE_CACHE_READ_COST_PER_1M_USD", 0.0)
AI_USAGE_CACHE_WRITE_COST_PER_1M_USD = _get_non_negative_float_env("AI_USAGE_CACHE_WRITE_COST_PER_1M_USD", 0.0)


def _begin_stats_creation_savepoint(session: Session) -> SessionTransaction:
    """Keep first-row inserts inside the caller's real SQLite transaction."""
    if session.get_bind().dialect.name == "sqlite":
        connection = session.connection()
        driver = cast(SQLiteConnection, connection.connection.driver_connection)
        if not driver.in_transaction:
            connection.exec_driver_sql("BEGIN")
    return session.begin_nested()



def resolve_prose_word_counts(session: Session, files: list[File], **log_fields: Any) -> dict[str, int]:
    """Editor word count per file id: trust the current-revision cache, recompute the rest.

    ``files`` may be loaded with only ``id`` and ``file_metadata``. Files whose cache is
    missing or from an older counting revision are reloaded with content, stamped and
    committed once; a failed backfill commit is logged and the computed counts returned.
    """
    counts: dict[str, int] = {}
    stale_ids: list[str] = []
    for file in files:
        cached = cached_word_count(file.file_metadata)
        if cached is None:
            stale_ids.append(file.id)
        else:
            counts[file.id] = cached
    if not stale_ids:
        return counts
    for file in session.exec(select(File).where(col(File.id).in_(stale_ids))).all():
        counts[file.id] = stamp_word_count(file)
        session.add(file)
    try:
        session.commit()
    except Exception as exc:  # pragma: no cover - infra dependent
        session.rollback()
        log_with_context(
            logger,
            30,  # WARNING
            "Failed to backfill prose word_count metadata (continuing)",
            error=str(exc),
            error_type=type(exc).__name__,
            **log_fields,
        )
    return counts

class WritingStatsService:
    """Service for managing writing statistics and streaks."""

    _CHINESE_CHAPTER_NUMS = {
        "零": 0,
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
        "百": 100,
        "千": 1000,
    }

    def _normalize_title(self, title: str | None) -> str:
        """Normalize title for matching."""
        return (title or "").strip().lower()

    def _parse_chinese_number(self, value: str | None) -> int:
        """Parse a Chinese number string to integer."""
        if not value:
            return 0

        result = 0
        temp = 0
        for char in value:
            num = self._CHINESE_CHAPTER_NUMS.get(char)
            if num is None:
                continue
            if num in {10, 100, 1000}:
                if temp == 0:
                    temp = 1
                result += temp * num
                temp = 0
            else:
                temp = num
        result += temp
        return result if result > 0 else 0

    def _extract_chapter_number(self, title: str | None) -> int | None:
        """
        Extract chapter number from title.

        Supports:
        - 第一章 / 第二章 (Chinese numerals)
        - 第1章 / 第2章 (Arabic numerals)
        - 1xxx / 2xxx (plain leading numbers)
        """
        if not title:
            return None

        chinese_match = re.search(r"第([零一二三四五六七八九十百千]+)章", title)
        if chinese_match:
            parsed = self._parse_chinese_number(chinese_match.group(1))
            return parsed if parsed > 0 else None

        arabic_match = re.search(r"第(\d+)章", title)
        if arabic_match:
            return int(arabic_match.group(1))

        leading_num_match = re.match(r"^(\d+)", title.strip())
        if leading_num_match:
            return int(leading_num_match.group(1))

        return None

    def _extract_chapter_number_from_file(self, file: File) -> int | None:
        """
        Extract chapter number from metadata first, then title fallback.
        """
        metadata_value = file.get_metadata_field("chapter_number")
        if isinstance(metadata_value, int) and metadata_value > 0:
            return metadata_value
        if isinstance(metadata_value, str) and metadata_value.isdigit():
            return int(metadata_value)
        return self._extract_chapter_number(file.title)

    def _parse_positive_int(self, value: Any) -> int | None:
        """Parse positive integer from metadata-like value."""
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value > 0 else None
        if isinstance(value, float):
            parsed = int(value)
            return parsed if parsed > 0 else None
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                parsed = int(stripped)
                return parsed if parsed > 0 else None
        return None

    def _resolve_completion_target(
        self,
        outline: File | None,
        draft: File | None,
        min_words_for_complete: int,
    ) -> tuple[int | None, int]:
        """Resolve visible target and effective completion baseline."""
        target_word_count: int | None = None
        for file in (outline, draft):
            if not file:
                continue
            parsed_target = self._parse_positive_int(file.get_metadata_field("word_count_target"))
            if parsed_target:
                target_word_count = parsed_target
                break

        fallback_target = max(1, int(min_words_for_complete))
        return target_word_count, (target_word_count or fallback_target)

    def _evaluate_chapter_progress(
        self,
        word_count: int,
        completion_target: int,
    ) -> tuple[str, int]:
        """Evaluate chapter status and completion percentage."""
        if word_count <= 0:
            return "not_started", 0

        target = max(1, completion_target)
        completion_percentage = min(100, int((word_count / target) * 100))
        if word_count >= target:
            return "complete", completion_percentage
        return "in_progress", completion_percentage

    def get_or_create_daily_stats(
        self,
        session: Session,
        user_id: str,
        project_id: str,
        stats_date: date | None = None,
        *,
        commit: bool = True,
    ) -> WritingStats:
        """
        Get or create writing stats for a specific date.

        Args:
            session: Database session
            user_id: User ID
            project_id: Project ID
            stats_date: Date to get stats for (defaults to today)

        Returns:
            WritingStats for the specified date
        """
        if stats_date is None:
            stats_date = utcnow().date()

        # Try to find existing stats
        existing = session.exec(
            select(WritingStats).where(
                and_(
                    WritingStats.user_id == user_id,
                    WritingStats.project_id == project_id,
                    WritingStats.stats_date == stats_date,
                )
            )
        ).first()

        if existing:
            return existing

        # Create new stats record
        stats = WritingStats(
            user_id=user_id,
            project_id=project_id,
            stats_date=stats_date,
            word_count=0,
            words_added=0,
            words_deleted=0,
            edit_sessions=0,
            total_edit_time_seconds=0,
        )
        try:
            with _begin_stats_creation_savepoint(session):
                session.add(stats)
                session.flush()
        except IntegrityError:
            stats = session.exec(
                select(WritingStats).where(
                    and_(
                        WritingStats.user_id == user_id,
                        WritingStats.project_id == project_id,
                        WritingStats.stats_date == stats_date,
                    )
                )
            ).first()
            if not stats:
                raise

        if commit:
            session.commit()
            session.refresh(stats)
        return stats

    def record_word_count(
        self,
        session: Session,
        user_id: str,
        project_id: str,
        word_count: int,
        words_added: int = 0,
        words_deleted: int = 0,
        edit_time_seconds: int = 0,
        stats_date: date | None = None,
        *,
        commit: bool = True,
    ) -> WritingStats:
        """
        Record word count changes for a project on a specific date.

        Args:
            session: Database session
            user_id: User ID
            project_id: Project ID
            word_count: Total word count for the day
            words_added: Words added in this session
            words_deleted: Words deleted in this session
            edit_time_seconds: Time spent editing
            stats_date: Date to record for (defaults to today)

        Returns:
            Updated WritingStats record
        """
        stats = self.get_or_create_daily_stats(
            session=session,
            user_id=user_id,
            project_id=project_id,
            stats_date=stats_date,
            commit=commit,
        )

        # Add activity against persisted values so concurrent records retain every increment.
        session.exec(
            update(WritingStats)
            .where(col(WritingStats.id) == stats.id)
            .values(
                word_count=word_count,
                words_added=col(WritingStats.words_added) + words_added,
                words_deleted=col(WritingStats.words_deleted) + words_deleted,
                edit_sessions=col(WritingStats.edit_sessions) + 1,
                total_edit_time_seconds=col(WritingStats.total_edit_time_seconds) + edit_time_seconds,
                updated_at=utcnow(),
            )
            .execution_options(synchronize_session=False)
        )
        if commit:
            session.commit()
        else:
            session.flush()
        session.refresh(stats)

        log_with_context(
            logger,
            20,  # INFO
            "Word count recorded",
            user_id=user_id,
            project_id=project_id,
            word_count=word_count,
            words_added=words_added,
            words_deleted=words_deleted,
            stats_date=str(stats_date or utcnow().date()),
        )

        return stats

    def get_daily_word_count(
        self,
        session: Session,
        user_id: str,
        project_id: str,
        target_date: date,
    ) -> int:
        """
        Get word count for a specific date.

        Args:
            session: Database session
            user_id: User ID
            project_id: Project ID
            target_date: Date to get word count for

        Returns:
            Word count for the date (0 if no record exists)
        """
        stats = session.exec(
            select(WritingStats).where(
                and_(
                    WritingStats.user_id == user_id,
                    WritingStats.project_id == project_id,
                    WritingStats.stats_date == target_date,
                )
            )
        ).first()

        return stats.word_count if stats else 0

    def get_word_count_trend(
        self,
        session: Session,
        user_id: str,
        project_id: str,
        period: str = "daily",
        days: int = 7,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get word count trend over a period.

        Args:
            session: Database session
            user_id: User ID
            project_id: Project ID
            period: Aggregation period - "daily", "weekly", or "monthly"
            days: Number of days to look back
            end_date: Inclusive end date for the trend window (defaults to today UTC)

        Returns:
            List of dicts with date/period and word_count, words_added,
            words_deleted, net_words, edit_sessions
        """
        if end_date is None:
            end_date = utcnow().date()
        start_date = end_date - timedelta(days=days)

        # Get all stats in range
        stats_list = session.exec(
            select(WritingStats)
            .where(WritingStats.user_id == user_id)
            .where(WritingStats.project_id == project_id)
            .where(WritingStats.stats_date >= start_date)
            .where(WritingStats.stats_date <= end_date)
            .order_by(WritingStats.stats_date.asc())
        ).all()

        if period == "daily":
            # Return daily stats directly with net words calculation
            return [
                {
                    "date": str(stats.stats_date),
                    "word_count": stats.word_count,
                    "words_added": stats.words_added,
                    "words_deleted": stats.words_deleted,
                    "net_words": stats.words_added - stats.words_deleted,
                    "edit_sessions": stats.edit_sessions,
                }
                for stats in stats_list
            ]

        elif period == "weekly":
            # Aggregate by week (Monday to Sunday)
            weekly_data: dict[str, dict[str, Any]] = {}

            for stats in stats_list:
                # Get the start of the week (Monday)
                week_start = stats.stats_date - timedelta(days=stats.stats_date.weekday())
                week_key = str(week_start)

                if week_key not in weekly_data:
                    weekly_data[week_key] = {
                        "date": week_key,
                        "period_label": self._format_week_label(week_start),
                        "word_count": 0,
                        "words_added": 0,
                        "words_deleted": 0,
                        "edit_sessions": 0,
                        "days_with_activity": 0,
                        "latest_date": stats.stats_date,
                    }

                # For word_count, track the latest value in the week
                if stats.stats_date >= weekly_data[week_key]["latest_date"]:
                    weekly_data[week_key]["word_count"] = stats.word_count
                    weekly_data[week_key]["latest_date"] = stats.stats_date

                # Sum additive metrics
                weekly_data[week_key]["words_added"] += stats.words_added
                weekly_data[week_key]["words_deleted"] += stats.words_deleted
                weekly_data[week_key]["edit_sessions"] += stats.edit_sessions
                weekly_data[week_key]["days_with_activity"] += 1

            # Build result with calculated fields
            result = []
            for week_key in sorted(weekly_data.keys()):
                data = weekly_data[week_key]
                result.append({
                    "date": data["date"],
                    "period_label": data["period_label"],
                    "word_count": data["word_count"],
                    "words_added": data["words_added"],
                    "words_deleted": data["words_deleted"],
                    "net_words": data["words_added"] - data["words_deleted"],
                    "edit_sessions": data["edit_sessions"],
                    "days_with_activity": data["days_with_activity"],
                    "avg_words_per_day": (
                        data["words_added"] // data["days_with_activity"]
                        if data["days_with_activity"] > 0 else 0
                    ),
                })
            return result

        elif period == "monthly":
            # Aggregate by month
            monthly_data: dict[str, dict[str, Any]] = {}

            for stats in stats_list:
                # Get the start of the month
                month_key = stats.stats_date.strftime("%Y-%m-01")

                if month_key not in monthly_data:
                    monthly_data[month_key] = {
                        "date": month_key,
                        "period_label": stats.stats_date.strftime("%B %Y"),
                        "word_count": 0,
                        "words_added": 0,
                        "words_deleted": 0,
                        "edit_sessions": 0,
                        "days_with_activity": 0,
                        "latest_date": stats.stats_date,
                    }

                # For word_count, track the latest value in the month
                if stats.stats_date >= monthly_data[month_key]["latest_date"]:
                    monthly_data[month_key]["word_count"] = stats.word_count
                    monthly_data[month_key]["latest_date"] = stats.stats_date

                # Sum additive metrics
                monthly_data[month_key]["words_added"] += stats.words_added
                monthly_data[month_key]["words_deleted"] += stats.words_deleted
                monthly_data[month_key]["edit_sessions"] += stats.edit_sessions
                monthly_data[month_key]["days_with_activity"] += 1

            # Build result with calculated fields
            result = []
            for month_key in sorted(monthly_data.keys()):
                data = monthly_data[month_key]
                result.append({
                    "date": data["date"],
                    "period_label": data["period_label"],
                    "word_count": data["word_count"],
                    "words_added": data["words_added"],
                    "words_deleted": data["words_deleted"],
                    "net_words": data["words_added"] - data["words_deleted"],
                    "edit_sessions": data["edit_sessions"],
                    "days_with_activity": data["days_with_activity"],
                    "avg_words_per_day": (
                        data["words_added"] // data["days_with_activity"]
                        if data["days_with_activity"] > 0 else 0
                    ),
                })
            return result

        return []

    def _format_week_label(self, week_start: date) -> str:
        """
        Format a week start date as a human-readable label.

        Args:
            week_start: The Monday date starting the week

        Returns:
            Formatted string like "Feb 10-16" or "Feb 24 - Mar 2"
        """
        week_end = week_start + timedelta(days=6)

        if week_start.month == week_end.month:
            return f"{week_start.strftime('%b')} {week_start.day}-{week_end.day}"
        else:
            return f"{week_start.strftime('%b')} {week_start.day} - {week_end.strftime('%b')} {week_end.day}"

    def get_total_word_count(
        self,
        session: Session,
        user_id: str,
        project_id: str,
    ) -> int:
        """
        Get total word count across all time for a project.

        This calculates the current total word count from draft files,
        not the sum of daily records.

        Args:
            session: Database session
            user_id: User ID
            project_id: Project ID

        Returns:
            Total word count from all draft files
        """
        _ = user_id  # kept for interface parity with other stats methods

        # Fast path: sum cached word_count from file_metadata to avoid loading draft content.
        draft_files = session.exec(
            select(File)
            .options(load_only(File.id, File.file_metadata))
            .where(
                and_(
                    File.project_id == project_id,
                    col(File.file_type).in_(CONTENT_FILE_TYPES),
                    File.is_deleted == False,
                )
            )
        ).all()

        total = sum(resolve_prose_word_counts(session, list(draft_files), project_id=project_id).values())
        return total

    def get_words_written_in_period(
        self,
        session: Session,
        user_id: str,
        project_id: str,
        start_date: date,
        end_date: date,
    ) -> dict[str, int]:
        """
        Get words written in a specific period.

        Args:
            session: Database session
            user_id: User ID
            project_id: Project ID
            start_date: Start date (inclusive)
            end_date: End date (inclusive)

        Returns:
            Dict with words_added, words_deleted, net_words, edit_sessions
        """
        result = session.exec(
            select(
                func.sum(WritingStats.words_added).label("total_added"),
                func.sum(WritingStats.words_deleted).label("total_deleted"),
                func.sum(WritingStats.edit_sessions).label("total_sessions"),
            ).where(
                WritingStats.user_id == user_id,
                WritingStats.project_id == project_id,
                WritingStats.stats_date >= start_date,
                WritingStats.stats_date <= end_date,
            )
        ).first()

        words_added = result.total_added or 0
        words_deleted = result.total_deleted or 0

        return {
            "words_added": words_added,
            "words_deleted": words_deleted,
            "net_words": words_added - words_deleted,
            "edit_sessions": result.total_sessions or 0,
        }

    def get_chapter_completion_stats(
        self,
        session: Session,
        project_id: str,
        min_words_for_complete: int = 50,
    ) -> dict[str, Any]:
        """
        Calculate chapter completion percentage from draft files.

        Compares the number of outline files (planned chapters) against
        draft files with meaningful content to calculate completion.

        Args:
            session: Database session
            project_id: Project ID
            min_words_for_complete: Minimum word count for a draft to be
                considered "complete". Default 50 words.

        Returns:
            Dict with completion statistics:
            - total_chapters: Total number of planned chapters (outlines)
            - completed_chapters: Number of drafts with sufficient content
            - in_progress_chapters: Drafts with some content but not complete
            - completion_percentage: Overall completion percentage
            - chapter_details: List of individual chapter statuses
        """
        # Get all outline files (planned chapters)
        outline_files = session.exec(
            select(File)
            .options(
                load_only(
                    File.id,
                    File.title,
                    File.order,
                    File.file_metadata,
                )
            )
            .where(
                and_(
                    File.project_id == project_id,
                    File.file_type == FILE_TYPE_OUTLINE,
                    File.is_deleted == False,
                )
            )
            .order_by(File.order.asc())
        ).all()

        # Get all draft files
        draft_files = session.exec(
            select(File)
            .options(
                load_only(
                    File.id,
                    File.title,
                    File.order,
                    File.file_metadata,
                )
            )
            .where(
                and_(
                    File.project_id == project_id,
                    col(File.file_type).in_(CONTENT_FILE_TYPES),
                    File.is_deleted == False,
                )
            )
            .order_by(File.order.asc())
        ).all()

        # Build draft indexes for robust matching with outlines.
        # Matching priority:
        # 1) Explicit metadata link (outline_id)
        # 2) Exact title match
        # 3) Chapter number match (metadata/title)
        # 4) Same order index
        # 5) Loose title contains fallback
        drafts_by_id: dict[str, File] = {draft.id: draft for draft in draft_files}
        used_draft_ids: set[str] = set()

        drafts_by_outline_ref: dict[str, list[str]] = {}
        drafts_by_title: dict[str, list[str]] = {}
        drafts_by_chapter_number: dict[int, list[str]] = {}
        drafts_by_order: dict[int, list[str]] = {}

        for draft in draft_files:
            metadata = draft.get_metadata()
            outline_ref = metadata.get("outline_id")
            if outline_ref is None:
                outline_ref = metadata.get("outlineId")
            if outline_ref is not None:
                key = str(outline_ref)
                drafts_by_outline_ref.setdefault(key, []).append(draft.id)

            normalized_draft_title = self._normalize_title(draft.title)
            if normalized_draft_title:
                drafts_by_title.setdefault(normalized_draft_title, []).append(draft.id)

            chapter_number = self._extract_chapter_number_from_file(draft)
            if chapter_number is not None:
                drafts_by_chapter_number.setdefault(chapter_number, []).append(draft.id)

            drafts_by_order.setdefault(draft.order, []).append(draft.id)

        def pick_first_unused(draft_ids: list[str] | None) -> File | None:
            if not draft_ids:
                return None
            for draft_id in draft_ids:
                if draft_id not in used_draft_ids and draft_id in drafts_by_id:
                    used_draft_ids.add(draft_id)
                    return drafts_by_id[draft_id]
            return None

        total_chapters = len(outline_files)
        completed_chapters = 0
        in_progress_chapters = 0
        chapter_details: list[dict[str, Any]] = []

        outline_draft_pairs: list[tuple[File, File | None]] = []

        for outline in outline_files:
            outline_title_lower = self._normalize_title(outline.title)
            outline_chapter_number = self._extract_chapter_number_from_file(outline)

            # 1) Explicit metadata link
            draft = pick_first_unused(drafts_by_outline_ref.get(str(outline.id)))

            # 2) Exact normalized title match
            if not draft:
                draft = pick_first_unused(drafts_by_title.get(outline_title_lower))

            # 3) Chapter number match
            if not draft and outline_chapter_number is not None:
                draft = pick_first_unused(drafts_by_chapter_number.get(outline_chapter_number))

            # 4) Same order fallback
            if not draft:
                draft = pick_first_unused(drafts_by_order.get(outline.order))

            # 5) Loose contains fallback over remaining drafts
            if not draft and outline_title_lower:
                for candidate_id, candidate in drafts_by_id.items():
                    if candidate_id in used_draft_ids:
                        continue
                    candidate_title = self._normalize_title(candidate.title)
                    if not candidate_title:
                        continue
                    if outline_title_lower in candidate_title or candidate_title in outline_title_lower:
                        used_draft_ids.add(candidate_id)
                        draft = candidate
                        break

            outline_draft_pairs.append((outline, draft))

        # Resolve word_count for matched drafts.
        draft_word_counts = resolve_prose_word_counts(
            session, [draft for _outline, draft in outline_draft_pairs if draft], project_id=project_id
        )

        for outline, draft in outline_draft_pairs:
            if draft:
                word_count = draft_word_counts.get(draft.id, 0)
                target_word_count, completion_target = self._resolve_completion_target(
                    outline=outline,
                    draft=draft,
                    min_words_for_complete=min_words_for_complete,
                )
                status, chapter_completion_percentage = self._evaluate_chapter_progress(
                    word_count=word_count,
                    completion_target=completion_target,
                )

                if status == "complete":
                    completed_chapters += 1
                elif status == "in_progress":
                    in_progress_chapters += 1

                chapter_details.append({
                    "outline_id": outline.id,
                    "draft_id": draft.id,
                    "title": outline.title,
                    "word_count": word_count,
                    "target_word_count": target_word_count,
                    "status": status,
                    "completion_percentage": chapter_completion_percentage,
                })
            else:
                # No matching draft found
                target_word_count, _ = self._resolve_completion_target(
                    outline=outline,
                    draft=None,
                    min_words_for_complete=min_words_for_complete,
                )
                chapter_details.append({
                    "outline_id": outline.id,
                    "draft_id": None,
                    "title": outline.title,
                    "word_count": 0,
                    "target_word_count": target_word_count,
                    "status": "not_started",
                    "completion_percentage": 0,
                })

        # Calculate completion percentage
        if total_chapters > 0:
            completion_percentage = int((completed_chapters / total_chapters) * 100)
        else:
            # If no outlines, use drafts as reference
            total_chapters = len(draft_files)
            completed_chapters = 0
            in_progress_chapters = 0
            chapter_details = []

            draft_word_counts = resolve_prose_word_counts(session, list(draft_files), project_id=project_id)

            for draft in draft_files:
                word_count = draft_word_counts.get(draft.id, 0)
                target_word_count, completion_target = self._resolve_completion_target(
                    outline=None,
                    draft=draft,
                    min_words_for_complete=min_words_for_complete,
                )
                status, chapter_completion_percentage = self._evaluate_chapter_progress(
                    word_count=word_count,
                    completion_target=completion_target,
                )
                if status == "complete":
                    completed_chapters += 1
                elif status == "in_progress":
                    in_progress_chapters += 1

                chapter_details.append({
                    "outline_id": draft.id,
                    "draft_id": draft.id,
                    "title": draft.title,
                    "word_count": word_count,
                    "target_word_count": target_word_count,
                    "status": status,
                    "completion_percentage": chapter_completion_percentage,
                })

            completion_percentage = int((completed_chapters / total_chapters) * 100) if total_chapters > 0 else 0

        log_with_context(
            logger,
            20,  # INFO
            "Chapter completion stats calculated",
            project_id=project_id,
            total_chapters=total_chapters,
            completed_chapters=completed_chapters,
            completion_percentage=completion_percentage,
        )

        return {
            "total_chapters": total_chapters,
            "completed_chapters": completed_chapters,
            "in_progress_chapters": in_progress_chapters,
            "not_started_chapters": total_chapters - completed_chapters - in_progress_chapters,
            "completion_percentage": completion_percentage,
            "chapter_details": chapter_details,
        }

    # ==========================================
    # Writing Streak Tracking Methods
    # ==========================================

    def get_or_create_streak(
        self,
        session: Session,
        user_id: str,
        project_id: str,
        *,
        commit: bool = True,
    ) -> WritingStreak:
        """
        Get or create a writing streak record for a user/project.

        Args:
            session: Database session
            user_id: User ID
            project_id: Project ID

        Returns:
            WritingStreak record for the user/project
        """
        existing = session.exec(
            select(WritingStreak).where(
                and_(
                    WritingStreak.user_id == user_id,
                    WritingStreak.project_id == project_id,
                )
            )
        ).first()

        if existing:
            return existing

        # Create new streak record
        streak = WritingStreak(
            user_id=user_id,
            project_id=project_id,
            current_streak=0,
            longest_streak=0,
            last_writing_date=None,
            streak_start_date=None,
            streak_recovery_count=0,
        )
        try:
            with _begin_stats_creation_savepoint(session):
                session.add(streak)
                session.flush()
        except IntegrityError:
            streak = session.exec(
                select(WritingStreak).where(
                    and_(
                        WritingStreak.user_id == user_id,
                        WritingStreak.project_id == project_id,
                    )
                )
            ).first()
            if not streak:
                raise

        if commit:
            session.commit()
            session.refresh(streak)
        return streak

    def update_streak(
        self,
        session: Session,
        user_id: str,
        project_id: str,
        words_written: int = 0,
        stats_date: date | None = None,
        *,
        commit: bool = True,
    ) -> WritingStreak:
        """
        Update writing streak when user writes.

        Implements streak logic with recovery options:
        - Consecutive days increase streak
        - Grace period allows missing 1 day without breaking streak
        - Recovery count tracks how many times streak was saved

        Args:
            session: Database session
            user_id: User ID
            project_id: Project ID
            words_written: Words written today (for threshold check)
            stats_date: Date to record (defaults to today)

        Returns:
            Updated WritingStreak record
        """
        if stats_date is None:
            stats_date = utcnow().date()

        streak = self.get_or_create_streak(session, user_id, project_id, commit=commit)

        # Check if minimum words threshold is met
        if words_written > 0 and words_written < STREAK_MIN_WORDS_FOR_DAY:
            # Not enough words to count as a writing day
            log_with_context(
                logger,
                20,  # INFO
                "Streak not updated - below word threshold",
                user_id=user_id,
                project_id=project_id,
                words_written=words_written,
                min_required=STREAK_MIN_WORDS_FOR_DAY,
            )
            return streak

        # Historical daily records must not rewind or inflate the current streak.
        if streak.last_writing_date is not None and stats_date <= streak.last_writing_date:
            return streak

        previous_date = streak.last_writing_date
        streak_broken = False
        streak_recovered = False

        if previous_date is None:
            # First time writing - start new streak
            streak.current_streak = 1
            streak.streak_start_date = stats_date
        else:
            days_since_last = (stats_date - previous_date).days

            if days_since_last == 1:
                # Consecutive day - increment streak
                streak.current_streak += 1
            elif days_since_last <= STREAK_GRACE_PERIOD_DAYS + 1:
                # Within grace period - streak continues but mark as recovered
                streak.current_streak += 1
                streak.streak_recovery_count += 1
                streak_recovered = True
            else:
                # Streak broken - start new streak
                streak_broken = True
                streak.current_streak = 1
                streak.streak_start_date = stats_date

        # Update longest streak if current exceeds it
        if streak.current_streak > streak.longest_streak:
            streak.longest_streak = streak.current_streak

        # Update last writing date
        streak.last_writing_date = stats_date
        streak.updated_at = utcnow()

        session.add(streak)
        if commit:
            session.commit()
        else:
            session.flush()
        session.refresh(streak)

        log_with_context(
            logger,
            20,  # INFO
            "Streak updated",
            user_id=user_id,
            project_id=project_id,
            current_streak=streak.current_streak,
            longest_streak=streak.longest_streak,
            streak_broken=streak_broken,
            streak_recovered=streak_recovered,
            stats_date=str(stats_date),
        )

        return streak

    def get_streak(
        self,
        session: Session,
        user_id: str,
        project_id: str,
        reference_date: date | None = None,
    ) -> dict[str, Any]:
        """
        Get current streak status with detailed information.

        Args:
            session: Database session
            user_id: User ID
            project_id: Project ID
            reference_date: Optional client-local date used as "today"

        Returns:
            Dict with streak information:
            - current_streak: Current consecutive days
            - longest_streak: All-time longest streak
            - streak_status: "active", "at_risk", "broken", or "none"
            - days_until_break: Days until streak breaks (if at_risk)
            - last_writing_date: Date of last writing
            - streak_start_date: When current streak started
            - streak_recovery_count: Times streak was saved by grace period
            - can_recover: Whether streak can still be recovered
        """
        streak = self.get_or_create_streak(session, user_id, project_id)
        today = reference_date or utcnow().date()

        # Determine streak status
        streak_status = "none"
        days_until_break = 0
        can_recover = False

        if streak.last_writing_date is None:
            streak_status = "none"
        elif streak.last_writing_date == today:
            streak_status = "active"
        elif streak.last_writing_date > today:
            # Defensive handling for client-local dates ahead of UTC reference date.
            # Treat as active instead of incorrectly flagging at-risk.
            streak_status = "active"
        else:
            days_since_last = (today - streak.last_writing_date).days

            if days_since_last == 1:
                # Yesterday - at risk but can still save
                streak_status = "at_risk"
                days_until_break = STREAK_GRACE_PERIOD_DAYS
                can_recover = True
            elif days_since_last <= STREAK_GRACE_PERIOD_DAYS + 1:
                # Within grace period - can still recover
                streak_status = "at_risk"
                days_until_break = (STREAK_GRACE_PERIOD_DAYS + 1) - days_since_last
                can_recover = True
            else:
                # Streak broken
                streak_status = "broken"

        return {
            "current_streak": streak.current_streak,
            "longest_streak": streak.longest_streak,
            "streak_status": streak_status,
            "days_until_break": days_until_break,
            "last_writing_date": str(streak.last_writing_date) if streak.last_writing_date else None,
            "streak_start_date": str(streak.streak_start_date) if streak.streak_start_date else None,
            "streak_recovery_count": streak.streak_recovery_count,
            "can_recover": can_recover,
            "grace_period_days": STREAK_GRACE_PERIOD_DAYS,
            "min_words_for_day": STREAK_MIN_WORDS_FOR_DAY,
        }

    def freeze_streak(
        self,
        session: Session,
        user_id: str,
        project_id: str,
        freeze_days: int = 1,
    ) -> WritingStreak:
        """
        Freeze a streak to prevent it from breaking.

        Uses streak freeze to protect streak during planned breaks.
        Limited by STREAK_FREEZE_MAX_DAYS configuration.

        Args:
            session: Database session
            user_id: User ID
            project_id: Project ID
            freeze_days: Number of days to freeze (max STREAK_FREEZE_MAX_DAYS)

        Returns:
            Updated WritingStreak record

        Raises:
            ValueError: If freeze_days exceeds maximum
        """
        if freeze_days > STREAK_FREEZE_MAX_DAYS:
            raise ValueError(
                f"Cannot freeze streak for more than {STREAK_FREEZE_MAX_DAYS} days"
            )

        streak = self.get_or_create_streak(session, user_id, project_id)

        if streak.last_writing_date is None:
            raise ValueError("Cannot freeze a streak that hasn't started")

        # Extend last_writing_date by freeze_days
        streak.last_writing_date = streak.last_writing_date + timedelta(days=freeze_days)
        streak.streak_recovery_count += 1
        streak.updated_at = utcnow()

        session.add(streak)
        session.commit()
        session.refresh(streak)

        log_with_context(
            logger,
            20,  # INFO
            "Streak frozen",
            user_id=user_id,
            project_id=project_id,
            freeze_days=freeze_days,
            new_last_writing_date=str(streak.last_writing_date),
        )

        return streak

    def reset_streak(
        self,
        session: Session,
        user_id: str,
        project_id: str,
    ) -> WritingStreak:
        """
        Reset a streak to zero.

        Used when user wants to start fresh or when manually resetting.

        Args:
            session: Database session
            user_id: User ID
            project_id: Project ID

        Returns:
            Reset WritingStreak record
        """
        streak = self.get_or_create_streak(session, user_id, project_id)

        streak.current_streak = 0
        streak.last_writing_date = None
        streak.streak_start_date = None
        streak.updated_at = utcnow()

        session.add(streak)
        session.commit()
        session.refresh(streak)

        log_with_context(
            logger,
            20,  # INFO
            "Streak reset",
            user_id=user_id,
            project_id=project_id,
        )

        return streak

    def get_streak_history(
        self,
        session: Session,
        user_id: str,
        project_id: str,
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """
        Get streak history showing daily writing activity.

        Args:
            session: Database session
            user_id: User ID
            project_id: Project ID
            days: Number of days to look back

        Returns:
            List of dicts with date, wrote, word_count, streak_count
        """
        end_date = utcnow().date()
        start_date = end_date - timedelta(days=days)

        # Get all stats in range
        stats_list = session.exec(
            select(WritingStats)
            .where(WritingStats.user_id == user_id)
            .where(WritingStats.project_id == project_id)
            .where(WritingStats.stats_date >= start_date)
            .where(WritingStats.stats_date <= end_date)
            .order_by(WritingStats.stats_date.asc())
        ).all()

        # Build a map for quick lookup
        stats_map = {stats.stats_date: stats for stats in stats_list}

        # Get current streak info
        streak = self.get_or_create_streak(session, user_id, project_id)

        history = []
        streak_count = 0

        for i in range(days):
            current_date = start_date + timedelta(days=i)
            stats = stats_map.get(current_date)
            activity_words = (stats.words_added + stats.words_deleted) if stats else 0
            wrote = activity_words >= STREAK_MIN_WORDS_FOR_DAY

            # Calculate running streak count
            if wrote:
                streak_count += 1
            else:
                # Check if within grace period of current streak
                if streak.last_writing_date:
                    days_diff = (current_date - streak.last_writing_date).days
                    if days_diff > STREAK_GRACE_PERIOD_DAYS + 1:
                        streak_count = 0
                else:
                    streak_count = 0

            history.append({
                "date": str(current_date),
                "wrote": wrote,
                "word_count": activity_words,
                "streak_count": streak_count if wrote else 0,
            })

        return history

    def _coerce_non_negative_int(self, value: Any) -> int:
        """Convert value to non-negative integer."""
        if isinstance(value, bool):
            return 0

        if isinstance(value, int):
            return max(0, value)

        if isinstance(value, float):
            return max(0, int(value))

        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return 0
            with_value = stripped.replace(",", "")
            if with_value.isdigit():
                return max(0, int(with_value))
            try:
                return max(0, int(float(with_value)))
            except ValueError:
                return 0

        return 0

    def _empty_token_metrics(self) -> dict[str, Any]:
        """Create an empty token/cost metrics payload."""
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 0,
            "estimated_tokens": 0,
            "estimated_cost_usd": 0.0,
        }

    def _parse_usage_from_metadata(self, raw_metadata: str | None) -> tuple[dict[str, int], bool]:
        """
        Parse token usage payload from ChatMessage.message_metadata.

        Returns:
            Tuple of (usage_tokens, has_usage_payload).
        """
        usage_tokens = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 0,
        }

        if not raw_metadata:
            return usage_tokens, False

        try:
            metadata = json_module.loads(raw_metadata)
        except (TypeError, ValueError):
            return usage_tokens, False

        if not isinstance(metadata, dict):
            return usage_tokens, False

        usage_payload = metadata.get("usage")
        if not isinstance(usage_payload, dict):
            return usage_tokens, False

        input_tokens = self._coerce_non_negative_int(
            usage_payload.get("input_tokens", usage_payload.get("prompt_tokens", 0))
        )
        output_tokens = self._coerce_non_negative_int(
            usage_payload.get("output_tokens", usage_payload.get("completion_tokens", 0))
        )
        cache_read_tokens = self._coerce_non_negative_int(usage_payload.get("cache_read_tokens", 0))
        cache_write_tokens = self._coerce_non_negative_int(usage_payload.get("cache_write_tokens", 0))
        total_tokens = self._coerce_non_negative_int(usage_payload.get("total_tokens", 0))

        if total_tokens <= 0:
            total_tokens = input_tokens + output_tokens + cache_read_tokens + cache_write_tokens

        usage_tokens.update({
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "total_tokens": total_tokens,
        })

        return usage_tokens, True

    def _estimate_usage_cost_usd(self, usage_tokens: dict[str, int]) -> float:
        """Estimate LLM usage cost in USD based on configured per-1M token pricing."""
        weighted_total = (
            usage_tokens["input_tokens"] * AI_USAGE_INPUT_COST_PER_1M_USD
            + usage_tokens["output_tokens"] * AI_USAGE_OUTPUT_COST_PER_1M_USD
            + usage_tokens["cache_read_tokens"] * AI_USAGE_CACHE_READ_COST_PER_1M_USD
            + usage_tokens["cache_write_tokens"] * AI_USAGE_CACHE_WRITE_COST_PER_1M_USD
        )
        return weighted_total / 1_000_000

    def _get_assistant_message_token_metrics(self, message: ChatMessage) -> dict[str, Any]:
        """Get token/cost metrics for an assistant message with legacy fallback."""
        usage_tokens, has_usage_payload = self._parse_usage_from_metadata(message.message_metadata)

        if not has_usage_payload:
            # Backward compatibility for older records without usage metadata.
            usage_tokens["total_tokens"] = len(message.content or "") // 4

        return {
            "input_tokens": usage_tokens["input_tokens"],
            "output_tokens": usage_tokens["output_tokens"],
            "cache_read_tokens": usage_tokens["cache_read_tokens"],
            "cache_write_tokens": usage_tokens["cache_write_tokens"],
            "total_tokens": usage_tokens["total_tokens"],
            "estimated_tokens": usage_tokens["total_tokens"],
            "estimated_cost_usd": self._estimate_usage_cost_usd(usage_tokens) if has_usage_payload else 0.0,
        }

    def _aggregate_assistant_token_metrics(self, assistant_messages: list[ChatMessage]) -> dict[str, Any]:
        """Aggregate token/cost metrics from assistant messages."""
        totals = self._empty_token_metrics()
        for message in assistant_messages:
            usage = self._get_assistant_message_token_metrics(message)
            totals["input_tokens"] += int(usage["input_tokens"])
            totals["output_tokens"] += int(usage["output_tokens"])
            totals["cache_read_tokens"] += int(usage["cache_read_tokens"])
            totals["cache_write_tokens"] += int(usage["cache_write_tokens"])
            totals["total_tokens"] += int(usage["total_tokens"])
            totals["estimated_tokens"] += int(usage["estimated_tokens"])
            totals["estimated_cost_usd"] += float(usage["estimated_cost_usd"])

        totals["estimated_cost_usd"] = round(float(totals["estimated_cost_usd"]), 6)
        return totals

    def _append_assistant_usage_to_bucket(self, bucket: dict[str, Any], message: ChatMessage) -> None:
        """Append assistant token usage to trend bucket."""
        if message.role != "assistant":
            return

        usage = self._get_assistant_message_token_metrics(message)
        bucket["input_tokens"] += int(usage["input_tokens"])
        bucket["output_tokens"] += int(usage["output_tokens"])
        bucket["cache_read_tokens"] += int(usage["cache_read_tokens"])
        bucket["cache_write_tokens"] += int(usage["cache_write_tokens"])
        bucket["total_tokens"] += int(usage["total_tokens"])
        bucket["estimated_tokens"] += int(usage["estimated_tokens"])
        bucket["estimated_cost_usd"] += float(usage["estimated_cost_usd"])

    def _finalize_usage_bucket(self, bucket: dict[str, Any]) -> None:
        """Finalize rounding/derived fields for usage bucket."""
        bucket["estimated_tokens"] = int(bucket.get("total_tokens", 0))
        bucket["estimated_cost_usd"] = round(float(bucket.get("estimated_cost_usd", 0.0)), 6)

    # ==========================================
    # AI Usage Metrics Methods
    # ==========================================

    def get_ai_usage_stats(
        self,
        session: Session,
        user_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        """
        Get AI usage statistics for a project.

        Aggregates chat sessions and messages to show AI interaction metrics.

        Args:
            session: Database session
            user_id: User ID
            project_id: Project ID

        Returns:
            Dict with AI usage statistics:
            - total_sessions: Total number of chat sessions
            - active_session_id: ID of currently active session (if any)
            - total_messages: Total messages across all sessions
            - user_messages: Messages sent by user
            - ai_messages: AI assistant responses
            - tool_messages: Tool call results
            - input_tokens/output_tokens/cache_*_tokens: Real token usage from model metadata
            - total_tokens: Total token consumption (real usage, fallback to legacy estimate)
            - estimated_tokens: Backward-compatible alias of total_tokens
            - estimated_cost_usd: Estimated LLM usage cost (configurable per-1M token rates)
            - first_interaction_date: Date of first AI interaction
            - last_interaction_date: Date of most recent interaction
        """
        current_stats, _, _ = self._load_ai_usage_stats(session, user_id, project_id)
        return current_stats

    def _load_ai_usage_stats(
        self,
        session: Session,
        user_id: str,
        project_id: str,
    ) -> tuple[dict[str, Any], list[str], list[ChatMessage]]:
        """Load current metrics and scoped assistants for reuse across summary periods."""
        # Get all chat sessions for this project
        chat_sessions = session.exec(
            select(ChatSession).where(
                and_(
                    ChatSession.user_id == user_id,
                    ChatSession.project_id == project_id,
                )
            )
        ).all()

        if not chat_sessions:
            return {
                "total_sessions": 0,
                "active_session_id": None,
                "total_messages": 0,
                "user_messages": 0,
                "ai_messages": 0,
                "tool_messages": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "total_tokens": 0,
                "estimated_tokens": 0,
                "estimated_cost_usd": 0.0,
                "first_interaction_date": None,
                "last_interaction_date": None,
            }, [], []

        session_ids = [s.id for s in chat_sessions]
        active_session = next((s for s in chat_sessions if s.is_active), None)

        # Aggregate message metrics in SQL to avoid loading full message bodies.
        aggregated = session.exec(
            select(
                func.count(ChatMessage.id).label("total_messages"),
                func.sum(case((ChatMessage.role == "user", 1), else_=0)).label("user_messages"),
                func.sum(case((ChatMessage.role == "assistant", 1), else_=0)).label("ai_messages"),
                func.sum(case((ChatMessage.role == "tool", 1), else_=0)).label("tool_messages"),
                func.min(ChatMessage.created_at).label("first_date"),
                func.max(ChatMessage.created_at).label("last_date"),
            ).where(ChatMessage.session_id.in_(session_ids))
        ).one()

        total_messages = int(aggregated.total_messages or 0)
        user_messages = int(aggregated.user_messages or 0)
        ai_messages = int(aggregated.ai_messages or 0)
        tool_messages = int(aggregated.tool_messages or 0)
        first_date = aggregated.first_date
        last_date = aggregated.last_date
        assistant_messages = list(session.exec(
            select(ChatMessage)
            .options(load_only(
                cast(QueryableAttribute[str | None], ChatMessage.message_metadata),
                cast(QueryableAttribute[str], ChatMessage.content),
                cast(QueryableAttribute[datetime], ChatMessage.created_at),
            ))
            .where(ChatMessage.session_id.in_(session_ids))
            .where(ChatMessage.role == "assistant")
        ).all())
        token_metrics = self._aggregate_assistant_token_metrics(assistant_messages)

        return {
            "total_sessions": len(chat_sessions),
            "active_session_id": active_session.id if active_session else None,
            "total_messages": total_messages,
            "user_messages": user_messages,
            "ai_messages": ai_messages,
            "tool_messages": tool_messages,
            "input_tokens": token_metrics["input_tokens"],
            "output_tokens": token_metrics["output_tokens"],
            "cache_read_tokens": token_metrics["cache_read_tokens"],
            "cache_write_tokens": token_metrics["cache_write_tokens"],
            "total_tokens": token_metrics["total_tokens"],
            "estimated_tokens": token_metrics["estimated_tokens"],
            "estimated_cost_usd": token_metrics["estimated_cost_usd"],
            "first_interaction_date": first_date.isoformat() if first_date else None,
            "last_interaction_date": last_date.isoformat() if last_date else None,
        }, session_ids, assistant_messages

    def get_ai_usage_trend(
        self,
        session: Session,
        user_id: str,
        project_id: str,
        period: str = "daily",
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """
        Get AI usage trend over a period.

        Args:
            session: Database session
            user_id: User ID
            project_id: Project ID
            period: Aggregation period - "daily", "weekly", or "monthly"
            days: Number of days to look back

        Returns:
            List of dicts with date/period and message counts
        """
        end_date = utcnow().date()
        start_date = end_date - timedelta(days=days)
        start_datetime = datetime.combine(start_date, datetime.min.time())

        # Get all chat sessions for this project
        chat_sessions = session.exec(
            select(ChatSession).where(
                and_(
                    ChatSession.user_id == user_id,
                    ChatSession.project_id == project_id,
                )
            )
        ).all()

        if not chat_sessions:
            return []

        session_ids = [s.id for s in chat_sessions]

        # Get messages in date range
        messages = session.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id.in_(session_ids))
            .where(ChatMessage.created_at >= start_datetime)
            .order_by(ChatMessage.created_at.asc())
        ).all()

        if period == "daily":
            # Aggregate by day
            daily_data: dict[str, dict[str, Any]] = {}

            for msg in messages:
                msg_date = str(msg.created_at.date())
                if msg_date not in daily_data:
                    daily_data[msg_date] = {
                        "date": msg_date,
                        "total_messages": 0,
                        "user_messages": 0,
                        "ai_messages": 0,
                        "tool_messages": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                        "total_tokens": 0,
                        "estimated_tokens": 0,
                        "estimated_cost_usd": 0.0,
                    }

                daily_data[msg_date]["total_messages"] += 1
                if msg.role == "user":
                    daily_data[msg_date]["user_messages"] += 1
                elif msg.role == "assistant":
                    daily_data[msg_date]["ai_messages"] += 1
                elif msg.role == "tool":
                    daily_data[msg_date]["tool_messages"] += 1

                self._append_assistant_usage_to_bucket(daily_data[msg_date], msg)

            for day in daily_data.values():
                self._finalize_usage_bucket(day)

            return list(daily_data.values())

        elif period == "weekly":
            # Aggregate by week (Monday to Sunday)
            weekly_data: dict[str, dict[str, Any]] = {}

            for msg in messages:
                # Get the start of the week (Monday)
                msg_date = msg.created_at.date()
                week_start = msg_date - timedelta(days=msg_date.weekday())
                week_key = str(week_start)

                if week_key not in weekly_data:
                    weekly_data[week_key] = {
                        "date": week_key,
                        "period_label": self._format_week_label(week_start),
                        "total_messages": 0,
                        "user_messages": 0,
                        "ai_messages": 0,
                        "tool_messages": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                        "total_tokens": 0,
                        "estimated_tokens": 0,
                        "estimated_cost_usd": 0.0,
                        "days_with_activity": set(),
                    }

                weekly_data[week_key]["total_messages"] += 1
                if msg.role == "user":
                    weekly_data[week_key]["user_messages"] += 1
                elif msg.role == "assistant":
                    weekly_data[week_key]["ai_messages"] += 1
                elif msg.role == "tool":
                    weekly_data[week_key]["tool_messages"] += 1

                self._append_assistant_usage_to_bucket(weekly_data[week_key], msg)

                weekly_data[week_key]["days_with_activity"].add(str(msg_date))

            # Build result
            result = []
            for week_key in sorted(weekly_data.keys()):
                data = weekly_data[week_key]
                self._finalize_usage_bucket(data)
                result.append({
                    "date": data["date"],
                    "period_label": data["period_label"],
                    "total_messages": data["total_messages"],
                    "user_messages": data["user_messages"],
                    "ai_messages": data["ai_messages"],
                    "tool_messages": data["tool_messages"],
                    "input_tokens": data["input_tokens"],
                    "output_tokens": data["output_tokens"],
                    "cache_read_tokens": data["cache_read_tokens"],
                    "cache_write_tokens": data["cache_write_tokens"],
                    "total_tokens": data["total_tokens"],
                    "estimated_tokens": data["estimated_tokens"],
                    "estimated_cost_usd": data["estimated_cost_usd"],
                    "days_with_activity": len(data["days_with_activity"]),
                })
            return result

        elif period == "monthly":
            # Aggregate by month
            monthly_data: dict[str, dict[str, Any]] = {}

            for msg in messages:
                msg_date = msg.created_at.date()
                month_key = msg_date.strftime("%Y-%m-01")

                if month_key not in monthly_data:
                    monthly_data[month_key] = {
                        "date": month_key,
                        "period_label": msg_date.strftime("%B %Y"),
                        "total_messages": 0,
                        "user_messages": 0,
                        "ai_messages": 0,
                        "tool_messages": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                        "total_tokens": 0,
                        "estimated_tokens": 0,
                        "estimated_cost_usd": 0.0,
                        "days_with_activity": set(),
                    }

                monthly_data[month_key]["total_messages"] += 1
                if msg.role == "user":
                    monthly_data[month_key]["user_messages"] += 1
                elif msg.role == "assistant":
                    monthly_data[month_key]["ai_messages"] += 1
                elif msg.role == "tool":
                    monthly_data[month_key]["tool_messages"] += 1

                self._append_assistant_usage_to_bucket(monthly_data[month_key], msg)

                monthly_data[month_key]["days_with_activity"].add(str(msg_date))

            # Build result
            result = []
            for month_key in sorted(monthly_data.keys()):
                data = monthly_data[month_key]
                self._finalize_usage_bucket(data)
                result.append({
                    "date": data["date"],
                    "period_label": data["period_label"],
                    "total_messages": data["total_messages"],
                    "user_messages": data["user_messages"],
                    "ai_messages": data["ai_messages"],
                    "tool_messages": data["tool_messages"],
                    "input_tokens": data["input_tokens"],
                    "output_tokens": data["output_tokens"],
                    "cache_read_tokens": data["cache_read_tokens"],
                    "cache_write_tokens": data["cache_write_tokens"],
                    "total_tokens": data["total_tokens"],
                    "estimated_tokens": data["estimated_tokens"],
                    "estimated_cost_usd": data["estimated_cost_usd"],
                    "days_with_activity": len(data["days_with_activity"]),
                })
            return result

        return []

    def get_ai_usage_summary(
        self,
        session: Session,
        user_id: str,
        project_id: str,
        reference_date: date | None = None,
    ) -> dict[str, Any]:
        """
        Get a comprehensive AI usage summary for the project dashboard.

        Combines current stats with recent trends.

        Args:
            session: Database session
            user_id: User ID
            project_id: Project ID
            reference_date: Calendar date for period boundaries (defaults to today UTC)

        Returns:
            Dict with:
            - current: Current AI usage stats
            - today: Today's usage
            - this_week: This week's usage
            - this_month: This month's usage
        """
        current_stats, session_ids, assistant_messages = self._load_ai_usage_stats(session, user_id, project_id)

        # Get today's usage
        today = reference_date or utcnow().date()
        today_start = datetime.combine(today, datetime.min.time())
        period_end = datetime.combine(today + timedelta(days=1), datetime.min.time())

        today_messages = {
            "total": 0,
            "user": 0,
            "ai": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 0,
            "estimated_tokens": 0,
            "estimated_cost_usd": 0.0,
        }
        week_messages = {
            "total": 0,
            "user": 0,
            "ai": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 0,
            "estimated_tokens": 0,
            "estimated_cost_usd": 0.0,
        }
        month_messages = {
            "total": 0,
            "user": 0,
            "ai": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 0,
            "estimated_tokens": 0,
            "estimated_cost_usd": 0.0,
        }

        if session_ids:
            def summarize_messages(start_dt: datetime) -> dict[str, Any]:
                aggregated = session.exec(
                    select(
                        func.count(ChatMessage.id).label("total"),
                        func.sum(case((ChatMessage.role == "user", 1), else_=0)).label("user"),
                        func.sum(case((ChatMessage.role == "assistant", 1), else_=0)).label("ai"),
                    )
                    .where(ChatMessage.session_id.in_(session_ids))
                    .where(ChatMessage.created_at >= start_dt)
                    .where(ChatMessage.created_at < period_end)
                ).one()

                period_assistant_messages = [
                    message for message in assistant_messages
                    if start_dt <= message.created_at < period_end
                ]
                token_metrics = self._aggregate_assistant_token_metrics(period_assistant_messages)

                return {
                    "total": int(aggregated.total or 0),
                    "user": int(aggregated.user or 0),
                    "ai": int(aggregated.ai or 0),
                    "input_tokens": token_metrics["input_tokens"],
                    "output_tokens": token_metrics["output_tokens"],
                    "cache_read_tokens": token_metrics["cache_read_tokens"],
                    "cache_write_tokens": token_metrics["cache_write_tokens"],
                    "total_tokens": token_metrics["total_tokens"],
                    "estimated_tokens": token_metrics["estimated_tokens"],
                    "estimated_cost_usd": token_metrics["estimated_cost_usd"],
                }

            # Today
            today_messages = summarize_messages(today_start)

            # This week
            week_start = today - timedelta(days=today.weekday())
            week_start_dt = datetime.combine(week_start, datetime.min.time())
            week_messages = summarize_messages(week_start_dt)

            # This month
            month_start = today.replace(day=1)
            month_start_dt = datetime.combine(month_start, datetime.min.time())
            month_messages = summarize_messages(month_start_dt)
        log_with_context(
            logger,
            20,  # INFO
            "AI usage summary calculated",
            user_id=user_id,
            project_id=project_id,
            total_messages=current_stats["total_messages"],
            total_sessions=current_stats["total_sessions"],
            total_tokens=current_stats["total_tokens"],
            estimated_cost_usd=current_stats["estimated_cost_usd"],
        )

        return {
            "current": current_stats,
            "today": today_messages,
            "this_week": week_messages,
            "this_month": month_messages,
        }


# Singleton instance
writing_stats_service = WritingStatsService()
