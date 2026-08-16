"""SDLC stage agents: the actual work performed at each node in the graph.

Each `make_*_handler` factory returns a `HandlerFn` (see graph.py) closed
over whatever scenario-specific config it needs (a workspace path, a raw
requirement string, a task lexicon). The factories themselves -- and the
analysis functions below (`detect_ambiguities`, `decompose_tasks`,
`analyze_codebase`) -- are scenario-agnostic: the exact same code drives
all three scenarios in scenarios/, only the data passed in differs. That
is what makes this "an orchestration layer", not three bespoke scripts.

Extensibility point: every handler currently calls a deterministic,
rule-based analysis/generation step. To swap in a live LLM backend for
requirements analysis or code generation, implement the `AgentBackend`
protocol below and pass an instance through the `backend=` parameter --
the graph/engine code does not need to change.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .capabilities import (
    CAPABILITY_REGISTRY,
    CAPABILITY_TARGET_MODULES,
    _extract_endpoints,
)
from .graph import Node, NodeResult
from .introspect import introspect_routes
from .state import RunContext
from .testrunner import run_pytest


# --------------------------------------------------------------------------- #
# Pluggable backend interface (extensibility point; see module docstring).
# --------------------------------------------------------------------------- #

class AgentBackend(Protocol):
    def analyze_requirement(self, raw_requirement: str) -> dict: ...


class RuleBasedBackend:
    """The default backend used throughout this prototype: transparent,
    deterministic, offline-testable. See docs/testing_and_tradeoffs.md for
    why this was chosen over calling a live LLM in the critical path of a
    reliability-focused pipeline."""

    def analyze_requirement(self, raw_requirement: str) -> dict:
        return {
            "ambiguities": detect_ambiguities(raw_requirement),
        }


# --------------------------------------------------------------------------- #
# Requirements analysis
# --------------------------------------------------------------------------- #

AMBIGUOUS_TERMS = {
    "reliable": "no measurable SLO given (e.g. target availability, error rate, or latency bound)",
    "reliability": "no measurable SLO given (e.g. target availability, error rate, or latency bound)",
    "fast": "'fast' has no numeric latency target",
    "faster": "no baseline or target latency given to measure improvement against",
    "better": "'better' is not a measurable acceptance criterion",
    "improve": "no metric or target defined for what 'improve' means",
    "scalable": "no target load (RPS, concurrent users) given to design/validate against",
    "performing": "'performing' does not specify which metric (latency, throughput, error rate)",
    "visibility": "'visibility' does not specify which signals or by whom they'll be consumed",
    "secure": "no specific threat model or compliance requirement named",
    "soon": "no concrete deadline given",
}


def detect_ambiguities(raw_requirement: str) -> list[dict]:
    lowered = raw_requirement.lower()
    found = []
    seen_terms = set()
    for term, reason in AMBIGUOUS_TERMS.items():
        if term in lowered and term not in seen_terms:
            found.append({"term": term, "reason": reason})
            seen_terms.add(term)
    return found


ASSUMPTION_RESOLUTIONS = {
    "reliable": "Interpreted as: redirect path stays correct under expired/deactivated/missing links "
                "(proper 404/410 responses) and absorbs read traffic via a TTL cache; no formal SLA "
                "target was given so none is claimed.",
    "reliability": "Interpreted as: redirect path stays correct under expired/deactivated/missing links "
                   "(proper 404/410 responses) and absorbs read traffic via a TTL cache; no formal SLA "
                   "target was given so none is claimed.",
    "visibility": "Interpreted as: an aggregate operational summary endpoint (link/click volume, cache "
                  "effectiveness) rather than per-request tracing or a full analytics dashboard.",
    "performing": "Interpreted as: click volume and redirect-cache hit rate as the initial performance "
                  "signals, extensible to latency percentiles if requested.",
}


def propose_assumptions(ambiguities: list[dict]) -> list[dict]:
    proposals = []
    for a in ambiguities:
        resolution = ASSUMPTION_RESOLUTIONS.get(a["term"])
        if resolution:
            proposals.append({"term": a["term"], "assumption": resolution})
    return proposals


# --------------------------------------------------------------------------- #
# Task decomposition
# --------------------------------------------------------------------------- #

def decompose_tasks(raw_requirement: str, lexicon: list[dict]) -> list[dict]:
    """Select tasks from `lexicon` whose trigger phrases appear in the raw
    requirement text. `lexicon` entries: {id, capability, description,
    triggers: [str, ...]}. This is a deliberately simple, auditable
    rule-based decomposer (see AgentBackend docstring for the LLM
    extension point) -- every selected task can be traced back to the
    literal phrase in the requirement that justified it."""
    lowered = raw_requirement.lower()
    tasks = []
    for entry in lexicon:
        matched_trigger = next((t for t in entry["triggers"] if t in lowered), None)
        if matched_trigger:
            tasks.append({
                "id": entry["id"],
                "capability": entry["capability"],
                "description": entry["description"],
                "matched_trigger": matched_trigger,
            })
    return tasks


# --------------------------------------------------------------------------- #
# Codebase reasoning (brownfield / ambiguous: operating on an existing repo)
# --------------------------------------------------------------------------- #

def analyze_codebase(workspace: Path) -> dict:
    app_dir = workspace / "app"
    if not app_dir.exists():
        return {"existing_modules": [], "existing_endpoints": []}

    modules = sorted(p.name for p in app_dir.glob("*.py"))
    source_code = "\n".join((app_dir / m).read_text() for m in modules)
    endpoints = _extract_endpoints(source_code)
    return {"existing_modules": modules, "existing_endpoints": endpoints}


def predict_impacted_modules(tasks: list[dict]) -> list[dict]:
    impacted = []
    for t in tasks:
        modules = CAPABILITY_TARGET_MODULES.get(t["capability"], [])
        impacted.append({"task_id": t["id"], "capability": t["capability"], "modules": modules})
    return impacted


# --------------------------------------------------------------------------- #
# Stage handler factories
# --------------------------------------------------------------------------- #

def make_requirements_handler(raw_requirement: str, scenario: str, lexicon: list[dict],
                               workspace: Path | None, backend: AgentBackend | None = None):
    backend = backend or RuleBasedBackend()

    def handler(ctx: RunContext, node: Node) -> NodeResult:
        # Read back any mid-run amendment (see Orchestrator.amend_spec_and_replan)
        # so a re-plan actually changes what gets analyzed, not just re-runs
        # the same closure-captured text.
        active_requirement = ctx.spec.get("raw_requirement", raw_requirement)

        analysis = backend.analyze_requirement(active_requirement)
        ambiguities = analysis["ambiguities"]
        assumptions = propose_assumptions(ambiguities)
        tasks = decompose_tasks(active_requirement, lexicon)
        codebase = analyze_codebase(workspace) if workspace is not None else {"existing_modules": [], "existing_endpoints": []}
        impacted = predict_impacted_modules(tasks)

        spec_patch = {
            "raw_requirement": active_requirement,
            "scenario": scenario,
            "ambiguities": ambiguities,
            "assumptions": assumptions,
            "tasks": tasks,
            "impacted_modules": impacted,
            "existing_modules": codebase["existing_modules"],
            "existing_endpoints": codebase["existing_endpoints"],
        }
        ctx.amend_spec(spec_patch, reason="initial requirements analysis")

        decisions = [f"decomposed {len(tasks)} task(s) from the raw requirement"]
        if ambiguities:
            decisions.append(
                f"flagged {len(ambiguities)} ambiguous term(s): "
                f"{', '.join(a['term'] for a in ambiguities)}; proposed {len(assumptions)} "
                "assumption(s) pending human confirmation at the scope_confirmation checkpoint"
            )
        if codebase["existing_modules"]:
            decisions.append(
                f"codebase reasoning: found {len(codebase['existing_modules'])} existing module(s) "
                f"and {len(codebase['existing_endpoints'])} existing endpoint(s) in the target repo"
            )
        return NodeResult(output=spec_patch, decisions=decisions, artifacts=[])

    return handler


def make_confirmation_handler(kind: str):
    """A human-approval-gated pass-through node (scope_confirmation,
    change_control_review). Node.requires_approval=True is what actually
    creates the checkpoint; this handler just packages the review context
    into a durable output for lineage/audit purposes."""

    def handler(ctx: RunContext, node: Node) -> NodeResult:
        spec = ctx.spec
        summary = {
            "kind": kind,
            "ambiguities": spec.get("ambiguities", []),
            "assumptions": spec.get("assumptions", []),
            "impacted_modules": spec.get("impacted_modules", []),
            "task_ids": [t["id"] for t in spec.get("tasks", [])],
        }
        return NodeResult(
            output=summary,
            decisions=[f"{kind} approved by human reviewer; proceeding with confirmed scope"],
            artifacts=[],
        )

    return handler


def approval_summary_for_confirmation(kind: str):
    def summarize(ctx: RunContext, node: Node) -> str:
        spec = ctx.spec
        lines = [f"{kind} for scenario '{spec.get('scenario', '?')}'"]
        if spec.get("ambiguities"):
            lines.append("Ambiguous terms: " + "; ".join(
                f"{a['term']} ({a['reason']})" for a in spec["ambiguities"]
            ))
        if spec.get("assumptions"):
            lines.append("Proposed assumptions: " + "; ".join(
                f"{a['term']} -> {a['assumption']}" for a in spec["assumptions"]
            ))
        if spec.get("impacted_modules"):
            mods = sorted({m for im in spec["impacted_modules"] for m in im["modules"]})
            lines.append("Impacted modules: " + ", ".join(mods))
        tasks = spec.get("tasks", [])
        lines.append(f"Planned tasks ({len(tasks)}): " + ", ".join(t["id"] for t in tasks))
        return "\n".join(lines)
    return summarize


def design_handler(ctx: RunContext, node: Node) -> NodeResult:
    tasks = ctx.spec.get("tasks", [])
    components = [
        {
            "task_id": t["id"],
            "capability": t["capability"],
            "target_modules": CAPABILITY_TARGET_MODULES.get(t["capability"], []),
        }
        for t in tasks
    ]
    capability_set = {t["capability"] for t in tasks}
    decisions = [
        "Redirect hot path is fronted by an in-memory TTL cache (app/cache.py) to keep p95 "
        "redirect latency low without adding an external dependency.",
        "Public write endpoints (shorten, shorten/bulk) are protected by a fixed-window rate "
        "limiter keyed per client IP to bound abuse.",
        "Click analytics hash client IPs (sha256, truncated) before persistence -- no raw PII "
        "is ever written to disk (enforced again at the policy-guardrail exit gate).",
    ]
    if "fix_alias_race_condition" in capability_set:
        decisions.append(
            "Short-code/alias allocation is redesigned to rely on the database PRIMARY KEY "
            "constraint as the single source of truth for uniqueness, closing a check-then-act "
            "race condition present in the current implementation."
        )
    if "add_bulk_shorten_endpoint" in capability_set:
        decisions.append(
            "Bulk shorten is implemented as N calls into the existing single-link path (reused, "
            "not duplicated) with per-item error reporting, bounded by a max batch size, so a "
            "single caller can't create unbounded write amplification."
        )
    if "add_analytics_summary_endpoint" in capability_set or "add_latency_percentile" in capability_set:
        decisions.append(
            "Operational visibility is exposed as one aggregate summary endpoint rather than a "
            "full events API, matching the confirmed (not raw) scope from scope_confirmation."
        )

    output = {"components": components, "decisions": decisions}
    return NodeResult(output=output, decisions=decisions, artifacts=[])


def make_dependency_health_check_handler(fail_first_n: int = 2):
    """A pre-flight check node with genuine, observable retry behavior in a
    real scenario run (not just the synthetic engine test suite). This
    simulates verifying a downstream dependency (e.g. a staging database)
    before a change that touches existing data is applied -- a realistic
    pattern in brownfield changes, and deliberately flaky here so the
    bounded-retry control is exercised end-to-end. See
    docs/testing_and_tradeoffs.md for why this is fault injection and not
    a hidden defect in the URL shortener itself."""
    state = {"calls": 0}

    def handler(ctx: RunContext, node: Node) -> NodeResult:
        state["calls"] += 1
        if state["calls"] <= fail_first_n:
            raise RuntimeError(
                f"simulated transient dependency-check failure (attempt {state['calls']} of "
                f"{fail_first_n} expected failures before recovery)"
            )
        return NodeResult(
            output={"attempt": state["calls"], "dependency": "staging-db-connectivity"},
            decisions=[f"dependency health check passed on attempt {state['calls']}"],
            artifacts=[],
        )

    return handler


def make_implementation_handler(workspace: Path):
    def handler(ctx: RunContext, node: Node) -> NodeResult:
        tasks = ctx.spec.get("tasks", [])
        # Incremental re-planning: on a re-run triggered by a spec amendment,
        # only apply capabilities that haven't already been materialized in
        # this workspace. The one-shot text-patch capabilities in
        # capabilities.py are not idempotent (their anchor text is consumed
        # by the first application), so blindly replaying every currently
        # decomposed task on re-plan would corrupt the workspace. Tracking
        # what's already applied makes re-planning apply only the delta.
        applied = ctx.spec.setdefault("applied_capabilities", [])

        files_changed: list[str] = []
        new_endpoints: list[str] = []
        tested_endpoints: list[str] = []
        decisions: list[str] = []
        source_snippets: list[str] = []

        for task in tasks:
            if task["capability"] in applied:
                decisions.append(f"[{task['id']}] capability '{task['capability']}' already applied in a "
                                  "previous pass; skipped on re-plan (incremental execution)")
                continue
            capability_fn = CAPABILITY_REGISTRY.get(task["capability"])
            if capability_fn is None:
                raise RuntimeError(f"no capability registered for '{task['capability']}'")
            result = capability_fn(workspace, ctx.spec, task)
            files_changed += result.get("files_changed", [])
            new_endpoints += result.get("new_endpoints", [])
            tested_endpoints += result.get("tested_endpoints", [])
            if result.get("decision"):
                decisions.append(f"[{task['id']}] {result['decision']}")
            if result.get("source_code"):
                source_snippets.append(result["source_code"])
            applied.append(task["capability"])

        output = {
            "files_changed": files_changed,
            "new_endpoints": new_endpoints,
            "tested_endpoints": tested_endpoints,
        }
        policy_payload = {
            "source_code": "\n".join(source_snippets),
            "new_endpoints": new_endpoints,
            "tested_endpoints": tested_endpoints,
        }
        if not tasks:
            decisions = ["no implementation tasks were decomposed from the requirement"]
        elif not decisions:
            # Every task matched was already applied (skipped) or its
            # capability didn't report a human-readable decision string.
            decisions = [f"dispatched {len(tasks)} task(s), no net new changes to report"]

        return NodeResult(
            output=output, decisions=decisions,
            artifacts=files_changed, policy_payload=policy_payload,
        )

    return handler


def make_testing_handler(workspace: Path, scaffold_baseline_tests: bool = False):
    def handler(ctx: RunContext, node: Node) -> NodeResult:
        if scaffold_baseline_tests:
            CAPABILITY_REGISTRY["scaffold_baseline_tests"](workspace, ctx.spec, {})

        report = run_pytest(workspace)
        output = {
            "passed": report["passed"],
            "failed": report["failed"],
            "errors": report["errors"],
            "total": report["total"],
            "returncode": report["returncode"],
            "summary_line": report["summary_line"],
        }
        decisions = [f"pytest: {report['summary_line']} (exit code {report['returncode']})"]
        if report["returncode"] != 0:
            raise RuntimeError(
                f"test suite failed ({report['summary_line']}); tail:\n{report['stdout_tail']}"
            )
        return NodeResult(output=output, decisions=decisions, artifacts=[f"{workspace}/tests (pytest run)"])

    return handler


def make_policy_scan_handler(workspace: Path):
    """A second, independent verification pass over the SAME implementation
    output that unit tests already ran against -- runs in parallel with
    the testing node (both depend only on implementation) so `documentation`
    has to synchronize on both before proceeding, exercising the
    fan-out/fan-in path for real in every scenario run."""

    def handler(ctx: RunContext, node: Node) -> NodeResult:
        impl_record = ctx.get("implementation")
        source_code = ""
        new_endpoints: list[str] = []
        tested_endpoints: list[str] = []
        if impl_record is not None:
            new_endpoints = impl_record.output.get("new_endpoints", [])
            tested_endpoints = impl_record.output.get("tested_endpoints", [])
        app_dir = workspace / "app"
        if app_dir.exists():
            source_code = "\n".join(p.read_text() for p in sorted(app_dir.glob("*.py")))

        output = {"scanned_files": sorted(p.name for p in app_dir.glob("*.py"))} if app_dir.exists() else {"scanned_files": []}
        policy_payload = {
            "source_code": source_code,
            "new_endpoints": new_endpoints,
            "tested_endpoints": tested_endpoints,
        }
        return NodeResult(
            output=output,
            decisions=[f"policy scan covered {len(output['scanned_files'])} source file(s)"],
            artifacts=[],
            policy_payload=policy_payload,
        )

    return handler


def make_documentation_handler(workspace: Path, scenario: str):
    def handler(ctx: RunContext, node: Node) -> NodeResult:
        routes = introspect_routes(workspace)
        spec = ctx.spec
        lines = [f"# API Reference -- {scenario} scenario\n"]
        for r in routes:
            lines.append(f"- `{', '.join(r['methods'])} {r['rule']}`")
        doc_text = "\n".join(lines)

        doc_path = workspace / "API_REFERENCE.generated.md"
        doc_path.write_text(doc_text)

        decisions = [
            f"generated API reference from {len(routes)} live route(s) introspected from the "
            "running Flask app (not hand-written, so it can't drift from the implementation)"
        ]
        output = {"routes": routes, "doc_path": str(doc_path.relative_to(workspace))}
        return NodeResult(output=output, decisions=decisions, artifacts=[output["doc_path"]])

    return handler


def make_release_readiness_handler():
    def handler(ctx: RunContext, node: Node) -> NodeResult:
        testing = ctx.get("testing")
        policy_scan = ctx.get("policy_scan")
        implementation = ctx.get("implementation")

        report = {
            "tests_passed": testing.output.get("passed") if testing else None,
            "tests_failed": testing.output.get("failed") if testing else None,
            "files_changed": implementation.output.get("files_changed") if implementation else [],
            "new_endpoints": implementation.output.get("new_endpoints") if implementation else [],
            "policy_scan_files": policy_scan.output.get("scanned_files") if policy_scan else [],
            "assumptions": ctx.spec.get("assumptions", []),
            "decision_lineage_entries": len(ctx.lineage()),
        }
        decisions = ["release readiness report assembled from testing, policy_scan, and implementation outputs"]
        return NodeResult(output=report, decisions=decisions, artifacts=[])

    return handler


def make_release_notification_handler(webhook_url: str, outbox_dir: Path):
    """Primary path: POST a release notification to a webhook. This
    sandbox's network egress does not allow arbitrary outbound hosts, so in
    practice this genuinely fails here -- a real, not staged, demonstration
    of the fallback path degrading gracefully to a local artifact instead
    of silently losing the notification."""
    import json
    import urllib.request

    def primary(ctx: RunContext, node: Node) -> NodeResult:
        release = ctx.get("release_readiness")
        payload = {"event": "release_ready", "report": release.output if release else {}}
        req = urllib.request.Request(
            webhook_url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            resp.read()
        return NodeResult(output={"delivered_via": "webhook"}, decisions=["notification delivered via webhook"])

    def fallback(ctx: RunContext, node: Node) -> NodeResult:
        release = ctx.get("release_readiness")
        outbox_dir.mkdir(parents=True, exist_ok=True)
        out_path = outbox_dir / f"{ctx.run_id}_release_notification.json"
        out_path.write_text(json.dumps({"event": "release_ready", "report": release.output if release else {}}, indent=2, default=str))
        return NodeResult(
            output={"delivered_via": "local_outbox", "path": str(out_path)},
            decisions=[f"webhook unreachable (no external egress in this environment); "
                       f"fell back to local outbox at {out_path}"],
        )

    primary.__fallback__ = fallback  # for introspection/reporting only
    return primary, fallback
