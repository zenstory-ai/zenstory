"""
Material upload API endpoints.

Handles file upload and job retry operations for the material library:
- Upload novels and start decomposition
- Retry failed decomposition jobs
- Internal file download for Prefect workers
"""
import contextlib
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from services.auth import get_current_active_user
from sqlalchemy import update
from sqlmodel import Session, select
from starlette.datastructures import UploadFile as StarletteUploadFile

from config.datetime_utils import normalize_datetime_to_utc, utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from core.permissions import (
    FeatureNotIncludedException,
    QuotaExceededException,
    check_quota,
)
from database import get_session
from middleware.rate_limit import require_user_rate_limit
from models import User
from models.material_models import IngestionJob, Novel
from services.infra.upload_storage import (
    LocalUploadStorage,
    S3UploadStorage,
    UploadNotFoundError,
    UploadReferenceError,
    UploadStorageError,
    get_upload_storage,
)
from services.material.ingestion_jobs_service import IngestionJobsService
from services.material.job_errors import REFUNDABLE_JOB_ERROR_CODES
from services.material.novel_text import (
    UTF8_BOM,
    NovelDecodeError,
    decode_novel_bytes,
    split_novel_text,
    truncate_novel_text,
)
from services.quota_service import quota_service
from utils.logger import get_logger

from .access import require_materials_upload
from .constants import (
    ALLOWED_EXTENSIONS,
    MAX_FILE_SIZE,
    MAX_TEXT_CHARACTERS,
    MAX_UPLOAD_REQUEST_BYTES,
    RETRY_RATE_LIMIT_MAX_REQUESTS,
    RETRY_RATE_LIMIT_WINDOW_SECONDS,
    UPLOAD_RATE_LIMIT_MAX_REQUESTS,
    UPLOAD_RATE_LIMIT_WINDOW_SECONDS,
)
from .helpers import _get_novel_or_404, _start_flow_deployment
from .schemas import MaterialUploadResponse

logger = get_logger(__name__)

# Router without prefix/tags - will be set by parent router
router = APIRouter()

SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
DISPATCH_FAILURE_MESSAGE = "Failed to dispatch ingestion flow"
UPLOAD_FILENAME_TOKEN_BYTES = 8
MAX_UPLOAD_FILENAME_ATTEMPTS = 10


def _storage():
    from config.material_settings import material_settings

    return get_upload_storage(
        material_root=material_settings.UPLOAD_FOLDER,
        feedback_root=os.getenv("FEEDBACK_UPLOAD_DIR", "uploads/feedback"),
    )


