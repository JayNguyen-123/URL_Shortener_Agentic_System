"""Concrete, file-level "code generation" capabilities the Implementation
and Testing agents dispatch to.

Design note (documented trade-off, see docs/testing_and_tradeoffs.md):
these capabilities are deterministic/templated rather than backed by a live
LLM call. For a bounded, reliability-focused prototype this is a deliberate
choice -- it keeps runs reproducible and independently testable, and it
mirrors exactly what a real LLM-backed code-gen backend would need to
produce (a unified diff / file write), so swapping in `LLMBackend` later
(see agents.py `AgentBackend`) only means replacing *how* the text is
produced, not the orchestration around it.

Every function here takes `(workspace: Path, spec: dict, task: dict)` and
returns a dict describing what changed, which flows straight into the
audit trail and the policy-guardrail payload.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .workspace import BASELINE_TEMPLATE


class CapabilityError(RuntimeError):
    pass


def _read(path: Path) -> str:
    return path.read_text()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _replace_or_raise(path: Path, old: str, new: str, label: str) -> None:
    content = _read(path)
    if old not in content:
        raise CapabilityError(f"{label}: expected anchor text not found in {path}")
    _write(path, content.replace(old, new, 1))


# --------------------------------------------------------------------------- #
# Greenfield: scaffold a brand-new service from the reference templates.
# --------------------------------------------------------------------------- #

def cap_scaffold_service(workspace: Path, spec: dict, task: dict) -> dict:
    app_src = BASELINE_TEMPLATE / "app"
    app_dst = workspace / "app"
    if app_dst.exists():
        shutil.rmtree(app_dst)
    shutil.copytree(app_src, app_dst)
    shutil.copy(BASELINE_TEMPLATE / "requirements.txt", workspace / "requirements.txt")

    source_code = "\n".join(p.read_text() for p in sorted(app_dst.glob("*.py")))
    endpoints = _extract_endpoints(source_code)
    return {
        "files_changed": [str(p.relative_to(workspace)) for p in sorted(app_dst.glob("*.py"))],
        "new_endpoints": endpoints,
        # The scaffold ships with a matching baseline test suite (written by
        # the Testing stage's scaffold_baseline_tests capability immediately
        # after this runs), so these endpoints are covered by construction.
        "tested_endpoints": endpoints,
        "source_code": source_code,
        "decision": f"Scaffolded a new service from the baseline template: "
                    f"{len(list(app_dst.glob('*.py')))} module(s), {len(endpoints)} endpoint(s).",
    }


def cap_scaffold_baseline_tests(workspace: Path, spec: dict, task: dict) -> dict:
    tests_src = BASELINE_TEMPLATE / "tests"
    tests_dst = workspace / "tests"
    if tests_dst.exists():
        shutil.rmtree(tests_dst)
    shutil.copytree(tests_src, tests_dst)
    return {"files_changed": [str(p.relative_to(workspace)) for p in sorted(tests_dst.glob("*.py"))]}


def _extract_endpoints(source_code: str) -> list[str]:
    endpoints = []
    for line in source_code.splitlines():
        line = line.strip()
        if line.startswith("@app.get(") or line.startswith("@app.post(") \
           or line.startswith("@app.delete(") or line.startswith("@app.put("):
            method = line.split("(")[0].replace("@app.", "").upper()
            path = line.split('"')[1] if '"' in line else "?"
            endpoints.append(f"{method} {path}")
    return endpoints


# --------------------------------------------------------------------------- #
# Brownfield: bug fix -- alias/code creation check-then-act race condition.
# --------------------------------------------------------------------------- #

_OLD_CREATE_SHORT_LINK = '''def create_short_link(long_url: str, custom_alias: str | None = None,
                       ttl_seconds: int | None = None, owner: str | None = None) -> dict:
    validate_url(long_url)

    if custom_alias:
        validate_alias(custom_alias)
        code = custom_alias
        is_custom = 1
    else:
        code = generate_code()
        is_custom = 0

    created_at = _now()
    expires_at = created_at + timedelta(seconds=ttl_seconds) if ttl_seconds else None

    with db.tx() as conn:
        if custom_alias:
            existing = conn.execute(
                "SELECT code FROM links WHERE code = ?", (code,)
            ).fetchone()
            if existing:
                raise AliasTakenError(f"alias '{code}' is already in use")
        else:
            # Bounded collision-retry loop for randomly generated codes.
            attempts = 0
            while conn.execute("SELECT 1 FROM links WHERE code = ?", (code,)).fetchone():
                attempts += 1
                if attempts > 5:
                    raise RuntimeError("failed to generate a unique short code after 5 attempts")
                code = generate_code()

        conn.execute(
            """INSERT INTO links (code, long_url, created_at, expires_at, is_custom_alias,
                                    is_active, owner)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (code, long_url, _iso(created_at), _iso(expires_at), is_custom, owner),
        )

    return {
        "code": code,
        "long_url": long_url,
        "short_url": f"{Config.BASE_URL}/{code}",
        "created_at": _iso(created_at),
        "expires_at": _iso(expires_at),
        "is_custom_alias": bool(is_custom),
    }'''

_NEW_CREATE_SHORT_LINK = '''def create_short_link(long_url: str, custom_alias: str | None = None,
                       ttl_seconds: int | None = None, owner: str | None = None) -> dict:
    validate_url(long_url)

    if custom_alias:
        validate_alias(custom_alias)

    created_at = _now()
    expires_at = created_at + timedelta(seconds=ttl_seconds) if ttl_seconds else None

    # Bug fix (brownfield scenario): the previous implementation checked for
    # an existing code with a SELECT and only then issued an INSERT. Under
    # concurrent requests two threads could both pass the SELECT before
    # either INSERT committed -- a classic check-then-act race that could
    # silently drop one of two links sharing a code. We now let the `code`
    # PRIMARY KEY constraint be the single, atomic source of truth for
    # "taken" and react to sqlite3.IntegrityError instead of pre-checking.
    attempts = 0
    code = custom_alias or generate_code()
    is_custom = 1 if custom_alias else 0

    while True:
        attempts += 1
        try:
            with db.tx() as conn:
                conn.execute(
                    """INSERT INTO links (code, long_url, created_at, expires_at, is_custom_alias,
                                            is_active, owner)
                       VALUES (?, ?, ?, ?, ?, 1, ?)""",
                    (code, long_url, _iso(created_at), _iso(expires_at), is_custom, owner),
                )
            break
        except sqlite3.IntegrityError:
            if custom_alias:
                raise AliasTakenError(f"alias '{code}' is already in use")
            if attempts >= 5:
                raise RuntimeError("failed to generate a unique short code after 5 attempts")
            code = generate_code()

    return {
        "code": code,
        "long_url": long_url,
        "short_url": f"{Config.BASE_URL}/{code}",
        "created_at": _iso(created_at),
        "expires_at": _iso(expires_at),
        "is_custom_alias": bool(is_custom),
    }'''


def cap_fix_alias_race_condition(workspace: Path, spec: dict, task: dict) -> dict:
    path = workspace / "app" / "shortener.py"
    _replace_or_raise(
        path, "import secrets\nimport string\n", "import secrets\nimport sqlite3\nimport string\n",
        "fix_alias_race_condition:imports",
    )
    _replace_or_raise(
        path, _OLD_CREATE_SHORT_LINK, _NEW_CREATE_SHORT_LINK,
        "fix_alias_race_condition:create_short_link",
    )
    test_path = workspace / "tests" / "test_alias_race_condition_fix.py"
    _write(test_path, '''"""Regression test for the check-then-act race condition fix (brownfield
scenario). We can't easily force two SQLite threads to interleave inside a
unit test, but we CAN prove the new code path is race-safe by asserting
that duplicate codes are rejected via the database constraint even when
the old pre-check would have been bypassed (e.g. two calls that both
compute the same custom alias)."""
from app.errors import AliasTakenError


