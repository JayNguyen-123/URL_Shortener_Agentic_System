# Setup Instructions

## Requirements

- Python 3.11+ (this was built and tested against 3.11.15)
- No external services required — everything (including the "database")
  is local (SQLite file, in-memory cache/rate-limiter).

## 1. Install dependencies

On a normal machine with PyPI access:

```bash
pip install -r service/requirements.txt   # flask, pydantic, pytest
```

### A note on this sandbox's environment

This prototype was built inside a network-restricted sandbox where PyPI
(`pypi.org`) was not on the egress allowlist, so `pip install` could not
fetch new packages. The sandbox happened to already have `Flask`,
`pydantic`, and `httpx` pre-installed for the system Python 3.11, but
`pytest` was only available as a separately-provisioned, isolated
Python 3.11 environment (via `uv tool install`) that could not see those
packages. `scripts/run_pytest.sh` handles this transparently: it tries
plain `pytest` first (what a normal machine will hit) and only falls back
to bridging the two interpreters via `PYTHONPATH` if needed. **Use
`scripts/run_pytest.sh` instead of calling `pytest` directly** to stay
portable across both environments:

```bash
scripts/run_pytest.sh -q service/tests
scripts/run_pytest.sh -q orchestrator/tests
```

This is also why the product is built on **Flask** rather than FastAPI:
FastAPI was the original choice (see `docs/architecture.md`) but was not
installable in this environment, and Flask + Werkzeug were already present
and fully sufficient for this scope. A team with normal PyPI access can
swap frameworks without touching the orchestrator at all — only
`service/app/main.py`'s HTTP layer and `service/requirements.txt` would
change.

## 2. Run the product's own test suite

```bash
scripts/run_pytest.sh -q service/tests
# 21 passed
```

## 3. Run the product locally

```bash
cd service
FLASK_APP=app.main:create_app python3 -m flask run --port 8000
# or: python3 -m app.main
```

Then:

```bash
curl -X POST localhost:8000/api/v1/shorten -H 'content-type: application/json' \
  -d '{"url": "https://www.anthropic.com"}'
curl -i localhost:8000/<code-from-response>
curl localhost:8000/api/v1/analytics/<code>
curl localhost:8000/api/v1/health
```

## 4. Run the orchestration engine's own test suite

These tests exercise the engine mechanics (parallel waves, retries,
fallback, approval pause/resume/reject, policy guardrails, rollback
cascades, dynamic re-planning) with trivial synthetic nodes, independent
of the URL-shortener domain:

```bash
scripts/run_pytest.sh -q orchestrator/tests
# 11 passed
```

## 5. Run the three end-to-end scenarios

Each scenario builds/modifies a real on-disk workspace under
`runs/<scenario>/workspace/`, drives it through the full SDLC graph, pauses
at human approval checkpoints (auto-approved in-script with a logged
rationale, exactly as a human reviewer would), runs the *real* pytest
suite against the generated code, and writes a report to
`docs/scenario_reports/<scenario>.{json,md}`:

```bash
python3 scenarios/run_greenfield.py
python3 scenarios/run_brownfield.py
python3 scenarios/run_ambiguous.py
```

Each prints a running trace of node status, approval requests/decisions,
and final reliability metrics, and exits non-zero if the run does not
reach `completed`.

To inspect a generated workspace directly:

```bash
scripts/run_pytest.sh -q runs/greenfield/workspace/tests
cat runs/greenfield/workspace/API_REFERENCE.generated.md
cat runs/brownfield/workspace/app/shortener.py   # race-condition fix
cat runs/ambiguous/workspace/app/latency.py      # added after the re-plan
```

To inspect the full audit trail of a run:

```bash
cat runs/ambiguous/audit.jsonl | python3 -m json.tool  # (per-line JSON; pipe one line at a time, or use jq)
jq -c '.event_type' runs/ambiguous/audit.jsonl | sort | uniq -c
```

## 6. Clean slate

`runs/` is entirely regenerable output (workspaces, audit logs, approval
records, notification outbox). Safe to delete:

```bash
rm -rf runs/*/workspace runs/*/audit.jsonl runs/*/approvals runs/*/notifications
```

Re-running the scenario scripts recreates everything.