def _read_legacy_local_material(filename: str, user_id: str) -> bytes:
    """Read one pre-object-storage material without weakening S3 key validation."""
    from config.material_settings import material_settings

    local_storage = LocalUploadStorage(
        material_root=Path(material_settings.UPLOAD_FOLDER),
        feedback_root=Path(os.getenv("FEEDBACK_UPLOAD_DIR", "uploads/feedback")),
    )
    lexical_path = local_storage.material_root / filename
    if lexical_path.is_symlink():
        raise UploadReferenceError("legacy material path must not be a symlink")
    local_storage.material_reference_for_name(
        owner_id=user_id,
        object_name=filename,
    )
    try:
        descriptor = os.open(
            lexical_path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
    except FileNotFoundError as exc:
        raise UploadNotFoundError("private upload object not found") from exc
    except OSError as exc:
        if lexical_path.is_symlink():
            raise UploadReferenceError("legacy material path must not be a symlink") from exc
        raise
    with os.fdopen(descriptor, "rb") as handle:
        file_stat = os.fstat(handle.fileno())
        if not stat.S_ISREG(file_stat.st_mode):
            raise UploadReferenceError("legacy material path must be a regular file")
        if file_stat.st_size > MAX_FILE_SIZE:
            raise APIException(error_code=ErrorCode.FILE_TOO_LARGE, status_code=413)
        content = handle.read(MAX_FILE_SIZE + 1)
    if len(content) > MAX_FILE_SIZE:
        raise APIException(error_code=ErrorCode.FILE_TOO_LARGE, status_code=413)
    return content


def _download_material_object(filename: str, user_id: str) -> Response:
    storage = None
    try:
        storage = _storage()
        if isinstance(storage, S3UploadStorage) and filename.startswith(f"{user_id}_"):
            content = _read_legacy_local_material(filename, user_id)
        else:
            content = storage.read_material_name(owner_id=user_id, object_name=filename)
    except UploadReferenceError as exc:
        raise APIException(error_code=ErrorCode.NOT_AUTHORIZED, status_code=403) from exc
    except UploadNotFoundError as exc:
        raise APIException(error_code=ErrorCode.FILE_NOT_FOUND, status_code=404) from exc
    except (UploadStorageError, OSError, ValueError) as exc:
        raise APIException(error_code=ErrorCode.SERVICE_UNAVAILABLE, status_code=503) from exc
    finally:
        if storage is not None:
            getattr(storage, "close", lambda: None)()
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _quota_period_iso(period_start: datetime) -> str:
    """Serialize a reserved quota period as an unambiguous UTC timestamp."""
    return normalize_datetime_to_utc(period_start).isoformat()


def _sanitize_original_filename(filename: str) -> str:
    """
    Sanitize client-provided filename to prevent path traversal and unsafe chars.
    """
    base_name = os.path.basename(filename).replace("\x00", "").strip()
    if not base_name:
        raise APIException(error_code=ErrorCode.VALIDATION_ERROR, status_code=400)

    stem, ext = os.path.splitext(base_name)
    safe_ext = ext.lower()
    if safe_ext not in ALLOWED_EXTENSIONS:
        raise APIException(error_code=ErrorCode.FILE_TYPE_INVALID, status_code=400)

    safe_stem = SAFE_FILENAME_RE.sub("_", stem).strip("._")
    if not safe_stem:
        safe_stem = "material"

    return f"{safe_stem}{safe_ext}"


def _build_safe_upload_path(upload_dir: str, filename: str) -> str:
    """
    Build a canonical path under upload_dir and reject path escape.
    """
    if not filename or filename != os.path.basename(filename) or "\x00" in filename:
        raise APIException(error_code=ErrorCode.VALIDATION_ERROR, status_code=400)

    upload_dir_real = os.path.realpath(upload_dir)
    file_path = os.path.realpath(os.path.join(upload_dir_real, filename))
    if os.path.commonpath([upload_dir_real, file_path]) != upload_dir_real:
        raise APIException(error_code=ErrorCode.VALIDATION_ERROR, status_code=400)
    return file_path


def _build_unique_upload_filename(
    user_id: str,
    timestamp: str,
    sanitized_original_filename: str,
) -> str:
    """Build a collision-resistant upload filename while preserving readable suffix."""
    unique_token = secrets.token_hex(UPLOAD_FILENAME_TOKEN_BYTES)
    return f"{user_id}_{timestamp}_{unique_token}_{sanitized_original_filename}"


def _write_upload_file_without_overwrite(
    upload_dir: str,
    user_id: str,
    timestamp: str,
    sanitized_original_filename: str,
    content_bytes: bytes,
) -> tuple[str, str]:
    """Write uploaded bytes to a newly-created path and never overwrite existing files."""
    for _ in range(MAX_UPLOAD_FILENAME_ATTEMPTS):
        safe_filename = _build_unique_upload_filename(
            user_id,
            timestamp,
            sanitized_original_filename,
        )
        file_path = _build_safe_upload_path(upload_dir, safe_filename)

        try:
            with open(file_path, "xb") as f:
                f.write(content_bytes)
            return safe_filename, file_path
        except FileExistsError:
            continue

    raise APIException(
        error_code=ErrorCode.SERVICE_UNAVAILABLE,
        status_code=503,
        detail="Failed to allocate unique upload filename",
    )


@dataclass(frozen=True)
class _UploadAnalysis:
    """What the upload stores and dispatches, after the pre-check."""

    content_bytes: bytes
    encoding: str
    char_count: int
    chapter_count: int
    # Chapters in the file the author picked (> chapter_count when a free
    # trial kept only the first chapters).
    source_chapter_count: int
    source_char_count: int


def _content_too_long(char_count: int, trial_chapters: int | None = None) -> APIException:
    """400 with the counted size, so the author sees how far over the limit the text is."""
    detail: dict[str, int] = {"char_count": char_count, "limit": MAX_TEXT_CHARACTERS}
    if trial_chapters is not None:
        detail["trial_chapters"] = trial_chapters
    return APIException(
        error_code=ErrorCode.FILE_CONTENT_TOO_LONG,
        status_code=400,
        detail=detail,
    )


def _analyze_upload(
    content_bytes: bytes,
    trial_max_chapters: int | None = None,
) -> _UploadAnalysis:
    """
    Decode and split the upload exactly like the ingestion worker's stage0.

    A free trial decomposes only the first ``trial_max_chapters`` chapters, so
    only those are checked against the character limit, and only those are
    stored (cut at the next chapter heading, re-encoded as UTF-8 with a BOM so
    the worker decodes them unambiguously). A paid upload keeps the whole-book
    limits.

    Runs in a worker thread (CPU-bound). Every rejection happens before any
    quota is consumed.
    """
    from config.material_settings import material_settings

    try:
        text, encoding = decode_novel_bytes(content_bytes)
    except NovelDecodeError as exc:
        raise APIException(
            error_code=ErrorCode.FILE_ENCODING_UNSUPPORTED,
            status_code=400,
        ) from exc
    source_char_count = len(text)

    if trial_max_chapters is not None:
        kept = truncate_novel_text(text, trial_max_chapters)
        if kept.total_chapters == 0:
            raise APIException(error_code=ErrorCode.MATERIAL_NO_CHAPTERS, status_code=400)
        if len(kept.text) > MAX_TEXT_CHARACTERS:
            raise _content_too_long(
                len(kept.text), kept.kept_chapters if kept.truncated else None
            )
        stored = UTF8_BOM + kept.text.encode("utf-8") if kept.truncated else content_bytes
        return _UploadAnalysis(
            content_bytes=stored,
            encoding="utf-8-sig" if kept.truncated else encoding,
            char_count=len(kept.text),
            chapter_count=kept.kept_chapters,
            source_chapter_count=kept.total_chapters,
            source_char_count=source_char_count,
        )

    if source_char_count > MAX_TEXT_CHARACTERS:
        raise _content_too_long(source_char_count)
    chapter_count = len(split_novel_text(text))
    if chapter_count == 0:
        raise APIException(error_code=ErrorCode.MATERIAL_NO_CHAPTERS, status_code=400)
    if chapter_count > material_settings.MAX_CHAPTERS_PER_NOVEL:
        raise APIException(
            error_code=ErrorCode.MATERIAL_TOO_MANY_CHAPTERS,
            status_code=400,
            detail=(
                f"{chapter_count} chapters exceed the limit of "
                f"{material_settings.MAX_CHAPTERS_PER_NOVEL}"
            ),
        )
    return _UploadAnalysis(
        content_bytes=content_bytes,
        encoding=encoding,
        char_count=source_char_count,
        chapter_count=chapter_count,
        source_chapter_count=chapter_count,
        source_char_count=source_char_count,
    )


def _check_upload_content_length(request: Request) -> None:
    """
    Reject declared-oversized bodies before the multipart form is parsed.

    A body without Content-Length (chunked transfer, e.g. re-chunked by a
    proxy) is left to the global request-body limit; the bounded file read
    below still caps what this endpoint holds in memory.
    """
    raw_length = request.headers.get("content-length")
    if raw_length is None:
        return
    try:
        content_length = int(raw_length)
    except ValueError as exc:
        raise APIException(error_code=ErrorCode.BAD_REQUEST, status_code=400) from exc
    if content_length > MAX_UPLOAD_REQUEST_BYTES:
        raise APIException(error_code=ErrorCode.FILE_TOO_LARGE, status_code=413)


async def _read_upload_file(request: Request) -> StarletteUploadFile:
    """Parse the multipart body (one file part) after auth and size pre-checks."""
    form = await request.form(max_files=1, max_fields=10)
    upload = form.get("file")
    if not isinstance(upload, StarletteUploadFile):
        raise APIException(error_code=ErrorCode.VALIDATION_ERROR, status_code=400)
    return upload


def _mark_job_dispatch_failed(
    session: Session,
    job_id: int,
) -> None:
    """Use the shared durable failure/refund transition for dispatch failures."""
    job = session.get(IngestionJob, job_id)
    if job is None:
        return

    IngestionJobsService().fail_job(
        session, job, error_code=ErrorCode.MATERIAL_DISPATCH_FAILED,
        stage="deployment_start", reason="deployment_start",
    )


# ==================== Internal Endpoints ====================

@router.get("/internal/files/{filename}")
async def download_upload_file(
    filename: str,
    current_user: User = Depends(get_current_active_user),
):
    """
    Internal endpoint for Prefect worker to download uploaded files.

    Verifies file ownership based on filename format:
    {user_id}_{timestamp}_{unique_token}_{original_filename}
    """
    return _download_material_object(filename, current_user.id)


@router.get("/internal/system/files/{filename}")
async def download_upload_file_for_worker(
    filename: str,
    user_id: str = Query(..., description="Owner user id"),
    internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    """
    Internal endpoint for worker-to-server file download.

    Uses a shared secret (`MATERIAL_INTERNAL_TOKEN`) and explicit user_id ownership check.
    """
    expected_token = os.getenv("MATERIAL_INTERNAL_TOKEN", "")
    if not expected_token or not internal_token or not secrets.compare_digest(internal_token, expected_token):
        raise APIException(
            error_code=ErrorCode.AUTH_UNAUTHORIZED,
            status_code=401,
            message="Invalid internal token",
        )

    return _download_material_object(filename, user_id)


# ==================== Upload Endpoints ====================

_UPLOAD_OPENAPI_EXTRA = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {"file": {"type": "string", "format": "binary"}},
                }
            }
        },
    }
}


