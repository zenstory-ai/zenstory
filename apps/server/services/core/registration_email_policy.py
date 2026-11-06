"""Email domains that cannot be used for a new account (REGISTRATION_BLOCKED_EMAIL_DOMAINS)."""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from email_validator import EmailNotValidError, validate_email

from core.error_codes import ErrorCode
from core.error_handler import APIException
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)


def _ascii_domain(domain: str) -> str | None:
    """A lower-case ASCII domain, or None when the text cannot be one."""
    try:
        return validate_email(f"x@{domain}", check_deliverability=False, test_environment=True).ascii_domain.lower()
    except EmailNotValidError:
        return None


@lru_cache(maxsize=4)
def _blocked_domains(raw: str) -> frozenset[str]:
    """Parse the comma-separated setting. Empty means nothing is blocked; a bad entry is skipped."""
    domains = set()
    for item in raw.split(","):
        entry = item.strip().removeprefix("*").strip(".@").strip()
        if not entry:
            continue
        domain = _ascii_domain(entry)
        if domain:
            domains.add(domain)
        else:
            log_with_context(logger, logging.WARNING, "registration_blocked_domain_entry_ignored")
    return frozenset(domains)


def is_registration_email_blocked(email: str) -> bool:
    """Whether the address's domain is a configured domain or a subdomain of one.

    Only the parsed domain is compared, label by label, so a lookalike or the same text
    elsewhere in the address never matches.
    """
    blocked = _blocked_domains(os.getenv("REGISTRATION_BLOCKED_EMAIL_DOMAINS", ""))
    if not blocked:
        return False
    local, separator, domain_text = str(email).strip().rpartition("@")
    if not separator or not local:
        return False
    domain = _ascii_domain(domain_text.rstrip("."))
    if domain is None:
        return False
    labels = domain.split(".")
    return any(".".join(labels[index:]) in blocked for index in range(len(labels)))


def ensure_registration_email_allowed(email: str, *, entry: str) -> None:
    """Refuse a blocked address with the neutral signup error."""
    if is_registration_email_blocked(email):
        log_with_context(logger, logging.INFO, "registration_email_domain_blocked", entry=entry)
        raise APIException(error_code=ErrorCode.AUTH_EMAIL_DOMAIN_NOT_ALLOWED, status_code=400)
