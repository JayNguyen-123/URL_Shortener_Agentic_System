"""Explicit dependency graph for SDLC stages.

A `Graph` is pure topology + behavior (nodes, dependencies, gates,
handlers). Execution *state* (what's running, what's done) lives in
`engine.RunState`, so the same Graph object can be re-walked after a
dynamic re-plan without rebuilding it.

Nodes with no dependency relationship between them are natural parallel
candidates: the engine (engine.py) executes every currently-READY node in
a wave concurrently via a thread pool, and a node only becomes READY once
*all* of its dependencies have COMPLETED -- this is the synchronization
barrier for fan-out/fan-in (parallel) paths.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .reliability import RetryPolicy
from .state import RunContext


class NodeStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ROLLED_BACK = "rolled_back"
    STALE = "stale"


@dataclass
class NodeResult:
    output: dict = field(default_factory=dict)
    decisions: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    policy_payload: dict = field(default_factory=dict)


HandlerFn = Callable[[RunContext, "Node"], NodeResult]
EntryGateFn = Callable[[RunContext, "Node"], tuple[bool, str]]
ExitGateFn = Callable[[RunContext, "Node", NodeResult], tuple[bool, str]]


@dataclass
class Node:
    id: str
    name: str
    stage: str  # requirements | design | implementation | testing | documentation | release
    handler: HandlerFn
    depends_on: list[str] = field(default_factory=list)
    entry_gate: EntryGateFn | None = None
    exit_gate: ExitGateFn | None = None
    requires_approval: bool = False
    approval_summary: Callable[[RunContext, "Node"], str] | None = None
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    fallback: HandlerFn | None = None
    rollback: Callable[[RunContext, "Node"], None] | None = None
    rollback_on_failure: list[str] = field(default_factory=list)  # upstream node ids to undo if THIS node fails terminally
    parallel_group: str | None = None  # informational label for reports/diagrams
    safe_stop_on_failure: bool = False  # halt the ENTIRE run (not just this branch) if terminal


class Graph:
    def __init__(self, nodes: list[Node]):
        self.nodes: dict[str, Node] = {n.id: n for n in nodes}
        self._validate()

    def _validate(self) -> None:
        for node in self.nodes.values():
            for dep in node.depends_on:
                if dep not in self.nodes:
                    raise ValueError(f"node '{node.id}' depends on unknown node '{dep}'")
        # Cycle detection (Kahn's algorithm)
        in_degree = {nid: len(n.depends_on) for nid, n in self.nodes.items()}
        queue = [nid for nid, d in in_degree.items() if d == 0]
        visited = 0
        dependents = self._dependents_map()
        while queue:
            nid = queue.pop()
            visited += 1
            for dep_id in dependents.get(nid, []):
                in_degree[dep_id] -= 1
                if in_degree[dep_id] == 0:
                    queue.append(dep_id)
        if visited != len(self.nodes):
            raise ValueError("dependency graph contains a cycle")

    def _dependents_map(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {nid: [] for nid in self.nodes}
        for node in self.nodes.values():
            for dep in node.depends_on:
                out[dep].append(node.id)
        return out

    def dependents(self, node_id: str) -> list[str]:
        return self._dependents_map().get(node_id, [])

    def ready_nodes(self, statuses: dict[str, NodeStatus]) -> list[Node]:
        """Nodes whose status is PENDING/STALE/READY and whose every
        dependency is COMPLETED -- i.e. safe to execute right now."""
        ready = []
        for node in self.nodes.values():
            if statuses.get(node.id) not in (NodeStatus.PENDING, NodeStatus.STALE, None):
                continue
            if all(statuses.get(dep) == NodeStatus.COMPLETED for dep in node.depends_on):
                ready.append(node)
        return ready

    def reachable_from(self, node_id: str) -> set[str]:
        """All nodes downstream of `node_id` (inclusive) -- used by the
        re-planner to invalidate everything that could depend, directly or
        transitively, on a changed upstream output."""
        dependents = self._dependents_map()
        seen: set[str] = set()
        stack = [node_id]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(dependents.get(cur, []))
        return seen

    def topological_waves(self) -> list[list[str]]:
        """Grouping of node ids into sequential 'waves' where every node in
        a wave can run in parallel. Used for architecture diagrams/reports,
        not for execution (the engine computes readiness dynamically so it
        can react to retries/approvals mid-run)."""
        statuses = {nid: NodeStatus.PENDING for nid in self.nodes}
        waves: list[list[str]] = []
        remaining = set(self.nodes)
        while remaining:
            wave = [n.id for n in self.ready_nodes(statuses) if n.id in remaining]
            if not wave:
                raise ValueError("unable to compute topological waves (cycle?)")
            waves.append(sorted(wave))
            for nid in wave:
                statuses[nid] = NodeStatus.COMPLETED
                remaining.discard(nid)
        return waves