@router.post(
    "/upload",
    response_model=MaterialUploadResponse,
    openapi_extra=_UPLOAD_OPENAPI_EXTRA,
)
async def upload_material(
    request: Request,
    title: str | None = Query(None, description="Novel title (optional, auto-detect from file)"),
    author: str | None = Query(None, description="Author name (optional)"),
    current_user: User = Depends(require_materials_upload),
    _rate_limit: int = Depends(
        require_user_rate_limit(
            "materials_upload",
            UPLOAD_RATE_LIMIT_MAX_REQUESTS,
            UPLOAD_RATE_LIMIT_WINDOW_SECONDS,
        )
    ),
    session: Session = Depends(get_session),
):
    """
    Upload a novel file and start decomposition.

    Constraints:
    - Paid materials-library entitlement (or an unused free trial) and per-user
      rate limit are checked before the request body is read
    - Only .txt files allowed, maximum 20MB, maximum 300,000 characters
    - The text must split into 1..MATERIAL_MAX_CHAPTERS_PER_NOVEL chapters
    - A free trial keeps only the first TRIAL_MAX_CHAPTERS chapters: the
      character limit applies to those, and only those are stored and dispatched
    - Returns success only after the decomposition flow dispatch is accepted
    """
    # Size pre-check, then parse the (single-file) multipart body.
    _check_upload_content_length(request)
    file = await _read_upload_file(request)
    return await process_material_upload(
        file=file,
        title=title,
        author=author,
        current_user=current_user,
        session=session,
    )


