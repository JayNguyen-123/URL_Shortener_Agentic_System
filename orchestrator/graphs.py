"""Scenario graph builders.

This module is the only place that is genuinely scenario-specific: it
supplies the raw requirement text, the task lexicon (see
agents.decompose_tasks), and the dependency-graph wiring (who requires
approval, what retries/rollbacks apply, which nodes are safety-critical).
The stage *behavior* comes entirely from orchestrator/agents.py, which has
no knowledge of which scenario is running.
"""
from __future__ import annotations

from pathlib import Path

from . import agents
from .governance import default_policy_engine
from .graph import Graph, Node, NodeResult
from .reliability import RetryPolicy
from .workspace import create_empty_workspace, create_seeded_workspace, workspace_path


# --------------------------------------------------------------------------- #
# Shared gates
# --------------------------------------------------------------------------- #

def _implementation_entry_gate(ctx, node):
    tasks = ctx.spec.get("tasks", [])
    if not tasks:
        return False, "no tasks were decomposed from the requirement -- nothing to implement"
    return True, f"{len(tasks)} task(s) ready for implementation"


def _testing_exit_gate(ctx, node, result: NodeResult):
    total = result.output.get("total", 0)
    if total == 0:
        return False, "zero tests executed -- refusing to treat an empty run as a pass"
    return True, f"executed {total} test(s)"


def _release_notification_node(workspace: Path) -> Node:
    primary, fallback = agents.make_release_notification_handler(
        webhook_url="https://hooks.example.invalid/release",
        outbox_dir=workspace.parent / "notifications",
    )
    return Node(
        id="release_notification", name="Release Notification", stage="release",
        handler=primary, fallback=fallback, depends_on=["release_readiness"],
        retry_policy=RetryPolicy(max_attempts=1),
    )


# --------------------------------------------------------------------------- #
# Greenfield: build the URL shortener from scratch.
# --------------------------------------------------------------------------- #

GREENFIELD_REQUIREMENT = (
    "Build a URL shortener service from scratch with core APIs (create short link, redirect, "
    "deactivate link), click analytics, and basic reliability features like a redirect cache and "
    "rate limiting on write endpoints."
)
GREENFIELD_LEXICON = [
    {
        "id": "T1-scaffold", "capability": "scaffold_service",
        "description": "Scaffold the core service (shorten/redirect/analytics/rate-limit/cache) from scratch.",
        "triggers": ["from scratch", "core api"],
    },
]


def build_greenfield_graph(run_id: str = "greenfield"):
    workspace = create_empty_workspace("greenfield")
    policy = default_policy_engine()

    nodes = [
        Node(
            id="requirements", name="Requirements Analysis", stage="requirements",
            handler=agents.make_requirements_handler(
                GREENFIELD_REQUIREMENT, "greenfield", GREENFIELD_LEXICON, workspace=None,
            ),
            safe_stop_on_failure=True,
        ),
        Node(
            id="design", name="Architecture & Design", stage="design",
            handler=agents.design_handler, depends_on=["requirements"],
        ),
        Node(
            id="implementation", name="Implementation", stage="implementation",
            handler=agents.make_implementation_handler(workspace), depends_on=["design"],
            entry_gate=_implementation_entry_gate, retry_policy=RetryPolicy(max_attempts=2),
        ),
        Node(
            id="testing", name="Automated Testing", stage="testing",
            handler=agents.make_testing_handler(workspace, scaffold_baseline_tests=True),
            depends_on=["implementation"], exit_gate=_testing_exit_gate,
            retry_policy=RetryPolicy(max_attempts=1), rollback_on_failure=["implementation"],
            parallel_group="verification",
        ),
        Node(
            id="policy_scan", name="Policy & Security Scan", stage="testing",
            handler=agents.make_policy_scan_handler(workspace), depends_on=["implementation"],
            parallel_group="verification",
        ),
        Node(
            id="documentation", name="Documentation Generation", stage="documentation",
            handler=agents.make_documentation_handler(workspace, "greenfield"),
            depends_on=["testing", "policy_scan"],
        ),
        Node(
            id="release_readiness", name="Release Readiness Review", stage="release",
            handler=agents.make_release_readiness_handler(), depends_on=["documentation"],
            requires_approval=True,
            approval_summary=agents.approval_summary_for_confirmation("release_readiness"),
        ),
        _release_notification_node(workspace),
    ]
    graph = Graph(nodes)
    return graph, workspace, policy


# --------------------------------------------------------------------------- #
# Brownfield: enhancement + bug fix against an existing codebase.
# --------------------------------------------------------------------------- #

BROWNFIELD_REQUIREMENT = (
    "Users occasionally hit errors creating custom-alias short links under concurrent load -- looks "
    "like a race condition in short-code allocation. Separately, the growth team wants a bulk "
    "endpoint so their campaign tooling can shorten many URLs in one request."
)
BROWNFIELD_LEXICON = [
    {
        "id": "T1-fix-race", "capability": "fix_alias_race_condition",
        "description": "Fix the check-then-act race condition in short-code/alias allocation.",
        "triggers": ["race condition"],
    },
    {
        "id": "T2-bulk", "capability": "add_bulk_shorten_endpoint",
        "description": "Add a bulk shorten endpoint for campaign tooling.",
        "triggers": ["bulk endpoint", "one request"],
    },
]


