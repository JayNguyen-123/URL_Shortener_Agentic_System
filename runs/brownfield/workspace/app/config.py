"""Centralized configuration for the URL shortener service.

All values are overridable via environment variables so the same code
runs unmodified across dev/test/prod. Keeping configuration in one place
(instead of scattered literals) is a deliberate maintainability choice
flagged by the Architecture stage of the orchestrator.
"""
from __future__ import annotations

import os


class Config:
    # Storage
    DB_PATH: str = os.environ.get("URLSHORT_DB_PATH", "urlshortener.db")

    # Short code generation
    CODE_LENGTH: int = int(os.environ.get("URLSHORT_CODE_LENGTH", "7"))
    CODE_ALPHABET: str = "23456789abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"
    # Excludes 0/O/1/l/I to avoid visually ambiguous short codes.

    # Base URL used to build the fully-qualified short link returned to clients.
    BASE_URL: str = os.environ.get("URLSHORT_BASE_URL", "http://localhost:8000")

    # Rate limiting (token bucket): requests per window per client key.
    RATE_LIMIT_MAX_REQUESTS: int = int(os.environ.get("URLSHORT_RATE_LIMIT_MAX", "20"))
    RATE_LIMIT_WINDOW_SECONDS: int = int(os.environ.get("URLSHORT_RATE_LIMIT_WINDOW", "60"))

    # Redirect cache (in-memory TTL cache in front of SQLite lookups).
    CACHE_TTL_SECONDS: int = int(os.environ.get("URLSHORT_CACHE_TTL", "30"))
    CACHE_MAX_ENTRIES: int = int(os.environ.get("URLSHORT_CACHE_MAX_ENTRIES", "10000"))

    # Safety limits
    MAX_URL_LENGTH: int = int(os.environ.get("URLSHORT_MAX_URL_LENGTH", "2048"))
    DEFAULT_TTL_DAYS: int | None = None  # None => links never expire unless requested
    MAX_BULK_URLS: int = int(os.environ.get("URLSHORT_MAX_BULK_URLS", "50"))
