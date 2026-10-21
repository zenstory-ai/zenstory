"""Where new accounts came from: first-touch acquisition recorded at signup.

The web app keeps the first visit that carried a signal (the internal ``?source=``,
UTM parameters, or an external referrer host) and sends it with the registration
(email sign-up body, or Google OAuth state). It is stored in the ``signup_success``
activation event's metadata under ``acquisition`` with a derived ``channel``, so no
schema change is needed. Only codes and host names are kept: no full URLs, query
strings or personal data.

The admin view counts the registration cohort of a window by channel and by source,
with the same activation (author-initiated AI use) and paid (paid order) definitions
as the growth dashboard. It shows signups only: visits are not counted server-side.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import timedelta
from typing import Any

from sqlmodel import Session, func, select

from config.datetime_utils import beijing_day_bounds, normalize_datetime_to_utc, utcnow
from models.activation_event import ACTIVATION_EVENT_SIGNUP_SUCCESS, ActivationEvent
from models.entities import User
from models.llm_usage import LLM_USAGE_SOURCE_SUGGEST, LLMUsageEvent
from models.payment import PaymentOrder

ACQUISITION_DAYS = (7, 14, 30, 90)
MAX_SOURCE_ROWS = 30

_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._\-]{0,63}$")
_HOST = re.compile(r"^[a-z0-9.\-]{1,253}$")
_PATH = re.compile(r"^/[A-Za-z0-9/_\-.~%]{0,199}$")
_TOKEN_FIELDS = ("source", "utm_source", "utm_medium", "utm_campaign")

CHANNELS = ("search", "ai", "social", "org_site", "referral", "campaign", "paid", "direct", "unknown")

_SEARCH_HOSTS = (
    "google.", "bing.com", "baidu.com", "sogou.com", "so.com", "sm.cn", "yandex.", "duckduckgo.com",
    "search.yahoo.", "naver.com", "ecosia.org", "brave.com",
)
_AI_HOSTS = (
    "chatgpt.com", "chat.openai.com", "perplexity.ai", "claude.ai", "gemini.google.com",
    "copilot.microsoft.com", "kimi.moonshot.cn", "kimi.com", "doubao.com", "chat.deepseek.com",
    "yuanbao.tencent.com", "tongyi.aliyun.com", "poe.com", "you.com", "phind.com",
)
_SOCIAL_HOSTS = (
    "t.co", "x.com", "twitter.com", "reddit.com", "weibo.com", "weibo.cn", "zhihu.com",
    "xiaohongshu.com", "xhslink.com", "bilibili.com", "douyin.com", "v2ex.com", "facebook.com",
    "instagram.com", "youtube.com", "linkedin.com", "discord.com", "t.me", "telegram.org",
    "github.com", "juejin.cn", "douban.com", "tieba.baidu.com", "news.ycombinator.com",
)
_ORG_HOSTS = ("zenstory.ai", "www.zenstory.ai", "geo-preview.zenstory.ai")
_PAID_MEDIUMS = {"cpc", "ppc", "paid", "paid_search", "paidsearch", "paid_social", "display", "cpm", "ads"}


def _host_matches(host: str, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        if pattern.endswith("."):
            if host.startswith(pattern) or f".{pattern}" in f".{host}":
                return True
        elif host == pattern or host.endswith(f".{pattern}"):
            return True
    return False


def classify_channel(acquisition: dict[str, Any]) -> str:
    """Coarse channel from already-normalized fields. Paid campaigns win, then the referrer."""
    medium = acquisition.get("utm_medium") or ""
    host = acquisition.get("referrer_host") or ""
    source = acquisition.get("source") or ""
    if medium in _PAID_MEDIUMS:
        return "paid"
    if host:
        if _host_matches(host, _AI_HOSTS):
            return "ai"
        if _host_matches(host, _SEARCH_HOSTS):
            return "search"
        if _host_matches(host, _SOCIAL_HOSTS):
            return "social"
        if host in _ORG_HOSTS:
            return "org_site"
    if source.startswith("org_"):
        return "org_site"
    if acquisition.get("utm_source"):
        return "campaign"
    if host:
        return "referral"
    return "direct"


def normalize_acquisition(raw: Any) -> dict[str, Any] | None:
    """Keep only well-formed codes, a host name and a landing path; add the channel."""
    if not isinstance(raw, dict):
        return None
    result: dict[str, Any] = {}
    for key in _TOKEN_FIELDS:
        value = raw.get(key)
        if isinstance(value, str):
            value = value.strip().lower()
            if _TOKEN.match(value):
                result[key] = value
    host = raw.get("referrer_host")
    if isinstance(host, str):
        host = host.strip().lower().rstrip(".")
        if _HOST.match(host) and "." in host:
            result["referrer_host"] = host
    path = raw.get("landing_path")
    if isinstance(path, str):
        path = path.split("?", 1)[0].split("#", 1)[0]
        if _PATH.match(path):
            result["landing_path"] = path
    result["channel"] = classify_channel(result)
    return result


def _source_label(acquisition: dict[str, Any]) -> str:
    return (
        acquisition.get("utm_source")
        or acquisition.get("referrer_host")
        or acquisition.get("source")
        or ""
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def get_acquisition_stats(session: Session, *, days: int) -> dict[str, Any]:
    """Signups of the window by channel and source, with activation and paid conversion."""
    now = normalize_datetime_to_utc(utcnow())
    period_start = beijing_day_bounds(now - timedelta(days=days - 1))[0]
    start, end = period_start.replace(tzinfo=None), now.replace(tzinfo=None)
    cohort = (User.is_superuser.is_(False), User.created_at >= start, User.created_at < end)

    rows = session.exec(
        select(User.id, ActivationEvent.event_metadata)
        .join(
            ActivationEvent,
            (ActivationEvent.user_id == User.id)
            & (ActivationEvent.event_name == ACTIVATION_EVENT_SIGNUP_SUCCESS),
            isouter=True,
        )
        .where(*cohort)
    ).all()

    activated_ids = set(
        session.exec(
            select(func.distinct(User.id))
            .join(LLMUsageEvent, LLMUsageEvent.user_id == User.id)
            .where(
                *cohort,
                LLMUsageEvent.source != LLM_USAGE_SOURCE_SUGGEST,
                LLMUsageEvent.is_backfilled.is_(False),
                LLMUsageEvent.occurred_at >= User.created_at,
                LLMUsageEvent.occurred_at < end,
            )
        ).all()
    )
    paid_ids = set(
        session.exec(
            select(func.distinct(User.id))
            .join(PaymentOrder, PaymentOrder.user_id == User.id)
            .where(
                *cohort,
                PaymentOrder.status == "paid",
                PaymentOrder.paid_at.is_not(None),
                PaymentOrder.paid_at >= User.created_at,
                PaymentOrder.paid_at < end,
            )
        ).all()
    )

    by_channel: dict[str, list[str]] = defaultdict(list)
    by_source: dict[tuple[str, str], list[str]] = defaultdict(list)
    seen: set[str] = set()
    for user_id, metadata in rows:
        if user_id in seen:
            continue
        seen.add(user_id)
        acquisition = metadata.get("acquisition") if isinstance(metadata, dict) else None
        if isinstance(acquisition, dict) and acquisition.get("channel") in CHANNELS:
            channel = acquisition["channel"]
            label = _source_label(acquisition)
        else:
            channel, label = "unknown", ""
        by_channel[channel].append(user_id)
        by_source[(channel, label)].append(user_id)

    def _counts(user_ids: list[str]) -> dict[str, Any]:
        signups = len(user_ids)
        activated = sum(1 for user_id in user_ids if user_id in activated_ids)
        paid = sum(1 for user_id in user_ids if user_id in paid_ids)
        return {
            "signups": signups,
            "activated_users": activated,
            "activation_rate": _rate(activated, signups),
            "paid_users": paid,
            "paid_rate": _rate(paid, signups),
        }

    channels = [{"channel": channel, **_counts(ids)} for channel, ids in by_channel.items()]
    channels.sort(key=lambda item: (-item["signups"], CHANNELS.index(item["channel"])))
    sources = [
        {"channel": channel, "source": label, **_counts(ids)}
        for (channel, label), ids in by_source.items()
    ]
    sources.sort(key=lambda item: (-item["signups"], item["channel"], item["source"]))
    total = len(seen)
    return {
        "window_days": days,
        "period_start": period_start.isoformat(),
        "period_end": now.isoformat(),
        "total_signups": total,
        "attributed_signups": total - len(by_channel.get("unknown", [])),
        "channels": channels,
        "sources": sources[:MAX_SOURCE_ROWS],
    }
