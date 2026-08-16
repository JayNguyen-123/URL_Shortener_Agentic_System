"""Core URL-shortening domain logic: validation, code generation, CRUD."""
from __future__ import annotations

import re
import secrets
import sqlite3
import string
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from . import db
from .cache import redirect_cache
from .config import Config
from .errors import AliasTakenError, InvalidURLError, LinkExpiredError, NotFoundError

_ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
_ALLOWED_SCHEMES = {"http", "https"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def validate_url(long_url: str) -> None:
    if not long_url or len(long_url) > Config.MAX_URL_LENGTH:
        raise InvalidURLError(
            f"url must be non-empty and at most {Config.MAX_URL_LENGTH} characters"
        )
    parsed = urlparse(long_url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise InvalidURLError("url must use http or https scheme")
    if not parsed.netloc:
        raise InvalidURLError("url must include a host")


def validate_alias(alias: str) -> None:
    if not _ALIAS_RE.match(alias):
        raise InvalidURLError(
            "alias must be 3-32 characters and contain only letters, digits, '_' or '-'"
        )


def generate_code(length: int = Config.CODE_LENGTH) -> str:
    return "".join(secrets.choice(Config.CODE_ALPHABET) for _ in range(length))


def create_short_link(long_url: str, custom_alias: str | None = None,
                       ttl_seconds: int | None = None, owner: str | None = None) -> dict:
    validate_url(long_url)

    if custom_alias:
        validate_alias(custom_alias)

    created_at = _now()
    expires_at = created_at + timedelta(seconds=ttl_seconds) if ttl_seconds else None

    # Bug fix (brownfield scenario): the previous implementation checked for
    # an existing code with a SELECT and only then issued an INSERT. Under
    # concurrent requests two threads could both pass the SELECT before
    # either INSERT committed -- a classic check-then-act race that could
    # silently drop one of two links sharing a code. We now let the `code`
    # PRIMARY KEY constraint be the single, atomic source of truth for
    # "taken" and react to sqlite3.IntegrityError instead of pre-checking.
    attempts = 0
    code = custom_alias or generate_code()
    is_custom = 1 if custom_alias else 0

    while True:
        attempts += 1
        try:
            with db.tx() as conn:
                conn.execute(
                    """INSERT INTO links (code, long_url, created_at, expires_at, is_custom_alias,
                                            is_active, owner)
                       VALUES (?, ?, ?, ?, ?, 1, ?)""",
                    (code, long_url, _iso(created_at), _iso(expires_at), is_custom, owner),
                )
            break
        except sqlite3.IntegrityError:
            if custom_alias:
                raise AliasTakenError(f"alias '{code}' is already in use")
            if attempts >= 5:
                raise RuntimeError("failed to generate a unique short code after 5 attempts")
            code = generate_code()

    return {
        "code": code,
        "long_url": long_url,
        "short_url": f"{Config.BASE_URL}/{code}",
        "created_at": _iso(created_at),
        "expires_at": _iso(expires_at),
        "is_custom_alias": bool(is_custom),
    }


def _row_to_link(row) -> dict:
    return {
        "code": row["code"],
        "long_url": row["long_url"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "is_custom_alias": bool(row["is_custom_alias"]),
        "is_active": bool(row["is_active"]),
    }


def get_link(code: str, use_cache: bool = True) -> dict:
    if use_cache:
        cached = redirect_cache.get(code)
        if cached is not None:
            if cached == "__MISSING__":
                raise NotFoundError(f"no link found for code '{code}'")
            _check_not_expired(cached)
            return cached

    conn = db.get_conn()
    row = conn.execute("SELECT * FROM links WHERE code = ?", (code,)).fetchone()
    if row is None:
        if use_cache:
            redirect_cache.set(code, "__MISSING__")
        raise NotFoundError(f"no link found for code '{code}'")

    link = _row_to_link(row)
    if use_cache:
        redirect_cache.set(code, link)
    _check_not_expired(link)
    return link


def _check_not_expired(link: dict) -> None:
    if link["expires_at"]:
        expires_at = datetime.fromisoformat(link["expires_at"])
        if _now() > expires_at:
            raise LinkExpiredError(f"link '{link['code']}' expired at {link['expires_at']}")
    if not link["is_active"]:
        raise NotFoundError(f"link '{link['code']}' has been deactivated")


def deactivate_link(code: str) -> None:
    with db.tx() as conn:
        cur = conn.execute("UPDATE links SET is_active = 0 WHERE code = ?", (code,))
        if cur.rowcount == 0:
            raise NotFoundError(f"no link found for code '{code}'")
    redirect_cache.invalidate(code)


def record_click(code: str, referrer: str | None, ip_hash: str | None,
                  user_agent: str | None) -> None:
    with db.tx() as conn:
        conn.execute(
            """INSERT INTO clicks (code, clicked_at, referrer, ip_hash, user_agent)
               VALUES (?, ?, ?, ?, ?)""",
            (code, _iso(_now()), referrer, ip_hash, user_agent),
        )
