"""Writes a scenario run's audit trail, metrics, and decision lineage to
docs/scenario_reports/ as both machine-readable JSON and a human-readable
Markdown summary."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

REPORTS_DIR = Path(__file__).parent.parent / "docs" / "scenario_reports"


def write_scenario_report(orch, scenario_name: str, narrative: list[str]) -> tuple[Path, Path]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    status = orch.status_report()
    metrics = orch.audit  # placeholder to keep type checkers quiet; real metrics computed below
    from .metrics import compute_metrics
    m = compute_metrics(orch.audit, total_nodes=len(orch.graph.nodes))

    data = {
        "scenario": scenario_name,
        "run_id": orch.run_id,
        "final_status": status,
        "metrics": asdict(m),
        "spec_snapshot": orch.context.snapshot(),
        "decision_lineage": orch.context.lineage(),
        "audit_trail": orch.audit.to_list(),
        "narrative": narrative,
    }

    json_path = REPORTS_DIR / f"{scenario_name}.json"
    json_path.write_text(json.dumps(data, indent=2, default=str))

    md_lines = [f"# Scenario report: {scenario_name}", ""]
    md_lines.append(f"- Run ID: `{orch.run_id}`")
    md_lines.append(f"- Final status: **{status['statuses']}**".replace("'", ""))
    md_lines.append(f"- Spec version: {status['spec_version']}")
    md_lines.append("")
    md_lines.append("## Narrative")
    for line in narrative:
        md_lines.append(f"- {line}")
    md_lines.append("")
    md_lines.append("## Node outcomes")
    for node_id, s in status["statuses"].items():
        md_lines.append(f"- `{node_id}`: **{s}**")
    md_lines.append("")
    md_lines.append("## Reliability metrics")
    for k, v in asdict(m).items():
        if k == "per_node_latency_seconds":
            continue
        md_lines.append(f"- {k}: {v}")
    md_lines.append("")
    md_lines.append("## Per-node latency (s)")
    for node_id, seconds in m.per_node_latency_seconds.items():
        md_lines.append(f"- `{node_id}`: {seconds}")
    md_lines.append("")
    md_lines.append("## Decision lineage")
    for entry in orch.context.lineage():
        if entry["type"] == "spec_amendment":
            md_lines.append(f"- spec amended (v{entry['spec_version']}): {entry['reason']}")
        else:
            decisions = "; ".join(entry.get("decisions", [])) or "(no decisions recorded)"
            md_lines.append(f"- `{entry['node_id']}` (v{entry['version']}): {decisions}")

    md_path = REPORTS_DIR / f"{scenario_name}.md"
    md_path.write_text("\n".join(md_lines))
    return json_path, md_path
