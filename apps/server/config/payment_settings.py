"""Validated environment configuration for Zpay checkout."""

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

ZPAY_CHECKOUT_ACTION = "https://zpayz.cn/submit.php"


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


@dataclass(frozen=True)
class PaymentSettings:
    enabled: bool
    pid: str
    key: str
    notify_url: str
    return_url: str
    cid: str | None

    @property
    def notify_url_valid(self) -> bool:
        return _valid_callback_url(self.notify_url)

    @property
    def return_url_valid(self) -> bool:
        return _valid_callback_url(self.return_url)

    @property
    def credentials_configured(self) -> bool:
        # Existing orders must remain fulfillable even if checkout URLs are later
        # changed or temporarily invalid. Callback verification only needs these.
        return bool(self.pid and self.key)

    @property
    def checkout_configured(self) -> bool:
        return (
            self.credentials_configured
            and self.notify_url_valid
            and self.return_url_valid
        )

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
    )