async def process_material_upload(
    file: StarletteUploadFile,
    title: str | None,
    author: str | None,
    current_user: User,
    session: Session,
) -> MaterialUploadResponse:
    """Validate an uploaded novel, charge one decomposition, and dispatch the flow."""
    # 1. Validate file extension
    if not file.filename:
        raise APIException(error_code=ErrorCode.VALIDATION_ERROR, status_code=400)

    _, ext = os.path.splitext(file.filename)
    if ext.lower() not in ALLOWED_EXTENSIONS:
        raise APIException(
            error_code=ErrorCode.FILE_TYPE_INVALID,
            status_code=400,
        )

    # 2. Bounded read, then decode + chapter pre-check off the event loop.
    content_bytes = await file.read(MAX_FILE_SIZE + 1)
    if len(content_bytes) > MAX_FILE_SIZE:
        raise APIException(
            error_code=ErrorCode.FILE_TOO_LARGE,
            status_code=400,
        )

    # Paid authors spend a monthly decomposition; everyone else reaches this point
    # only with an unused free trial (require_materials_upload), which covers the
    # first TRIAL_MAX_CHAPTERS chapters of one book.
    from config.material_settings import material_settings

    use_trial = not quota_service.has_feature_access(
        session, current_user.id, "materials_library_access"
    )
    trial_chapter_limit = int(material_settings.TRIAL_MAX_CHAPTERS) if use_trial else None

    analysis = await run_in_threadpool(_analyze_upload, content_bytes, trial_chapter_limit)
    # A trial stores and dispatches only the chapters it decomposes.
    content_bytes = analysis.content_bytes
    char_count = analysis.char_count

    original_filename = file.filename
    sanitized_original_filename = _sanitize_original_filename(original_filename)
    novel_title = title or os.path.splitext(file.filename)[0]

    # Check before file I/O, then reserve authoritatively with job creation in
    # one short transaction. No quota write lock spans the file write.
    if not use_trial:
        check_quota("material_decompose", session, current_user.id)
    dispatch_accepted = False
    job_id: int | None = None
    file_path: str | None = None
    job_persisted = False
    commit_attempted = False
    upload_storage = None

    try:
        # 3. Save the source before quota reservation or database writes.
        timestamp = utcnow().strftime("%Y%m%d_%H%M%S")
        try:
            upload_storage = _storage()
            stored = await run_in_threadpool(
                upload_storage.put_material,
                owner_id=current_user.id,
                timestamp=timestamp,
                original_name=sanitized_original_filename,
                content=content_bytes,
            )
        except (UploadStorageError, OSError, ValueError) as exc:
            raise APIException(error_code=ErrorCode.SERVICE_UNAVAILABLE, status_code=503) from exc
        file_path = stored.reference
        logger.info("Material source saved (%s bytes)", len(content_bytes))

        quota_period_start = None
        if use_trial:
            if not quota_service.reserve_material_trial(session, current_user.id, commit=False):
                session.rollback()
                raise FeatureNotIncludedException(feature_type="material_decompose")
        else:
            quota_period_start = quota_service.reserve_feature_quota(
                session, current_user.id, "material_decompose", commit=False,
            )
            if quota_period_start is None:
                session.rollback()
                _, used, limit = quota_service.check_feature_quota(
                    session, current_user.id, "material_decompose",
                )
                raise QuotaExceededException(feature_type="material_decompose", used=used, limit=limit)

        source_meta = {
            "file_path": file_path,
            "file_size": len(content_bytes),
            "char_count": char_count,
            "chapter_count": analysis.chapter_count,
            "encoding": analysis.encoding,
            "original_filename": original_filename,
        }
        if trial_chapter_limit is not None:
            source_meta["trial_chapter_limit"] = trial_chapter_limit
            source_meta["source_chapter_count"] = analysis.source_chapter_count
            source_meta["source_char_count"] = analysis.source_char_count
        novel = Novel(
            user_id=current_user.id,
            title=novel_title,
            author=author,
            source_meta=json.dumps(source_meta),
        )
        session.add(novel)
        session.flush()

        job = IngestionJob(
            novel_id=novel.id,
            source_path=file_path,
            status="pending",
            total_chapters=0,
            processed_chapters=0,
        )
        job.update_stage_progress("queue", "pending", message="等待调度")
        if trial_chapter_limit is not None:
            IngestionJobsService.set_billing(
                job,
                quota_charged=True,
                quota_refunded=False,
                quota_mode="trial",
                chapter_limit=trial_chapter_limit,
            )
        else:
            IngestionJobsService.set_billing(
                job,
                quota_charged=True,
                quota_refunded=False,
                quota_period_start=_quota_period_iso(quota_period_start),
            )
        session.add(job)
        session.flush()
        job_id = job.id
        commit_attempted = True
        session.commit()
        job_persisted = True
        session.refresh(job)

        flow_run_id = await _start_flow_deployment(
            file_path=file_path,
            novel_title=novel_title,
            author=author,
            user_id=str(current_user.id),
            novel_id=novel.id,
            job_id=job.id,
        )

        if flow_run_id is None:
            raise APIException(
                error_code=ErrorCode.SERVICE_UNAVAILABLE,
                status_code=503,
                detail=DISPATCH_FAILURE_MESSAGE,
            )

        dispatch_accepted = True
        logger.info(
            "Novel ingestion dispatched: novel_id=%s, job_id=%s, flow_run_id=%s",
            novel.id,
            job.id,
            flow_run_id,
        )
        return MaterialUploadResponse(
            novel_id=novel.id,
            title=novel.title,
            job_id=job.id,
            status="pending",
            message="Novel upload successful, decomposition started",
        )
    except Exception:
        session.rollback()
        if not dispatch_accepted:
            if job_persisted and job_id is not None:
                try:
                    _mark_job_dispatch_failed(session, job_id)
                except Exception:
                    session.rollback()
                    logger.error(
                        "Failed to persist material upload dispatch failure: job_id=%s",
                        job_id,
                        exc_info=True,
                    )
            elif file_path is not None and upload_storage is not None and not commit_attempted:
                # This request owns the new file and no DB commit was attempted.
                # Preserve it after an ambiguous commit failure: a durable job
                # may reference it and watchdog reconciliation needs that source.
                try:
                    upload_storage.delete(
                        file_path,
                        kind="material",
                        owner_id=current_user.id,
                    )
                except (UploadStorageError, OSError):
                    logger.warning("Failed to remove uncommitted material source")
        raise
    finally:
        if upload_storage is not None:
            getattr(upload_storage, "close", lambda: None)()


