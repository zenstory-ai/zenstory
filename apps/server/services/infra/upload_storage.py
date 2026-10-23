"""Private local/S3-compatible storage for material sources and feedback images."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from config.upload_storage import UploadStorageSettings
from utils.logger import get_logger

logger = get_logger(__name__)

MATERIAL_PREFIX = "material"
FEEDBACK_PREFIX = "feedback"
PREFLIGHT_PREFIX = "preflight"
SAFE_OWNER_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
OPAQUE_NAME_RE = re.compile(r"^[0-9a-f]{32}\.[a-z0-9]{1,8}$")
MAX_MATERIAL_BYTES = 20 * 1024 * 1024
MAX_FEEDBACK_BYTES = 5 * 1024 * 1024
MAX_ERROR_BODY_BYTES = 4096
S3_ERROR_CODE_RE = re.compile(rb"<Code>([A-Za-z]{1,64})</Code>")
# Fixed, secret-free failure classes keyed by the S3 error code. A 403 alone does
# not mean the secret is wrong: only SignatureDoesNotMatch says the signing key
# differs from the provider's; other 401/403 responses fall back to "auth".
S3_ERROR_REASONS = {
    "SignatureDoesNotMatch": "signature",
    "InvalidAccessKeyId": "auth",
    "AccessDenied": "auth",
    "AllAccessDisabled": "auth",
    "AccountProblem": "auth",
    "ExpiredToken": "auth",
    "InvalidToken": "auth",
    "RequestTimeTooSkewed": "clock",
    "AuthorizationHeaderMalformed": "config",
    "NoSuchBucket": "config",
    "PermanentRedirect": "config",
}


class UploadStorageError(RuntimeError):
    """A private storage operation failed or a reference was unsafe."""

    def __init__(self, message: str, *, reason: str | None = None, status: int | None = None):
        super().__init__(message)
        self.reason = reason
        self.status = status


class UploadReferenceError(UploadStorageError):
    """A stored reference, owner, prefix, or local path was not trusted."""


class UploadNotFoundError(UploadStorageError):
    """A trusted private object does not exist."""


class UploadConflictError(UploadStorageError):
    """A no-overwrite PUT found an existing object at the generated key."""


@dataclass(frozen=True)
class StoredObject:
    reference: str
    size: int
    sha256: str
    created: bool = True


@dataclass(frozen=True)
class StorageProbeResult:
    ok: bool
    step: str
    reason: str | None
    cleanup: str
    key: str


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_suffix(suffix: str) -> str:
    normalized = suffix.lower().lstrip(".")
    if not re.fullmatch(r"[a-z0-9]{1,8}", normalized):
        raise UploadReferenceError("unsafe upload suffix")
    return normalized


def _s3_error_code(response: httpx.Response) -> str | None:
    """Read a known S3 error code from a bounded raw prefix of a failed, unread response."""
    encoding = response.headers.get("content-encoding", "").strip().lower()
    if encoding not in {"", "identity"}:
        # Decompressing could inflate a small body without bound: classify by status only.
        return None
    body = bytearray()
    try:
        # Raw transport chunks: at most one read past the limit is ever pulled.
        for chunk in response.iter_raw():
            body += chunk[: MAX_ERROR_BODY_BYTES - len(body)]
            if len(body) >= MAX_ERROR_BODY_BYTES:
                break
    except httpx.StreamConsumed:
        # A transport that hands back an already-read body (in-memory mocks).
        body = bytearray(response.content[:MAX_ERROR_BODY_BYTES])
    except httpx.HTTPError:
        return None
    match = S3_ERROR_CODE_RE.search(body)
    code = match.group(1).decode() if match else None
    return code if code in S3_ERROR_REASONS else None


def _storage_failure(method: str, reason: str, *, status: int | None = None, code: str | None = None):
    # Only fixed values reach the log: never the response body, URL or credentials.
    logger.warning(
        "Private object storage %s failed: reason=%s status=%s code=%s",
        method,
        reason,
        status if status is not None else "-",
        code or "-",
    )
    return UploadStorageError(
        f"private object storage {method} failed ({reason})", reason=reason, status=status
    )


def _safe_owner(owner_id: str) -> str:
    if not SAFE_OWNER_RE.fullmatch(owner_id):
        raise UploadReferenceError("unsafe material owner id")
    return owner_id


class LocalUploadStorage:
    def __init__(self, *, material_root: Path, feedback_root: Path):
        self.material_root = material_root.resolve()
        self.feedback_root = feedback_root.resolve()

    @staticmethod
    def _safe_child(root: Path, name: str) -> Path:
        if not name or name != os.path.basename(name) or "\x00" in name:
            raise UploadReferenceError("unsafe upload object name")
        path = (root / name).resolve()
        if os.path.commonpath([str(root), str(path)]) != str(root):
            raise UploadReferenceError("upload path escapes configured root")
        return path

    @staticmethod
    def _write_unique(root: Path, name_factory, content: bytes) -> StoredObject:
        root.mkdir(parents=True, exist_ok=True)
        for _ in range(10):
            path = LocalUploadStorage._safe_child(root, name_factory())
            try:
                with path.open("xb") as handle:
                    handle.write(content)
            except FileExistsError:
                continue
            return StoredObject(str(path), len(content), _sha256_bytes(content))
        raise UploadStorageError("failed to allocate a unique upload path")

    def put_material(
        self,
        *,
        owner_id: str,
        timestamp: str,
        original_name: str,
        content: bytes,
        object_name: str | None = None,
    ) -> StoredObject:
        owner = _safe_owner(owner_id)
        if object_name is not None:
            raise UploadReferenceError("local material storage does not accept object_name")
        return self._write_unique(
            self.material_root,
            lambda: f"{owner}_{timestamp}_{secrets.token_hex(8)}_{original_name}",
            content,
        )

    def put_feedback(
        self, *, suffix: str, content: bytes, object_name: str | None = None
    ) -> StoredObject:
        safe_suffix = _safe_suffix(suffix)
        if object_name is not None and not OPAQUE_NAME_RE.fullmatch(object_name):
            raise UploadReferenceError("feedback object name is not opaque")
        if object_name is not None and Path(object_name).suffix != f".{safe_suffix}":
            raise UploadReferenceError("feedback object suffix mismatch")
        return self._write_unique(
            self.feedback_root,
            lambda: object_name or f"{secrets.token_hex(16)}.{safe_suffix}",
            content,
        )

    def material_reference_for_name(self, *, owner_id: str, object_name: str) -> str:
        owner = _safe_owner(owner_id)
        if not object_name.startswith(f"{owner}_"):
            raise UploadReferenceError("material object does not belong to owner")
        return str(self._safe_child(self.material_root, object_name))

    def read_material_name(self, *, owner_id: str, object_name: str) -> bytes:
        path = Path(self.material_reference_for_name(owner_id=owner_id, object_name=object_name))
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise UploadNotFoundError("private upload object not found") from exc

    def read_feedback(self, reference: str) -> bytes:
        path = Path(reference).expanduser().resolve()
        if os.path.commonpath([str(self.feedback_root), str(path)]) != str(self.feedback_root):
            raise UploadReferenceError("feedback path is outside configured root")
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise UploadNotFoundError("private upload object not found") from exc

    def feedback_reference_exists(self, reference: str | None) -> bool:
        if not reference or reference.startswith("s3://"):
            return False
        try:
            path = Path(reference).expanduser().resolve()
            return (
                os.path.commonpath([str(self.feedback_root), str(path)])
                == str(self.feedback_root)
                and path.is_file()
            )
        except (OSError, ValueError):
            return False

    def delete(self, reference: str, *, kind: str, owner_id: str | None = None) -> None:
        root = self.material_root if kind == MATERIAL_PREFIX else self.feedback_root
        path = Path(reference).resolve()
        if kind == MATERIAL_PREFIX and (
            owner_id is None or not path.name.startswith(f"{_safe_owner(owner_id)}_")
        ):
            raise UploadReferenceError("material object does not belong to owner")
        if os.path.commonpath([str(root), str(path)]) != str(root):
            raise UploadReferenceError("refusing to delete outside configured upload root")
        path.unlink(missing_ok=True)

    def close(self) -> None:
        return None


class S3UploadStorage:
    def __init__(
        self,
        settings: UploadStorageSettings,
        *,
        client: httpx.Client | None = None,
    ):
        if settings.backend != "s3":
            raise ValueError("S3UploadStorage requires the s3 backend")
        self.settings = settings
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)

    @property
    def bucket(self) -> str:
        assert self.settings.bucket is not None
        return self.settings.bucket

    def _uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    def _key_from_uri(
        self, reference: str, *, kind: str, owner_id: str | None = None
    ) -> str:
        parsed = urlsplit(reference)
        if (
            parsed.scheme != "s3"
            or parsed.netloc != self.bucket
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise UploadReferenceError("invalid private S3 upload reference")
        key = parsed.path.lstrip("/")
        parts = key.split("/")
        if kind == MATERIAL_PREFIX:
            if owner_id is None or len(parts) != 3:
                raise UploadReferenceError("invalid material object key")
            owner = _safe_owner(owner_id)
            if parts[0] != MATERIAL_PREFIX or parts[1] != owner:
                raise UploadReferenceError("material object does not belong to owner")
            object_name = parts[2]
        elif kind == FEEDBACK_PREFIX:
            if len(parts) != 2 or parts[0] != FEEDBACK_PREFIX:
                raise UploadReferenceError("invalid feedback object key")
            object_name = parts[1]
        else:
            raise UploadReferenceError("invalid upload object kind")
        if not OPAQUE_NAME_RE.fullmatch(object_name):
            raise UploadReferenceError("upload object name is not opaque")
        return key

    def _signed_request(
        self,
        method: str,
        key: str,
        content: bytes = b"",
        *,
        max_response_bytes: int | None = None,
    ) -> httpx.Response:
        assert self.settings.endpoint is not None
        assert self.settings.region is not None
        assert self.settings.access_key_id is not None
        assert self.settings.secret_access_key is not None
        now = datetime.now(UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload_hash = _sha256_bytes(content)
        endpoint = urlsplit(self.settings.endpoint)
        if self.settings.url_style == "virtual":
            host = f"{self.bucket}.{endpoint.netloc}"
            canonical_uri = "/" + quote(key, safe="/-_.~")
        else:
            host = endpoint.netloc
            canonical_uri = "/" + quote(f"{self.bucket}/{key}", safe="/-_.~")
        canonical_headers = (
            f"host:{host}\n"
            f"x-amz-content-sha256:{payload_hash}\n"
            f"x-amz-date:{amz_date}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join(
            [method, canonical_uri, "", canonical_headers, signed_headers, payload_hash]
        )
        scope = f"{date_stamp}/{self.settings.region}/s3/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode()).hexdigest(),
            ]
        )

        def sign(key_bytes: bytes, message: str) -> bytes:
            return hmac.new(key_bytes, message.encode(), hashlib.sha256).digest()

        date_key = sign(f"AWS4{self.settings.secret_access_key}".encode(), date_stamp)
        region_key = sign(date_key, self.settings.region)
        service_key = sign(region_key, "s3")
        signing_key = sign(service_key, "aws4_request")
        signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
        authorization = (
            f"AWS4-HMAC-SHA256 Credential={self.settings.access_key_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        url = f"{endpoint.scheme}://{host}{canonical_uri}"
        request_headers = {
            "Authorization": authorization,
            "Host": host,
            "X-Amz-Content-Sha256": payload_hash,
            "X-Amz-Date": amz_date,
            # Unsigned; keeps error bodies readable without decompression.
            "Accept-Encoding": "identity",
        }
        if method == "PUT":
            request_headers["If-None-Match"] = "*"
        try:
            request = self.client.build_request(
                method,
                url,
                headers=request_headers,
                content=content if method == "PUT" else None,
            )
            # Stream every method so a failed response is never buffered whole.
            response = self.client.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise _storage_failure(method, "network") from exc
        if method == "PUT" and response.status_code == 412:
            response.close()
            raise UploadConflictError(
                "private upload object already exists", reason="conflict", status=412
            )
        if response.status_code < 200 or response.status_code >= 300:
            status = response.status_code
            try:
                code = _s3_error_code(response)
            finally:
                response.close()
            if method == "GET" and status == 404 and code != "NoSuchBucket":
                raise UploadNotFoundError(
                    "private upload object not found", reason="not_found", status=404
                )
            if code is not None:
                reason = S3_ERROR_REASONS[code]
            elif status in {401, 403}:
                reason = "auth"
            else:
                reason = "provider"
            raise _storage_failure(method, reason, status=status, code=code)
        if method == "GET":
            if max_response_bytes is None:
                response.close()
                raise UploadStorageError("private object download limit is missing")
            chunks: list[bytes] = []
            size = 0
            try:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_response_bytes:
                        raise UploadStorageError(
                            "private object exceeds download limit", reason="integrity"
                        )
                    chunks.append(chunk)
                return httpx.Response(
                    status_code=response.status_code,
                    headers=response.headers,
                    content=b"".join(chunks),
                    request=request,
                )
            except httpx.HTTPError as exc:
                raise _storage_failure(method, "network") from exc
            finally:
                response.close()
        response.close()
        return response

    def _put(self, key: str, content: bytes) -> StoredObject:
        created = True
        try:
            self._signed_request("PUT", key, content)
        except UploadConflictError:
            if self._signed_request("GET", key, max_response_bytes=len(content)).content != content:
                raise
            created = False
        return StoredObject(self._uri(key), len(content), _sha256_bytes(content), created)

    def put_material(
        self,
        *,
        owner_id: str,
        timestamp: str,
        original_name: str,
        content: bytes,
        object_name: str | None = None,
    ) -> StoredObject:
        del timestamp
        owner = _safe_owner(owner_id)
        suffix = _safe_suffix(Path(original_name).suffix)
        if object_name is not None and not OPAQUE_NAME_RE.fullmatch(object_name):
            raise UploadReferenceError("material object name is not opaque")
        if object_name is not None and Path(object_name).suffix != f".{suffix}":
            raise UploadReferenceError("material object suffix mismatch")
        key = f"{MATERIAL_PREFIX}/{owner}/{object_name or f'{secrets.token_hex(16)}.{suffix}'}"
        return self._put(key, content)

    def put_feedback(
        self, *, suffix: str, content: bytes, object_name: str | None = None
    ) -> StoredObject:
        safe_suffix = _safe_suffix(suffix)
        if object_name is not None and not OPAQUE_NAME_RE.fullmatch(object_name):
            raise UploadReferenceError("feedback object name is not opaque")
        if object_name is not None and Path(object_name).suffix != f".{safe_suffix}":
            raise UploadReferenceError("feedback object suffix mismatch")
        key = f"{FEEDBACK_PREFIX}/{object_name or f'{secrets.token_hex(16)}.{safe_suffix}'}"
        return self._put(key, content)

    def material_reference_for_name(self, *, owner_id: str, object_name: str) -> str:
        reference = self._uri(f"{MATERIAL_PREFIX}/{_safe_owner(owner_id)}/{object_name}")
        self._key_from_uri(reference, kind=MATERIAL_PREFIX, owner_id=owner_id)
        return reference

    def read_material_name(self, *, owner_id: str, object_name: str) -> bytes:
        reference = self.material_reference_for_name(owner_id=owner_id, object_name=object_name)
        key = self._key_from_uri(reference, kind=MATERIAL_PREFIX, owner_id=owner_id)
        return self._signed_request("GET", key, max_response_bytes=MAX_MATERIAL_BYTES).content

    def read_material_reference(self, reference: str, *, owner_id: str) -> bytes:
        key = self._key_from_uri(reference, kind=MATERIAL_PREFIX, owner_id=owner_id)
        return self._signed_request("GET", key, max_response_bytes=MAX_MATERIAL_BYTES).content

    def read_feedback(self, reference: str) -> bytes:
        key = self._key_from_uri(reference, kind=FEEDBACK_PREFIX)
        return self._signed_request("GET", key, max_response_bytes=MAX_FEEDBACK_BYTES).content

    def feedback_reference_exists(self, reference: str | None) -> bool:
        if not reference:
            return False
        try:
            self._key_from_uri(reference, kind=FEEDBACK_PREFIX)
        except UploadReferenceError:
            return False
        return True

    def delete(self, reference: str, *, kind: str, owner_id: str | None = None) -> None:
        key = self._key_from_uri(reference, kind=kind, owner_id=owner_id)
        self._signed_request("DELETE", key)

    def probe_round_trip(self) -> StorageProbeResult:
        """PUT, GET and DELETE one synthetic object through the production signing path.

        The object gets a fresh random name under ``preflight/`` and the no-overwrite
        PUT, so the probe never touches an upload. It is deleted unless the PUT was
        rejected outright (nothing written) or found someone else's object.
        """
        key = f"{PREFLIGHT_PREFIX}/{secrets.token_hex(16)}.bin"
        content = secrets.token_bytes(64)
        step = "put"
        failure: UploadStorageError | None = None
        try:
            self._signed_request("PUT", key, content)
            step = "get"
            body = self._signed_request("GET", key, max_response_bytes=len(content)).content
            step = "verify"
            if _sha256_bytes(body) != _sha256_bytes(content):
                raise UploadStorageError("synthetic object bytes differ", reason="integrity")
        except UploadStorageError as exc:
            failure = exc
        cleanup = "not_needed"
        rejected_put = (
            step == "put" and failure is not None and failure.status is not None and failure.status < 500
        )
        if not rejected_put:
            try:
                self._signed_request("DELETE", key)
                cleanup = "deleted"
            except UploadStorageError as exc:
                cleanup = "failed"
                if failure is None:
                    failure, step = exc, "delete"
        return StorageProbeResult(
            ok=failure is None,
            step="done" if failure is None else step,
            reason=None if failure is None else failure.reason,
            cleanup=cleanup,
            key=key,
        )

    def close(self) -> None:
        self.client.close()


def get_upload_storage(
    *,
    material_root: str | Path,
    feedback_root: str | Path,
    settings: UploadStorageSettings | None = None,
    client: httpx.Client | None = None,
) -> LocalUploadStorage | S3UploadStorage:
    resolved_settings = settings or UploadStorageSettings.from_environment()
    if resolved_settings.backend == "s3":
        return S3UploadStorage(resolved_settings, client=client)
    return LocalUploadStorage(
        material_root=Path(material_root),
        feedback_root=Path(feedback_root),
    )
