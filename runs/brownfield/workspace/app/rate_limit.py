"""Fixed-window rate limiter (per client key, in-memory).

A fixed-window counter is chosen over a token bucket for simplicity and
predictable memory use; the documented trade-off is permitting brief bursts
at window boundaries. For a single-process prototype this is acceptable;
docs/testing_and_tradeoffs.md notes that a production deployment behind
multiple workers would need a shared store (e.g. Redis) for this to be
correct across processes.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .config import Config


@dataclass
class _Window:
    count: int = 0
    window_start: float = field(default_factory=time.time)


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._windows: dict[str, _Window] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, dict]:
        now = time.time()
        with self._lock:
            w = self._windows.get(key)
            if w is None or now - w.window_start >= self.window_seconds:
                w = _Window(count=0, window_start=now)
                self._windows[key] = w
            w.count += 1
            remaining = max(0, self.max_requests - w.count)
            reset_in = max(0.0, self.window_seconds - (now - w.window_start))
            allowed = w.count <= self.max_requests
            return allowed, {
                "limit": self.max_requests,
                "remaining": remaining,
                "reset_in_seconds": round(reset_in, 2),
            }

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()


rate_limiter = RateLimiter(Config.RATE_LIMIT_MAX_REQUESTS, Config.RATE_LIMIT_WINDOW_SECONDS)