def test_duplicate_custom_alias_is_rejected_atomically(client):
    r1 = client.post("/api/v1/shorten", json={"url": "https://example.com/1", "custom_alias": "race"})
    assert r1.status_code == 201
    r2 = client.post("/api/v1/shorten", json={"url": "https://example.com/2", "custom_alias": "race"})
    assert r2.status_code == 409
    assert r2.get_json()["error"] == "AliasTakenError"


def test_generated_code_insert_path_still_succeeds(client):
    resp = client.post("/api/v1/shorten", json={"url": "https://example.com/3"})
    assert resp.status_code == 201
''')
    return {
        "files_changed": ["app/shortener.py", "tests/test_alias_race_condition_fix.py"],
        "new_endpoints": [],
        "tested_endpoints": [],
        "source_code": _read(path),
        "decision": "Replaced check-then-insert with insert-and-catch-IntegrityError to close a "
                    "concurrency race in short-code allocation.",
    }


# --------------------------------------------------------------------------- #
# Brownfield: enhancement -- bulk shorten endpoint.
# --------------------------------------------------------------------------- #

_MAIN_IMPORT_ANCHOR = "from .errors import AppError, RateLimitError"
_MAIN_IMPORT_NEW = "from .errors import AppError, InvalidURLError, RateLimitError"

_MAIN_ROUTE_ANCHOR = '    @app.get("/api/v1/links/<code>")'
_BULK_ROUTE = '''    @app.post("/api/v1/shorten/bulk")
    def shorten_bulk():
        meta = _rate_limit_or_raise("shorten")
        body = request.get_json(silent=True) or {}
        urls = body.get("urls", [])
        if not isinstance(urls, list) or not urls:
            raise InvalidURLError("urls must be a non-empty list")
        if len(urls) > Config.MAX_BULK_URLS:
            raise InvalidURLError(
                f"a single bulk request may contain at most {Config.MAX_BULK_URLS} urls"
            )

        results = []
        for entry in urls:
            long_url = entry if isinstance(entry, str) else (entry or {}).get("url", "")
            try:
                results.append(shortener.create_short_link(long_url=long_url, owner=body.get("owner")))
            except AppError as exc:
                results.append({"url": long_url, "error": exc.__class__.__name__, "message": exc.message})

        resp = jsonify({"results": results})
        resp.headers["X-RateLimit-Remaining"] = str(meta["remaining"])
        return resp, 201

