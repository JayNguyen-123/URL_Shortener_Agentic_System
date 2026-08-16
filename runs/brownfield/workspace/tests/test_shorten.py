def test_shorten_returns_short_url(client):
    resp = client.post("/api/v1/shorten", json={"url": "https://example.com/some/long/path"})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["long_url"] == "https://example.com/some/long/path"
    assert len(data["code"]) == 7
    assert data["short_url"].endswith(data["code"])


def test_shorten_rejects_missing_url(client):
    resp = client.post("/api/v1/shorten", json={})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "InvalidURLError"


def test_shorten_rejects_bad_scheme(client):
    resp = client.post("/api/v1/shorten", json={"url": "ftp://example.com/file"})
    assert resp.status_code == 400


def test_shorten_rejects_missing_host(client):
    resp = client.post("/api/v1/shorten", json={"url": "https://"})
    assert resp.status_code == 400


def test_shorten_rejects_oversized_url(client):
    long_url = "https://example.com/" + ("a" * 3000)
    resp = client.post("/api/v1/shorten", json={"url": long_url})
    assert resp.status_code == 400


def test_custom_alias_is_used_verbatim(client):
    resp = client.post(
        "/api/v1/shorten", json={"url": "https://example.com", "custom_alias": "my-alias"}
    )
    assert resp.status_code == 201
    assert resp.get_json()["code"] == "my-alias"


def test_custom_alias_conflict_returns_409(client):
    client.post("/api/v1/shorten", json={"url": "https://example.com", "custom_alias": "dup"})
    resp = client.post("/api/v1/shorten", json={"url": "https://other.com", "custom_alias": "dup"})
    assert resp.status_code == 409


def test_custom_alias_rejects_invalid_characters(client):
    resp = client.post(
        "/api/v1/shorten", json={"url": "https://example.com", "custom_alias": "a b!"}
    )
    assert resp.status_code == 400


def test_generated_codes_are_unique_across_many_requests(client):
    from app.rate_limit import rate_limiter

    original_max = rate_limiter.max_requests
    rate_limiter.max_requests = 1000  # isolate uniqueness from rate limiting
    try:
        codes = set()
        for _ in range(30):
            resp = client.post("/api/v1/shorten", json={"url": "https://example.com/x"})
            assert resp.status_code == 201
            codes.add(resp.get_json()["code"])
        assert len(codes) == 30
    finally:
        rate_limiter.max_requests = original_max
