"""Agentic SDLC orchestration engine.

This package is the "critical differentiator" piece of the assignment: an
orchestration layer that coordinates the full software delivery lifecycle
(requirements -> architecture -> implementation -> testing -> documentation
-> release readiness) as an explicit dependency graph with entry/exit
gates, parallel execution with synchronization, human approval checkpoints,
bounded retries/fallback/rollback/safe-stop, policy guardrails, audit-grade
observability, reliability metrics, and dynamic re-planning.

It is intentionally decoupled from the URL-shortener domain: `orchestrator/`
knows nothing about short links. Domain-specific behavior lives in
`orchestrator/agents.py` (the stage handlers) and in the `service/` package
that those handlers operate on. This separation is what lets the same
engine drive three very different scenarios (greenfield, brownfield,
ambiguous requirements) without any orchestration code changing.
"""
