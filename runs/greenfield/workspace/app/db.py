"""SQLite persistence layer.

A thin wrapper rather than an ORM: the schema is small and stable, and a
direct sqlite3 layer keeps the dependency footprint minimal (relevant in
network-restricted deployment environments) while still being easy to
swap for Postgres later behind the same functions (`get_conn`, `init_db`).
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager

from .config import Config

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection (SQLite connections are not
    safe to share across threads)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(Config.DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        _local.conn = conn
    return conn


@contextmanager
def tx():
    """Context manager providing a transaction with commit/rollback."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
    code TEXT PRIMARY KEY,
    long_url TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    is_custom_alias INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    owner TEXT
);

CREATE TABLE IF NOT EXISTS clicks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL REFERENCES links(code) ON DELETE CASCADE,
    clicked_at TEXT NOT NULL,
    referrer TEXT,
    ip_hash TEXT,
    user_agent TEXT
);

CREATE INDEX IF NOT EXISTS idx_clicks_code ON clicks(code);
CREATE INDEX IF NOT EXISTS idx_clicks_clicked_at ON clicks(clicked_at);
"""


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()


def close_conn() -> None:
    """Close and drop the current thread's cached connection so a
    subsequent get_conn() reconnects (e.g. after Config.DB_PATH changes,
    which matters for test isolation between scenarios/tests)."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


def reset_db() -> None:
    """Test helper: drop and recreate all tables."""
    conn = get_conn()
    conn.executescript(
        """
        DROP TABLE IF EXISTS clicks;
        DROP TABLE IF EXISTS links;
        """
    )
    conn.commit()
    init_db()
