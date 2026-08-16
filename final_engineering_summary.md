# Final Engineering Summary

## Plan and rationale

The assignment asked for two things at once: (1) a URL shortener service,
and (2) an agentic orchestration layer that demonstrates real SDLC
governance, not just a linear script that calls an LLM a few times in a
row. The plan was to build both honestly and let the harder one (the
orchestrator) actually drive the easier one (the shortener), so every
claim about "governed autonomy" is backed by something that runs, not by
prose.

Order of work:

1. Build and fully test the URL shortener as a standalone product first
   (`service/`), so there was a known-good reference implementation before
   any orchestration logic touched it.
2. Build the orchestration engine (`orchestrator/graph.py`, `state.py`,
   `governance.py`, `reliability.py`, `replanner.py`, `audit.py`,
   `metrics.py`, `engine.py`) as a fully generic, domain-agnostic layer,
   and prove its mechanics with synthetic nodes before wiring in any
   URL-shortener-specific behavior (`orchestrator/tests/test_engine_core.py`,
   11 tests, all passing before agents.py was written).
3. Build the SDLC stage agents (`agents.py`) and code-generation
   capabilities (`capabilities.py`) on top of the proven engine.
4. Compose three scenario graphs (`graphs.py`) — greenfield, brownfield,
   ambiguous requirement — that reuse the same engine and mostly the same
   agent code, differing only in requirement text, task lexicon, and which
   nodes require human approval (risk-scaled per scenario, see
   `architecture.md` §4).
5. Run all three scenarios for real, fix what broke (two real bugs were
   found and fixed this way — see below), and generate reports from the
   actual audit trail rather than hand-written summaries.

## Artifacts produced

- `service/` — the URL shortener (Flask + SQLite), 21 passing tests.
- `orchestrator/` — the orchestration engine, 11 passing engine-mechanics
  tests, fully documented module-by-module in `docs/architecture.md`.
- `scenarios/run_{greenfield,brownfield,ambiguous}.py` — three end-to-end,
  runnable scenarios.
- `docs/scenario_reports/{greenfield,brownfield,ambiguous}.{json,md}` —
  generated from each run's actual audit trail, decision lineage, and
  metrics (not hand-written).
- `runs/*/workspace/` — the real, on-disk generated/modified codebases
  produced by each scenario run, each with its own passing pytest suite.
- `docs/architecture.md`, `docs/setup.md`, `docs/testing_and_tradeoffs.md`
  — supporting documentation.

## Risks, trade-offs, and how they were validated

See `docs/testing_and_tradeoffs.md` for the full list with rationale. The
headline ones: deterministic/templated code generation instead of a live
LLM call (reproducibility over generality — extension point documented);
process-local cache/rate-limiter (fine for a single instance, documented
gap for horizontal scale); fixed-window rate limiting (simplicity over
burst-smoothing precision); SQLite (appropriate for scope; the
concurrency fix applied in the brownfield scenario generalizes directly to
a client-server database). Each is stated as a trade-off with a reason,
not asserted as costless.

Two things surfaced as genuine defects while validating this system, and
both were fixed rather than worked around:

1. **Race condition in short-code allocation** (`service/app/shortener.py`,
   pre-fix): the original implementation checked for an existing code with
   a `SELECT` and only then issued a separate `INSERT` — a classic
   check-then-act race under concurrent requests. This became the
   brownfield scenario's bug-fix task: the fix relies on the database's
   `PRIMARY KEY` constraint and reacts to `sqlite3.IntegrityError` instead
   of pre-checking, making uniqueness atomic at the database layer. Two
   regression tests were added
   (`tests/test_alias_race_condition_fix.py`).
2. **Metrics double-counting across re-plan rounds**: the first version of
   `metrics.compute_metrics` incremented `completed_nodes` once per
   `node_complete` *event* rather than tracking each node's latest
   terminal outcome, so a node re-executed after a re-plan (like every
   node in the ambiguous scenario's round 2) was counted twice, producing
   a `success_rate` of 2.0 in the ambiguous scenario's real output. Caught
   by actually reading the printed metrics from a real run (not just
   trusting the unit tests, which only ever exercised single-pass runs),
   fixed to track latest-terminal-state per node id, and re-verified
   against all three scenarios plus the full test suite (32 tests) before
   finalizing. This is exactly the kind of thing the assignment's
   "validation and risk management rigor" criterion is asking to see
   evidence of — including the miss, not just the fix.

## Assumptions

- "AI assistance" for this take-home means using Claude to design, build,
  and validate the system (as instructed) — the delivered orchestrator
  itself uses a deterministic rule-based backend by default rather than
  making live LLM calls in its execution path, for the reproducibility
  reasons explained in `testing_and_tradeoffs.md`. The `AgentBackend`
  extension point exists specifically so a live LLM backend could be
  substituted without changing the orchestration engine.
- No specific traffic/SLA targets were given for the URL shortener, so
  none are claimed; reliability features (caching, rate limiting) are
  included because they're standard for this class of service, not
  because a specific number was targeted.
- The three required scenarios were interpreted as: greenfield = build a
  new system from nothing; brownfield = a bug fix + an enhancement against
  an existing codebase; ambiguous = a requirement containing genuinely
  unmeasurable terms ("reliable", "better", "visibility") requiring
  explicit assumption-making and a human confirmation checkpoint before
  implementation.
- "Production-grade" was interpreted, given the 2-3 day/take-home framing,
  as: real tests that actually run and pass, real error handling, real
  security basics (hashed PII, input validation, rate limiting), and
  honest documentation of what was scoped out — not as "ready to deploy to
  real traffic without further work." The gaps are itemized in
  `testing_and_tradeoffs.md`'s Known Limitations section rather than
  glossed over.

## Limitations

See `docs/testing_and_tradeoffs.md` → "Known limitations" for the full,
itemized list (ambiguity-detection lexicon is fixed, not general NLU; task
decomposition is scenario-scoped, not a general planner; no auth layer on
the API; MTTR only captures in-process retry recovery, not
human-in-the-loop fixes across separate runs; and others).
