"""Validated environment configuration for Zpay and Creem checkouts."""

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

ZPAY_CHECKOUT_ACTION = "https://zpayz.cn/submit.php"
ZPAY_ORDER_QUERY_ENDPOINT = "https://zpayz.cn/api.php"
ZPAY_NOTIFY_PATH = "/api/v1/payments/zpay/notify"

CREEM_LIVE_API_BASE = "https://api.creem.io/v1"
CREEM_TEST_API_BASE = "https://test-api.creem.io/v1"
CREEM_WEBHOOK_PATH = "/api/v1/payments/creem/webhook"
PAYMENT_RETURN_PATH = "/dashboard/billing/payment-return"
CREEM_ALLOWED_CHECKOUT_HOSTS = frozenset({"creem.io", "www.creem.io", "checkout.creem.io"})
CREEM_TEST_KEY_PREFIX = "creem_test_"
CREEM_PRODUCT_ID_PREFIX = "prod_"


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


def _env_truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def get_payment_settings() -> PaymentSettings:
    return PaymentSettings(
        enabled=_env_truthy("ZPAY_ENABLED"),
        pid=os.getenv("ZPAY_PID", "").strip(),
        key=os.getenv("ZPAY_KEY", "").strip(),
        notify_url=os.getenv("ZPAY_NOTIFY_URL", "").strip(),
        return_url=os.getenv("ZPAY_RETURN_URL", "").strip(),
        cid=os.getenv("ZPAY_CID", "").strip() or None,
        frontend_url=os.getenv("FRONTEND_URL", "").strip() or None,
    )


@dataclass(frozen=True)
class CreemSettings:
    enabled: bool
    test_mode: bool
    api_key: str
    webhook_secret: str
    product_id_pro_month: str
    product_id_pro_year: str
    return_url: str
    frontend_url: str | None = None

    @property
    def api_base_url(self) -> str:
        return CREEM_TEST_API_BASE if self.test_mode else CREEM_LIVE_API_BASE

    @property
    def return_url_valid(self) -> bool:
        return not self.return_url_problems()

    def return_url_problems(self) -> list[str]:
        """Why Creem could not return the buyer to the frontend PaymentReturnPage."""
        if not _valid_callback_url(self.return_url):
            return ["CREEM_RETURN_URL must be an https URL without query or credentials"]
        if urlsplit(self.return_url).path != PAYMENT_RETURN_PATH:
            return [f"CREEM_RETURN_URL path must be exactly {PAYMENT_RETURN_PATH}"]
        return []

    def product_id_for_cycle(self, cycle: str) -> str | None:
        if cycle == "month":
            return self.product_id_pro_month or None
        if cycle == "year":
            return self.product_id_pro_year or None
        return None

    def configuration_problems(self) -> list[str]:
        """Human-readable Creem checkout problems; never includes secrets."""
        problems = []
        if not self.api_key:
            problems.append("CREEM_API_KEY is missing")
        elif self.test_mode and not self.api_key.startswith(CREEM_TEST_KEY_PREFIX):
            problems.append("CREEM_API_KEY must be a test key when CREEM_TEST_MODE=true")
        elif not self.test_mode and self.api_key.startswith(CREEM_TEST_KEY_PREFIX):
            problems.append("CREEM_API_KEY must not be a test key when CREEM_TEST_MODE=false")
        if not self.webhook_secret:
            problems.append("CREEM_WEBHOOK_SECRET is missing")
        if not self.product_id_pro_month or not self.product_id_pro_month.startswith(CREEM_PRODUCT_ID_PREFIX):
            problems.append("CREEM_PRODUCT_ID_PRO_MONTH must be a Creem prod_ identifier")
        if not self.product_id_pro_year or not self.product_id_pro_year.startswith(CREEM_PRODUCT_ID_PREFIX):
            problems.append("CREEM_PRODUCT_ID_PRO_YEAR must be a Creem prod_ identifier")
        if (
            self.product_id_pro_month
            and self.product_id_pro_year
            and self.product_id_pro_month == self.product_id_pro_year
        ):
            problems.append("CREEM_PRODUCT_ID_PRO_MONTH and CREEM_PRODUCT_ID_PRO_YEAR must differ")
        problems.extend(self.return_url_problems())
        return problems

    @property
    def credentials_configured(self) -> bool:
        """API key alone allows querying/syncing existing orders."""
        if not self.api_key:
            return False
        if self.test_mode and not self.api_key.startswith(CREEM_TEST_KEY_PREFIX):
            return False
        return self.test_mode or not self.api_key.startswith(CREEM_TEST_KEY_PREFIX)

    @property
    def webhook_configured(self) -> bool:
        return bool(self.webhook_secret)

    @property
    def checkout_configured(self) -> bool:
        return not self.configuration_problems()

    @property
    def checkout_enabled(self) -> bool:
        return self.enabled and self.checkout_configured


def _default_creem_return_url(frontend_url: str | None) -> str:
    explicit = os.getenv("CREEM_RETURN_URL", "").strip()
    if explicit:
        return explicit
    zpay_return = os.getenv("ZPAY_RETURN_URL", "").strip()
    if zpay_return:
        return zpay_return
    if frontend_url:
        return f"{frontend_url.rstrip('/')}{PAYMENT_RETURN_PATH}"
    return ""


def get_creem_settings() -> CreemSettings:
    frontend_url = os.getenv("FRONTEND_URL", "").strip() or None
    return CreemSettings(
        enabled=_env_truthy("CREEM_ENABLED"),
        test_mode=_env_truthy("CREEM_TEST_MODE"),
        api_key=os.getenv("CREEM_API_KEY", "").strip(),
        webhook_secret=os.getenv("CREEM_WEBHOOK_SECRET", "").strip(),
        product_id_pro_month=os.getenv("CREEM_PRODUCT_ID_PRO_MONTH", "").strip(),
        product_id_pro_year=os.getenv("CREEM_PRODUCT_ID_PRO_YEAR", "").strip(),
        return_url=_default_creem_return_url(frontend_url),
        frontend_url=frontend_url,
    )
