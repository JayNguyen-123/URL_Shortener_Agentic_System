"""Dynamic re-planning: react to upstream output changes mid-run.

Two triggers are supported:
1. A node's output hash changes on re-execution (e.g. a retry produced a
   materially different artifact than a prior attempt).
2. An external amendment to the shared spec (e.g. a human/agent changes a
   requirement after some stages have already completed -- the "ambiguous
   requirement" scenario uses this when clarification changes scope).

Either trigger invalidates (STALE) every node reachable from the changed
node, using `Graph.reachable_from`, and preserves node outputs that were
not affected -- i.e. this is incremental re-planning, not "start over".
"""
from __future__ import annotations

from .audit import AuditLog
from .graph import Graph, NodeStatus
from .state import RunContext


class Replanner:
    def __init__(self, graph: Graph, context: RunContext, audit: AuditLog):
        self.graph = graph
        self.context = context
        self.audit = audit

    def invalidate_downstream(self, changed_node_id: str, statuses: dict[str, NodeStatus],
                               reason: str) -> list[str]:
        affected = self.graph.reachable_from(changed_node_id) - {changed_node_id}
        invalidated = []
        for nid in affected:
            if statuses.get(nid) == NodeStatus.COMPLETED:
                statuses[nid] = NodeStatus.STALE
                invalidated.append(nid)
        self.audit.emit(
            "replan",
            node_id=changed_node_id,
            reason=reason,
            invalidated_nodes=invalidated,
        )
        return invalidated

    def on_spec_amendment(self, statuses: dict[str, NodeStatus], from_node_id: str,
                           reason: str) -> list[str]:
        """A spec amendment conceptually originates at the requirements
        node; everything downstream of (and including re-run of) that node
        must be re-evaluated against the new spec."""
        statuses[from_node_id] = NodeStatus.STALE
        invalidated = self.invalidate_downstream(from_node_id, statuses, reason)
        return [from_node_id] + invalidated