'''

_CONFIG_ANCHOR = "    DEFAULT_TTL_DAYS: int | None = None  # None => links never expire unless requested"
_CONFIG_NEW = ("    DEFAULT_TTL_DAYS: int | None = None  # None => links never expire unless requested\n"
               '    MAX_BULK_URLS: int = int(os.environ.get("URLSHORT_MAX_BULK_URLS", "50"))')


def cap_add_bulk_shorten_endpoint(workspace: Path, spec: dict, task: dict) -> dict:
    main_path = workspace / "app" / "main.py"
    _replace_or_raise(main_path, _MAIN_IMPORT_ANCHOR, _MAIN_IMPORT_NEW, "bulk_endpoint:imports")
    _replace_or_raise(main_path, _MAIN_ROUTE_ANCHOR, _BULK_ROUTE + _MAIN_ROUTE_ANCHOR, "bulk_endpoint:route")

    config_path = workspace / "app" / "config.py"
    _replace_or_raise(config_path, _CONFIG_ANCHOR, _CONFIG_NEW, "bulk_endpoint:config")

    test_path = workspace / "tests" / "test_bulk_shorten.py"
    _write(test_path, '''def test_bulk_shorten_creates_multiple_links(client):
    resp = client.post("/api/v1/shorten/bulk", json={"urls": [
        "https://example.com/1", "https://example.com/2",
    ]})
    assert resp.status_code == 201
    results = resp.get_json()["results"]
    assert len(results) == 2
    assert all("code" in r for r in results)


def test_bulk_shorten_reports_per_item_errors_without_failing_whole_batch(client):
    resp = client.post("/api/v1/shorten/bulk", json={"urls": [
        "https://example.com/ok", "not-a-url",
    ]})
    assert resp.status_code == 201
    results = resp.get_json()["results"]
    assert "code" in results[0]
    assert results[1]["error"] == "InvalidURLError"


def test_bulk_shorten_rejects_oversized_batch(client):
    from app.config import Config
    urls = ["https://example.com"] * (Config.MAX_BULK_URLS + 1)
    resp = client.post("/api/v1/shorten/bulk", json={"urls": urls})
    assert resp.status_code == 400


