# Testing Approach, Limitations, and Trade-offs

## Testing approach

Three independent layers, each proven separately so a failure in one is
attributable rather than lost in an end-to-end blur:

1. **Product unit/integration tests** (`service/tests/`, 21 tests): the
   URL shortener's own test suite, run directly against the hand-authored
   reference implementation — validation, custom aliases, collisions,
   redirects, expiry, deactivation + cache invalidation, click analytics,
   rate limiting. This is the suite the greenfield scenario's Testing Agent
   also scaffolds into every generated workspace.
2. **Orchestration engine tests** (`orchestrator/tests/test_engine_core.py`,
   11 tests): prove the engine mechanics — linear ordering, parallel
   fan-out/fan-in synchronization, bounded retry-then-success, exhausted
   retry falling back, terminal failure skipping only its downstream
   branch (siblings unaffected), approval pause/resume/reject, rejection
   cascading a skip, unattended `auto_decision` runs, policy-guardrail
   blocking, rollback-on-downstream-failure, and re-plan invalidating only
   the reachable subgraph — using trivial synthetic nodes, deliberately
   independent of the URL-shortener domain, so these prove the engine, not
   the agents built on top of it.
3. **Scenario runs** (`scenarios/run_*.py`): the engine and agents
   together, driving *real* code generation into a real on-disk workspace,
   validated by actually invoking pytest as a subprocess
   (`orchestrator/testrunner.py`) against that workspace — not a simulated
   or asserted pass. Every scenario's `testing` node genuinely fails the
   whole node (and, via its exit gate, cannot report success on zero
   executed tests) if `pytest`'s exit code is non-zero.

Run everything: `scripts/run_pytest.sh -q service/tests orchestrator/tests`
plus the three `python3 scenarios/run_*.py` invocations (see `setup.md`).

## Key trade-offs (and why)

**Deterministic/templated code generation instead of a live LLM call.**
`orchestrator/capabilities.py`'s Implementation Agent capabilities are
rule-based text patches, not calls to an LLM API. For a reliability-focused
prototype this is a deliberate choice: runs are reproducible byte-for-byte,
testable offline, and don't depend on external API availability or cost —
important properties for something whose entire premise is bounded,
governed autonomy. The cost is that the "requirements understanding" and
"code generation" are only as good as the rule-based lexicon/templates
authored for this scope; they don't generalize to an arbitrary new
requirement the way a real LLM backend would. The `AgentBackend` protocol
in `agents.py` is the documented extension point for swapping in a live
LLM backend later without touching the orchestration engine.

**Fault injection instead of a hidden bug.** The brownfield scenario's
`dependency_health_check` node is deliberately flaky (fails twice, then
succeeds) so the bounded-retry control is exercised against a *real*
scenario run, not only the synthetic engine tests. This was a conscious
choice not to introduce a fake defect into the actual URL-shortener
feature code just to force a retry to fire — that would misrepresent the
product's real quality. The fault injection is clearly labeled in code and
in the scenario's narrative output.

**In-process, single-node reliability primitives.** The redirect cache
(`TTLCache`) and rate limiter (`RateLimiter`) are process-local. Correct
for a single-process prototype; a multi-worker/multi-instance production
deployment would need a shared store (Redis) for the rate limiter to be
correct across processes, and would need cache invalidation to be
broadcast (or accept the documented staleness window) across instances.
This is noted directly in `service/app/cache.py` and `rate_limit.py`.

**Fixed-window rate limiting over a token bucket.** Simpler and more
memory-predictable; the trade-off is permitting brief bursts at window
boundaries. Acceptable for this scope; called out in
`service/app/rate_limit.py`.

**SQLite instead of a client-server database.** Appropriate for a
prototype and for the take-home's time-box; the short-code allocation fix
in the brownfield scenario (see below) specifically leans on SQLite's
PRIMARY KEY constraint + `IntegrityError` for atomicity, a pattern that
carries over directly to Postgres/MySQL.

**Governance rules are plain Python callables, not an external policy
engine.** `governance.PolicyEngine` rules are small, explicit, and
readable in this codebase rather than expressed in an external DSL like
OPA/Rego. Right-sized for a bounded prototype with four rules; a real
deployment with dozens of cross-cutting compliance rules would likely want
a dedicated policy engine with a query language and a separate rule
repository.

**Approval auto-decision in scenario scripts.** The three `scenarios/*.py`
drivers call `orch.approve(...)` programmatically (with a logged rationale)
rather than blocking on real human input, so the scenarios are runnable
end-to-end without a person present for grading. The pause/resume
mechanism itself is real and independently proven:
`orchestrator/tests/test_engine_core.py::test_approval_checkpoint_pauses_and_resumes_on_approve`
and `::test_approval_checkpoint_rejection_rolls_back_branch` exercise it
with `run()` genuinely returning a `"paused"` status and no `auto_decision`
callback configured. `governance.ApprovalGate` also persists every
request/decision to disk (`runs/<scenario>/approvals/*.json`) exactly as
it would need to for a real out-of-process human reviewer.

## Known limitations

- The rule-based ambiguity detector (`agents.AMBIGUOUS_TERMS`) is a fixed
  lexicon, not real NLU — it will miss ambiguous phrasing that doesn't
  contain one of its known terms, and could false-positive on a term used
  in a non-ambiguous way. It's transparent about this (every flagged term
  is traceable to the literal substring match) rather than claiming deeper
  understanding than it has.
- Task decomposition (`agents.decompose_tasks`) is similarly a fixed
  per-scenario lexicon (`orchestrator/graphs.py`), not a general planner —
  it can only select from tasks it was authored to know about. This is
  explicitly the bounded scope of this prototype, not a claim of general
  requirement-to-task synthesis.
- The Implementation Agent's capability patches (`capabilities.py`) are
  one-shot: each looks for specific anchor text and fails loudly
  (`CapabilityError`) if it's not found, rather than degrading gracefully
  to a fuzzy patch. This is why the Implementation Agent tracks
  `applied_capabilities` and skips already-applied ones on re-plan instead
  of naively re-running everything (see `docs/architecture.md` §6) — a
  narrower but more honest form of idempotency than a general-purpose
  merge/patch system would provide.
- `MTTR` in the metrics is only populated when a node's audit trail shows
  a `node_failed` event followed later by a `node_complete` for the same
  node id within the same run (recovery-after-retry). It does not capture
  wall-clock time-to-recovery for a human-in-the-loop fix outside the
  process (e.g. a human editing code and re-triggering a run) — that would
  require correlating across separate run ids, out of scope here.
- No authentication/authorization layer on the URL shortener API — anyone
  who can reach it can create/deactivate links. Reasonable for a
  prototype; flagged explicitly as a gap a production deployment must
  close (would likely be an API-key or OAuth check ahead of the rate
  limiter in `main.py`).
