"""In-memory TTL cache used to absorb read traffic on the hot redirect path.

Reliability rationale (documented for the Architecture/Design stage):
short-link redirects are extremely read-heavy and latency-sensitive. A
small process-local TTL cache in front of SQLite avoids a disk read on
every redirect for popular links, at the cost of eventual consistency
(a deactivated/updated link can serve a stale cache entry for up to
CACHE_TTL_SECONDS). That trade-off is acceptable for a URL shortener and
is called out explicitly in docs/testing_and_tradeoffs.md.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Optional

from .config import Config


class TTLCache:
    def __init__(self, ttl_seconds: int, max_entries: int):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._store: "OrderedDict[str, tuple[float, object]]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str):
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            expires_at, value = entry
            if expires_at < time.time():
                del self._store[key]
                self.misses += 1
                return None
            self._store.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: str, value) -> None:
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (time.time() + self.ttl_seconds, value)
            while len(self._store) > self.max_entries:
                self._store.popitem(last=False)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "entries": len(self._store),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 4) if total else 0.0,
            }


redirect_cache = TTLCache(Config.CACHE_TTL_SECONDS, Config.CACHE_MAX_ENTRIES)
