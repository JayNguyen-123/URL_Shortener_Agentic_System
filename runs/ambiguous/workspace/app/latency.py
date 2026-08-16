"""Rolling-window latency recorder used to report p95 redirect latency.

A fixed-size deque is enough for a single-process prototype; a production
deployment would export this to a metrics backend (Prometheus/Datadog)
instead of computing percentiles in-process. Documented as a trade-off.
"""
from __future__ import annotations

import threading
from collections import deque


class LatencyRecorder:
    def __init__(self, window_size: int = 500):
        self._samples: deque[float] = deque(maxlen=window_size)
        self._lock = threading.Lock()

    def record(self, milliseconds: float) -> None:
        with self._lock:
            self._samples.append(milliseconds)

    def percentile(self, p: float) -> float | None:
        with self._lock:
            if not self._samples:
                return None
            data = sorted(self._samples)
        k = max(0, min(len(data) - 1, int(round(p / 100 * (len(data) - 1)))))
        return round(data[k], 2)

    def stats(self) -> dict:
        return {"p50_ms": self.percentile(50), "p95_ms": self.percentile(95), "samples": len(self._samples)}


latency_recorder = LatencyRecorder()
