"""Reliability metrics derived from the audit log.

Deliberately computed *from* the audit trail rather than tracked as
separate counters, so the metrics are always consistent with -- and
explainable by -- the same event stream a human would read to audit the
run (single source of truth).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .audit import AuditLog


@dataclass
class RunMetrics:
    total_nodes: int = 0
    completed_nodes: int = 0
    failed_nodes: int = 0
    skipped_nodes: int = 0
    rolled_back_nodes: int = 0
    retry_events: int = 0
    rollback_events: int = 0
    fallback_events: int = 0
    approval_requests: int = 0
    approvals_granted: int = 0
    approvals_rejected: int = 0
    policy_violations: int = 0
    replans: int = 0
    success_rate: float = 0.0
    retry_frequency: float = 0.0  # retries per node
    rollback_frequency: float = 0.0  # rollbacks per node
    mttr_seconds: float | None = None
    end_to_end_latency_seconds: float | None = None
    per_node_latency_seconds: dict = field(default_factory=dict)


def compute_metrics(audit: AuditLog, total_nodes: int) -> RunMetrics:
    events = audit.to_list()
    m = RunMetrics(total_nodes=total_nodes)

    node_start: dict[str, float] = {}
    node_end: dict[str, float] = {}
    recovery_durations: list[float] = []
    failure_open_at: dict[str, float] = {}
    # A node can pass through multiple terminal states across re-plans in the
    # same run (e.g. completed in round 1, re-executed and completed again in
    # round 2). We want "how many of the graph's N nodes are currently in
    # each terminal state" (bounded by N), not a raw event count, so track
    # each node's LATEST terminal outcome rather than incrementing per event.
    latest_terminal: dict[str, str] = {}

    run_start = min((e["timestamp"] for e in events), default=None)
    run_end = max((e["timestamp"] for e in events), default=None)

    for e in events:
        et = e["event_type"]
        nid = e.get("node_id")

        if et == "node_start" and nid:
            node_start[nid] = e["timestamp"]
        elif et == "node_complete" and nid:
            node_end[nid] = e["timestamp"]
            latest_terminal[nid] = "completed"
            if nid in failure_open_at:
                recovery_durations.append(e["timestamp"] - failure_open_at.pop(nid))
        elif et == "node_failed" and nid:
            latest_terminal[nid] = "failed"
            failure_open_at.setdefault(nid, e["timestamp"])
        elif et == "node_skipped" and nid:
            latest_terminal[nid] = "skipped"
        elif et == "node_rolled_back" and nid:
            latest_terminal[nid] = "rolled_back"
            m.rollback_events += 1
        elif et == "retry_attempt":
            m.retry_events += 1
        elif et == "fallback_used":
            m.fallback_events += 1
        elif et == "approval_requested":
            m.approval_requests += 1
        elif et == "approval_decided":
            if e.get("approved"):
                m.approvals_granted += 1
            else:
                m.approvals_rejected += 1
        elif et == "policy_violation":
            m.policy_violations += 1
        elif et == "replan":
            m.replans += 1

    for nid, start in node_start.items():
        end = node_end.get(nid)
        if end is not None:
            m.per_node_latency_seconds[nid] = round(end - start, 4)

    m.completed_nodes = sum(1 for s in latest_terminal.values() if s == "completed")
    m.failed_nodes = sum(1 for s in latest_terminal.values() if s == "failed")
    m.skipped_nodes = sum(1 for s in latest_terminal.values() if s == "skipped")
    m.rolled_back_nodes = sum(1 for s in latest_terminal.values() if s == "rolled_back")

    m.success_rate = round(m.completed_nodes / total_nodes, 4) if total_nodes else 0.0
    m.retry_frequency = round(m.retry_events / total_nodes, 4) if total_nodes else 0.0
    m.rollback_frequency = round(m.rollback_events / total_nodes, 4) if total_nodes else 0.0
    if recovery_durations:
        m.mttr_seconds = round(sum(recovery_durations) / len(recovery_durations), 4)
    if run_start is not None and run_end is not None:
        m.end_to_end_latency_seconds = round(run_end - run_start, 4)

    return m
