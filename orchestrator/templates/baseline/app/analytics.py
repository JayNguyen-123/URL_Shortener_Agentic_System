"""Click analytics: aggregation queries over the clicks table.

IP addresses are hashed (see main.py) before they ever reach this module or
storage, so raw IPs are never persisted — a policy-guardrail requirement
enforced by the orchestrator's governance stage (see orchestrator/governance.py
POLICY_NO_RAW_PII).
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from . import db
from .errors import NotFoundError


def get_link_analytics(code: str) -> dict:
    conn = db.get_conn()
    link = conn.execute("SELECT * FROM links WHERE code = ?", (code,)).fetchone()
    if link is None:
        raise NotFoundError(f"no link found for code '{code}'")

    clicks = conn.execute(
        "SELECT clicked_at, referrer FROM clicks WHERE code = ? ORDER BY clicked_at",
        (code,),
    ).fetchall()

    by_day = Counter()
    by_referrer = Counter()
    for c in clicks:
        day = c["clicked_at"][:10]
        by_day[day] += 1
        by_referrer[c["referrer"] or "direct"] += 1

    return {
        "code": code,
        "long_url": link["long_url"],
        "created_at": link["created_at"],
        "total_clicks": len(clicks),
        "last_clicked_at": clicks[-1]["clicked_at"] if clicks else None,
        "clicks_by_day": dict(sorted(by_day.items())),
        "clicks_by_referrer": dict(by_referrer.most_common(10)),
    }


def get_top_links(limit: int = 10) -> list[dict]:
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
    ]
