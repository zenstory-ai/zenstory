"""Strict configuration for private uploaded-object storage."""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

S3_REQUIRED_ENV = {
    "endpoint": "UPLOAD_S3_ENDPOINT",
    "region": "UPLOAD_S3_REGION",
    "bucket": "UPLOAD_S3_BUCKET",
    "access_key_id": "UPLOAD_S3_ACCESS_KEY_ID",
    "secret_access_key": "UPLOAD_S3_SECRET_ACCESS_KEY",
    "url_style": "UPLOAD_S3_URL_STYLE",
}
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


@dataclass(frozen=True)
class UploadStorageSettings:
    backend: str
    endpoint: str | None = None
    region: str | None = None
    bucket: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    url_style: str | None = None

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> UploadStorageSettings:
        values = os.environ if environment is None else environment
        backend = values.get("UPLOAD_STORAGE_BACKEND", "local").strip().lower() or "local"
        if backend == "local":
            return cls(backend="local")
        if backend != "s3":
            raise ValueError("UPLOAD_STORAGE_BACKEND must be 'local' or 's3'")

        configured = {
            field: values.get(env_name, "").strip()
            for field, env_name in S3_REQUIRED_ENV.items()
        }
        missing = [S3_REQUIRED_ENV[field] for field, value in configured.items() if not value]
        if missing:
            raise ValueError(f"missing S3 upload storage configuration: {', '.join(sorted(missing))}")

        endpoint = configured["endpoint"].rstrip("/")
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("UPLOAD_S3_ENDPOINT must be an HTTP(S) origin without credentials or a path")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("UPLOAD_S3_ENDPOINT must use HTTPS outside localhost")

        bucket = configured["bucket"]
        if not BUCKET_RE.fullmatch(bucket) or ".." in bucket or ".-" in bucket or "-." in bucket:
            raise ValueError("UPLOAD_S3_BUCKET is not a valid restricted bucket name")
        try:
            ipaddress.ip_address(bucket)
        except ValueError:
            pass
        else:
            raise ValueError("UPLOAD_S3_BUCKET must not be an IP address")
        if configured["url_style"] not in {"virtual", "path"}:
            raise ValueError("UPLOAD_S3_URL_STYLE must be 'virtual' or 'path'")

        return cls(backend="s3", endpoint=endpoint, **{k: v for k, v in configured.items() if k != "endpoint"})