# ==================== Retry Endpoints ====================

@router.post("/{novel_id}/retry")
async def retry_material_job(
    novel_id: int,
    current_user: User = Depends(get_current_active_user),
    _rate_limit: int = Depends(
        require_user_rate_limit(
            "materials_retry",
            RETRY_RATE_LIMIT_MAX_REQUESTS,
            RETRY_RATE_LIMIT_WINDOW_SECONDS,
        )
    ),
    session: Session = Depends(get_session),
):
    """
    Retry failed decomposition task.

    Creates a new ingestion job and resumes its incomplete capabilities. Every
    retry is charged one decomposition: platform failures refund the failed
    job's quota when they happen (see IngestionJobsService.fail_job), so a
    retry is never free and a refunded job cannot be retried for free.
    """
    # Verify novel ownership and soft delete check
    _get_novel_or_404(session, novel_id, current_user.id)

    # Get latest job
    latest_job = session.exec(
        select(IngestionJob)
        .where(IngestionJob.novel_id == novel_id)
        .order_by(IngestionJob.created_at.desc())
    ).first()

    if not latest_job:
        raise APIException(error_code=ErrorCode.FILE_NOT_FOUND, status_code=404)

    IngestionJobsService().reconcile_stale_job(session, latest_job)
    if (
        latest_job.status == "failed"
        and latest_job.error_message in REFUNDABLE_JOB_ERROR_CODES
        and IngestionJobsService.get_billing(latest_job).get("quota_charged") is True
    ):
        raise APIException(error_code=ErrorCode.SERVICE_UNAVAILABLE, status_code=503)

    if latest_job.status in {"pending", "processing"}:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=409,
            detail="Decomposition job is already running",
        )

    # Failed and partially completed jobs can resume their incomplete work.
    if latest_job.status not in {"failed", "completed_with_errors"}:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400,
        )

    if not quota_service.has_feature_access(
        session, current_user.id, "materials_library_access"
    ):
        raise FeatureNotIncludedException(feature_type="material_decompose")

    check_quota("material_decompose", session, current_user.id)

    quota_period_start = quota_service.reserve_feature_quota(
        session, current_user.id, "material_decompose", commit=False,
    )

    if quota_period_start is None:
        session.rollback()
        _allowed, used, limit = quota_service.check_feature_quota(
            session, current_user.id, "material_decompose"
        )
        # This request did not charge quota, so it must not create a new
        # runnable job, dispatch, or refund. Raise even if a concurrent
        # change makes the advisory check report allowed=True.
        raise QuotaExceededException(
            feature_type="material_decompose",
            used=used,
            limit=limit,
        )
    new_job: IngestionJob | None = None
    job_persisted = False
    try:
        # Match migration's lock order so a retry observes one current source
        # reference and persists that same reference into its new job.
        locked_novel = session.exec(
            select(Novel).where(Novel.id == novel_id).with_for_update()
        ).one()
        locked_latest_job = session.exec(
            select(IngestionJob).where(IngestionJob.id == latest_job.id).with_for_update()
        ).one()
        source_meta = {}
        if locked_novel.source_meta:
            with contextlib.suppress(Exception):
                parsed = json.loads(locked_novel.source_meta)
                if isinstance(parsed, dict):
                    source_meta = parsed
        current_source_path = source_meta.get("file_path", locked_latest_job.source_path)
        # A free-trial book stores only its first chapters; a retry (after
        # upgrading) resumes those and never pretends to cover the whole book.
        trial_chapter_limit = source_meta.get("trial_chapter_limit")

        # Two retries that read the same failed job cannot both persist a new
        # charge/job. Claim and reservation roll back together for the loser.
        # Advance even when the clock is frozen or moves backwards, otherwise
        # an unchanged timestamp would let a second stale claim succeed.
        claim_time = max(
            utcnow(),
            normalize_datetime_to_utc(latest_job.updated_at) + timedelta(microseconds=1),
        )
        claim = session.exec(
            update(IngestionJob).where(
                IngestionJob.id == latest_job.id,
                IngestionJob.status == latest_job.status,
                IngestionJob.updated_at == latest_job.updated_at,
                IngestionJob.stage_progress == latest_job.stage_progress,
            ).values(updated_at=claim_time).execution_options(synchronize_session=False)
        )
        if claim.rowcount != 1:
            raise APIException(error_code=ErrorCode.VALIDATION_ERROR, status_code=409)

        # Create a runnable job only after the atomic quota decision succeeds.
        new_job = IngestionJob(
            novel_id=novel_id,
            source_path=current_source_path,
            status="pending",
            total_chapters=0,
            processed_chapters=0,
        )
        new_job.update_stage_progress("queue", "pending", message="等待重试调度")
        IngestionJobsService.set_billing(
            new_job,
            quota_charged=True,
            quota_refunded=False,
            quota_period_start=_quota_period_iso(quota_period_start),
        )
        if isinstance(trial_chapter_limit, int) and not isinstance(trial_chapter_limit, bool):
            IngestionJobsService.set_billing(new_job, chapter_limit=trial_chapter_limit)
        session.add(new_job)
        session.commit()
        job_persisted = True
        session.refresh(new_job)

        file_path = current_source_path

        flow_run_id = await _start_flow_deployment(
            file_path=file_path,
            novel_title=locked_novel.title,
            author=locked_novel.author,
            user_id=str(current_user.id),
            novel_id=novel_id,
            job_id=new_job.id,
        )
        if flow_run_id is None:
            raise APIException(
                error_code=ErrorCode.SERVICE_UNAVAILABLE,
                status_code=503,
                detail=DISPATCH_FAILURE_MESSAGE,
            )
    except Exception:
        session.rollback()
        if job_persisted and new_job is not None and new_job.id is not None:
            try:
                _mark_job_dispatch_failed(session, new_job.id)
            except Exception:
                session.rollback()
                logger.error(
                    "Failed to persist material retry dispatch failure: job_id=%s",
                    new_job.id,
                    exc_info=True,
                )
        raise

    assert new_job.id is not None
    logger.info(
        "Material job retry dispatched: novel_id=%s, new_job_id=%s, flow_run_id=%s",
        novel_id,
        new_job.id,
        flow_run_id,
    )

    return {
        "message": "Retry started successfully",
        "job_id": new_job.id,
        "status": "pending",
    }


__all__ = ["router"]
