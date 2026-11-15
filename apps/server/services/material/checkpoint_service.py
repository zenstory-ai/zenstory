"""
Checkpoint service - SQLModel version.
Handles ProcessCheckpoint tracking for ingestion stages.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import and_, or_
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from models.material_models import IngestionJob, ProcessCheckpoint


class CheckpointService:
    """Process checkpoint service using SQLModel patterns."""

    def get(
        self, session: Session, novel_id: int, stage: str, job_id: int | None = None
    ) -> ProcessCheckpoint | None:
        """Read this job's checkpoint, or a prior terminal/legacy resume seed."""
        return self._read(session, novel_id, stage, job_id)

    def get_latest(
        self, session: Session, novel_id: int, job_id: int | None = None
    ) -> ProcessCheckpoint | None:
        """Get the latest checkpoint for a novel (any stage)."""
        return self._read(session, novel_id, None, job_id)

    def _read(
        self, session: Session, novel_id: int, stage: str | None, job_id: int | None,
    ) -> ProcessCheckpoint | None:
        statement = select(ProcessCheckpoint).where(ProcessCheckpoint.novel_id == novel_id)
        if stage is not None:
            statement = statement.where(ProcessCheckpoint.stage == stage)
        if job_id is None:
            # Existing unscoped callers retain historical read behavior.
            return session.exec(statement.order_by(
                ProcessCheckpoint.created_at.desc(), ProcessCheckpoint.id.desc(),
            )).first()

        statement = statement.order_by(ProcessCheckpoint.updated_at.desc(), ProcessCheckpoint.id.desc())

        job = session.get(IngestionJob, job_id)
        if not job or job.novel_id != novel_id:
            raise ValueError("Checkpoint job does not belong to novel")
        current = session.exec(statement.where(ProcessCheckpoint.job_id == job_id)).first()
        if current:
            return current

        previous_jobs = select(IngestionJob.id).where(
            IngestionJob.novel_id == novel_id,
            IngestionJob.status.in_(["failed", "completed", "completed_with_errors"]),
            or_(
                IngestionJob.created_at < job.created_at,
                and_(IngestionJob.created_at == job.created_at, IngestionJob.id < job_id),
            ),
        )
        # A seed is immutable input to a new job, never a row the new job owns.
        return session.exec(statement.where(
            ProcessCheckpoint.updated_at <= job.created_at,
            or_(ProcessCheckpoint.job_id.is_(None), ProcessCheckpoint.job_id.in_(previous_jobs)),
        )).first()

    def upsert(
        self,
        session: Session,
        novel_id: int,
        stage: str,
        data: dict[str, Any] | None,
        *,
        status: str | None = None,
        job_id: int | None = None,
        error: str | None = None,
    ) -> ProcessCheckpoint:
        """Write only this job's row; copy historical resume data on first write."""
        source = self.get(session, novel_id, stage, job_id=job_id) if job_id is not None else None
        statement = (
            select(ProcessCheckpoint)
            .where(
                ProcessCheckpoint.novel_id == novel_id,
                ProcessCheckpoint.stage == stage,
                ProcessCheckpoint.job_id == job_id,
            )
            .order_by(ProcessCheckpoint.created_at.desc())
        )
        cp = session.exec(statement).first()

        if cp is None:
            # Create new checkpoint
            inherited = {}
            if source and source.checkpoint_data:
                try:
                    inherited = json.loads(source.checkpoint_data)
                except (json.JSONDecodeError, TypeError):
                    inherited = {}
                if not isinstance(inherited, dict):
                    inherited = {}
            inherited.update(data or {})
            cp = ProcessCheckpoint(
                novel_id=novel_id,
                job_id=job_id,
                stage=stage,
                stage_status=status or (source.stage_status if source else "processing"),
                checkpoint_data=json.dumps(inherited),
            )
            session.add(cp)
            session.flush()
            return cp

        # Update existing checkpoint
        if status:
            cp.stage_status = status
        if data:
            # Parse existing data, update, and serialize back
            existing_data = {}
            if cp.checkpoint_data:
                try:
                    existing_data = json.loads(cp.checkpoint_data) if isinstance(cp.checkpoint_data, str) else cp.checkpoint_data
                except (json.JSONDecodeError, TypeError):
                    existing_data = {}
            existing_data.update(data)
            cp.checkpoint_data = json.dumps(existing_data)
        if error:
            cp.mark_failed(error)

        cp.updated_at = utcnow()

        session.add(cp)
        session.flush()
        return cp
