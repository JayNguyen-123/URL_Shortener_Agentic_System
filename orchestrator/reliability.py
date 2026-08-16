"""Reliability controls: bounded retry, fallback, rollback, and safe-stop.

These wrap node execution in the engine (see engine.py). Kept as a
standalone module so the policy (how many attempts, what backoff, what
counts as retryable) is explicit and testable independent of the graph
walker.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


class SafeStop(Exception):
    """Raised to halt an entire run immediately (vs. isolating a branch)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    backoff_seconds: float = 0.0  # kept at 0 for fast deterministic test/demo runs
    retryable: Callable[[Exception], bool] = lambda e: True


class RetryOutcome:
    def __init__(self):
        self.attempts: int = 0
        self.succeeded: bool = False
        self.result = None
        self.errors: list[str] = []
        self.used_fallback: bool = False


def run_with_retries(fn: Callable[[], object], policy: RetryPolicy,
                      fallback: Callable[[], object] | None,
                      on_attempt=None, on_retry=None, on_fallback=None) -> RetryOutcome:
    """Execute `fn`, retrying up to policy.max_attempts times on retryable
    exceptions. If all attempts fail and `fallback` is provided, run it
    once and record that a fallback path was used (this is itself surfaced
    in metrics as a reliability signal, not silently swallowed)."""
    outcome = RetryOutcome()
    last_exc: Exception | None = None

    for attempt in range(1, policy.max_attempts + 1):
        outcome.attempts = attempt
        if on_attempt:
            on_attempt(attempt)
        try:
            outcome.result = fn()
            outcome.succeeded = True
            return outcome
        except SafeStop:
            raise
        except Exception as exc:  # noqa: BLE001 - intentionally broad: node handlers vary
            last_exc = exc
            outcome.errors.append(str(exc))
            if not policy.retryable(exc) or attempt == policy.max_attempts:
                break
            if on_retry:
                on_retry(attempt, exc)
            if policy.backoff_seconds:
                time.sleep(policy.backoff_seconds * attempt)

    if fallback is not None:
        try:
            if on_fallback:
                on_fallback(last_exc)
            outcome.result = fallback()
            outcome.succeeded = True
            outcome.used_fallback = True
            return outcome
        except Exception as exc:  # noqa: BLE001
            outcome.errors.append(f"fallback_failed: {exc}")

    outcome.succeeded = False
    return outcome


class RollbackRegistry:
    """Tracks rollback actions registered by completed nodes so a later
    failure/rejection can unwind prior side effects in reverse order."""

    def __init__(self):
        self._actions: list[tuple[str, Callable[[], None]]] = []

    def register(self, node_id: str, action: Callable[[], None]) -> None:
        self._actions.append((node_id, action))

    def rollback_node(self, node_id: str) -> bool:
        for nid, action in reversed(self._actions):
            if nid == node_id:
                action()
                return True
        return False

    def rollback_all_after(self, node_ids: list[str]) -> list[str]:
        """Roll back, in reverse registration order, every registered
        action belonging to one of `node_ids`. Returns the ids rolled back."""
        rolled_back = []
        for nid, action in reversed(self._actions):
            if nid in node_ids:
                action()
                rolled_back.append(nid)
        return rolled_back
