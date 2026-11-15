"""
Helper functions for materials API.

Contains shared utility functions used across material library endpoints.
"""
from sqlmodel import Session

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import create_session
from models.material_models import IngestionJob, Novel
from services.material.ingestion_jobs_service import IngestionJobsService
from utils.logger import get_logger

logger = get_logger(__name__)


def _resolve_lang(accept_language: str | None) -> str:
    """Resolve supported language code from Accept-Language header."""
    if not accept_language:
        return "zh"
    lang = accept_language.split(",")[0].split("-")[0].strip().lower()
    return "en" if lang == "en" else "zh"


def _get_novel_or_404(session: Session, novel_id: int, user_id: str) -> Novel:
    """
    Get novel with ownership and soft delete check.

    Args:
        session: Database session
        novel_id: Novel ID to retrieve
        user_id: User ID for ownership verification

    Returns:
        Novel instance if found and authorized

    Raises:
        APIException: 403 if novel not found, not owned by user, or soft deleted
    """
    novel = session.get(Novel, novel_id)
    if not novel or novel.user_id != user_id or novel.deleted_at is not None:
        raise APIException(error_code=ErrorCode.NOT_AUTHORIZED, status_code=403)
    return novel


async def _start_flow_deployment(
    file_path: str,
    novel_title: str,
    author: str | None,
    user_id: str,
    novel_id: int,
    job_id: int,
):
    """
    Start the novel ingestion flow via Prefect deployment.

    This async function triggers the Prefect flow for processing
    the uploaded novel. The flow runs asynchronously and does not
    block the API response.

    Args:
        file_path: Path to the uploaded novel file
        novel_title: Title of the novel
        author: Author name (optional)
        user_id: User ID who uploaded the novel
        novel_id: Novel ID in the database
        job_id: Exact ingestion job ID

    Returns:
        Flow run ID if successful, None if failed
    """
    def _get_target_job(session: Session) -> IngestionJob | None:
        job = session.get(IngestionJob, job_id)
        if job is None or job.novel_id != novel_id:
            return None
        return job

    def _mark_target_job_failed() -> None:
        session = create_session()
        try:
            target_job = _get_target_job(session)
            if not target_job:
                return

            IngestionJobsService().fail_job(
                session, target_job, error_code=ErrorCode.MATERIAL_DISPATCH_FAILED,
                stage="deployment_start", reason="deployment_start",
            )
        finally:
            session.close()

    def _mark_target_job_dispatched(flow_run_id: str) -> None:
        session = create_session()
        try:
            target_job = _get_target_job(session)
            if not target_job:
                return

            target_job.correlation_id = flow_run_id
            target_job.update_stage_progress(
                "queue",
                "processing",
                message="调度成功，等待工作流执行",
                flow_run_id=flow_run_id,
            )
            target_job.updated_at = utcnow()
            session.add(target_job)
            session.commit()
        finally:
            session.close()

    validation_session = create_session()
    try:
        if _get_target_job(validation_session) is None:
            logger.error(
                "Refusing material deployment for missing or mismatched job: "
                "novel_id=%s job_id=%s",
                novel_id,
                job_id,
            )
            return None
    finally:
        validation_session.close()

    try:
        from prefect.deployments import run_deployment

        logger.info(f"Starting novel ingestion deployment for user {user_id}: {novel_title}")

        # Create the deployment run and return immediately after Prefect accepts it.
        parameters = {
            "file_path": file_path,
            "user_id": user_id,
            "novel_title": novel_title,
            "author": author,
            "resume_from_checkpoint": True,
            "novel_id": novel_id,
            "job_id": job_id,
        }

        flow_run = await run_deployment(
            name="novel_ingestion_v3/novel_ingestion_v3",
            parameters=parameters,
            timeout=0,
            as_subflow=False,
        )
    except Exception as e:
        logger.error(f"Failed to start novel ingestion deployment: {e}", exc_info=True)
        try:
            _mark_target_job_failed()
        except Exception as db_err:
            logger.error(f"Failed to mark ingestion job as failed: {db_err}", exc_info=True)
        # In production, don't fallback to direct execution - fail fast and allow retry.
        return None

    logger.info(f"Novel ingestion deployment started: flow_run_id={flow_run.id}")
    try:
        _mark_target_job_dispatched(str(flow_run.id))
    except Exception as db_err:
        # Prefect accepting the run is authoritative. The V3 flow receives the
        # exact job_id and can repair correlation without a duplicate dispatch.
        logger.error(f"Failed to persist flow run correlation id: {db_err}", exc_info=True)
    return flow_run.id


__all__ = [
    "_get_novel_or_404",
    "_resolve_lang",
    "_start_flow_deployment",
]