def build_brownfield_graph(seed_dir: Path | None = None):
    workspace = create_seeded_workspace("brownfield", seed_dir=seed_dir)
    policy = default_policy_engine()

    nodes = [
        Node(
            id="requirements", name="Requirements Analysis", stage="requirements",
            handler=agents.make_requirements_handler(
                BROWNFIELD_REQUIREMENT, "brownfield", BROWNFIELD_LEXICON, workspace=workspace,
            ),
            safe_stop_on_failure=True,
        ),
        Node(
            id="change_control_review", name="Change Control Review", stage="requirements",
            handler=agents.make_confirmation_handler("change_control_review"),
            depends_on=["requirements"], requires_approval=True,
            approval_summary=agents.approval_summary_for_confirmation("change_control_review"),
        ),
        Node(
            id="dependency_health_check", name="Dependency Health Check", stage="design",
            handler=agents.make_dependency_health_check_handler(fail_first_n=2),
            depends_on=["change_control_review"], retry_policy=RetryPolicy(max_attempts=3),
        ),
        Node(
            id="design", name="Architecture & Design", stage="design",
            handler=agents.design_handler, depends_on=["dependency_health_check"],
        ),
        Node(
            id="implementation", name="Implementation", stage="implementation",
            handler=agents.make_implementation_handler(workspace), depends_on=["design"],
            entry_gate=_implementation_entry_gate, retry_policy=RetryPolicy(max_attempts=2),
        ),
        Node(
            id="testing", name="Automated Testing", stage="testing",
            handler=agents.make_testing_handler(workspace, scaffold_baseline_tests=False),
            depends_on=["implementation"], exit_gate=_testing_exit_gate,
            retry_policy=RetryPolicy(max_attempts=1), rollback_on_failure=["implementation"],
            parallel_group="verification",
        ),
        Node(
            id="policy_scan", name="Policy & Security Scan", stage="testing",
            handler=agents.make_policy_scan_handler(workspace), depends_on=["implementation"],
            parallel_group="verification",
        ),
        Node(
            id="documentation", name="Documentation Generation", stage="documentation",
            handler=agents.make_documentation_handler(workspace, "brownfield"),
            depends_on=["testing", "policy_scan"],
        ),
        Node(
            id="release_readiness", name="Release Readiness Review", stage="release",
            handler=agents.make_release_readiness_handler(), depends_on=["documentation"],
            requires_approval=True,
            approval_summary=agents.approval_summary_for_confirmation("release_readiness"),
        ),
        _release_notification_node(workspace),
    ]
    graph = Graph(nodes)
    return graph, workspace, policy


# --------------------------------------------------------------------------- #
# Ambiguous requirement: interpret, confirm scope, implement, then re-plan
# after a mid-run clarification.
# --------------------------------------------------------------------------- #

AMBIGUOUS_REQUIREMENT = (
    "Make the link service more reliable and give us better visibility into how links are performing."
)
AMBIGUOUS_LEXICON = [
    {
        "id": "T1-visibility", "capability": "add_analytics_summary_endpoint",
        "description": "Expose an aggregate operational visibility endpoint (scope pending confirmation).",
        "triggers": ["visibility", "performing"],
    },
    {
        "id": "T2-latency-p95", "capability": "add_latency_percentile",
        "description": "Add p95 redirect latency to the visibility endpoint (added after re-plan).",
        "triggers": ["p95", "percentile"],
    },
]


def build_ambiguous_graph(seed_dir: Path | None = None):
    workspace = create_seeded_workspace("ambiguous", seed_dir=seed_dir)
    policy = default_policy_engine()

    nodes = [
        Node(
            id="requirements", name="Requirements Analysis", stage="requirements",
            handler=agents.make_requirements_handler(
                AMBIGUOUS_REQUIREMENT, "ambiguous", AMBIGUOUS_LEXICON, workspace=workspace,
            ),
            safe_stop_on_failure=True,
        ),
        Node(
            id="scope_confirmation", name="Scope Confirmation", stage="requirements",
            handler=agents.make_confirmation_handler("scope_confirmation"),
            depends_on=["requirements"], requires_approval=True,
            approval_summary=agents.approval_summary_for_confirmation("scope_confirmation"),
        ),
        Node(
            id="design", name="Architecture & Design", stage="design",
            handler=agents.design_handler, depends_on=["scope_confirmation"],
        ),
        Node(
            id="implementation", name="Implementation", stage="implementation",
            handler=agents.make_implementation_handler(workspace), depends_on=["design"],
            entry_gate=_implementation_entry_gate, retry_policy=RetryPolicy(max_attempts=2),
        ),
        Node(
            id="testing", name="Automated Testing", stage="testing",
            handler=agents.make_testing_handler(workspace, scaffold_baseline_tests=False),
            depends_on=["implementation"], exit_gate=_testing_exit_gate,
            retry_policy=RetryPolicy(max_attempts=1), rollback_on_failure=["implementation"],
            parallel_group="verification",
        ),
        Node(
            id="policy_scan", name="Policy & Security Scan", stage="testing",
            handler=agents.make_policy_scan_handler(workspace), depends_on=["implementation"],
            parallel_group="verification",
        ),
        Node(
            id="documentation", name="Documentation Generation", stage="documentation",
            handler=agents.make_documentation_handler(workspace, "ambiguous"),
            depends_on=["testing", "policy_scan"],
        ),
        Node(
            id="release_readiness", name="Release Readiness Review", stage="release",
            handler=agents.make_release_readiness_handler(), depends_on=["documentation"],
            requires_approval=True,
            approval_summary=agents.approval_summary_for_confirmation("release_readiness"),
        ),
        _release_notification_node(workspace),
    ]
    graph = Graph(nodes)
    return graph, workspace, policy
