"""The orchestration engine: walks the Graph, enforcing gates, governance,
reliability controls, and producing an audit trail + metrics.

Execution model
----------------
The engine processes the graph in *waves*: on each iteration it computes
every node whose dependencies are all COMPLETED (`Graph.ready_nodes`) and
executes that whole wave concurrently (ThreadPoolExecutor). This gives
non-linear, stateful execution rather than a single linear chain --
independent branches genuinely run in parallel and only rejoin at nodes
that depend on more than one of them (a synchronization barrier for free,
since such a node simply never becomes "ready" until all its deps finish).

A run can *pause* (not fail) when a node requires human approval and no
`auto_decision` callback is configured on the ApprovalGate: `run()`
returns a RunResult with status "paused" and the pending request ids. The
caller inspects/decides the request(s) (via `approve`/`reject`) and calls
`run()` again to resume -- the engine picks up exactly where it left off
because all state lives in RunContext / RunState, not on the call stack.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .audit import AuditLog, new_id
from .governance import ApprovalGate, PolicyEngine, PolicyViolation
from .graph import Graph, Node, NodeStatus
from .metrics import RunMetrics, compute_metrics
from .reliability import RetryOutcome, RollbackRegistry, SafeStop, run_with_retries
from .replanner import Replanner
from .state import RunContext, content_hash


TERMINAL = {NodeStatus.COMPLETED, NodeStatus.FAILED, NodeStatus.SKIPPED, NodeStatus.ROLLED_BACK}


@dataclass
class RunResult:
    run_id: str
    status: str  # "completed" | "paused" | "failed" | "safe_stopped"
    statuses: dict[str, str]
    pending_approvals: list[str]
    metrics: RunMetrics
    duration_seconds: float


class Orchestrator:
    def __init__(self, graph: Graph, run_id: str | None = None, initial_spec: dict | None = None,
                 audit_path: str | None = None, approvals_dir: str = "runs/approvals",
                 policy_engine: PolicyEngine | None = None,
                 auto_decision=None, max_parallel_workers: int = 4):
        self.run_id = run_id or new_id("run")
        self.graph = graph
        self.context = RunContext(self.run_id, initial_spec=initial_spec)
        self.audit = AuditLog(self.run_id, out_path=audit_path)
        self.approvals = ApprovalGate(approvals_dir, auto_decision=auto_decision)
        self.policy = policy_engine or PolicyEngine()
        self.rollback_registry = RollbackRegistry()
        self.replanner = Replanner(graph, self.context, self.audit)
        self.max_parallel_workers = max_parallel_workers

        self.statuses: dict[str, NodeStatus] = {nid: NodeStatus.PENDING for nid in graph.nodes}
        self._approved_nodes: set[str] = set()
        self._safe_stopped: bool = False
        self._safe_stop_reason: str | None = None
        self._pending_approval_by_node: dict[str, str] = {}  # node_id -> request_id

    # ------------------------------------------------------------------ #
    # Public run loop
    # ------------------------------------------------------------------ #
    def run(self) -> RunResult:
        start = time.time()
        self.audit.emit("run_start", spec=self.context.spec)

        while True:
            if self._safe_stopped:
                break

            ready = self.graph.ready_nodes(self.statuses)
            if not ready:
                if self._pending_approval_by_node:
                    break  # paused, waiting on a human decision
                # Nothing ready, nothing pending approval: either done or
                # every remaining node is unreachable (upstream failed).
                if all(self.statuses[n] in TERMINAL for n in self.graph.nodes):
                    break
                self._skip_unreachable_remainder()
                if all(self.statuses[n] in TERMINAL for n in self.graph.nodes):
                    break
                # Defensive: avoid infinite loop if something truly is stuck.
                break

            self._run_wave(ready)

        duration = time.time() - start
        self.audit.emit("run_end", duration_seconds=round(duration, 4),
                         statuses={k: v.value for k, v in self.statuses.items()})

        return self._build_result(duration)

    def _run_wave(self, ready_nodes: list[Node]) -> None:
        self.audit.emit(
            "wave_start", wave_nodes=[n.id for n in ready_nodes],
        )
        with ThreadPoolExecutor(max_workers=min(self.max_parallel_workers, len(ready_nodes))) as pool:
            futures = {pool.submit(self._execute_node, node): node for node in ready_nodes}
            for fut in as_completed(futures):
                node = futures[fut]
                try:
                    fut.result()
                except SafeStop as stop:
                    self._safe_stopped = True
                    self._safe_stop_reason = stop.reason
                    self.audit.emit("safe_stop", node_id=node.id, reason=stop.reason)

    def _build_result(self, duration: float) -> RunResult:
        metrics = compute_metrics(self.audit, total_nodes=len(self.graph.nodes))
        metrics.end_to_end_latency_seconds = round(duration, 4)

        if self._safe_stopped:
            status = "safe_stopped"
        elif self._pending_approval_by_node:
            status = "paused"
        elif any(self.statuses[n] == NodeStatus.FAILED for n in self.graph.nodes):
            status = "failed"
        else:
            status = "completed"

        return RunResult(
            run_id=self.run_id,
            status=status,
            statuses={k: v.value for k, v in self.statuses.items()},
            pending_approvals=list(self._pending_approval_by_node.values()),
            metrics=metrics,
            duration_seconds=round(duration, 4),
        )

    def _skip_unreachable_remainder(self) -> None:
        for nid, status in list(self.statuses.items()):
            if status in TERMINAL:
                continue
            node = self.graph.nodes[nid]
            if any(self.statuses[dep] in (NodeStatus.FAILED, NodeStatus.SKIPPED, NodeStatus.ROLLED_BACK)
                   for dep in node.depends_on):
                self.statuses[nid] = NodeStatus.SKIPPED
                self.audit.emit("node_skipped", node_id=nid, reason="unreachable: upstream dependency did not complete")

    # ------------------------------------------------------------------ #
    # Node execution
    # ------------------------------------------------------------------ #
    def _execute_node(self, node: Node) -> None:
        self.statuses[node.id] = NodeStatus.RUNNING
        self.audit.emit("node_start", node_id=node.id, name=node.name, stage=node.stage)

        # Entry gate: precondition check before any work happens.
        if node.entry_gate is not None:
            passed, detail = node.entry_gate(self.context, node)
            self.audit.emit("entry_gate", node_id=node.id, passed=passed, detail=detail)
            if not passed:
                self._terminal_failure(node, reason=f"entry gate failed: {detail}")
                return

        # Human approval checkpoint (before the high-impact action executes).
        if node.requires_approval and node.id not in self._approved_nodes:
            self._request_approval(node)
            if node.id not in self._approved_nodes:
                # Still awaiting a real (non-auto) human decision: leave the
                # node parked in AWAITING_APPROVAL and stop this branch here.
                self.statuses[node.id] = NodeStatus.AWAITING_APPROVAL
                return

        input_hashes = {dep: (self.context.get(dep).output_hash if self.context.get(dep) else "")
                         for dep in node.depends_on}

        def attempt():
            result = node.handler(self.context, node)
            if node.exit_gate is not None:
                passed, detail = node.exit_gate(self.context, node, result)
                self.audit.emit("exit_gate", node_id=node.id, passed=passed, detail=detail)
                if not passed:
                    raise RuntimeError(f"exit gate failed: {detail}")
            if result.policy_payload:
                gate_results = self.policy.evaluate(f"{node.id}.exit", result.policy_payload)
                self.audit.emit("policy_check", node_id=node.id, results=gate_results)
            return result

        def fallback_wrapped():
            self.audit.emit("fallback_used", node_id=node.id)
            return node.fallback(self.context, node)

        outcome: RetryOutcome = run_with_retries(
            attempt,
            node.retry_policy,
            fallback_wrapped if node.fallback else None,
            on_attempt=lambda n: self.audit.emit("attempt_start", node_id=node.id, attempt=n),
            on_retry=lambda n, exc: (
                self.audit.emit("retry_attempt", node_id=node.id, attempt=n, error=str(exc))
            ),
            on_fallback=lambda exc: self.audit.emit("fallback_triggered", node_id=node.id, error=str(exc) if exc else None),
        )

        if not outcome.succeeded:
            self._terminal_failure(node, reason="; ".join(outcome.errors) or "unknown error",
                                    attempts=outcome.attempts)
            return

        result: NodeResult = outcome.result
        record = self.context.put(
            node.id, result.output, result.decisions, result.artifacts, input_hashes
        )

        if node.rollback is not None:
            self.rollback_registry.register(node.id, lambda n=node: n.rollback(self.context, n))

        self.statuses[node.id] = NodeStatus.COMPLETED
        self.audit.emit(
            "node_complete", node_id=node.id, attempts=outcome.attempts,
            used_fallback=outcome.used_fallback, output_hash=record.output_hash,
            decisions=result.decisions, artifacts=result.artifacts,
        )

    def _terminal_failure(self, node: Node, reason: str, attempts: int = 1) -> None:
        self.statuses[node.id] = NodeStatus.FAILED
        self.audit.emit("node_failed", node_id=node.id, reason=reason, attempts=attempts, terminal=True)

        # Cascade: everything downstream of this node is now unreachable.
        for nid in self.graph.reachable_from(node.id) - {node.id}:
            if self.statuses.get(nid) not in TERMINAL:
                self.statuses[nid] = NodeStatus.SKIPPED
                self.audit.emit("node_skipped", node_id=nid, reason=f"upstream '{node.id}' failed")

        # Rollback: undo any already-completed upstream side effects this
        # node's failure invalidates (e.g. a broken implementation whose
        # tests failed should not be left half-applied).
        for upstream_id in node.rollback_on_failure:
            rolled_back = self.rollback_registry.rollback_node(upstream_id)
            if rolled_back:
                self.statuses[upstream_id] = NodeStatus.ROLLED_BACK
                self.audit.emit(
                    "node_rolled_back", node_id=upstream_id,
                    reason=f"downstream '{node.id}' failed terminally: {reason}",
                )
                for nid in self.graph.reachable_from(upstream_id) - {upstream_id}:
                    if self.statuses.get(nid) not in TERMINAL:
                        self.statuses[nid] = NodeStatus.SKIPPED
                        self.audit.emit("node_skipped", node_id=nid, reason=f"upstream '{upstream_id}' rolled back")

        if getattr(node, "safe_stop_on_failure", False):
            raise SafeStop(f"critical node '{node.id}' failed: {reason}")

    # ------------------------------------------------------------------ #
    # Human approval API (used interactively or by scenario scripts)
    # ------------------------------------------------------------------ #
    def _request_approval(self, node: Node) -> None:
        summary = (
            node.approval_summary(self.context, node)
            if node.approval_summary else f"Approval required for high-impact stage '{node.name}'"
        )
        req = self.approvals.request(node.id, self.run_id, summary, payload={"stage": node.stage})
        self._pending_approval_by_node[node.id] = req.request_id
        self.audit.emit("approval_requested", node_id=node.id, request_id=req.request_id, summary=summary)

        if self.approvals.auto_decision is not None:
            approved, reason = self.approvals.auto_decision(req)
            self.decide_approval(req.request_id, approved, decided_by="auto-reviewer", reason=reason)

    def decide_approval(self, request_id: str, approved: bool, decided_by: str, reason: str) -> None:
        req = self.approvals.decide(request_id, approved, decided_by, reason)
        self.audit.emit(
            "approval_decided", node_id=req.node_id, request_id=request_id,
            approved=approved, decided_by=decided_by, reason=reason,
        )
        self._pending_approval_by_node.pop(req.node_id, None)

        if approved:
            self._approved_nodes.add(req.node_id)
            self.statuses[req.node_id] = NodeStatus.PENDING  # re-enters the ready queue
        else:
            self.statuses[req.node_id] = NodeStatus.ROLLED_BACK
            self.audit.emit("node_rolled_back", node_id=req.node_id, reason=f"approval rejected: {reason}")
            for nid in self.graph.reachable_from(req.node_id) - {req.node_id}:
                if self.statuses.get(nid) not in TERMINAL:
                    self.statuses[nid] = NodeStatus.SKIPPED
                    self.audit.emit("node_skipped", node_id=nid, reason=f"approval for '{req.node_id}' was rejected")

    def approve(self, request_id: str, decided_by: str, reason: str = "") -> None:
        self.decide_approval(request_id, True, decided_by, reason)

    def reject(self, request_id: str, decided_by: str, reason: str = "") -> None:
        self.decide_approval(request_id, False, decided_by, reason)

    def pending_approvals(self) -> list[dict]:
        return [
            {"request_id": r.request_id, "node_id": r.node_id, "summary": r.summary}
            for r in self.approvals.pending()
        ]

    # ------------------------------------------------------------------ #
    # Dynamic re-planning
    # ------------------------------------------------------------------ #
    def amend_spec_and_replan(self, patch: dict, reason: str, from_node_id: str) -> list[str]:
        self.context.amend_spec(patch, reason)
        invalidated = self.replanner.on_spec_amendment(self.statuses, from_node_id, reason)
        self._approved_nodes -= set(invalidated)
        return invalidated

    # ------------------------------------------------------------------ #
    # Reporting
    # ------------------------------------------------------------------ #
    def status_report(self) -> dict:
        return {
            "run_id": self.run_id,
            "statuses": {k: v.value for k, v in self.statuses.items()},
            "pending_approvals": self.pending_approvals(),
            "safe_stopped": self._safe_stopped,
            "safe_stop_reason": self._safe_stop_reason,
            "spec_version": self.context.spec_version,
        }
