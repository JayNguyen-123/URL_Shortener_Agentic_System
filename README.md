# Agentic Software Engineering System — URL Shortener

An agentic SDLC orchestration engine, driving a real URL-shortener service
through requirements → design → implementation → testing → documentation →
release readiness, with governance (policy guardrails + human approval
checkpoints), reliability controls (bounded retry, fallback, rollback,
safe-stop), audit-grade observability, reliability metrics, and dynamic
re-planning.

Built for the take-home assignment "Build an Agentic Software Engineering
System — URL Shortener." See `docs/` for the full write-up.

## What's in here

| Path | What it is |
|---|---|
| `service/` | The URL shortener product itself: a hand-authored, fully tested Flask service (this is also the reference implementation the orchestrator's Implementation Agent scaffolds from). |
| `orchestrator/` | The orchestration engine — the "critical differentiator" piece: dependency graph, gates, governance, reliability, audit, metrics, re-planning, and the SDLC stage agents. |
| `scenarios/` | Three runnable scenarios (greenfield, brownfield, ambiguous requirement) that drive the orchestrator end-to-end against real, generated code. |
| `docs/` | Architecture overview, setup instructions, testing/trade-offs, final engineering summary, and one report per scenario run (audit trail + metrics + decision lineage). |
| `runs/` | Output of the last scenario runs: generated workspaces, audit logs (JSONL), approval records. Safe to delete and regenerate. |

## Quick start

```bash
# 1. Run the service's own test suite (the hand-authored baseline)
scripts/run_pytest.sh -q service/tests

# 2. Run the orchestration engine's own test suite (graph/gates/retry/
#    approval/rollback/re-plan mechanics, independent of the URL shortener)
scripts/run_pytest.sh -q orchestrator/tests

# 3. Run all three end-to-end scenarios (each builds/modifies a real
#    workspace, runs real pytest against it, and writes a report to
#    docs/scenario_reports/)
python3 scenarios/run_greenfield.py
python3 scenarios/run_brownfield.py
python3 scenarios/run_ambiguous.py

# 4. Run the service locally
cd service && FLASK_APP=app.main:create_app python3 -m flask run --port 8000
```

See `docs/setup.md` for full setup instructions and environment notes,
`docs/architecture.md` for the design, and `docs/final_engineering_summary.md`
for the plan/rationale/risks/assumptions/limitations write-up.
