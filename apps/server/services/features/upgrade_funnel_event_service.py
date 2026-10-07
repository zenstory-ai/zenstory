"""Upgrade funnel event service for monetization analytics."""

from datetime import UTC, datetime, timedelta
from typing import TypedDict

from sqlmodel import Session, func, select

from config.datetime_utils import beijing_day_bounds, utcnow
from models import (
    UPGRADE_FUNNEL_ACTIONS,
    UPGRADE_FUNNEL_CTAS,
    UPGRADE_FUNNEL_EVENT_NAME_BY_ACTION,
    UPGRADE_FUNNEL_SURFACES,
    UpgradeFunnelEvent,
)


class UpgradeFunnelSourceStats(TypedDict):
    """Per-source upgrade funnel statistics."""

    source: str
    exposes: int
    clicks: int
    conversions: int
    click_through_rate: float
    conversion_rate_from_click: float
    conversion_rate_from_expose: float


class UpgradeFunnelStats(TypedDict):
    """Upgrade funnel aggregate response payload."""

    window_days: int
    period_start: str
    period_end: str
    totals: dict[str, int]
    sources: list[UpgradeFunnelSourceStats]


class UpgradeFunnelEventService:
    """Service for writing and aggregating upgrade funnel events."""

    def record_event(
        self,
        session: Session,
        *,
        user_id: str,
        action: str,
        source: str,
        surface: str,
        cta: str | None = None,
        destination: str | None = None,
        event_name: str | None = None,
        event_metadata: dict | None = None,
        occurred_at: datetime | None = None,
    ) -> UpgradeFunnelEvent:
        """Persist a single upgrade funnel event."""
        if action not in UPGRADE_FUNNEL_ACTIONS:
            raise ValueError(f"Unsupported upgrade funnel action: {action}")

        if surface not in UPGRADE_FUNNEL_SURFACES:
            raise ValueError(f"Unsupported upgrade funnel surface: {surface}")

        if cta and cta not in UPGRADE_FUNNEL_CTAS:
            raise ValueError(f"Unsupported upgrade funnel cta: {cta}")

        expected_event_name = UPGRADE_FUNNEL_EVENT_NAME_BY_ACTION[action]
        normalized_event_name = (event_name or expected_event_name).strip() or expected_event_name

        normalized_source = source.strip()
        if not normalized_source:
            raise ValueError("Upgrade funnel source is required")

        normalized_destination = destination.strip() if isinstance(destination, str) else None
        if normalized_destination == "":
            normalized_destination = None

        normalized_occurred_at = occurred_at or utcnow()
        if normalized_occurred_at.tzinfo is None:
            normalized_occurred_at = normalized_occurred_at.replace(tzinfo=UTC)
        else:
            normalized_occurred_at = normalized_occurred_at.astimezone(UTC)

        metadata = event_metadata if isinstance(event_metadata, dict) else {}

        event = UpgradeFunnelEvent(
            user_id=user_id,
            event_name=normalized_event_name,
            action=action,
            source=normalized_source,
            surface=surface,
            cta=cta,
            destination=normalized_destination,
            event_metadata=metadata,
            occurred_at=normalized_occurred_at,
        )
        session.add(event)
        session.commit()
        session.refresh(event)
        return event

    def get_funnel_stats(self, session: Session, *, days: int = 7) -> UpgradeFunnelStats:
        """Get upgrade funnel stats grouped by source for a rolling window."""
        window_days = max(1, min(days, 90))
        period_end = utcnow()
        # Window starts at 00:00 Beijing time, window_days - 1 days ago.
        period_start = beijing_day_bounds(period_end - timedelta(days=window_days - 1))[0]

        grouped_rows = session.exec(
            select(
                UpgradeFunnelEvent.source,
                UpgradeFunnelEvent.action,
                func.count(UpgradeFunnelEvent.id),
            ).where(
                UpgradeFunnelEvent.occurred_at >= period_start,
                UpgradeFunnelEvent.occurred_at <= period_end,
            ).group_by(
                UpgradeFunnelEvent.source,
                UpgradeFunnelEvent.action,
            )
        ).all()

        totals: dict[str, int] = {"expose": 0, "click": 0, "conversion": 0}
        source_map: dict[str, dict[str, int]] = {}

        for source, action, count_raw in grouped_rows:
            count = int(count_raw or 0)
            if action not in totals:
                continue

            totals[action] += count
            slot = source_map.setdefault(
                source,
                {"expose": 0, "click": 0, "conversion": 0},
            )
            slot[action] += count

        source_stats: list[UpgradeFunnelSourceStats] = []
        for source, counts in source_map.items():
            exposes = counts["expose"]
            clicks = counts["click"]
            conversions = counts["conversion"]

            source_stats.append(
                UpgradeFunnelSourceStats(
                    source=source,
                    exposes=exposes,
                    clicks=clicks,
                    conversions=conversions,
                    click_through_rate=round(clicks / exposes, 4) if exposes > 0 else 0.0,
                    conversion_rate_from_click=round(conversions / clicks, 4) if clicks > 0 else 0.0,
                    conversion_rate_from_expose=round(conversions / exposes, 4) if exposes > 0 else 0.0,
                )
            )

        source_stats.sort(
            key=lambda item: (
                -item["conversions"],
                -item["clicks"],
                -item["exposes"],
                item["source"],
            )
        )

        return UpgradeFunnelStats(
            window_days=window_days,
            period_start=period_start.isoformat(),
            period_end=period_end.isoformat(),
            totals=totals,
            sources=source_stats,
        )


upgrade_funnel_event_service = UpgradeFunnelEventService()
