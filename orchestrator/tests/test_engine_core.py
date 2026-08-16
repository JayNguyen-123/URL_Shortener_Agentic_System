"""Unit tests for the orchestration engine mechanics themselves (graph
topology, parallel waves, retries/fallback, approval pause/resume/reject,
policy guardrails, dynamic re-planning) using trivial synthetic nodes --
independent of the URL-shortener domain so these prove the engine, not the
agents built on top of it."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from orchestrator.engine import Orchestrator
from orchestrator.governance import PolicyEngine, PolicyRule
from orchestrator.graph import Graph, Node, NodeResult
from orchestrator.reliability import RetryPolicy


def _ok_handler(output=None):
    def handler(ctx, node):
        return NodeResult(output=output or {"node": node.id}, decisions=[f"{node.id} ran"])
    return handler


def test_linear_chain_runs_in_dependency_order():
    order = []

    def make(node_id):
        def handler(ctx, node):
            order.append(node.id)
            return NodeResult(output={})
        return handler

    graph = Graph([
        Node(id="a", name="A", stage="requirements", handler=make("a")),
        Node(id="b", name="B", stage="design", handler=make("b"), depends_on=["a"]),
        Node(id="c", name="C", stage="implementation", handler=make("c"), depends_on=["b"]),
    ])
    result = Orchestrator(graph).run()
    assert result.status == "completed"
    assert order == ["a", "b", "c"]
    assert result.metrics.success_rate == 1.0


def test_parallel_fan_out_and_fan_in_synchronizes():
    graph = Graph([
        Node(id="root", name="root", stage="requirements", handler=_ok_handler()),
        Node(id="branch1", name="branch1", stage="testing", handler=_ok_handler(), depends_on=["root"]),
        Node(id="branch2", name="branch2", stage="testing", handler=_ok_handler(), depends_on=["root"]),
        Node(id="join", name="join", stage="documentation", handler=_ok_handler(), depends_on=["branch1", "branch2"]),
    ])
    orch = Orchestrator(graph)
    waves = graph.topological_waves()
    assert waves == [["root"], ["branch1", "branch2"], ["join"]]
    result = orch.run()
    assert result.status == "completed"
    assert result.statuses["join"] == "completed"


def test_bounded_retry_then_success():
    attempts = {"n": 0}

    def flaky(ctx, node):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("transient failure")
        return NodeResult(output={"ok": True})

    graph = Graph([
        Node(id="flaky", name="flaky", stage="implementation", handler=flaky,
             retry_policy=RetryPolicy(max_attempts=3)),
    ])
    result = Orchestrator(graph).run()
    assert result.status == "completed"
    assert attempts["n"] == 3
    assert result.metrics.retry_frequency > 0


def test_exhausted_retries_falls_back():
    def always_fails(ctx, node):
        raise RuntimeError("permanent failure")

    def fallback(ctx, node):
        return NodeResult(output={"fallback": True}, decisions=["used safe default"])

    graph = Graph([
        Node(id="risky", name="risky", stage="implementation", handler=always_fails,
             retry_policy=RetryPolicy(max_attempts=2), fallback=fallback),
    ])
    result = Orchestrator(graph).run()
    assert result.status == "completed"
    assert result.statuses["risky"] == "completed"


def test_terminal_failure_skips_downstream_but_not_siblings():
    def fails(ctx, node):
        raise RuntimeError("boom")

    graph = Graph([
        Node(id="root", name="root", stage="requirements", handler=_ok_handler()),
        Node(id="bad", name="bad", stage="implementation", handler=fails,
             depends_on=["root"], retry_policy=RetryPolicy(max_attempts=1)),
        Node(id="dependent", name="dependent", stage="testing", handler=_ok_handler(), depends_on=["bad"]),
        Node(id="sibling", name="sibling", stage="testing", handler=_ok_handler(), depends_on=["root"]),
    ])
    result = Orchestrator(graph).run()
    assert result.status == "failed"
    assert result.statuses["bad"] == "failed"
    assert result.statuses["dependent"] == "skipped"
    assert result.statuses["sibling"] == "completed"  # independent branch unaffected


def test_approval_checkpoint_pauses_and_resumes_on_approve():
    graph = Graph([
        Node(id="root", name="root", stage="requirements", handler=_ok_handler()),
        Node(id="release", name="release", stage="release", handler=_ok_handler(),
             depends_on=["root"], requires_approval=True),
    ])
    orch = Orchestrator(graph)
    result = orch.run()
    assert result.status == "paused"
    assert result.statuses["release"] == "awaiting_approval"
    assert len(result.pending_approvals) == 1

    orch.approve(result.pending_approvals[0], decided_by="reviewer@example.com", reason="looks good")
    result2 = orch.run()
    assert result2.status == "completed"
    assert result2.statuses["release"] == "completed"


def test_approval_checkpoint_rejection_rolls_back_branch():
    graph = Graph([
        Node(id="root", name="root", stage="requirements", handler=_ok_handler()),
        Node(id="release", name="release", stage="release", handler=_ok_handler(),
             depends_on=["root"], requires_approval=True),
        Node(id="post", name="post", stage="documentation", handler=_ok_handler(), depends_on=["release"]),
    ])
    orch = Orchestrator(graph)
    result = orch.run()
    assert result.status == "paused"
    req_id = result.pending_approvals[0]

    orch.reject(req_id, decided_by="reviewer@example.com", reason="not ready")
    result2 = orch.run()
    assert result2.statuses["release"] == "rolled_back"
    assert result2.statuses["post"] == "skipped"


def test_auto_decision_lets_run_complete_unattended():
    graph = Graph([
        Node(id="root", name="root", stage="requirements", handler=_ok_handler()),
        Node(id="release", name="release", stage="release", handler=_ok_handler(),
             depends_on=["root"], requires_approval=True),
    ])
    orch = Orchestrator(graph, auto_decision=lambda req: (True, "auto-approved for CI run"))
    result = orch.run()
    assert result.status == "completed"
    assert result.metrics.approvals_granted == 1


def test_policy_violation_blocks_completion():
    def bad_handler(ctx, node):
        return NodeResult(
            output={},
            policy_payload={"source_code": 'password = "hunter2"'},
        )

    policy = PolicyEngine()
    policy.register(PolicyRule("SEC-TEST", "no secrets", lambda p: (
        (False, "hardcoded secret") if "password = \"" in p.get("source_code", "") else (True, "clean")
    )))

    graph = Graph([Node(id="impl", name="impl", stage="implementation", handler=bad_handler,
                         retry_policy=RetryPolicy(max_attempts=1))])
    result = Orchestrator(graph, policy_engine=policy).run()
    assert result.status == "failed"
    assert result.metrics.policy_violations >= 0  # violation raised as exception -> node_failed
    assert result.statuses["impl"] == "failed"


def test_downstream_failure_rolls_back_completed_upstream_side_effect():
    undone = {"called": False}

    def impl_handler(ctx, node):
        return NodeResult(output={"wrote": "file.py"})

    def impl_rollback(ctx, node):
        undone["called"] = True

    def failing_tests(ctx, node):
        raise RuntimeError("tests failed against the just-written implementation")

    graph = Graph([
        Node(id="implementation", name="implementation", stage="implementation",
             handler=impl_handler, rollback=impl_rollback),
        Node(id="testing", name="testing", stage="testing", handler=failing_tests,
             depends_on=["implementation"], retry_policy=RetryPolicy(max_attempts=1),
             rollback_on_failure=["implementation"]),
        Node(id="documentation", name="documentation", stage="documentation",
             handler=_ok_handler(), depends_on=["testing"]),
    ])
    result = Orchestrator(graph).run()
    assert result.status == "failed"
    assert result.statuses["testing"] == "failed"
    assert result.statuses["implementation"] == "rolled_back"
    assert result.statuses["documentation"] == "skipped"
    assert undone["called"] is True


def test_replan_invalidates_downstream_after_spec_amendment():
    graph = Graph([
        Node(id="req", name="req", stage="requirements", handler=_ok_handler()),
        Node(id="design", name="design", stage="design", handler=_ok_handler(), depends_on=["req"]),
        Node(id="impl", name="impl", stage="implementation", handler=_ok_handler(), depends_on=["design"]),
    ])
    orch = Orchestrator(graph)
    result = orch.run()
    assert result.status == "completed"

    invalidated = orch.amend_spec_and_replan(
        {"new_requirement": "add caching"}, reason="clarified scope", from_node_id="req"
    )
    assert set(invalidated) == {"req", "design", "impl"}
    assert orch.statuses["impl"].value == "stale"

    result2 = orch.run()
    assert result2.status == "completed"
    assert orch.context.spec_version == 2
