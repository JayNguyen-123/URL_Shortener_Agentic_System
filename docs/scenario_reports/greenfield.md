# Scenario report: greenfield

- Run ID: `greenfield-001`
- Final status: **{requirements: completed, design: completed, implementation: completed, testing: completed, policy_scan: completed, documentation: completed, release_readiness: completed, release_notification: completed}**
- Spec version: 2

## Narrative
- Started orchestration run for a brand-new URL shortener service.
- Human approval granted for 'release_readiness' by jay (engineering lead): Release checklist satisfied: tests pass, policy scan clean, docs generated.
- Run completed: service scaffolded, tested, documented, and approved for release.

## Node outcomes
- `requirements`: **completed**
- `design`: **completed**
- `implementation`: **completed**
- `testing`: **completed**
- `policy_scan`: **completed**
- `documentation`: **completed**
- `release_readiness`: **completed**
- `release_notification`: **completed**

## Reliability metrics
- total_nodes: 8
- completed_nodes: 8
- failed_nodes: 0
- skipped_nodes: 0
- rolled_back_nodes: 0
- retry_events: 0
- rollback_events: 0
- fallback_events: 1
- approval_requests: 1
- approvals_granted: 1
- approvals_rejected: 0
- policy_violations: 0
- replans: 0
- success_rate: 1.0
- retry_frequency: 0.0
- rollback_frequency: 0.0
- mttr_seconds: None
- end_to_end_latency_seconds: 1.1034

## Per-node latency (s)
- `requirements`: 0.0004
- `design`: 0.0003
- `implementation`: 0.0025
- `testing`: 0.646
- `policy_scan`: 0.0024
- `documentation`: 0.1685
- `release_readiness`: 0.0004
- `release_notification`: 0.278

## Decision lineage
- spec amended (v2): initial requirements analysis
- `requirements` (v1): decomposed 1 task(s) from the raw requirement; flagged 1 ambiguous term(s): reliability; proposed 1 assumption(s) pending human confirmation at the scope_confirmation checkpoint
- `design` (v1): Redirect hot path is fronted by an in-memory TTL cache (app/cache.py) to keep p95 redirect latency low without adding an external dependency.; Public write endpoints (shorten, shorten/bulk) are protected by a fixed-window rate limiter keyed per client IP to bound abuse.; Click analytics hash client IPs (sha256, truncated) before persistence -- no raw PII is ever written to disk (enforced again at the policy-guardrail exit gate).
- `implementation` (v1): [T1-scaffold] Scaffolded a new service from the baseline template: 9 module(s), 7 endpoint(s).
- `policy_scan` (v1): policy scan covered 9 source file(s)
- `testing` (v1): pytest: 21 passed in 0.23s (exit code 0)
- `documentation` (v1): generated API reference from 7 live route(s) introspected from the running Flask app (not hand-written, so it can't drift from the implementation)
- `release_readiness` (v1): release readiness report assembled from testing, policy_scan, and implementation outputs
- `release_notification` (v1): webhook unreachable (no external egress in this environment); fell back to local outbox at /home/claude/project/runs/greenfield/notifications/greenfield-001_release_notification.json