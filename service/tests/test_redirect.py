def _shorten(client, url="https://example.com/target"):
    resp = client.post("/api/v1/shorten", json={"url": url})
    return resp.get_json()["code"]


def test_redirect_follows_to_long_url(client):
    code = _shorten(client)
    resp = client.get(f"/{code}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"] == "https://example.com/target"


def test_redirect_unknown_code_returns_404(client):
    resp = client.get("/does-not-exist")
    assert resp.status_code == 404


def test_redirect_records_a_click(client):
    code = _shorten(client)
    client.get(f"/{code}")
    analytics = client.get(f"/api/v1/analytics/{code}").get_json()
    assert analytics["total_clicks"] == 1


def test_expired_link_returns_410(client):
    resp = client.post(
        "/api/v1/shorten", json={"url": "https://example.com", "ttl_seconds": -5}
    )
    code = resp.get_json()["code"]
    resp2 = client.get(f"/{code}")
    assert resp2.status_code == 410


def test_deactivated_link_returns_404_and_bypasses_stale_cache(client):
    code = _shorten(client)
    client.get(f"/{code}")  # warm the cache
    del_resp = client.delete(f"/api/v1/links/{code}")
    assert del_resp.status_code == 200
    resp = client.get(f"/{code}")
    assert resp.status_code == 404


def test_redirect_uses_cache_on_repeated_lookups(client):
    from app.cache import redirect_cache

    code = _shorten(client)
    client.get(f"/{code}")
    client.get(f"/{code}")
    stats = redirect_cache.stats()
    assert stats["hits"] >= 1
