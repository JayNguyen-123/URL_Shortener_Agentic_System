"""Flask application entrypoint / HTTP layer.

Keeps HTTP concerns (request parsing, status codes, headers) separate from
domain logic in shortener.py / analytics.py, so the domain layer stays unit
testable without spinning up a web server.
"""
from __future__ import annotations

import hashlib
import time

from flask import Flask, Response, g, jsonify, request

from . import analytics, db, shortener
from .cache import redirect_cache
from .config import Config
from .errors import AppError, InvalidURLError, RateLimitError
from .rate_limit import rate_limiter


def _hash_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:16]


def create_app(db_path: str | None = None) -> Flask:
    if db_path and db_path != Config.DB_PATH:
        Config.DB_PATH = db_path
        db.close_conn()  # force reconnect to the new path on next get_conn()

    app = Flask(__name__)
    db.init_db()
    redirect_cache.clear()
    rate_limiter.reset()

    # ---- middleware -----------------------------------------------------
    @app.before_request
    def _start_timer():
        g.start_time = time.time()

    @app.after_request
    def _add_latency_header(resp: Response):
        if hasattr(g, "start_time"):
            resp.headers["X-Response-Time-ms"] = str(round((time.time() - g.start_time) * 1000, 2))
        return resp

    @app.errorhandler(AppError)
    def _handle_app_error(err: AppError):
        payload = {"error": err.__class__.__name__, "message": err.message}
        payload.update(err.details)
        return jsonify(payload), err.status_code

    @app.errorhandler(404)
    def _handle_404(_err):
        return jsonify({"error": "NotFound", "message": "resource not found"}), 404

    def _rate_limit_or_raise(bucket: str):
        client_key = f"{bucket}:{request.remote_addr or 'unknown'}"
        allowed, meta = rate_limiter.allow(client_key)
        if not allowed:
            raise RateLimitError(
                "rate limit exceeded, please slow down",
                details={"rate_limit": meta},
            )
        return meta

    # ---- routes -----------------------------------------------------------
    @app.get("/api/v1/health")
    def health():
        return jsonify({"status": "ok", "cache": redirect_cache.stats()})

    @app.post("/api/v1/shorten")
    def shorten():
        meta = _rate_limit_or_raise("shorten")
        body = request.get_json(silent=True) or {}
        long_url = body.get("url", "")
        custom_alias = body.get("custom_alias")
        ttl_seconds = body.get("ttl_seconds")
        owner = body.get("owner")

        result = shortener.create_short_link(
            long_url=long_url,
            custom_alias=custom_alias,
            ttl_seconds=ttl_seconds,
            owner=owner,
        )
        resp = jsonify(result)
        resp.headers["X-RateLimit-Remaining"] = str(meta["remaining"])
        return resp, 201

    @app.post("/api/v1/shorten/bulk")
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

    @app.get("/api/v1/links/<code>")
    def get_link(code: str):
        link = shortener.get_link(code, use_cache=False)
        return jsonify(link)

    @app.delete("/api/v1/links/<code>")
    def delete_link(code: str):
        shortener.deactivate_link(code)
        return jsonify({"code": code, "is_active": False})

    @app.get("/api/v1/analytics/<code>")
    def get_analytics(code: str):
        return jsonify(analytics.get_link_analytics(code))

    @app.get("/api/v1/analytics/top")
    def top_links():
        limit = request.args.get("limit", default=10, type=int)
        return jsonify({"top_links": analytics.get_top_links(limit)})

    @app.get("/<code>")
    def redirect_to_long_url(code: str):
        link = shortener.get_link(code, use_cache=True)
        shortener.record_click(
            code,
            referrer=request.headers.get("Referer"),
            ip_hash=_hash_ip(request.remote_addr),
            user_agent=request.headers.get("User-Agent"),
        )
        resp = Response(status=302)
        resp.headers["Location"] = link["long_url"]
        return resp

    return app


if __name__ == "__main__":
    # `flask --app app.main:create_app run` also works via the app factory
    # pattern, and is the preferred way to run under a dev/prod server.
    create_app().run(host="0.0.0.0", port=8000)
