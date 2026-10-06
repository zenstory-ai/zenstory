"""
Ingestion jobs service - SQLModel version.
Handles IngestionJob tracking and status updates.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import update
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from models.material_models import IngestionJob, Novel
from services.material.job_errors import REFUNDABLE_JOB_ERROR_CODES
from utils.logger import get_logger

logger = get_logger(__name__)

# Pending without a Prefect flow run id: the API never finished dispatching.
DISPATCH_STALE_AFTER = timedelta(minutes=10)
# Pending with a flow run id: Prefect accepted the run but no worker started it.
DISPATCHED_PENDING_STALE_AFTER = timedelta(minutes=30)
PROCESSING_STALE_AFTER = timedelta(hours=2)

TERMINAL_JOB_STATUSES = frozenset({"completed", "completed_with_errors", "failed"})
MATERIAL_DECOMPOSE_FEATURE = "material_decompose"

# stage_progress key holding the effective stage map recorded at flow start
# (keys: config.material_settings.STAGE_KEYS).
ENABLED_STAGES_KEY = "enabled_stages"
# stage_progress key holding the job's quota state:
# {"quota_charged": bool, "quota_refunded": bool, "refund_reason": str}.
# quota_charged means this job currently holds one material_decompose unit.
BILLING_KEY = "billing"


def _load_stage_progress(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


class IngestionJobsService:
    """Ingestion job tracking service using SQLModel patterns."""

    def create_job(
        self,
        session: Session,
        novel_id: int,
        total_chapters: int,
        status: str = "pending",
        source_path: str = "",
        correlation_id: str | None = None,
    ) -> IngestionJob:
        """Create a new ingestion job."""
        job = IngestionJob(
            novel_id=novel_id,
            total_chapters=total_chapters,
            status=status,
            source_path=source_path,
            correlation_id=correlation_id,
        )
        session.add(job)
        session.flush()
        return job

    def get_latest_by_novel(
        self, session: Session, novel_id: int
    ) -> IngestionJob | None:
        """Get the latest ingestion job for a novel."""
        statement = (
            select(IngestionJob)
            .where(IngestionJob.novel_id == novel_id)
            .order_by(IngestionJob.created_at.desc())
        )
        return session.exec(statement).first()

    def update_status(self, session: Session, job_id: int, status: str) -> None:
        """Update job status."""
        job = session.get(IngestionJob, job_id)
        if job:
            job.status = status
            if status == "processing" and job.started_at is None:
                job.started_at = utcnow()
            if status in {"completed", "completed_with_errors", "failed"}:
                job.completed_at = utcnow()
            job.updated_at = utcnow()
            session.add(job)
            session.flush()

    def update_processed(
        self,
        session: Session,
        job_id: int,
        processed_chapters: int | None = None,
        status: str | None = None,
        stage: str | None = None,
        stage_status: str | None = None,
        stage_data: dict[str, Any] | None = None,
        error_message: str | None = None,
        error_details: dict[str, Any] | None = None,
    ) -> None:
        """Update processed count, status, and optional stage/error metadata."""
        job = session.get(IngestionJob, job_id)
        if not job:
            return

        if processed_chapters is not None:
            normalized = max(0, processed_chapters)
            if job.total_chapters > 0:
                normalized = min(normalized, job.total_chapters)
            # Keep monotonic progress to avoid regressions on retries/resumes.
            job.processed_chapters = max(job.processed_chapters, normalized)

        if status is not None:
            job.status = status
            if status == "processing" and job.started_at is None:
                job.started_at = utcnow()
            if status in {"completed", "completed_with_errors", "failed"}:
                job.completed_at = utcnow()

        if stage:
            payload = stage_data or {}
            job.update_stage_progress(stage, stage_status or status or "processing", **payload)

        if error_message:
            job.error_message = error_message
        if error_details:
            job.error_details = json.dumps(error_details, ensure_ascii=False)

        job.updated_at = utcnow()
        session.add(job)
        session.flush()

    def update_stage_progress(
        self,
        session: Session,
        job_id: int,
        stage: str,
        status: str,
        **stage_payload: Any,
    ) -> None:
        """Update only stage progress payload for the job."""
        job = session.get(IngestionJob, job_id)
        if not job:
            return
        if status == "processing" and job.started_at is None:
            job.started_at = utcnow()
        job.update_stage_progress(stage, status, **stage_payload)
        job.updated_at = utcnow()
        session.add(job)
        session.flush()

    def set_enabled_stages(
        self,
        session: Session,
        job_id: int,
        enabled_stages: dict[str, bool],
    ) -> None:
        """Persist the stage map under stage_progress["enabled_stages"].

        Semantics: "stage may have produced data" for this novel. The new map is
        OR-merged with every earlier snapshot of the same novel (this job's own
        snapshot on resume, earlier jobs' snapshots on retry), so turning a stage
        off and resuming/retrying never hides data an earlier run produced.
        """
        job = session.get(IngestionJob, job_id)
        if not job:
            return
        merged = {k: bool(v) for k, v in enabled_stages.items()}
        novel_jobs = session.exec(
            select(IngestionJob).where(IngestionJob.novel_id == job.novel_id)
        ).all()
        for other in novel_jobs:
            previous = self.get_enabled_stages(other) or {}
            if "stories" in previous and "storylines" not in previous:
                # Older snapshots used "stories" for both 剧情 and 故事线.
                previous["storylines"] = previous["stories"]
            for key, value in previous.items():
                merged[key] = merged.get(key, False) or value
        progress = _load_stage_progress(job.stage_progress)
        progress[ENABLED_STAGES_KEY] = merged
        job.stage_progress = json.dumps(progress, ensure_ascii=False)
        job.updated_at = utcnow()
        session.add(job)
        session.flush()

    @staticmethod
    def get_enabled_stages(job: IngestionJob | None) -> dict[str, bool] | None:
        """Read the enabled-stage snapshot; None for jobs created before snapshots existed."""
        if job is None:
            return None
        snapshot = _load_stage_progress(job.stage_progress).get(ENABLED_STAGES_KEY)
        if not isinstance(snapshot, dict):
            return None
        return {str(k): bool(v) for k, v in snapshot.items()}

    @staticmethod
    def get_billing(job: IngestionJob | None) -> dict[str, Any]:
        """Read the job's quota state; empty for jobs created before it existed."""
        if job is None:
            return {}
        billing = _load_stage_progress(job.stage_progress).get(BILLING_KEY)
        return dict(billing) if isinstance(billing, dict) else {}

    @staticmethod
    def set_billing(job: IngestionJob, **fields: Any) -> None:
        """Merge quota-state fields into the job (caller commits)."""
        progress = _load_stage_progress(job.stage_progress)
        billing = progress.get(BILLING_KEY)
        billing = dict(billing) if isinstance(billing, dict) else {}
        billing.update(fields)
        progress[BILLING_KEY] = billing
        job.stage_progress = json.dumps(progress, ensure_ascii=False)

    def fail_job(
        self,
        session: Session,
        job: IngestionJob,
        *,
        error_code: str,
        stage: str,
        reason: str,
        details: dict[str, Any] | None = None,
        only_if_unchanged: bool = False,
    ) -> bool:
        """
        Mark ``job`` failed with an error code and refund its quota when the code is
        refundable and the job still holds a charged unit.

        The transition always compares the exact loaded snapshot. Watchdog
        callers set ``only_if_unchanged`` to avoid retrying after fresh progress;
        flow failures may retry once after a concurrent progress update.
        Refund settlement is a separate atomic transaction: exceptions leave
        the failed job charged so the next read can retry safely.
        """
        if job.status in TERMINAL_JOB_STATUSES:
            if job.status == "failed":
                self._settle_job_refund(session, job)
            return False

        now = utcnow()
        progress = _load_stage_progress(job.stage_progress)
        progress["watchdog" if stage in {"dispatch_timeout", "processing_timeout"} else "failed"] = {
            "status": "failed",
            "timestamp": now.isoformat(),
            "reason": reason,
        }
        error_details = {"stage": stage, "error_code": error_code, **(details or {})}
        values = {
            "status": "failed",
            "error_message": error_code,
            "error_details": json.dumps(error_details, ensure_ascii=False),
            "stage_progress": json.dumps(progress, ensure_ascii=False),
            "completed_at": now,
            "updated_at": now,
        }
        statement = update(IngestionJob).where(
            IngestionJob.id == job.id,
            IngestionJob.status == job.status,
            IngestionJob.updated_at == job.updated_at,
            IngestionJob.stage_progress == job.stage_progress,
        )
        result = session.exec(statement.values(**values).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            session.rollback()
            session.refresh(job)
            if not only_if_unchanged:
                return self.fail_job(
                    session, job, error_code=error_code, stage=stage, reason=reason,
                    details=details, only_if_unchanged=True,
                )
            if job.status == "failed":
                self._settle_job_refund(session, job)
            return False
        session.commit()
        session.refresh(job)
        self._settle_job_refund(session, job)
        return True

    def _settle_job_refund(self, session: Session, job: IngestionJob) -> None:
        """Commit quota release and the exact failed job's billing together."""
        billing = self.get_billing(job)
        if (
            job.status != "failed" or job.error_message not in REFUNDABLE_JOB_ERROR_CODES
            or billing.get("quota_charged") is not True
        ):
            return

        old_progress, old_updated = job.stage_progress, job.updated_at
        try:
            refunded = self._release_job_quota(session, job)
            progress = _load_stage_progress(old_progress)
            billing.update(
                quota_charged=False, quota_refunded=refunded, refund_reason=job.error_message,
            )
            progress[BILLING_KEY] = billing
            result = session.exec(
                update(IngestionJob).where(
                    IngestionJob.id == job.id,
                    IngestionJob.status == "failed",
                    IngestionJob.error_message == job.error_message,
                    IngestionJob.updated_at == old_updated,
                    IngestionJob.stage_progress == old_progress,
                ).values(
                    stage_progress=json.dumps(progress, ensure_ascii=False), updated_at=utcnow(),
                ).execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                session.rollback()
            else:
                session.commit()
        except Exception:
            session.rollback()
            logger.error("Failed to settle material quota refund: job_id=%s", job.id, exc_info=True)
        session.refresh(job)

    def _release_job_quota(self, session: Session, job: IngestionJob) -> bool:
        from services.quota_service import quota_service

        novel = session.get(Novel, job.novel_id)
        if novel is None:
            return False
        billing = self.get_billing(job)
        raw_period_start = billing.get("quota_period_start")
        period_start = None
        consumed_at = job.created_at if raw_period_start is None else None
        if raw_period_start is not None:
            if not isinstance(raw_period_start, str):
                raise ValueError("Invalid material quota period type")
            period_start = datetime.fromisoformat(raw_period_start.replace("Z", "+00:00"))
        return quota_service.release_feature_quota(
            session, novel.user_id, MATERIAL_DECOMPOSE_FEATURE,
            period_start=period_start, consumed_at=consumed_at, commit=False,
        )

    def reconcile_stale_job(self, session: Session, job: IngestionJob) -> IngestionJob:
        """
        Best-effort reconciliation for stale pending/processing jobs.

        This provides a read-path safety net so orphaned jobs (never dispatched,
        accepted by Prefect but never started by a worker, or a worker that
        crashed mid-run) become failed, refund their quota, and can be retried.
        A flow run that starts after its job was reconciled exits without work.
        """
        if job.status == "failed":
            self._settle_job_refund(session, job)
            return job

        now = utcnow()
        last_updated = job.updated_at or job.created_at or now
        if getattr(last_updated, "tzinfo", None) is None and getattr(now, "tzinfo", None) is not None:
            from datetime import UTC

            last_updated = last_updated.replace(tzinfo=UTC)

        age = now - last_updated

        if job.status == "pending":
            threshold = DISPATCHED_PENDING_STALE_AFTER if job.correlation_id else DISPATCH_STALE_AFTER
            if age >= threshold:
                self.fail_job(
                    session,
                    job,
                    error_code=ErrorCode.MATERIAL_DISPATCH_TIMEOUT,
                    stage="dispatch_timeout",
                    reason="dispatch_timeout",
                    details={"reconciled": True, "dispatched": bool(job.correlation_id)},
                    only_if_unchanged=True,
                )
            return job

        if job.status == "processing" and age >= PROCESSING_STALE_AFTER:
            self.fail_job(
                session,
                job,
                error_code=ErrorCode.MATERIAL_PROCESSING_TIMEOUT,
                stage="processing_timeout",
                reason="processing_timeout",
                details={"reconciled": True},
                only_if_unchanged=True,
            )
            return job

        return job
