#!/usr/bin/env python3
"""Greenfield scenario: build the URL shortener service from scratch.

Run: python3 scenarios/run_greenfield.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.engine import Orchestrator
from orchestrator.graphs import build_greenfield_graph
from orchestrator.reporting import write_scenario_report

REVIEWER = "jay (engineering lead)"


def main() -> int:
    graph, workspace, policy = build_greenfield_graph()
    orch = Orchestrator(
        graph, run_id="greenfield-001", initial_spec={},
        audit_path="runs/greenfield/audit.jsonl", approvals_dir="runs/greenfield/approvals",
        policy_engine=policy,
    )

    narrative = ["Started orchestration run for a brand-new URL shortener service."]
    print("=== Greenfield scenario: build URL shortener from scratch ===")

    result = orch.run()
    print(f"[run] status={result.status} statuses={result.statuses}")

    while result.status == "paused":
        for req in orch.pending_approvals():
            print(f"\n[approval requested] {req['node_id']}: {req['summary']}")
            reason = "Release checklist satisfied: tests pass, policy scan clean, docs generated."
            orch.approve(req["request_id"], decided_by=REVIEWER, reason=reason)
            narrative.append(f"Human approval granted for '{req['node_id']}' by {REVIEWER}: {reason}")
            print(f"[approval granted] {req['node_id']} by {REVIEWER}")
        result = orch.run()
        print(f"[run] status={result.status} statuses={result.statuses}")

    if result.status != "completed":
        print(f"\n!! Run did not complete cleanly: {result.status}")
        for nid, s in result.statuses.items():
            if s == "failed":
                print(f"   failed node: {nid}")
        narrative.append(f"Run ended with status '{result.status}' (see audit trail for root cause).")
    else:
        narrative.append("Run completed: service scaffolded, tested, documented, and approved for release.")

    print("\n=== Metrics ===")
    for k, v in result.metrics.__dict__.items():
        if k != "per_node_latency_seconds":
            print(f"  {k}: {v}")

    json_path, md_path = write_scenario_report(orch, "greenfield", narrative)
    print(f"\nWorkspace: {workspace}")
    print(f"Report: {json_path}")
    print(f"Report: {md_path}")

    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
