"""Constants for materials API."""

# Allowed file extensions for material upload
ALLOWED_EXTENSIONS = {".txt"}

# Maximum file size for uploads (20MB). 300,000 characters is at most ~1.2MB in
# UTF-8; the headroom covers padded or oddly encoded files without letting a
# request hold hundreds of MB in API memory. Keep in sync with
# apps/web/src/lib/materialUploadValidation.ts.
MAX_FILE_SIZE = 20 * 1024 * 1024

# Content-Length ceiling checked before the multipart body is parsed: the file
# plus multipart framing.
MAX_UPLOAD_REQUEST_BYTES = MAX_FILE_SIZE + 64 * 1024

# Maximum total characters for a single materials decomposition upload
MAX_TEXT_CHARACTERS = 300_000

# Per-user rate limits for endpoints that can start a decomposition.
UPLOAD_RATE_LIMIT_MAX_REQUESTS = 20
UPLOAD_RATE_LIMIT_WINDOW_SECONDS = 3600
RETRY_RATE_LIMIT_MAX_REQUESTS = 20
RETRY_RATE_LIMIT_WINDOW_SECONDS = 3600

__all__ = [
    "ALLOWED_EXTENSIONS",
    "MAX_FILE_SIZE",
    "MAX_TEXT_CHARACTERS",
    "MAX_UPLOAD_REQUEST_BYTES",
    "RETRY_RATE_LIMIT_MAX_REQUESTS",
    "RETRY_RATE_LIMIT_WINDOW_SECONDS",
    "UPLOAD_RATE_LIMIT_MAX_REQUESTS",
    "UPLOAD_RATE_LIMIT_WINDOW_SECONDS",
]
