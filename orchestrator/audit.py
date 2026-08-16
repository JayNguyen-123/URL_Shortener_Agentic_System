"""Audit-grade observability: an append-only, structured event log.

Every meaningful thing the orchestrator does (node start/finish, gate
evaluation, retry, rollback, approval request/decision, policy violation,
re-plan) is written as one JSON object per line (JSONL) to the run's audit
file, plus buffered in memory for the metrics collector. This gives full
traceability ("why did the engine do X") without needing a database.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


@dataclass
class AuditEvent:
    run_id: str
    event_type: str
    node_id: str | None
    timestamp: float
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "run_id": self.run_id,
                "event_type": self.event_type,
                "node_id": self.node_id,
                "timestamp": self.timestamp,
                "iso_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.timestamp)),
                **self.data,
            },
            default=str,
        )


class AuditLog:
    """Thread-safe structured logger. One instance per orchestration run."""

    def __init__(self, run_id: str, out_path: str | Path | None = None):
        self.run_id = run_id
        self._events: list[AuditEvent] = []
        self._lock = threading.Lock()
        self._out_path = Path(out_path) if out_path else None
        if self._out_path:
            self._out_path.parent.mkdir(parents=True, exist_ok=True)
            self._out_path.write_text("")  # truncate/create

    def emit(self, event_type: str, node_id: str | None = None, **data: Any) -> AuditEvent:
        event = AuditEvent(
            run_id=self.run_id,
            event_type=event_type,
            node_id=node_id,
            timestamp=time.time(),
            data=data,
        )
        with self._lock:
            self._events.append(event)
            if self._out_path:
                with self._out_path.open("a") as f:
                    f.write(event.to_json() + "\n")
        return event

    def events(self) -> list[AuditEvent]:
        with self._lock:
            return list(self._events)

    def events_for(self, node_id: str) -> list[AuditEvent]:
        return [e for e in self.events() if e.node_id == node_id]

    def to_list(self) -> list[dict]:
        return [json.loads(e.to_json()) for e in self.events()]
