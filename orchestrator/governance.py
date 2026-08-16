"""Governance: policy guardrails and human approval checkpoints.

Two distinct control mechanisms, both required by the assignment:

1. Policy guardrails (`PolicyEngine`) are automated rules evaluated at a
   node's entry and exit gates -- security/compliance/change-control checks
   that block progress on violation (e.g. "no raw PII persisted", "no
   hardcoded secrets", "public write endpoints must be rate limited").

2. Approval checkpoints (`ApprovalGate`) are places where a *human* must
   explicitly approve or reject before the run proceeds past a high-impact
   node (e.g. schema changes, release readiness). The engine pauses the
   affected branch (not the whole run) and persists an approval request to
   disk so a human can review it out-of-band and call approve()/reject().
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


class PolicyViolation(Exception):
    def __init__(self, rule_id: str, message: str):
        super().__init__(f"[{rule_id}] {message}")
        self.rule_id = rule_id
        self.message = message


@dataclass
class PolicyRule:
    rule_id: str
    description: str
    check: Callable[[dict], tuple[bool, str]]  # (context_payload) -> (passed, detail)
    severity: str = "blocking"  # "blocking" | "warning"


class PolicyEngine:
    """A small, explicit rule set. Rules are plain Python callables rather
    than an external DSL/engine -- appropriate for a bounded prototype, and
    documented as a trade-off (a real deployment might use OPA/Rego)."""

    def __init__(self, rules: list[PolicyRule] | None = None):
        self.rules: list[PolicyRule] = rules or []

    def register(self, rule: PolicyRule) -> None:
        self.rules.append(rule)

    def evaluate(self, gate: str, payload: dict) -> list[dict]:
        """Run every rule applicable to this gate. Returns a list of result
        dicts; raises PolicyViolation on the first blocking failure."""
        results = []
        for rule in self.rules:
            passed, detail = rule.check(payload)
            results.append(
                {
                    "rule_id": rule.rule_id,
                    "gate": gate,
                    "passed": passed,
                    "severity": rule.severity,
                    "detail": detail,
                }
            )
            if not passed and rule.severity == "blocking":
                raise PolicyViolation(rule.rule_id, detail)
        return results


@dataclass
class ApprovalRequest:
    request_id: str
    node_id: str
    run_id: str
    summary: str
    payload: dict
    status: str = "pending"  # pending | approved | rejected
    decided_by: str | None = None
    decision_reason: str | None = None
    created_at: float = field(default_factory=time.time)
    decided_at: float | None = None


class ApprovalGate:
    """Human-in-the-loop checkpoint. Requests are persisted as JSON files
    under `approvals_dir` so a human (or a scripted reviewer in tests/
    scenarios) can inspect and decide on them independently of the
    in-process orchestrator run."""

    def __init__(self, approvals_dir: str | Path,
                 auto_decision: Callable[[ApprovalRequest], tuple[bool, str]] | None = None):
        self.approvals_dir = Path(approvals_dir)
        self.approvals_dir.mkdir(parents=True, exist_ok=True)
        self._requests: dict[str, ApprovalRequest] = {}
        # Optional callback used by unattended scenario runs to simulate a
        # human decision deterministically (still logged identically to a
        # real approval, so the mechanism itself is exercised for real).
        self.auto_decision = auto_decision

    def request(self, node_id: str, run_id: str, summary: str, payload: dict) -> ApprovalRequest:
        req = ApprovalRequest(
            request_id=f"appr_{node_id}_{int(time.time() * 1000)}",
            node_id=node_id,
            run_id=run_id,
            summary=summary,
            payload=payload,
        )
        self._requests[req.request_id] = req
        self._persist(req)
        return req

    def decide(self, request_id: str, approved: bool, decided_by: str, reason: str) -> ApprovalRequest:
        req = self._requests[request_id]
        req.status = "approved" if approved else "rejected"
        req.decided_by = decided_by
        req.decision_reason = reason
        req.decided_at = time.time()
        self._persist(req)
        return req

    def pending(self) -> list[ApprovalRequest]:
        return [r for r in self._requests.values() if r.status == "pending"]

    def _persist(self, req: ApprovalRequest) -> None:
        path = self.approvals_dir / f"{req.request_id}.json"
        path.write_text(
            json.dumps(
                {
                    "request_id": req.request_id,
                    "node_id": req.node_id,
                    "run_id": req.run_id,
                    "summary": req.summary,
                    "payload": req.payload,
                    "status": req.status,
                    "decided_by": req.decided_by,
                    "decision_reason": req.decision_reason,
                    "created_at": req.created_at,
                    "decided_at": req.decided_at,
                },
                indent=2,
                default=str,
            )
        )


# ---------------------------------------------------------------------------
# Default policy rules for this system. Kept here (rather than in agents.py)
# because governance rules are cross-cutting policy, not stage logic.
# ---------------------------------------------------------------------------

def rule_no_raw_pii(payload: dict) -> tuple[bool, str]:
    code = payload.get("source_code", "")
    if "clicks" in code.lower() and "ip_hash" not in code and "hashlib" not in code:
        return False, "click/analytics storage must hash client IPs, never store them raw"
    return True, "no raw PII fields detected"


def rule_no_hardcoded_secrets(payload: dict) -> tuple[bool, str]:
    code = payload.get("source_code", "")
    lowered = code.lower()
    suspicious = ["api_key = \"", "password = \"", "secret = \"", "token = \""]
    for s in suspicious:
        if s in lowered:
            return False, f"possible hardcoded secret literal matching '{s.strip()}'"
    return True, "no hardcoded secret literals detected"


def rule_public_write_endpoints_rate_limited(payload: dict) -> tuple[bool, str]:
    code = payload.get("source_code", "")
    if "@app.post" in code and "rate_limit" not in code.lower():
        return False, "public POST endpoints must apply rate limiting"
    return True, "public write endpoints are rate limited"


def rule_tests_present_for_new_endpoints(payload: dict) -> tuple[bool, str]:
    new_endpoints = payload.get("new_endpoints", [])
    tested_endpoints = payload.get("tested_endpoints", [])
    missing = [e for e in new_endpoints if e not in tested_endpoints]
    if missing:
        return False, f"new endpoints missing test coverage: {missing}"
    return True, "all new endpoints have test coverage"


def default_policy_engine() -> PolicyEngine:
    engine = PolicyEngine()
    engine.register(PolicyRule("SEC-001", "No raw PII persisted", rule_no_raw_pii))
    engine.register(PolicyRule("SEC-002", "No hardcoded secrets", rule_no_hardcoded_secrets))
    engine.register(
        PolicyRule(
            "SEC-003",
            "Public write endpoints must be rate limited",
            rule_public_write_endpoints_rate_limited,
        )
    )
    engine.register(
        PolicyRule(
            "QA-001",
            "New endpoints must have test coverage before release",
            rule_tests_present_for_new_endpoints,
            severity="blocking",
        )
    )
    return engine
