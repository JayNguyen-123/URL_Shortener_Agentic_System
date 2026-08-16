"""Cross-stage context store and decision lineage.

`RunContext` is the shared memory of a run: every node reads its upstream
dependencies' outputs from it and writes its own output back. Each write is
content-hashed and linked to the hashes of the inputs that produced it, so
we can answer "what upstream decisions led to this artifact" (decision
lineage) and "did this output actually change" (used by the re-planner to
decide whether downstream nodes need to re-run).
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any


def content_hash(obj: Any) -> str:
    try:
        blob = json.dumps(obj, sort_keys=True, default=str)
    except TypeError:
        blob = str(obj)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class NodeRecord:
    node_id: str
    output: dict = field(default_factory=dict)
    output_hash: str = ""
    input_hashes: dict[str, str] = field(default_factory=dict)  # dep_node_id -> hash used
    decisions: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    version: int = 1
    updated_at: float = field(default_factory=time.time)


class RunContext:
    """Thread-safe store of {node_id: NodeRecord}, plus a mutable
    "requirement spec" that scenarios/replanning can amend mid-run."""

    def __init__(self, run_id: str, initial_spec: dict | None = None):
        self.run_id = run_id
        self._lock = threading.Lock()
        self._records: dict[str, NodeRecord] = {}
        self.spec: dict = initial_spec or {}
        self.spec_version = 1
        self._lineage: list[dict] = []  # append-only decision lineage log

    # ---- spec (the evolving normalized requirement) ----------------------
    def amend_spec(self, patch: dict, reason: str) -> int:
        with self._lock:
            before_hash = content_hash(self.spec)
            self.spec.update(patch)
            self.spec_version += 1
            after_hash = content_hash(self.spec)
            self._lineage.append(
                {
                    "type": "spec_amendment",
                    "reason": reason,
                    "before_hash": before_hash,
                    "after_hash": after_hash,
                    "spec_version": self.spec_version,
                    "patch": patch,
                    "timestamp": time.time(),
                }
            )
            return self.spec_version

    # ---- node outputs ------------------------------------------------------
    def get(self, node_id: str) -> NodeRecord | None:
        with self._lock:
            return self._records.get(node_id)

    def get_output(self, node_id: str) -> dict:
        rec = self.get(node_id)
        if rec is None:
            raise KeyError(f"no output recorded yet for node '{node_id}'")
        return rec.output

    def put(self, node_id: str, output: dict, decisions: list[str],
             artifacts: list[str], input_hashes: dict[str, str]) -> NodeRecord:
        with self._lock:
            prev = self._records.get(node_id)
            version = (prev.version + 1) if prev else 1
            rec = NodeRecord(
                node_id=node_id,
                output=output,
                output_hash=content_hash(output),
                input_hashes=input_hashes,
                decisions=decisions,
                artifacts=artifacts,
                version=version,
            )
            self._records[node_id] = rec
            self._lineage.append(
                {
                    "type": "node_output",
                    "node_id": node_id,
                    "version": version,
                    "output_hash": rec.output_hash,
                    "input_hashes": input_hashes,
                    "decisions": decisions,
                    "timestamp": rec.updated_at,
                }
            )
            return rec

    def changed_since(self, node_id: str, previous_hash: str | None) -> bool:
        rec = self.get(node_id)
        if rec is None:
            return True
        return rec.output_hash != previous_hash

    def lineage(self) -> list[dict]:
        with self._lock:
            return list(self._lineage)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "run_id": self.run_id,
                "spec_version": self.spec_version,
                "spec": self.spec,
                "nodes": {
                    nid: {
                        "version": r.version,
                        "output_hash": r.output_hash,
                        "decisions": r.decisions,
                        "artifacts": r.artifacts,
                    }
                    for nid, r in self._records.items()
                },
            }
