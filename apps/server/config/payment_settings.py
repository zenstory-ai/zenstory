"""Validated environment configuration for Zpay checkout."""

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

ZPAY_CHECKOUT_ACTION = "https://zpayz.cn/submit.php"
ZPAY_ORDER_QUERY_ENDPOINT = "https://zpayz.cn/api.php"
ZPAY_NOTIFY_PATH = "/api/v1/payments/zpay/notify"


def _valid_callback_url(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and bool(parsed.path)
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def _hostname(value: str | None) -> str | None:
    if not value:
        return None
    try:
        hostname = urlsplit(value).hostname
    except ValueError:
        return None
    return hostname.lower() if hostname else None


@dataclass(frozen=True)
class PaymentSettings:
    enabled: bool
    pid: str
    key: str
    notify_url: str
    return_url: str
    cid: str | None
    frontend_url: str | None = None

    @property
    def notify_url_valid(self) -> bool:
        return not self.notify_url_problems()

    @property
    def return_url_valid(self) -> bool:
        return _valid_callback_url(self.return_url)

    def notify_url_problems(self) -> list[str]:
        """Why Zpay could not reach the notify endpoint with this URL."""
        if not _valid_callback_url(self.notify_url):
            return ["ZPAY_NOTIFY_URL must be an https URL without query or credentials"]
        problems = []
        if urlsplit(self.notify_url).path != ZPAY_NOTIFY_PATH:
            problems.append(f"ZPAY_NOTIFY_URL path must be exactly {ZPAY_NOTIFY_PATH}")
        frontend_host = _hostname(self.frontend_url)
        if frontend_host and _hostname(self.notify_url) == frontend_host:
            # The web frontend host does not serve /api; callbacks would 404 there.
            problems.append("ZPAY_NOTIFY_URL host must be the API host, not FRONTEND_URL host")
        return problems

    def configuration_problems(self) -> list[str]:
        """Human-readable checkout configuration problems; never includes secrets."""
        problems = []
        if not self.pid:
            problems.append("ZPAY_PID is missing")
        if not self.key:
            problems.append("ZPAY_KEY is missing")
        problems.extend(self.notify_url_problems())
        if not self.return_url_valid:
            problems.append("ZPAY_RETURN_URL must be an https URL without query or credentials")
        return problems

    @property
    def credentials_configured(self) -> bool:
        # Existing orders must remain fulfillable even if checkout URLs are later
        # changed or temporarily invalid. Callback verification only needs these.
        return bool(self.pid and self.key)

    @property
    def checkout_configured(self) -> bool:
        return not self.configuration_problems()

    @property
    def checkout_enabled(self) -> bool:
        return self.enabled and self.checkout_configured


def get_payment_settings() -> PaymentSettings:
    return PaymentSettings(
        enabled=os.getenv("ZPAY_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        pid=os.getenv("ZPAY_PID", "").strip(),
        key=os.getenv("ZPAY_KEY", "").strip(),
        notify_url=os.getenv("ZPAY_NOTIFY_URL", "").strip(),
        return_url=os.getenv("ZPAY_RETURN_URL", "").strip(),
        cid=os.getenv("ZPAY_CID", "").strip() or None,
        frontend_url=os.getenv("FRONTEND_URL", "").strip() or None,
    )
