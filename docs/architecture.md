# Architecture Overview

## 1. Two systems, cleanly separated

This repository contains two things that are deliberately decoupled:

1. **The product**: a URL shortener service (`service/`) — the thing being
   built. It knows nothing about agents or orchestration.
2. **The orchestration engine** (`orchestrator/`) — an agentic SDLC
   coordination layer that is *generic*: it has no knowledge of URL
   shorteners. It only knows about nodes, dependencies, gates, governance,
   and reliability controls.

The link between them is `orchestrator/agents.py` (stage handlers) and
`orchestrator/capabilities.py` (concrete code-generation actions), which
are domain-specific by necessity — task decomposition and implementation
have to know *something* about the product — but the engine that walks the
graph, enforces approvals, retries, rolls back, and computes metrics does
not. This is what lets three very different scenarios (greenfield,
brownfield, ambiguous requirement) run through the exact same engine code
(`orchestrator/engine.py`) with zero changes.

```
                 ┌─────────────────────────────────────────────┐
                 │           orchestrator/graphs.py              │
                 │   (scenario-specific graph assembly: which    │
                 │    nodes, deps, approvals, retry policies)     │
                 └───────────────────┬─────────────────────────┘
                                      │ builds
                                      ▼
┌──────────────┐   drives    ┌─────────────────┐   emits    ┌───────────────┐
│ scenarios/*.py│ ─────────► │ engine.Orchestrator│ ────────►│ audit.AuditLog │
│ (run/approve/  │            │  (graph walker)   │           │ (JSONL trail)  │
│  amend/replan) │            └─────┬────┬────┬───┘           └───────┬───────┘
└──────────────┘                    │    │    │                       │
                                     │    │    │                       ▼
                     entry/exit  ┌───▼┐ ┌─▼──┐ ▼ approval      metrics.compute_metrics
                     gates       │gov-│ │reli-│  gate           (success rate, retry/
                     (governance)│ern-│ │abil-│ (governance.    rollback freq, MTTR,
                                 │ance│ │ity  │  ApprovalGate)   e2e latency)
                                 └────┘ └────┘
                                      │
                                      ▼ handler(ctx, node)
                          orchestrator/agents.py (stage logic)
                                      │
                                      ▼ dispatches by task.capability
                       orchestrator/capabilities.py (real file writes)
                                      │
                                      ▼
                     runs/<scenario>/workspace/  (a real, on-disk,
                     git-free Flask app + pytest suite -- validated by
                     actually running pytest via orchestrator/testrunner.py)
```

## 2. The orchestration engine (`orchestrator/`)

| Module | Responsibility |
|---|---|
| `graph.py` | `Node`/`Graph`: explicit dependency DAG, cycle detection, readiness computation (`ready_nodes`), reachability (`reachable_from`, used by rollback cascades and the re-planner). |
| `state.py` | `RunContext`: the shared, content-hashed memory of a run. Every node's output is hashed and linked to the hashes of the inputs that produced it (`NodeRecord.input_hashes`) — this is the decision-lineage mechanism. |
| `governance.py` | `PolicyEngine` (automated security/compliance/change-control rules evaluated at gates) and `ApprovalGate` (human-in-the-loop checkpoints, persisted to disk as JSON so a real reviewer could act on them out-of-process). |
| `reliability.py` | `RetryPolicy` + `run_with_retries` (bounded retry with optional fallback), `RollbackRegistry` (undo completed side effects), `SafeStop` (halt the whole run, not just a branch). |
| `replanner.py` | Marks everything downstream of a changed node `STALE` (via `Graph.reachable_from`) so re-execution is incremental, not "start over." |
| `audit.py` | Append-only structured JSONL event log — the single source of truth every other subsystem reads from. |
| `metrics.py` | Computes success rate, retry/rollback frequency, MTTR, and end-to-end + per-node latency *from the audit log*, not from separately-tracked counters, so metrics can never drift from the trail a human would read to audit the run. |
| `engine.py` | `Orchestrator`: the graph walker that ties all of the above together. |
| `agents.py` | The six SDLC stage handlers (requirements, design, implementation, testing, documentation, release readiness) plus reusable analysis functions (ambiguity detection, task decomposition, codebase reasoning). |
| `capabilities.py` | The concrete, file-level "code generation" actions the Implementation Agent dispatches to (deterministic/templated — see `docs/testing_and_tradeoffs.md` for why). |
| `graphs.py` | Scenario-specific graph assembly: the only place that differs meaningfully between greenfield/brownfield/ambiguous. |
| `testrunner.py` / `introspect.py` | Run the real pytest suite against a scenario workspace; generate API docs from the actually-registered Flask routes. |

## 3. Execution model: waves, not a linear chain

`Orchestrator.run()` repeatedly computes the set of nodes whose
dependencies are **all** `COMPLETED` (`Graph.ready_nodes`) and executes
that entire "wave" concurrently via a thread pool. Independent branches
(e.g. `testing` and `policy_scan`, which both depend only on
`implementation`) genuinely run in parallel; a node that depends on both
(`documentation`) simply never becomes ready until both finish — a
synchronization barrier that falls directly out of the dependency graph,
with no special-casing needed. Every scenario in this repo exercises this
fan-out/fan-in path for real (see `docs/scenario_reports/*.md`).

