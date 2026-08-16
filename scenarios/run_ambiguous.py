#!/usr/bin/env python3
"""Ambiguous-requirement scenario: "make the link service more reliable and
give us better visibility into how links are performing."

Demonstrates: ambiguity detection, proposed assumptions, a human approval
checkpoint (scope_confirmation) before any code is written, a first
release, then a genuine dynamic re-plan triggered by reviewer feedback
("also track p95 latency, not just counts") that invalidates and
incrementally re-executes only the affected downstream stages.

Run: python3 scenarios/run_ambiguous.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.engine import Orchestrator
from orchestrator.graphs import build_ambiguous_graph
from orchestrator.reporting import write_scenario_report

REVIEWER = "jay (engineering lead)"


def run_to_completion_or_pause(orch, narrative, label):
    result = orch.run()
    print(f"[{label}] status={result.status} statuses={result.statuses}")
    while result.status == "paused":
        for req in orch.pending_approvals():
            print(f"\n[approval requested] {req['node_id']}:\n{req['summary']}")
            if req["node_id"] == "scope_confirmation":
                reason = ("Assumptions accepted: 'reliable' = correct handling of "
                          "expired/missing/deactivated links + cache-backed redirects; "
                          "'visibility' = an aggregate ops summary endpoint. Proceed.")
            else:
                reason = "Release checklist satisfied: tests pass, policy scan clean, docs regenerated."
            orch.approve(req["request_id"], decided_by=REVIEWER, reason=reason)
            narrative.append(f"Human approval granted for '{req['node_id']}' by {REVIEWER}: {reason}")
            print(f"[approval granted] {req['node_id']} by {REVIEWER}")
        result = orch.run()
        print(f"[{label}] status={result.status} statuses={result.statuses}")
    return result


def main() -> int:
    graph, workspace, policy = build_ambiguous_graph()
    orch = Orchestrator(
        graph, run_id="ambiguous-001", initial_spec={},
        audit_path="runs/ambiguous/audit.jsonl", approvals_dir="runs/ambiguous/approvals",
        policy_engine=policy,
    )

    narrative = [
        "Started orchestration run for the ambiguous requirement: "
        "'Make the link service more reliable and give us better visibility "
        "into how links are performing.'",
    ]
    print("=== Ambiguous-requirement scenario (round 1) ===")

    result = run_to_completion_or_pause(orch, narrative, "round1")
    if result.status != "completed":
        print(f"\n!! Round 1 did not complete cleanly: {result.status}")
        write_scenario_report(orch, "ambiguous", narrative)
        return 1
    narrative.append("Round 1 completed and released: operational summary endpoint shipped per "
                      "confirmed scope (link/click counts + cache stats).")

    print("\n=== Reviewer feedback arrives after round 1: add p95 latency, not just counts ===")
    invalidated = orch.amend_spec_and_replan(
        patch={
            "raw_requirement": (
                "Make the link service more reliable and give us better visibility into how "
                "links are performing, including p95 latency, not just averages/counts."
            )
        },
        reason="Reviewer feedback during release review: aggregate counts alone don't give an "
               "actionable performance signal; requested p95 redirect latency.",
        from_node_id="requirements",
    )
    narrative.append(f"Dynamic re-plan triggered by reviewer feedback; invalidated and queued for "
                      f"incremental re-execution: {invalidated}")
    print(f"[replan] invalidated nodes: {invalidated}")

    print("\n=== Ambiguous-requirement scenario (round 2, post re-plan) ===")
    result = run_to_completion_or_pause(orch, narrative, "round2")

    if result.status != "completed":
        print(f"\n!! Round 2 did not complete cleanly: {result.status}")
        narrative.append(f"Run ended with status '{result.status}' (see audit trail for root cause).")
    else:
        narrative.append("Round 2 completed and released: p95 redirect latency added to the "
                          "summary endpoint; only the affected stages were re-executed "
                          "(scaffold/scope-confirmation text and the T1 endpoint were NOT redone).")

    print("\n=== Metrics ===")
    for k, v in result.metrics.__dict__.items():
        if k != "per_node_latency_seconds":
            print(f"  {k}: {v}")

    json_path, md_path = write_scenario_report(orch, "ambiguous", narrative)
    print(f"\nWorkspace: {workspace}")
    print(f"Report: {json_path}")
    print(f"Report: {md_path}")

    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
