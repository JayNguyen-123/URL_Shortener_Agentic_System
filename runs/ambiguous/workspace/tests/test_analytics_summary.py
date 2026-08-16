def test_analytics_summary_reports_totals(client):
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
