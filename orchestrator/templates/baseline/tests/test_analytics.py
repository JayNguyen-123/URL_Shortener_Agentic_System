def _shorten(client, url="https://example.com/a"):
    return client.post("/api/v1/shorten", json={"url": url}).get_json()["code"]


def test_analytics_unknown_code_returns_404(client):
    resp = client.get("/api/v1/analytics/nope")
    assert resp.status_code == 404


def test_analytics_counts_multiple_clicks(client):
    code = _shorten(client)
    for _ in range(3):
        client.get(f"/{code}")
    data = client.get(f"/api/v1/analytics/{code}").get_json()
    assert data["total_clicks"] == 3
    assert data["code"] == code


def test_analytics_groups_by_referrer(client):
    code = _shorten(client)
    client.get(f"/{code}", headers={"Referer": "https://google.com"})
    client.get(f"/{code}")
    data = client.get(f"/api/v1/analytics/{code}").get_json()
    assert data["clicks_by_referrer"]["https://google.com"] == 1
    assert data["clicks_by_referrer"]["direct"] == 1


def test_top_links_orders_by_click_count(client):
    a = _shorten(client, "https://a.com")
    b = _shorten(client, "https://b.com")
    client.get(f"/{b}")
    client.get(f"/{b}")
    client.get(f"/{a}")
    top = client.get("/api/v1/analytics/top").get_json()["top_links"]
    assert top[0]["code"] == b
    assert top[0]["total_clicks"] == 2