## 4. Governance: two distinct mechanisms

- **Policy guardrails** (`governance.PolicyEngine`) are automated rules
  evaluated at a node's exit gate against the code/metadata it produced:
  no raw PII persisted (`SEC-001`), no hardcoded secrets (`SEC-002`),
  public write endpoints must be rate-limited (`SEC-003`), new endpoints
  must have test coverage before release (`QA-001`). A blocking violation
  raises `PolicyViolation`, which the retry/failure machinery treats like
  any other node failure (cascading skip of downstream nodes).
- **Human approval checkpoints** (`governance.ApprovalGate`) pause the
  *branch* (not the whole run) at high-impact nodes. The engine persists a
  JSON approval request to `runs/<scenario>/approvals/` and returns a
  `"paused"` `RunResult`; a human (or, for unattended CI-style runs, an
  `auto_decision` callback) later calls `approve()`/`reject()`, and
  `run()` resumes exactly where it left off — no re-execution of already
  completed nodes. Rejection marks the node `ROLLED_BACK` and cascades a
  skip to everything downstream of it.

Which nodes require approval is **risk-scaled per scenario**, not a
blanket rule: greenfield only gates `release_readiness` (new system,
unambiguous requirement, nothing existing at risk); brownfield adds a
`change_control_review` gate before any existing code is touched; the
ambiguous scenario adds a `scope_confirmation` gate before *any* work
starts, because the risk there is building the wrong thing, not breaking
something that already works.

## 5. Reliability controls, exercised for real

- **Bounded retry**: every node has a `RetryPolicy` (default 3 attempts).
  Exercised for real (not just in the synthetic engine test suite) by the
  brownfield scenario's `dependency_health_check` node, a deliberately
  flaky pre-flight check (see `docs/testing_and_tradeoffs.md`).
- **Fallback**: `release_notification` tries a webhook first; this
  sandbox's network egress genuinely does not allow it, so the fallback
  (write to a local outbox file) fires for real in every scenario run —
  an honest demonstration of graceful degradation, not a staged one.
- **Rollback**: `RollbackRegistry` records an undo action whenever a node
  with a `rollback` callable completes; `Node.rollback_on_failure` lets a
  downstream node (e.g. `testing`) trigger rollback of a specific upstream
  node (e.g. `implementation`) if it fails terminally, cascading a skip to
  everything downstream of the rolled-back node. Proven in
  `orchestrator/tests/test_engine_core.py::test_downstream_failure_rolls_back_completed_upstream_side_effect`;
  wired into every scenario's `testing` node so it would fire on a real
  test failure, not just in the unit test.
- **Safe-stop**: `Node.safe_stop_on_failure=True` on every scenario's
  `requirements` node — if requirements analysis itself fails, the whole
  run halts rather than proceeding on an unknown foundation.

## 6. Dynamic re-planning

`Orchestrator.amend_spec_and_replan(patch, reason, from_node_id)` updates
the shared spec and marks everything reachable from `from_node_id`
`STALE`. The ambiguous-requirement scenario demonstrates this for real:
after an initial release, simulated reviewer feedback ("also track p95
latency") amends the spec and invalidates `requirements` through
`release_notification`. Re-execution is **incremental**: the Implementation
Agent tracks which capabilities were already applied
(`ctx.spec["applied_capabilities"]`) and skips them on re-run, so round 2
only adds the new p95-latency capability instead of re-running (and
corrupting) the already-applied visibility-endpoint patch. See
`docs/scenario_reports/ambiguous.md` for the full trace.

## 7. The product architecture (`service/`)

- **Flask + SQLite**, no ORM: the schema is small and stable; a thin
  `sqlite3` wrapper (`app/db.py`) keeps the dependency footprint minimal.
  (`fastapi` was the initial choice but was unavailable in this sandbox's
  network-restricted package environment — see `docs/setup.md`.)
- **Domain logic separated from HTTP** (`app/shortener.py`,
  `app/analytics.py` vs `app/main.py`) so it's unit-testable without a
  running server.
- **Reliability**: an in-memory TTL cache (`app/cache.py`) fronts the
  read-heavy redirect path; a fixed-window rate limiter (`app/rate_limit.py`)
  protects public write endpoints.
- **Security**: client IPs are hashed (SHA-256, truncated) before being
  persisted for click analytics — never stored raw. Custom aliases are
  validated against a strict character/length pattern. Short-code
  allocation is atomic at the database layer (PRIMARY KEY +
  `IntegrityError` handling — see the brownfield scenario for why).

Full endpoint list: `docs/scenario_reports/greenfield.md` (or run
`python3 scenarios/run_greenfield.py` and read the generated
`API_REFERENCE.generated.md` in the workspace, which is produced by
introspecting the actual live Flask routes).
