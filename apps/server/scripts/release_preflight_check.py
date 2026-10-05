#!/usr/bin/env python3
"""
Release preflight checks for backend production readiness.

Usage:
    cd apps/server
    python scripts/release_preflight_check.py
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

MIN_SECRET_LENGTH = 32
LOCALHOST_MARKERS = ("localhost", "127.0.0.1")


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    load_dotenv()


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_valid_https_callback_url(value: str | None) -> bool:
    try:
        parsed = urlsplit((value or "").strip())
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


def _parse_migration_literal(value_node: ast.expr) -> str | list[str] | tuple[str, ...] | None:
    try:
        value = ast.literal_eval(value_node)
    except Exception:
        return None
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        cleaned = [str(item).strip() for item in value if isinstance(item, str) and str(item).strip()]
        return tuple(cleaned)
    return None


def _extract_revision_fields(path: Path) -> tuple[str | None, str | tuple[str, ...] | None]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    revision: str | None = None
    down_revision: str | tuple[str, ...] | None = None

    for node in tree.body:
        target_name: str | None = None
        value_node: ast.expr | None = None

        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target_name = node.targets[0].id
            value_node = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_name = node.target.id
            value_node = node.value

        if target_name == "revision" and value_node is not None:
            parsed = _parse_migration_literal(value_node)
            if isinstance(parsed, str):
                revision = parsed
        elif target_name == "down_revision" and value_node is not None:
            parsed = _parse_migration_literal(value_node)
            if isinstance(parsed, (str, tuple)):
                down_revision = parsed

    return revision, down_revision


def _get_alembic_heads(versions_dir: Path) -> list[str]:
    revisions: set[str] = set()
    referenced: set[str] = set()

    for file_path in versions_dir.glob("*.py"):
        if file_path.name.startswith("__"):
            continue

        revision, down_revision = _extract_revision_fields(file_path)
        if revision:
            revisions.add(revision)

        if isinstance(down_revision, str):
            referenced.add(down_revision)
        elif isinstance(down_revision, tuple):
            referenced.update(down_revision)

    return sorted(revisions - referenced)


def _print_result(ok: bool, title: str, detail: str) -> bool:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {title}: {detail}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Backend release preflight checks")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail when ENVIRONMENT is not production/staging",
    )
    args = parser.parse_args()

    _load_dotenv_if_available()

    all_ok = True

    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    is_production_like = environment in {"production", "staging"}
    if args.strict:
        all_ok &= _print_result(
            is_production_like,
            "ENVIRONMENT",
            f"{environment} (required: production/staging)",
        )
    else:
        all_ok &= _print_result(
            True,
            "ENVIRONMENT",
            f"{environment} (strict check disabled)",
        )

    jwt_secret = os.getenv("JWT_SECRET_KEY", "")
    jwt_secret_ok = bool(jwt_secret) and len(jwt_secret) >= MIN_SECRET_LENGTH
    all_ok &= _print_result(
        jwt_secret_ok,
        "JWT_SECRET_KEY",
        f"length={len(jwt_secret)}",
    )

    untyped_legacy = _is_truthy(os.getenv("ALLOW_LEGACY_UNTYPED_TOKENS", "false"))
    all_ok &= _print_result(
        not untyped_legacy,
        "ALLOW_LEGACY_UNTYPED_TOKENS",
        str(untyped_legacy).lower(),
    )

    refresh_legacy = _is_truthy(os.getenv("ALLOW_LEGACY_REFRESH_WITHOUT_JTI", "false"))
    all_ok &= _print_result(
        not refresh_legacy,
        "ALLOW_LEGACY_REFRESH_WITHOUT_JTI",
        str(refresh_legacy).lower(),
    )

    cors_origins = [item.strip() for item in os.getenv("CORS_ORIGINS", "").split(",") if item.strip()]
    cors_non_empty = len(cors_origins) > 0
    all_ok &= _print_result(
        cors_non_empty,
        "CORS_ORIGINS non-empty",
        ",".join(cors_origins) if cors_origins else "<empty>",
    )

    if is_production_like:
        has_localhost = any(marker in origin for origin in cors_origins for marker in LOCALHOST_MARKERS)
        all_ok &= _print_result(
            not has_localhost,
            "CORS_ORIGINS production safety",
            "contains localhost" if has_localhost else "no localhost origins",
        )
    else:
        _print_result(
            True,
            "CORS_ORIGINS production safety",
            "skipped (non-production environment)",
        )

    rate_limit_backend = os.getenv("RATE_LIMIT_BACKEND", "").strip().lower()
    rate_limit_ok = rate_limit_backend in {"redis", "auto"}
    all_ok &= _print_result(
        rate_limit_ok,
        "RATE_LIMIT_BACKEND",
        rate_limit_backend or "<unset>",
    )

    if _is_truthy(os.getenv("ZPAY_ENABLED", "false")):
        missing_or_invalid = []
        if not os.getenv("ZPAY_PID", "").strip():
            missing_or_invalid.append("ZPAY_PID")
        if not os.getenv("ZPAY_KEY", "").strip():
            missing_or_invalid.append("ZPAY_KEY")
        if not _is_valid_https_callback_url(os.getenv("ZPAY_NOTIFY_URL")):
            missing_or_invalid.append("ZPAY_NOTIFY_URL")
        if not _is_valid_https_callback_url(os.getenv("ZPAY_RETURN_URL")):
            missing_or_invalid.append("ZPAY_RETURN_URL")
        all_ok &= _print_result(
            not missing_or_invalid,
            "Zpay checkout configuration",
            (
                "configured"
                if not missing_or_invalid
                else "missing or invalid: " + ", ".join(missing_or_invalid)
            ),
        )
    else:
        _print_result(True, "Zpay checkout configuration", "disabled")

    versions_dir = Path(__file__).resolve().parent.parent / "alembic" / "versions"
    heads = _get_alembic_heads(versions_dir)
    heads_ok = len(heads) == 1
    all_ok &= _print_result(
        heads_ok,
        "Alembic heads",
        ", ".join(heads) if heads else "<none>",
    )

    print("-" * 72)
    if all_ok:
        print("✅ Release preflight checks passed.")
        return 0

    print("❌ Release preflight checks failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