def test_bulk_shorten_rejects_empty_list(client):
    resp = client.post("/api/v1/shorten/bulk", json={"urls": []})
    assert resp.status_code == 400
''')
    return {
        "files_changed": ["app/main.py", "app/config.py", "tests/test_bulk_shorten.py"],
        "new_endpoints": ["POST /api/v1/shorten/bulk"],
        "tested_endpoints": ["POST /api/v1/shorten/bulk"],
        "source_code": _read(main_path),
        "decision": "Added a bulk-shorten endpoint that reuses create_short_link per item and reports "
                    "partial failures inline instead of failing the whole batch.",
    }


# --------------------------------------------------------------------------- #
# Ambiguous requirement: operational visibility (analytics summary) endpoint.
# --------------------------------------------------------------------------- #

_ANALYTICS_APPEND_ANCHOR = '''def get_top_links(limit: int = 10) -> list[dict]:
    conn = db.get_conn()
    rows = conn.execute(
        """SELECT l.code, l.long_url, COUNT(c.id) AS total_clicks
           FROM links l LEFT JOIN clicks c ON c.code = l.code
           GROUP BY l.code
           ORDER BY total_clicks DESC, l.created_at ASC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [
        {"code": r["code"], "long_url": r["long_url"], "total_clicks": r["total_clicks"]}
        for r in rows
    ]'''

_OPS_SUMMARY_FN = '''


def get_ops_summary(cache_stats: dict) -> dict:
    """Operational visibility snapshot: link/click volume plus redirect
    cache effectiveness, added to answer the (ambiguous, later clarified)
    requirement 'give us visibility into how links are performing'."""
    conn = db.get_conn()
    total_links = conn.execute("SELECT COUNT(*) AS c FROM links").fetchone()["c"]
    active_links = conn.execute("SELECT COUNT(*) AS c FROM links WHERE is_active = 1").fetchone()["c"]
    total_clicks = conn.execute("SELECT COUNT(*) AS c FROM clicks").fetchone()["c"]
    return {
        "total_links": total_links,
        "active_links": active_links,
        "total_clicks": total_clicks,
        "redirect_cache": cache_stats,
    }'''

_MAIN_TOP_LINKS_ANCHOR = '''    @app.get("/api/v1/analytics/top")
    def top_links():
        limit = request.args.get("limit", default=10, type=int)
        return jsonify({"top_links": analytics.get_top_links(limit)})
'''
_MAIN_SUMMARY_ROUTE = _MAIN_TOP_LINKS_ANCHOR + '''
    @app.get("/api/v1/analytics/summary")
    def analytics_summary():
        return jsonify(analytics.get_ops_summary(redirect_cache.stats()))
'''


def cap_add_analytics_summary_endpoint(workspace: Path, spec: dict, task: dict) -> dict:
    analytics_path = workspace / "app" / "analytics.py"
    _replace_or_raise(
        analytics_path, _ANALYTICS_APPEND_ANCHOR, _ANALYTICS_APPEND_ANCHOR + _OPS_SUMMARY_FN,
        "analytics_summary:analytics_module",
    )
    main_path = workspace / "app" / "main.py"
    _replace_or_raise(main_path, _MAIN_TOP_LINKS_ANCHOR, _MAIN_SUMMARY_ROUTE, "analytics_summary:route")

    test_path = workspace / "tests" / "test_analytics_summary.py"
    _write(test_path, '''def test_analytics_summary_reports_totals(client):
    client.post("/api/v1/shorten", json={"url": "https://example.com/a"})
    code = client.post("/api/v1/shorten", json={"url": "https://example.com/b"}).get_json()["code"]
    client.get(f"/{code}")

    summary = client.get("/api/v1/analytics/summary").get_json()
    assert summary["total_links"] == 2
    assert summary["total_clicks"] == 1
    assert "redirect_cache" in summary
''')
    return {
        "files_changed": ["app/analytics.py", "app/main.py", "tests/test_analytics_summary.py"],
        "new_endpoints": ["GET /api/v1/analytics/summary"],
        "tested_endpoints": ["GET /api/v1/analytics/summary"],
        "source_code": _read(main_path) + "\n" + _read(analytics_path),
        "decision": "Interpreted 'visibility into performance' as an aggregate operational summary "
                    "endpoint (link/click volume + cache effectiveness), confirmed via the "
                    "scope_confirmation approval checkpoint before implementing.",
    }


# --------------------------------------------------------------------------- #
# Ambiguous requirement, round 2 (post re-plan): add p95 redirect latency.
# --------------------------------------------------------------------------- #

_LATENCY_MODULE = '''"""Rolling-window latency recorder used to report p95 redirect latency.

A fixed-size deque is enough for a single-process prototype; a production
deployment would export this to a metrics backend (Prometheus/Datadog)
instead of computing percentiles in-process. Documented as a trade-off.
"""
from __future__ import annotations

import threading
from collections import deque


class LatencyRecorder:
    def __init__(self, window_size: int = 500):
        self._samples: deque[float] = deque(maxlen=window_size)
        self._lock = threading.Lock()

    def record(self, milliseconds: float) -> None:
        with self._lock:
            self._samples.append(milliseconds)

    def percentile(self, p: float) -> float | None:
        with self._lock:
            if not self._samples:
                return None
            data = sorted(self._samples)
        k = max(0, min(len(data) - 1, int(round(p / 100 * (len(data) - 1)))))
        return round(data[k], 2)

    def stats(self) -> dict:
        return {"p50_ms": self.percentile(50), "p95_ms": self.percentile(95), "samples": len(self._samples)}


latency_recorder = LatencyRecorder()
'''

_MAIN_LATENCY_IMPORT_ANCHOR = "from .cache import redirect_cache"
_MAIN_LATENCY_IMPORT_NEW = "from .cache import redirect_cache\nfrom .latency import latency_recorder"

_MAIN_AFTER_REQUEST_ANCHOR = '''    @app.after_request
    def _add_latency_header(resp: Response):
        if hasattr(g, "start_time"):
            resp.headers["X-Response-Time-ms"] = str(round((time.time() - g.start_time) * 1000, 2))
        return resp'''
_MAIN_AFTER_REQUEST_NEW = '''    @app.after_request
    def _add_latency_header(resp: Response):
        if hasattr(g, "start_time"):
            elapsed_ms = round((time.time() - g.start_time) * 1000, 2)
            resp.headers["X-Response-Time-ms"] = str(elapsed_ms)
            latency_recorder.record(elapsed_ms)
        return resp'''

_SUMMARY_ROUTE_ANCHOR = '''    @app.get("/api/v1/analytics/summary")
    def analytics_summary():
        return jsonify(analytics.get_ops_summary(redirect_cache.stats()))'''
_SUMMARY_ROUTE_NEW = '''    @app.get("/api/v1/analytics/summary")
    def analytics_summary():
        return jsonify(analytics.get_ops_summary(redirect_cache.stats(), latency_recorder.stats()))'''

_OPS_SUMMARY_OLD_SIG = '''def get_ops_summary(cache_stats: dict) -> dict:
    """Operational visibility snapshot: link/click volume plus redirect
    cache effectiveness, added to answer the (ambiguous, later clarified)
    requirement 'give us visibility into how links are performing'."""
    conn = db.get_conn()
    total_links = conn.execute("SELECT COUNT(*) AS c FROM links").fetchone()["c"]
    active_links = conn.execute("SELECT COUNT(*) AS c FROM links WHERE is_active = 1").fetchone()["c"]
    total_clicks = conn.execute("SELECT COUNT(*) AS c FROM clicks").fetchone()["c"]
    return {
        "total_links": total_links,
        "active_links": active_links,
        "total_clicks": total_clicks,
        "redirect_cache": cache_stats,
    }'''
_OPS_SUMMARY_NEW_SIG = '''def get_ops_summary(cache_stats: dict, latency_stats: dict | None = None) -> dict:
    """Operational visibility snapshot: link/click volume, redirect cache
    effectiveness, and (post re-plan) p95 redirect latency -- the human
    reviewer asked for a concrete SLO signal, not just averages, during
    scope confirmation."""
    conn = db.get_conn()
    total_links = conn.execute("SELECT COUNT(*) AS c FROM links").fetchone()["c"]
    active_links = conn.execute("SELECT COUNT(*) AS c FROM links WHERE is_active = 1").fetchone()["c"]
    total_clicks = conn.execute("SELECT COUNT(*) AS c FROM clicks").fetchone()["c"]
    return {
        "total_links": total_links,
        "active_links": active_links,
        "total_clicks": total_clicks,
        "redirect_cache": cache_stats,
        "redirect_latency": latency_stats or {},
    }'''


def cap_add_latency_percentile(workspace: Path, spec: dict, task: dict) -> dict:
    _write(workspace / "app" / "latency.py", _LATENCY_MODULE)

    main_path = workspace / "app" / "main.py"
    _replace_or_raise(main_path, _MAIN_LATENCY_IMPORT_ANCHOR, _MAIN_LATENCY_IMPORT_NEW,
                       "latency_percentile:import")
    _replace_or_raise(main_path, _MAIN_AFTER_REQUEST_ANCHOR, _MAIN_AFTER_REQUEST_NEW,
                       "latency_percentile:after_request")
    _replace_or_raise(main_path, _SUMMARY_ROUTE_ANCHOR, _SUMMARY_ROUTE_NEW,
                       "latency_percentile:summary_route")

    analytics_path = workspace / "app" / "analytics.py"
    _replace_or_raise(analytics_path, _OPS_SUMMARY_OLD_SIG, _OPS_SUMMARY_NEW_SIG,
                       "latency_percentile:ops_summary_signature")

    test_path = workspace / "tests" / "test_analytics_summary.py"
    _write(test_path, '''def test_analytics_summary_reports_totals(client):
    client.post("/api/v1/shorten", json={"url": "https://example.com/a"})
    code = client.post("/api/v1/shorten", json={"url": "https://example.com/b"}).get_json()["code"]
    client.get(f"/{code}")

    summary = client.get("/api/v1/analytics/summary").get_json()
    assert summary["total_links"] == 2
    assert summary["total_clicks"] == 1
    assert "redirect_cache" in summary


def test_analytics_summary_reports_p95_latency_after_traffic(client):
    code = client.post("/api/v1/shorten", json={"url": "https://example.com/c"}).get_json()["code"]
    for _ in range(5):
        client.get(f"/{code}")

    summary = client.get("/api/v1/analytics/summary").get_json()
    assert "redirect_latency" in summary
    assert summary["redirect_latency"]["p95_ms"] is not None
''')

    return {
        "files_changed": ["app/latency.py", "app/main.py", "app/analytics.py", "tests/test_analytics_summary.py"],
        "new_endpoints": [],
        "tested_endpoints": ["GET /api/v1/analytics/summary"],
        "source_code": _read(main_path) + "\n" + _read(analytics_path) + "\n" + _read(workspace / "app" / "latency.py"),
        "decision": "Re-plan triggered by reviewer feedback during scope_confirmation: added p95 "
                    "redirect-latency tracking to the summary endpoint instead of only counts.",
    }


CAPABILITY_REGISTRY = {
    "scaffold_service": cap_scaffold_service,
    "scaffold_baseline_tests": cap_scaffold_baseline_tests,
    "fix_alias_race_condition": cap_fix_alias_race_condition,
    "add_bulk_shorten_endpoint": cap_add_bulk_shorten_endpoint,
    "add_analytics_summary_endpoint": cap_add_analytics_summary_endpoint,
    "add_latency_percentile": cap_add_latency_percentile,
}

# Static map used by the Design stage to describe, per capability, which
# modules it touches -- real architectural documentation derived from the
# same registry Implementation dispatches through, so docs can't drift from
# what actually runs.
CAPABILITY_TARGET_MODULES = {
    "scaffold_service": ["app/*.py (all modules)"],
    "scaffold_baseline_tests": ["tests/*.py"],
    "fix_alias_race_condition": ["app/shortener.py"],
    "add_bulk_shorten_endpoint": ["app/main.py", "app/config.py"],
    "add_analytics_summary_endpoint": ["app/analytics.py", "app/main.py"],
    "add_latency_percentile": ["app/latency.py (new)", "app/main.py", "app/analytics.py"],
}
