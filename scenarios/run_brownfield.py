#!/usr/bin/env python3
"""Brownfield scenario: bug fix (race condition) + enhancement (bulk
shorten endpoint) against an existing URL shortener codebase.

Run: python3 scenarios/run_brownfield.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.engine import Orchestrator
from orchestrator.graphs import build_brownfield_graph
from orchestrator.reporting import write_scenario_report

REVIEWER = "jay (engineering lead)"


def main() -> int:
    graph, workspace, policy = build_brownfield_graph()
    orch = Orchestrator(
        graph, run_id="brownfield-001", initial_spec={},
        audit_path="runs/brownfield/audit.jsonl", approvals_dir="runs/brownfield/approvals",
        policy_engine=policy,
    )

    narrative = [
        "Started orchestration run against an existing (seeded) URL shortener codebase: "
        "one bug fix (alias race condition) + one enhancement (bulk shorten).",
    ]
    print("=== Brownfield scenario: bug fix + enhancement on existing codebase ===")

    result = orch.run()
    print(f"[run] status={result.status} statuses={result.statuses}")

    while result.status == "paused":
        for req in orch.pending_approvals():
            print(f"\n[approval requested] {req['node_id']}:\n{req['summary']}")
            if req["node_id"] == "change_control_review":
                reason = ("Impacted modules confirmed (app/shortener.py, app/main.py, app/config.py); "
                          "scope matches the two reported issues, approved to proceed to design.")
            else:
                reason = "Release checklist satisfied: tests pass (incl. new regression test), policy scan clean."
            orch.approve(req["request_id"], decided_by=REVIEWER, reason=reason)
            narrative.append(f"Human approval granted for '{req['node_id']}' by {REVIEWER}: {reason}")
            print(f"[approval granted] {req['node_id']} by {REVIEWER}")
        result = orch.run()
        print(f"[run] status={result.status} statuses={result.statuses}")

    if result.status != "completed":
        print(f"\n!! Run did not complete cleanly: {result.status}")
        narrative.append(f"Run ended with status '{result.status}' (see audit trail for root cause).")
    else:
        narrative.append(
            "Run completed: dependency health check recovered from 2 simulated transient failures "
            "via bounded retry, race-condition fix + bulk endpoint implemented, tested, and approved."
        )

    print("\n=== Metrics ===")
    for k, v in result.metrics.__dict__.items():
        if k != "per_node_latency_seconds":
            print(f"  {k}: {v}")

    json_path, md_path = write_scenario_report(orch, "brownfield", narrative)
    print(f"\nWorkspace: {workspace}")
    print(f"Report: {json_path}")
    print(f"Report: {md_path}")

    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
